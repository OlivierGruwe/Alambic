"""
alambic_ui.doctype_generator — génération de champs de doctype depuis un PDF.

Reprend le pattern EdenAI de FlowerScan : EdenAI est un ROUTEUR LLM (endpoint
OpenAI-compatible) auquel on passe un modèle "provider/model" (ex "mistral/
mistral-large-latest") et la clé du compte. Changer de modèle = changer la
config, pas le code — c'est ce qui permet de viser une IA souveraine (Mistral)
tout en gardant l'abstraction multi-provider.

Flux : PDF → texte (extraction) → prompt LLM → JSON de champs → liste éditable.

Le point d'appel réseau (call_edenai) est isolé : tant que l'IA souveraine n'est
pas câblée en production, generate_fields_from_pdf lève NotConfiguredError, que
l'UI transforme en message clair. Le reste (parsing PDF, prompt, post-traitement)
est complet et testé.
"""

from __future__ import annotations

import json

from .doctype_schema import empty_field

# Modèle souverain visé par défaut (routable via EdenAI).
DEFAULT_PROVIDER = "mistral"
DEFAULT_MODEL = "mistral-large-latest"


class GenerationError(Exception):
    """Erreur de génération de doctype (extraction ou appel LLM)."""


class NotConfiguredError(GenerationError):
    """L'IA de génération n'est pas encore configurée pour ce déploiement."""


def extract_text_from_pdf(pdf_bytes: bytes) -> str:
    """Extrait le texte d'un PDF. Utilise pypdf (texte natif).

    Pour les PDF scannés (image), un OCR serait nécessaire — non couvert ici
    (le moteur OCR souverain sera porté séparément).
    """
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover
        raise GenerationError("pypdf n'est pas installé : impossible de lire le PDF.") from exc

    import io

    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        parts = [page.extract_text() or "" for page in reader.pages]
    except Exception as exc:
        raise GenerationError(f"PDF illisible : {exc}") from exc

    text = "\n".join(parts).strip()
    if not text:
        raise GenerationError(
            "Aucun texte extrait du PDF. S'il est scanné (image), un OCR est "
            "nécessaire — non disponible pour l'instant."
        )
    return text


def build_prompt(document_text: str) -> list[dict]:
    """Construit les messages LLM pour générer les champs d'un doctype.

    Demande au modèle de proposer une liste de champs à extraire, au format JSON
    aligné sur notre structure (field_name, field_type, field_description...).
    """
    system = (
        "Tu es un assistant qui analyse des documents administratifs et propose "
        "les champs structurés à en extraire. Réponds UNIQUEMENT en JSON valide, "
        "sans texte autour. Format attendu : "
        '{"document_type": "<nom_court_snake_case>", "fields": '
        '[{"field_name": "<snake_case>", "field_type": '
        '"string|number|date|float", "field_description": "<description courte>"}]}. '
        "Propose 5 à 15 champs pertinents."
    )
    user = f"Voici le texte d'un document. Propose les champs à extraire.\n\n{document_text[:6000]}"
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def call_edenai(
    endpoint: str,
    secret_key: str,
    provider: str,
    model: str,
    messages: list[dict],
    timeout: int = 60,
) -> dict:
    """Appelle EdenAI (endpoint OpenAI-compatible) et renvoie la réponse JSON.

    Reprend le contrat de FlowerScan : POST {model: "provider/model", messages,
    temperature:0} avec Authorization Bearer <secret_key>.

    POINT D'IMPLÉMENTATION : tant que l'endpoint/clé souverains ne sont pas
    fournis (config compte vide), on lève NotConfiguredError. Quand la config IA
    sera renseignée, ce corps fait l'appel réseau réel.
    """
    if not endpoint or not secret_key:
        raise NotConfiguredError(
            "La génération par IA n'est pas encore configurée. Renseignez la clé "
            "EdenAI du compte et l'endpoint de génération, puis réessayez."
        )

    import requests  # import local : dépendance optionnelle tant que non câblé

    resp = requests.post(
        endpoint,
        headers={
            "Authorization": f"Bearer {secret_key}",
            "Content-Type": "application/json",
        },
        json={"model": f"{provider}/{model}", "messages": messages, "temperature": 0},
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.json()


def _parse_llm_response(resp_body: dict) -> list[dict]:
    """Extrait la liste de champs de la réponse LLM (format OpenAI-compat)."""
    try:
        content = resp_body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise GenerationError("Réponse du modèle inattendue.") from exc

    # Le modèle peut entourer le JSON de ```json ... ``` : on nettoie.
    content = content.strip()
    if content.startswith("```"):
        content = content.split("```", 2)[1]
        if content.startswith("json"):
            content = content[4:]
    try:
        data = json.loads(content.strip())
    except (ValueError, TypeError) as exc:
        raise GenerationError("Le modèle n'a pas renvoyé de JSON valide.") from exc

    raw_fields = data.get("fields", [])
    fields = []
    for rf in raw_fields:
        f = empty_field()
        f["field_name"] = str(rf.get("field_name", "")).strip()
        ftype = str(rf.get("field_type", "string")).strip()
        f["field_type"] = (
            ftype if ftype in ("string", "number", "date", "float", "object", "array") else "string"
        )
        f["field_description"] = str(rf.get("field_description", "")).strip()
        if f["field_name"]:
            fields.append(f)
    return fields


def generate_fields_from_pdf(
    pdf_bytes: bytes,
    *,
    endpoint: str,
    secret_key: str,
    provider: str = DEFAULT_PROVIDER,
    model: str = DEFAULT_MODEL,
) -> list[dict]:
    """Génère une liste de champs éditables à partir d'un PDF.

    Orchestration complète : extraction texte → prompt → appel LLM → parsing.
    Lève NotConfiguredError si l'IA n'est pas câblée, GenerationError sinon.
    """
    text = extract_text_from_pdf(pdf_bytes)
    messages = build_prompt(text)
    resp = call_edenai(endpoint, secret_key, provider, model, messages)
    return _parse_llm_response(resp)
