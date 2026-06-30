"""alambic_core.ai.multi_doc_detector — détection de plusieurs documents par page.

Détecte si une page (image) contient PLUSIEURS documents physiques distincts —
typiquement des pièces d'identité photographiées ensemble (CNI + carte grise,
passeport + permis…). Le but : compter N documents et localiser chacun (bbox)
pour que le pipeline crée N sous-documents, chacun classifié/extrait séparément.

Choix d'architecture (souveraineté) :
- la détection passe par un LLM **vision** (Pixtral / Mistral vision via EdenAI),
  piloté par prompt. Contrairement à un détecteur d'objets pré-entraîné, le LLM
  comprend la sémantique « document d'identité » et reconnaît des types variés
  (carte grise française, titre de séjour…) sans entraînement dédié ;
- le provider est forcé sur Mistral/Pixtral pour rester souverain. Aucun repli
  vers un provider non souverain (contrairement au code FlowerScan d'origine).

Le coût de chaque appel est exposé (`cost`) pour être tracé par l'appelant via
record_cost, au même titre que l'OCR et la classification.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

import requests

from alambic_core.ai.edenai_endpoints import endpoint_for, normalize_llm_endpoint

logger = logging.getLogger("alambic.multi_doc")

# Provider vision souverain par défaut (Pixtral, modèle multimodal de Mistral).
DEFAULT_VISION_PROVIDER = "mistral"
DEFAULT_VISION_MODEL = "pixtral-large-latest"

# Score de confiance minimal pour retenir un document détecté.
MIN_CONFIDENCE = 0.4

# Types de documents d'identité reconnus (sert à filtrer le bruit : un visage,
# du texte, un objet de fond ne sont pas des documents). Comparaison souple
# (sous-chaîne, insensible à la casse) pour tolérer les variantes de libellé.
DOCUMENT_LABELS = (
    "passport",
    "passeport",
    "id card",
    "identity card",
    "carte d'identite",
    "carte nationale",
    "cni",
    "driving",
    "permis",
    "carte grise",
    "certificat d'immatriculation",
    "residence permit",
    "titre de sejour",
    "visa",
    "national id",
)


_SYSTEM_PROMPT = (
    "Tu es un expert en analyse de documents. Tu détectes quand PLUSIEURS "
    "documents d'identité physiques distincts apparaissent sur une même image "
    "scannée (passeport, CNI, permis de conduire, carte grise, titre de séjour, "
    "visa…). Réponds UNIQUEMENT en JSON valide, sans aucune explication."
)

_USER_PROMPT = """Analyse cette image et détecte les documents physiques distincts.

Réponds UNIQUEMENT avec ce JSON :
{
  "count": <nombre de documents distincts détectés>,
  "documents": [
    {
      "type": "<type de document>",
      "confidence": <0.0-1.0>,
      "bbox": { "x": <0-100>, "y": <0-100>, "w": <0-100>, "h": <0-100> }
    }
  ]
}

Règles :
- bbox en coordonnées pourcentage (0-100) relatives à la taille de l'image ;
- count=1 si l'image ne contient qu'un seul document (toute l'image) ;
- en cas de doute, renvoie count=1 ;
- ne détecte QUE des documents d'identité/officiels, pas le fond ni les objets.
"""


@dataclass
class MultiDocConfig:
    """Paramètres d'appel du détecteur vision (résolus depuis une Config)."""

    secret_key: str
    endpoint: str
    provider: str = DEFAULT_VISION_PROVIDER
    model: str = DEFAULT_VISION_MODEL
    # Repli (même schéma que OCR/extraction) : bascule si le primary échoue.
    fallback_provider: str = ""
    fallback_model: str = ""
    timeout: int = 60
    max_retries: int = 2


@dataclass
class MultiDocResult:
    """Résultat de détection sur une image."""

    count: int = 1
    documents: list = field(default_factory=list)  # [{type, confidence, bbox}]
    cost: float = 0.0
    provider: str = ""
    model: str = ""
    source: str = "vision_vbootstrap"

    @property
    def is_multi(self) -> bool:
        """True si plusieurs documents distincts ont été détectés."""
        return self.count > 1 and len(self.documents) > 1


def multi_doc_config_from_config(config) -> MultiDocConfig:
    """Construit la config d'appel vision depuis une Config Alambic.

    Le provider/modèle vision sont lus depuis edenai_settings (vision_llm_*),
    avec repli sur Pixtral/Mistral (souverain) si non renseignés. La clé EdenAI
    est résolue via la cascade habituelle (config → compte).
    """
    from alambic_core.ai.edenai_ocr import resolve_edenai_secret

    settings = config.edenai_settings or {}
    region = settings.get("region", "")
    provider = settings.get("vision_llm_provider") or DEFAULT_VISION_PROVIDER
    model = settings.get("vision_llm_model") or DEFAULT_VISION_MODEL
    endpoint = settings.get("vision_end_point") or endpoint_for("vision", region)

    return MultiDocConfig(
        secret_key=resolve_edenai_secret(config),
        endpoint=normalize_llm_endpoint(endpoint),
        provider=provider,
        model=model,
        fallback_provider=settings.get("fallback_vision_llm_provider", ""),
        fallback_model=settings.get("fallback_vision_llm_model", ""),
    )


def _safe_json(text: str) -> dict:
    """Parse JSON tolérant : retire les fences ```…``` et isole le 1er objet {}."""
    text = (text or "").strip()
    if text.startswith("```"):
        lines = text.split("\n")
        end = len(lines) - 1 if lines[-1].strip() == "```" else len(lines)
        text = "\n".join(lines[1:end]).strip()
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except (ValueError, TypeError):
                pass
    return {}


def _is_document_label(label: str) -> bool:
    """True si le libellé correspond à un document d'identité connu."""
    low = (label or "").strip().lower()
    return any(token in low for token in DOCUMENT_LABELS)


def _valid_bbox(bbox: dict) -> bool:
    """Vérifie qu'une bbox a x/y/w/h numériques dans [0, 100] et une aire > 0."""
    if not isinstance(bbox, dict):
        return False
    try:
        x = float(bbox.get("x"))
        y = float(bbox.get("y"))
        w = float(bbox.get("w"))
        h = float(bbox.get("h"))
    except (TypeError, ValueError):
        return False
    if w <= 0 or h <= 0:
        return False
    return all(0 <= v <= 100 for v in (x, y)) and w <= 100 and h <= 100


def parse_detection(raw: dict) -> list:
    """Normalise la réponse vision en liste [{type, confidence, bbox}] filtrée.

    Ne garde que les documents d'identité avec une bbox valide et une confiance
    suffisante. Robuste aux réponses partielles ou mal formées.
    """
    documents = raw.get("documents") if isinstance(raw, dict) else None
    if not isinstance(documents, list):
        return []

    kept = []
    for item in documents:
        if not isinstance(item, dict):
            continue
        label = str(item.get("type", "document"))
        if not _is_document_label(label):
            continue
        bbox = item.get("bbox")
        if not _valid_bbox(bbox):
            continue
        try:
            confidence = float(item.get("confidence", 1.0))
        except (TypeError, ValueError):
            confidence = 1.0
        if confidence < MIN_CONFIDENCE:
            continue
        kept.append(
            {
                "type": label,
                "confidence": round(confidence, 3),
                "bbox": {
                    "x": round(float(bbox["x"]), 1),
                    "y": round(float(bbox["y"]), 1),
                    "w": round(float(bbox["w"]), 1),
                    "h": round(float(bbox["h"]), 1),
                },
            }
        )
    return kept


class MultiDocDetector:
    """Détecteur multi-document par LLM vision (Pixtral/Mistral via EdenAI).

    Même robustesse que les autres appels EdenAI (OCR/extraction) :
    - retry HTTP sur 429/5xx (session avec backoff) ;
    - chaîne primary → fallback : bascule sur le provider de repli si le primary
      échoue, exactement comme l'OCR et l'extraction.
    La détection reste non bloquante : si toute la chaîne échoue, on renvoie un
    résultat mono-document plutôt que de lever.
    """

    def __init__(self, config: MultiDocConfig):
        self.config = config
        self.session = _build_session()

    def _call(self, provider: str, model: str, image_b64: str) -> dict:
        """Un appel vision EdenAI (lève en cas d'erreur HTTP/JSON)."""
        model_str = f"{provider}/{model}" if provider and model else (provider or model)
        response = self.session.post(
            self.config.endpoint,
            headers={
                "Authorization": f"Bearer {self.config.secret_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model_str,
                "temperature": 0,
                "messages": [
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": _USER_PROMPT},
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:image/png;base64,{image_b64}"},
                            },
                        ],
                    },
                ],
            },
            timeout=self.config.timeout,
        )
        response.raise_for_status()
        return response.json()

    def detect(self, image_b64: str) -> MultiDocResult:
        """Détecte les documents distincts sur une image (PNG base64).

        Parcourt la chaîne primary → fallback (avec retries). En cas d'échec ou
        d'ambiguïté sur toute la chaîne, renvoie un résultat mono-document
        (count=1) plutôt que de lever — la détection est un raffinement, pas une
        étape bloquante du pipeline.
        """
        # Chaîne de tentatives : primary, puis fallback si configuré.
        chain = [(self.config.provider, self.config.model)]
        if self.config.fallback_provider:
            chain.append((self.config.fallback_provider, self.config.fallback_model))

        # Source/provider rapportés = ceux du primary (pour le dashboard de coûts).
        primary_provider = self.config.provider
        primary_model = self.config.model
        source = f"vision_v{primary_model}" if primary_model else "vision_vbootstrap"

        last_exc: Exception | None = None
        for provider, model in chain:
            if not provider:
                continue
            for attempt in range(max(1, self.config.max_retries)):
                try:
                    body = self._call(provider, model, image_b64)
                except (requests.RequestException, ValueError) as exc:
                    last_exc = exc
                    logger.warning(
                        "Détection multi-doc (%s) tentative %d échouée : %s",
                        provider, attempt, exc,
                    )
                    continue

                cost = float(body.get("cost", 0) or 0)
                usage = body.get("usage") or {}
                if not cost and isinstance(usage, dict):
                    cost = float(usage.get("cost", 0) or 0)

                content = ""
                choices = body.get("choices") or []
                if choices:
                    content = (choices[0].get("message") or {}).get("content") or ""

                documents = parse_detection(_safe_json(content))
                count = len(documents) if documents else 1
                return MultiDocResult(
                    count=count,
                    documents=documents,
                    cost=cost,
                    provider=provider,
                    model=model,
                    source=source,
                )

        # Toute la chaîne a échoué → mono-document (non bloquant).
        logger.warning("Détection multi-doc indisponible (tous providers) : %s", last_exc)
        return MultiDocResult(
            count=1, provider=primary_provider, model=primary_model, source=source
        )


def _build_session():
    """Session requests avec retry sur 429/5xx (imports tardifs : requests lourd)."""
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry

    retry = Retry(
        total=2,
        backoff_factor=0.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"POST"}),
    )
    session = requests.Session()
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session
