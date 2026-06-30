"""alambic_core.ai.llm_classifier — classification documentaire par LLM (EdenAI).

Porté de FlowerScan (fcl_edenai_classifier), avec appel HTTP direct à l'endpoint
EdenAI (format OpenAI-compatible) au lieu du client litellm, par cohérence avec
le reste de la stack (OCR, embedder) et pour la souveraineté.

Deux modes (selon config.classifier_let_it_guess) :
- strict (défaut) : le LLM DOIT choisir une des catégories connues (doctypes).
- let_it_guess : le LLM peut proposer un nouveau type (avec description + champs).

Le LLM renvoie un JSON {type, description, fields, confidence}. En mode
let_it_guess, `fields` décrit aussi la stratégie d'extraction par champ
(use_ia / regexp / anchors / direction), réutilisable par l'extraction (brique G).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from alambic_core.ai.edenai_endpoints import endpoint_for, normalize_llm_endpoint
from alambic_core.ai.edenai_ocr import resolve_edenai_secret


class LLMTransientError(Exception):
    """Panne LLM transitoire/externe (auth, rate-limit, 5xx, timeout, réseau).

    Distincte des erreurs propres au document (réponse malformée, doctype
    incohérent). Une LLMTransientError signale que réessayer plus tard a du sens
    — la classification doit être différée, pas marquée en échec définitif.
    """

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


@dataclass
class ClassifierConfig:
    secret_key: str
    endpoint: str
    provider: str
    model: str = ""
    language: str = "fr"
    let_it_guess: bool = False
    account_id: str = ""
    timeout: int = 60


def classifier_config_from_config(config) -> ClassifierConfig:
    """Construit une ClassifierConfig depuis une Config Alambic."""
    settings = config.edenai_settings or {}
    general = config.general or {}
    # let_it_guess est un paramètre métier (bloc general). Repli sur l'ancien
    # emplacement (edenai_settings) pour les configs pas encore re-sauvegardées.
    let_it_guess = general.get("classifier_let_it_guess")
    if let_it_guess is None:
        let_it_guess = settings.get("classifier_let_it_guess", False)
    return ClassifierConfig(
        secret_key=resolve_edenai_secret(config),
        endpoint=normalize_llm_endpoint(
            settings.get("classifier_end_point") or settings.get("extract_end_point") or ""
        )
        or endpoint_for("classifier", settings.get("region", "")),
        provider=settings.get("classifier_provider", ""),
        model=settings.get("classifier_model", ""),
        language=settings.get("classifier_language", "fr"),
        let_it_guess=bool(let_it_guess),
        account_id=config.account_id or "",
    )


def _safe_json(text: str) -> dict:
    """Parse un JSON tolérant : direct, puis premier objet {...} trouvé."""
    if not text:
        return {}
    try:
        return json.loads(text)
    except Exception:  # noqa: BLE001
        pass
    for m in re.findall(r"\{.*?\}", text, re.DOTALL):
        try:
            return json.loads(m)
        except Exception:  # noqa: BLE001
            continue
    return {}


class LLMClassifier:
    """Classifieur documentaire par LLM EdenAI (appel HTTP direct)."""

    def __init__(self, config: ClassifierConfig):
        self.config = config
        self.registry = None
        self.total_cost = 0.0

    def set_registry(self, registry) -> None:
        self.registry = registry

    def _build_prompt(self, text: str) -> str:
        cats_with_desc = self.registry.categories_with_description if self.registry else []

        def _fmt(c: dict) -> str:
            return (
                f"  - {c['name']} : {c['description']}"
                if c.get("description")
                else f"  - {c['name']}"
            )

        cats_lines = "\n".join(_fmt(c) for c in cats_with_desc)

        if self.config.let_it_guess:
            category_rule = (
                f"Les catégories connues (avec leur description) sont :\n{cats_lines}\n"
                "- Si le document correspond clairement à une catégorie connue, utilise-la.\n"
                "- Sinon, propose un nouveau type en snake_case avec description et fields."
            )
        else:
            category_rule = (
                f"Tu DOIS choisir UNE des catégories suivantes (avec description) :\n{cats_lines}\n"
                "- NE propose PAS de nouvelle catégorie.\n"
                "- Si aucune ne correspond bien, choisis la plus proche avec confidence faible."
            )

        lang = self.config.language
        language_rule = (
            f"- Les `field_name` et `field_description` DOIVENT être en {lang}.\n"
            "- `field_name` : nom court en snake_case."
        )
        field_example = (
            '{"field_name": "numero_facture", "field_description": "Numéro unique de la facture", '
            '"field_type": "string", "field_format": "", "use_ia": 0, "regexp": "[A-Z0-9\\\\-]+", '
            '"anchors": "facture,n°,numero", "direction": "right", "max_distance": "40"}'
        )

        return f"""Tu as DEUX tâches d'égale importance : (1) classifier le document, et
(2) lister TOUS les champs utiles à en extraire. Ne néglige pas la tâche 2.

Texte :
{text}

Règles de classification :
{category_rule}

Règles pour les champs (`fields`) — OBLIGATOIRE et IMPORTANT :
- Liste TOUS les champs pertinents du document : vise 8 à 15 champs, JAMAIS moins de 8
  (si le document est riche, va jusqu'à 15). Un retour avec 2 ou 3 champs est une ERREUR.
- Parcours le document de haut en bas et n'oublie aucune donnée structurée :
  identifiants, dates, montants, noms, adresses, références, codes, totaux, etc.
{language_rule}
- Stratégie d'extraction par champ (`use_ia`, `regexp`, `anchors`, `direction`, `max_distance`) :
  * Champs STRUCTURÉS (date, email, IBAN, SIREN, n° TVA, téléphone, code postal,
    montant, références codifiées) : `use_ia`=0 et fournis une `regexp` adaptée,
    plus des `anchors` (mots-repères RÉELLEMENT présents dans le texte ci-dessus).
  * `direction` : position de la valeur vs l'ancre ("right", "left", "below", "above").
  * `max_distance` : distance max en caractères (ex "40").
  * Champs NON structurés (texte libre, nom, adresse, libellé variable) : `use_ia`=1
    et laisse `regexp`/`anchors`/`direction`/`max_distance` VIDES.
  * En cas de doute, préfère `use_ia`=1.
- Format strict JSON :
{{
  "type": "...",
  "description": "...",
  "fields": [{field_example}],
  "confidence": 0.0
}}
"""

    def _call_llm(self, text: str) -> tuple[dict, float]:
        """Appelle EdenAI (OpenAI-compat), renvoie (résultat_json, coût).

        Lève LLMTransientError pour les pannes externes (auth/crédit, rate-limit,
        5xx, timeout, réseau) afin que l'appelant puisse réessayer plus tard au
        lieu de marquer le document en échec définitif.
        """
        import requests

        provider = self.config.provider
        model = self.config.model
        model_str = f"{provider}/{model}" if provider and model else (provider or model)

        try:
            response = requests.post(
                self.config.endpoint,
                headers={
                    "Authorization": f"Bearer {self.config.secret_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model_str,
                    "temperature": 0,
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "Tu es un classifieur documentaire strict ET un "
                                "extracteur de schéma : tu classes le document et tu "
                                "listes de façon exhaustive les champs à en extraire."
                            ),
                        },
                        {"role": "user", "content": self._build_prompt(text)},
                    ],
                },
                timeout=self.config.timeout,
            )
        except (requests.Timeout, requests.ConnectionError) as exc:
            # Réseau/timeout : transitoire par nature.
            raise LLMTransientError(f"EdenAI injoignable : {exc}") from exc

        # Codes signalant une panne externe transitoire (auth/crédit, quota, serveur).
        if response.status_code in (401, 403, 408, 429) or response.status_code >= 500:
            raise LLMTransientError(
                f"EdenAI a renvoyé {response.status_code}", status_code=response.status_code
            )
        # Autres 4xx (requête malformée…) : erreur non transitoire, on laisse remonter.
        response.raise_for_status()
        body = response.json()

        cost = float(body.get("cost", 0) or 0)
        usage = body.get("usage") or {}
        if not cost and isinstance(usage, dict):
            cost = float(usage.get("cost", 0) or 0)

        content = ""
        choices = body.get("choices") or []
        if choices:
            content = (choices[0].get("message") or {}).get("content") or ""

        cleaned = content.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("```")[1]
            if cleaned.startswith("json"):
                cleaned = cleaned[4:]
            cleaned = cleaned.strip()

        return _safe_json(cleaned), cost

    def classify(self, text: str) -> tuple[dict, float]:
        """Classe le texte. Renvoie (résultat, coût).

        Résultat : {type, description, fields, confidence}. Type "unknown" si
        le LLM renvoie une réponse malformée (plutôt que de planter).
        """
        if not self.registry:
            raise RuntimeError("Registry non défini sur le LLMClassifier")

        categories = self.registry.categories
        if not categories and not self.config.let_it_guess:
            return {"type": "AUTRE", "confidence": 0.0, "description": "", "fields": []}, 0.0

        result, cost = self._call_llm(text)
        self.total_cost += cost

        return {
            "type": result.get("type", "unknown") or "unknown",
            "description": result.get("description", ""),
            "fields": result.get("fields", []),
            "confidence": float(result.get("confidence", 0.0) or 0.0),
        }, cost
