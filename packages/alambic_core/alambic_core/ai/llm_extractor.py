"""alambic_core.ai.llm_extractor — extraction de champs par LLM (EdenAI).

Porté de FlowerScan (fcl_edenai_extractor.EdenExtractorV3). Pour les champs marqués
use_ia=1, on demande à un LLM (via EdenAI v3, format OpenAI) d'extraire la valeur de
chaque champ depuis le texte du document, avec un score de confiance.

Le prompt est strict : « n'extraire que ce qui est explicitement présent, ne jamais
inventer, renvoyer uniquement du JSON ». La réponse attendue est
{field: {"value": "...", "score": "0.0-1.0"}}. Le parsing est tolérant (nettoie les
balises markdown, récupère le plus grand objet JSON) car les LLM encadrent souvent
leur JSON de texte.

Comme le classifier, l'endpoint est construit depuis la région de la config, et la
chaîne primary → fallback bascule sur échec. Le coût réel est accumulé.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

from alambic_core.ai.edenai_endpoints import endpoint_for, normalize_llm_endpoint
from alambic_core.ai.edenai_ocr import _extract_secret_key

logger = logging.getLogger(__name__)


@dataclass
class ExtractorConfig:
    """Paramètres d'un appel d'extraction LLM EdenAI."""

    secret_key: str
    endpoint: str
    provider: str
    model: str
    fallback_provider: str = ""
    fallback_model: str = ""
    account_id: str = ""
    timeout: int = 60
    max_retries: int = 2


def extractor_config_from_config(config) -> ExtractorConfig:
    """Construit une ExtractorConfig depuis une Config Alambic.

    Endpoint construit par région (extract → /v3/llm). Provider/modèle lus depuis
    edenai_settings (extract_provider/extract_model + fallbacks).
    """
    settings = config.edenai_settings or {}
    return ExtractorConfig(
        secret_key=_extract_secret_key(config.edenai_secret_enc),
        endpoint=normalize_llm_endpoint(settings.get("extract_end_point") or "")
        or endpoint_for("extract", settings.get("region", "")),
        provider=settings.get("extract_provider", ""),
        model=settings.get("extract_model", ""),
        fallback_provider=settings.get("fallback_extract_provider", ""),
        fallback_model=settings.get("fallback_extract_model", ""),
        account_id=config.account_id or "",
    )


def _field_name(f) -> str:
    """Nom d'un champ, qu'il soit une chaîne ou un dict."""
    return f if isinstance(f, str) else (f.get("field_name") or "")


def empty_indexes(field_names: list[str]) -> dict:
    """Indexes vides pour chaque champ (réponse honnête : rien trouvé)."""
    return {f: {"value": "", "score": "0.0"} for f in field_names}


def normalize(raw: dict, field_names: list[str]) -> dict:
    """Normalise la réponse LLM en {field: {value, score}} pour tous les champs.

    Tolère que le LLM renvoie une valeur nue (str) au lieu de {value, score}.
    """
    out = {}
    for f in field_names:
        v = (raw or {}).get(f)
        if isinstance(v, dict):
            value = str(v.get("value", "")).strip()
            try:
                score = str(float(v.get("score", "0.0") or 0.0))
            except (TypeError, ValueError):
                score = "0.0"
        else:
            value = str(v or "").strip()
            score = str(0.5 if value else 0.0)
        out[f] = {"value": value, "score": score}
    return out


def safe_json(content: str) -> dict:
    """Parse tolérant : nettoie les balises markdown, récupère le plus grand objet JSON."""
    if not content:
        return {}

    cleaned = content.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        end = len(lines) - 1 if lines[-1].strip() == "```" else len(lines)
        cleaned = "\n".join(lines[1:end]).strip()

    try:
        return json.loads(cleaned)
    except (json.JSONDecodeError, TypeError):
        pass

    # Plus grand objet JSON (greedy + DOTALL) : évite d'attraper un {} vide.
    m = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except (json.JSONDecodeError, TypeError):
            pass
    return {}


class LLMExtractor:
    """Extracteur de champs par LLM EdenAI, avec fallback et accumulation de coût."""

    def __init__(self, config: ExtractorConfig):
        self.config = config
        self.total_cost = 0.0

    def _build_messages(self, text: str, fields: list) -> list:
        """Construit les messages système + utilisateur (prompt d'extraction stricte)."""
        field_list = "\n".join(
            f"- {_field_name(f)}: {f.get('field_description', '')}"
            if isinstance(f, dict)
            else f"- {f}"
            for f in fields
        )
        schema = (
            "{\n"
            + ",\n".join(f'"{_field_name(f)}": {{"value": "", "score": "0.0"}}' for f in fields)
            + "\n}"
        )
        return [
            {
                "role": "system",
                "content": (
                    "You are a strict information extraction engine.\n"
                    "You must extract only explicitly present data from the text.\n"
                    "Never invent values.\n"
                    "Return ONLY valid JSON.\n"
                    "No explanation, no extra text."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Extract the following fields:\n{field_list}\n\n"
                    "Rules:\n"
                    "- If a value is not found, return empty string.\n"
                    "- Do not guess or infer.\n"
                    "- Keep original formatting.\n"
                    "- Score between 0.0 and 1.0 based on confidence.\n\n"
                    f"Expected JSON schema:\n{schema}\n\n"
                    f"Text:\n{text}"
                ),
            },
        ]

    def _call_llm(self, provider: str, model: str, messages: list) -> tuple[dict, float]:
        """Un appel LLM. Renvoie (corps_json, coût)."""
        import requests

        full_model = (
            f"{provider}/{model}"
            if provider and model and "/" not in provider
            else (provider or model)
        )
        response = requests.post(
            self.config.endpoint,
            headers={
                "Authorization": f"Bearer {self.config.secret_key}",
                "Content-Type": "application/json",
            },
            json={"model": full_model, "messages": messages, "temperature": 0},
            timeout=self.config.timeout,
        )
        response.raise_for_status()
        body = response.json()
        cost = float(body.get("cost", 0) or 0)
        usage = body.get("usage") or {}
        if not cost and isinstance(usage, dict):
            cost = float(usage.get("cost", 0) or 0)
        return body, cost

    @staticmethod
    def _content_of(body: dict) -> str:
        """Extrait le texte de la réponse (format OpenAI : choices[0].message.content)."""
        choices = body.get("choices") or []
        if choices and isinstance(choices[0], dict):
            msg = choices[0].get("message") or {}
            return msg.get("content") or ""
        # Replis possibles selon le wrapping EdenAI.
        return body.get("generated_text") or body.get("content") or ""

    def extract(self, text: str, doctype_name: str, doctype_desc: str, fields: list) -> dict:
        """Extrait les champs use_ia depuis le texte.

        Renvoie {"indexes": {field: {value, score}}, "extraction": {cost, provider, model, ...}}.
        Si aucun champ ou texte vide, renvoie des indexes vides (réponse honnête).
        """
        field_names = [_field_name(f) for f in fields if _field_name(f)]
        if not fields or not text.strip():
            return {
                "indexes": empty_indexes(field_names),
                "extraction": self._cost_payload(0, "", ""),
            }

        chain = [("primary", self.config.provider, self.config.model)]
        if self.config.fallback_provider:
            chain.append(("fallback", self.config.fallback_provider, self.config.fallback_model))

        messages = self._build_messages(text, fields)
        last_exc: Exception | None = None

        for name, provider, model in chain:
            if not provider:
                continue
            for attempt in range(self.config.max_retries):
                try:
                    body, cost = self._call_llm(provider, model, messages)
                    self.total_cost += cost
                    parsed = normalize(safe_json(self._content_of(body)), field_names)
                    return {
                        "indexes": parsed,
                        "extraction": self._cost_payload(
                            cost, body.get("provider", provider), model
                        ),
                    }
                except Exception as exc:  # noqa: BLE001
                    last_exc = exc
                    logger.warning(
                        "Extraction LLM %s tentative %d échouée : %s", name, attempt, exc
                    )

        logger.error("Extraction LLM échouée pour tous les providers : %s", last_exc)
        # Échec total : indexes vides (le résumé signalera les required manquants).
        return {"indexes": empty_indexes(field_names), "extraction": self._cost_payload(0, "", "")}

    def _cost_payload(self, cost: float, provider: str, model: str) -> dict:
        """Payload de coût/méta pour la persistance."""
        return {
            "cost": cost,
            "provider": provider,
            "model": model,
            "account_id": self.config.account_id,
        }
