"""alambic_core.services.consolidation_ws — définition d'un WS de consolidation.

Porté de FlowerScan (core.fcl_consolidation_ws). Une définition décrit COMMENT
appeler un web service externe pour valider/enrichir un champ extrait (ex.
vérifier un numéro de compte contre une base partenaire, et récupérer le nom du
titulaire). Les définitions vivent au niveau config (Config.consolidation_ws) ;
un champ de doctype les référence par leur `name`.

Schéma d'une définition (dict) :
{
    "name":            "client_lookup",          # identifiant unique (référé par les champs)
    "label":           "Vérification client",    # libellé UI
    "url":             "https://api.part.com/clients/{value}",  # {value} = valeur du champ
    "method":          "GET",                    # GET | POST
    "auth_type":       "none",                   # none | header | query
    "auth_key":        "X-API-Key",              # nom du header/param (si auth)
    "auth_secret":     "<chiffré>",              # secret chiffré (jamais en clair)
    "request_field":   "numero",                 # si POST : nom du champ JSON envoyé (= {value})
    "response_status_path": "found",             # clé de réponse → statut (bool/str)
    "response_mapping": {                        # clé de réponse → nom d'index à écrire
        "raison_sociale": "client_nom",
        "ville":          "client_ville"
    },
    "timeout":         5,                         # secondes
    "on_failure":      "skip"                     # skip (défaut, non bloquant) | error (bloquant)
}
"""

from __future__ import annotations

from ..security.url_guard import UrlGuardError, validate_url

_METHODS = {"GET", "POST"}
_AUTH_TYPES = {"none", "header", "query"}
_ON_FAILURE = {"skip", "error"}

DEFAULT_TIMEOUT = 5
DEFAULT_ON_FAILURE = "skip"  # non bloquant par défaut : un WS down ne gèle pas le pipeline


class WsDefinitionError(Exception):
    """Définition de WS invalide."""


def _empty(v) -> bool:
    return v is None or str(v).strip() == ""


def normalize_ws_definition(ws: dict) -> dict:
    """Applique les valeurs par défaut sur une définition (copie)."""
    d = dict(ws or {})
    d.setdefault("method", "GET")
    d.setdefault("auth_type", "none")
    d.setdefault("response_mapping", {})
    d.setdefault("timeout", DEFAULT_TIMEOUT)
    d.setdefault("on_failure", DEFAULT_ON_FAILURE)

    d["method"] = str(d.get("method", "GET")).upper()
    d["auth_type"] = str(d.get("auth_type", "none")).lower()
    d["on_failure"] = str(d.get("on_failure", DEFAULT_ON_FAILURE)).lower()
    try:
        d["timeout"] = int(d.get("timeout", DEFAULT_TIMEOUT))
    except (TypeError, ValueError):
        d["timeout"] = DEFAULT_TIMEOUT
    return d


def validate_ws_definition(ws: dict, allowed_domains=None) -> list[str]:
    """Valide une définition. Retourne la liste des erreurs (vide → OK)."""
    errors = []
    d = normalize_ws_definition(ws)

    if _empty(d.get("name")):
        errors.append("nom (name) requis")

    if _empty(d.get("url")):
        errors.append("url requise")
    else:
        # Anti-SSRF : on valide en remplaçant {value} par un segment neutre.
        probe = d["url"].replace("{value}", "x")
        try:
            validate_url(probe, allowed_domains=allowed_domains)
        except UrlGuardError as e:
            errors.append(f"url refusée : {e}")

    if d["method"] not in _METHODS:
        errors.append(f"method invalide : {d['method']} (GET/POST)")

    if d["auth_type"] not in _AUTH_TYPES:
        errors.append(f"auth_type invalide : {d['auth_type']} (none/header/query)")

    if d["auth_type"] in ("header", "query") and _empty(d.get("auth_key")):
        errors.append("auth_key requise quand auth_type = header/query")

    if d["on_failure"] not in _ON_FAILURE:
        errors.append(f"on_failure invalide : {d['on_failure']} (skip/error)")

    if d["timeout"] <= 0 or d["timeout"] > 30:
        errors.append("timeout doit être entre 1 et 30 secondes")

    rm = d.get("response_mapping")
    if rm is not None and not isinstance(rm, dict):
        errors.append("response_mapping doit être un objet {clé_réponse: nom_index}")

    # target_field : "@doctype:champ" — le champ ciblé par ce WS.
    target = d.get("target_field") or ""
    if _empty(target):
        errors.append("champ cible (target_field) requis : @doctype:champ")
    else:
        t = str(target).strip().lstrip("@")
        if ":" not in t or not t.split(":", 1)[0].strip() or not t.split(":", 1)[1].strip():
            errors.append("target_field invalide : attendu @doctype:champ")

    return errors


def validate_all(ws_list: list, allowed_domains=None) -> dict:
    """Valide une liste de définitions. Retourne {nom_ou_index: [erreurs]}.

    Vérifie aussi l'unicité des noms. Dict vide → tout est valide.
    """
    out = {}
    seen = set()
    for i, ws in enumerate(ws_list or []):
        key = (ws or {}).get("name") or f"#{i}"
        errs = validate_ws_definition(ws, allowed_domains=allowed_domains)
        name = (ws or {}).get("name")
        if name:
            if name in seen:
                errs.append(f"nom en double : {name}")
            seen.add(name)
        if errs:
            out[key] = errs
    return out


def ws_by_name(ws_list: list, name: str) -> dict | None:
    """Retrouve une définition par son name dans la liste de la config."""
    if not name:
        return None
    for ws in ws_list or []:
        if (ws or {}).get("name") == name:
            return ws
    return None
