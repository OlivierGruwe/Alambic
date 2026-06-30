"""alambic_core.ai.edenai_ocr — client OCR EdenAI (DocumentOcr porté).

Porté de FlowerScan (fcl_edenai_ocr.DocumentOcr). Appelle l'API EdenAI pour OCR
un document, avec :
- upload du fichier une seule fois (EdenAI référence ensuite par file_id) ;
- chaîne de providers primary → fallback (bascule sur échec OU résultat pauvre) ;
- circuit breaker par provider (cesse d'appeler un provider en panne) ;
- retry HTTP (429/5xx) ;
- accumulation du coût réel (chaque appel abouti est facturé, même si on bascule) ;
- parsing des positions de lignes selon le provider.

La config réseau (provider, endpoint, clé) est passée via OcrConfig, un petit
dataclass découplé du modèle SQLAlchemy — plus facile à tester et à construire
depuis une Config Alambic (cf. ocr_config_from_config).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from alambic_core.ai.circuit_breaker import CircuitBreaker
from alambic_core.ai.edenai_endpoints import endpoint_for, upload_endpoint
from alambic_core.ai.ocr_parsing import parse_positioned_lines

logger = logging.getLogger(__name__)

# Valeur par défaut dérivée de la région par défaut (eu), pour rester cohérente
# avec endpoint_for/upload_endpoint et ne pas coder « api.edenai.run » en dur.
# En pratique ocr_config_from_config surcharge toujours via upload_endpoint(region).
UPLOAD_URL = upload_endpoint()


@dataclass
class OcrConfig:
    """Paramètres nécessaires à un appel OCR EdenAI."""

    secret_key: str
    endpoint: str
    provider: str
    fallback_provider: str = ""
    language: str = "fr"
    account_id: str = ""
    upload_url: str = UPLOAD_URL


@dataclass
class OcrResult:
    """Résultat d'un OCR : texte, lignes positionnées, markdown, coût, provider."""

    text: str = ""
    lines: list = field(default_factory=list)
    pages_markdown: list = field(default_factory=list)
    provider: str = ""
    model: str = ""
    cost: float = 0.0
    raw_output: dict = field(default_factory=dict)


def _extract_secret_key(edenai_secret_enc: str) -> str:
    """Extrait la clé EdenAI du bloc déchiffré.

    edenai_secret_enc, déchiffré par EncryptedString, contient un JSON sérialisé
    {"secret_key": "..."} (cf. UI config_schema). On en extrait secret_key. Si ce
    n'est pas du JSON (ancienne donnée, ou clé brute), on renvoie la valeur telle
    quelle.
    """
    import json

    raw = edenai_secret_enc or ""
    if not raw:
        return ""
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data.get("secret_key", "") or ""
    except (json.JSONDecodeError, TypeError):
        pass
    return raw


def resolve_edenai_secret(config) -> str:
    """Résout la clé EdenAI effective pour une config, avec cascade.

    Modèle métier : la clé vit sur le COMPTE (Account.edenai_secret_key),
    partagée par toutes ses configs ; mais une CONFIG peut la surcharger
    (config.edenai_secret_enc). Règle de résolution :

      1. si la config a sa propre clé → on l'utilise (override) ;
      2. sinon → on retombe sur la clé du compte.

    Robuste vis-à-vis des sessions : pour lire la clé du compte, on tente d'abord
    la relation chargée (config.account) ; si elle n'est pas accessible (config
    détachée de sa session), on requête explicitement le compte par account_id.
    Renvoie une chaîne vide si aucune des deux n'est définie.
    """
    # 1. Override au niveau config.
    config_key = _extract_secret_key(getattr(config, "edenai_secret_enc", "") or "")
    if config_key:
        return config_key

    # 2. Repli sur la clé du compte — via la relation si dispo, sinon par requête.
    account_secret = ""
    try:
        account = getattr(config, "account", None)
        if account is not None:
            account_secret = getattr(account, "edenai_secret_key", "") or ""
    except Exception:  # noqa: BLE001
        # config détachée : la relation lazy n'est pas chargeable hors session.
        account_secret = ""

    if not account_secret:
        account_id = getattr(config, "account_id", None)
        if account_id:
            account_secret = _account_secret_by_id(account_id)

    return _extract_secret_key(account_secret)


def _account_secret_by_id(account_id: str) -> str:
    """Lit Account.edenai_secret_key par requête explicite (session dédiée)."""
    from alambic_core.db.session import session_scope
    from alambic_core.models import Account

    try:
        with session_scope() as s:
            acc = s.get(Account, account_id)
            return (acc.edenai_secret_key or "") if acc is not None else ""
    except Exception:  # noqa: BLE001
        return ""


def ocr_config_from_config(config) -> OcrConfig:
    """Construit une OcrConfig depuis une Config Alambic.

    Lit edenai_settings (JSONB) pour provider/endpoint/langue et
    edenai_secret_enc (déchiffré, JSON {secret_key}) pour la clé.
    """
    settings = config.edenai_settings or {}
    region = settings.get("region", "")
    return OcrConfig(
        secret_key=resolve_edenai_secret(config),
        endpoint=settings.get("ocr_end_point") or endpoint_for("ocr", region),
        provider=settings.get("ocr_provider", ""),
        fallback_provider=settings.get("fallback_ocr_provider", ""),
        language=settings.get("ocr_language", "fr"),
        account_id=config.account_id or "",
        upload_url=upload_endpoint(region),
    )


def _build_session():
    """Session requests avec retry sur 429/5xx (imports tardifs : requests lourd)."""
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry

    session = requests.Session()
    retry = Retry(
        total=3,
        backoff_factor=0.5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["POST"],
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def _is_poor_ocr(output: dict) -> bool:
    """Heuristique : résultat OCR pauvre (déclenche le fallback contenu)."""
    text = (output.get("text") or "").strip()
    boxes = output.get("bounding_boxes", [])
    if not text and not boxes:
        return True
    return len(text) < 10


class DocumentOcr:
    """Client OCR EdenAI avec fallback et circuit breaker (un par instance)."""

    def __init__(self, ocr_config: OcrConfig):
        self.conf = ocr_config
        self.session = _build_session()
        self.breakers = {"primary": CircuitBreaker(), "fallback": CircuitBreaker()}

    def _upload(self, file_path: str, filename: str) -> str:
        """Upload le fichier sur EdenAI, renvoie le file_id."""
        with open(file_path, "rb") as fh:
            return self._upload_stream(fh, filename)

    def _upload_bytes(self, data: bytes, filename: str) -> str:
        """Upload des octets (image rendue) sur EdenAI, renvoie le file_id."""
        import io

        return self._upload_stream(io.BytesIO(data), filename)

    def _upload_stream(self, stream, filename: str) -> str:
        resp = self.session.post(
            self.conf.upload_url,
            headers={"Authorization": f"Bearer {self.conf.secret_key}"},
            files={"file": (filename, stream)},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        file_id = data.get("file_id") or data.get("id") or ""
        if not file_id:
            raise ValueError("Upload EdenAI : file_id absent de la réponse")
        return file_id

    def _call_ocr(self, provider: str, file_id: str) -> tuple[dict, float, str, str]:
        """Un appel OCR. Renvoie (output, cost, provider_effectif, model)."""
        payload = {
            "model": provider,
            "input": {"language": self.conf.language, "file": file_id},
            "provider_params": {},
            "show_original_response": True,
        }
        resp = self.session.post(
            self.conf.endpoint,
            headers={"Authorization": f"Bearer {self.conf.secret_key}"},
            json=payload,
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        if "output" not in data:
            raise ValueError("Réponse OCR sans 'output'")

        output = data["output"]
        prov = data.get("provider", provider)
        cost = data.get("cost", 0) or 0
        model = data.get("subfeature", "")

        # Positions précises depuis original_response (best-effort).
        original = data.get("original_response") or output.get("original_response") or {}
        try:
            positioned = parse_positioned_lines(original, prov)
            if positioned:
                output["_lines_positioned"] = positioned
        except Exception:  # noqa: BLE001
            pass
        # Markdown structuré par page (Mistral le fournit ; sinon vide).
        try:
            from alambic_core.ai.ocr_parsing import mistral_pages_markdown

            if "mistral" in prov.lower():
                pages_md = mistral_pages_markdown(original)
                if pages_md:
                    output["_pages_markdown"] = pages_md
        except Exception:  # noqa: BLE001
            pass
        # On conserve la réponse native (utile au diagnostic / reparse éventuel).
        if original and "original_response" not in output:
            output["original_response"] = original

        return output, float(cost), prov, model

    def ocr(self, file_path: str, filename: str) -> OcrResult:
        """OCR un document depuis un fichier. Lève si tout échoue."""
        file_id = self._upload(file_path, filename)
        return self._ocr_with_file_id(file_id)

    def ocr_bytes(self, data: bytes, filename: str) -> OcrResult:
        """OCR un document depuis des octets (image rendue). Lève si tout échoue."""
        file_id = self._upload_bytes(data, filename)
        return self._ocr_with_file_id(file_id)

    def _ocr_with_file_id(self, file_id: str) -> OcrResult:
        chain = [("primary", self.conf.provider)]
        if self.conf.fallback_provider:
            chain.append(("fallback", self.conf.fallback_provider))

        accumulated_cost = 0.0
        last_exc: Exception | None = None

        for name, provider in chain:
            breaker = self.breakers[name]
            if not breaker.allow():
                logger.warning("OCR breaker OUVERT → skip %s", name)
                continue

            for attempt in range(2):
                try:
                    output, cost, prov, model = self._call_ocr(provider, file_id)
                    accumulated_cost += cost

                    # Fallback sur contenu pauvre (primary seulement).
                    if name == "primary" and _is_poor_ocr(output) and len(chain) > 1:
                        logger.warning("OCR pauvre (%s) → fallback", provider)
                        break

                    breaker.record_success()
                    return OcrResult(
                        text=(output.get("text") or ""),
                        lines=output.get("_lines_positioned", []),
                        pages_markdown=output.get("_pages_markdown", []),
                        provider=prov,
                        model=model,
                        cost=accumulated_cost,
                        raw_output=output,
                    )
                except Exception as exc:  # noqa: BLE001
                    last_exc = exc
                    logger.warning("OCR %s tentative %d échouée : %s", name, attempt, exc)
                    if attempt == 0:
                        time.sleep(0.5)
                    else:
                        breaker.record_failure()
                        break

        raise RuntimeError(f"OCR échoué pour tous les providers : {last_exc}")
