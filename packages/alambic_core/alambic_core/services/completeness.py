"""alambic_core.services.completeness — complétude d'un dossier.

Une config peut décrire un « dossier » : un ensemble de doctypes attendus, chacun
obligatoire ou optionnel (config.expected_doctypes). Ce service vérifie qu'une
transaction contient bien tous les doctypes OBLIGATOIRES (au moins un document de
chaque type), et expose un résumé pour l'UI et le blocage d'export.

Mesure en présence : « au moins un de chaque type obligatoire ». Le compte exact
n'est pas exigé (un dossier avec deux permis est complet comme avec un seul).

Un dossier est COMPLET si tous les doctypes obligatoires ont au moins un document
classé dans ce type parmi les documents actifs de la transaction.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .transaction_status import active_documents


@dataclass
class CompletenessResult:
    """État de complétude d'une transaction au regard de sa config."""

    enabled: bool = False  # la config demande-t-elle une vérification ?
    complete: bool = True  # tous les doctypes obligatoires présents ?
    required_total: int = 0
    required_present: int = 0
    missing_required: list[str] = field(default_factory=list)  # doctype_ids manquants
    present_doctypes: list[str] = field(default_factory=list)  # doctype_ids présents
    optional_present: list[str] = field(default_factory=list)
    optional_missing: list[str] = field(default_factory=list)


def _expected_entries(config) -> list[dict]:
    """Liste normalisée [{doctype_id, required}] depuis config.expected_doctypes.

    Tolère l'ancien format (liste d'IDs nus dans edenai_settings.doctype_ids),
    auquel cas tous sont considérés obligatoires.
    """
    raw = getattr(config, "expected_doctypes", None) or []
    entries = []
    for item in raw:
        if isinstance(item, dict) and item.get("doctype_id"):
            entries.append(
                {"doctype_id": item["doctype_id"], "required": bool(item.get("required", True))}
            )
        elif isinstance(item, str) and item:
            entries.append({"doctype_id": item, "required": True})
    return entries


def _doctype_of(doc) -> str:
    """Identifiant du doctype d'un document (nom ou id, selon ce qui est posé)."""
    if isinstance(doc, dict):
        return doc.get("doctype") or doc.get("doctype_id") or ""
    return getattr(doc, "doctype", "") or getattr(doc, "doctype_id", "") or ""


def _norm(value: str) -> str:
    """Normalise un nom de doctype pour une comparaison robuste : minuscules,
    espaces de bord retirés. Les classifications ("Facture") et les noms de
    doctype ("facture") diffèrent souvent par la casse uniquement.
    """
    return str(value or "").strip().lower()


def compute_completeness(
    config, documents: list, doctype_names: dict | None = None
) -> CompletenessResult:
    """Calcule l'état de complétude d'une transaction selon sa config.

    Le contrôle de complétude est IMPLICITE : il s'active dès qu'au moins un
    doctype attendu est marqué obligatoire. S'il n'y a aucun doctype obligatoire,
    il n'y a rien à contrôler (enabled=False / complete=True).

    Référentiels : `expected_doctypes` (config) liste des doctype_id, tandis que
    `document.doctype` porte le NOM du type classifié. `doctype_names` (mapping
    doctype_id → doctype_name) permet de comparer les deux : un doctype attendu
    est présent si un document porte son id OU son nom. Sans ce mapping, on
    compare tel quel (rétrocompat, mais id vs nom ne matcheront pas).
    """
    names = doctype_names or {}
    entries = _expected_entries(config)
    required = [e["doctype_id"] for e in entries if e["required"]]

    # Activation implicite : au moins un doctype obligatoire.
    if not required:
        return CompletenessResult(enabled=False, complete=True)

    optional = [e["doctype_id"] for e in entries if not e["required"]]

    # Types présents parmi les documents actifs (au moins un de ce type).
    actives = active_documents(documents)
    present = {_doctype_of(d) for d in actives if _doctype_of(d)}
    # Variante normalisée (casse/espaces) pour comparer les NOMS de façon robuste :
    # un document classé "Facture" doit matcher le doctype nommé "facture".
    present_norm = {_norm(p) for p in present}

    def _is_present(doctype_id: str) -> bool:
        # Présent si un document porte cet id exact OU le nom correspondant
        # (comparaison de nom insensible à la casse et aux espaces).
        if doctype_id in present:
            return True
        name = names.get(doctype_id)
        return bool(name) and _norm(name) in present_norm

    missing_required = [dt for dt in required if not _is_present(dt)]
    required_present = [dt for dt in required if _is_present(dt)]

    return CompletenessResult(
        enabled=True,
        complete=not missing_required,
        required_total=len(required),
        required_present=len(required_present),
        missing_required=sorted(missing_required),
        present_doctypes=sorted(present),
        optional_present=sorted(dt for dt in optional if _is_present(dt)),
        optional_missing=sorted(dt for dt in optional if not _is_present(dt)),
    )


def doctype_ids_from_expected(config) -> list[str]:
    """Tous les doctype_ids attendus (obligatoires + optionnels), pour la classification.

    Permet de dériver la restriction de classification depuis expected_doctypes,
    qui devient la source de vérité unique.
    """
    return [e["doctype_id"] for e in _expected_entries(config)]
