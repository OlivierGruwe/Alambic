"""alambic_core.ai.field_extractor — extraction d'un champ depuis une page OCR.

Porté de FlowerScan (fcl_field_extractor). Extrait la valeur d'un champ de
doctype selon 5 stratégies, dans l'ordre (premier match gagne) :
  1. Barcode  (si bcr_type) — lit doc["barcodes"] (readCAB)
  2. Anchor   (si anchors + regexp) — cherche près d'un libellé
  3. Zone     (si page_zone + regexp) — cherche dans une zone géométrique
  4. Regex    (si regexp) — cherche dans tout le texte de la page
  5. default_value

Structure attendue d'une page `doc` (= une page de PdfExtractor.to_json()) :
    {"lines": [{"text": <b64>, "position": {"x0","y0","x1","y1"}}],
     "barcodes": [{"value","format","position"}]}
Positions en pourcentages (0-100). Texte des lignes encodé base64.

Réutilisé par le découpage (brique F, champs séparateurs) et l'extraction
(brique G, tous les champs).
"""

from __future__ import annotations

import base64
import re


def _decode_text(data: str) -> str:
    """Décode le texte base64 d'une ligne (tolérant : '' si invalide)."""
    try:
        return base64.b64decode(data).decode("utf-8")
    except Exception:  # noqa: BLE001
        return ""


# ─── Compilation / parsing utilities ─────────────────────────────────────


def _split_anchor_terms(anchors: str | None) -> list[str]:
    """
    Decoupe une liste d'ancres en termes normalises (minuscule, strip).

    Tolere DEUX separateurs : la virgule ',' ET le pipe '|'. Historiquement le
    moteur splittait uniquement sur '|', alors que l'UI (et la generation de
    doctype) produisent des ancres separees par des virgules — d'ou des ancres
    jamais matchees ("Passeport n°,Passport no" traite comme une seule ancre).
    On accepte desormais les deux pour compatibilite ascendante.
    """
    if not anchors:
        return []
    raw = anchors.replace("|", ",")
    return [a.strip().lower() for a in raw.split(",") if a.strip()]


def compile_regexp(reg):
    """
    Compile une regex. Tolère :
      - "" / None         → None
      - "/.../flags"      → re.compile avec flags (i seulement supporté)
      - "<pattern>"       → re.compile(pattern) sans flag
    """
    if not reg:
        return None

    if reg.startswith("/"):
        body = reg.rsplit("/", 1)[0][1:]
        flags = reg.rsplit("/", 1)[1]
        f = 0
        if "i" in flags:
            f |= re.IGNORECASE
        return re.compile(body, f)

    return re.compile(reg)


def apply_blacklist(value: str | None, black_words: str | None) -> str | None:
    """
    Retourne None si la valeur contient un des black_words (séparés par '|').
    Comparaison case-insensitive.
    """
    if not value or not black_words:
        return value

    for w in black_words.split("|"):
        if w and w.lower() in value.lower():
            return None

    return value


def parse_zone(z: str | None):
    """
    Parse "x1,y1,x2,y2" → tuple(int, int, int, int) ou None si invalide.
    Coordonnées en % (0-100).
    """
    if not z:
        return None
    parts = z.split(",")
    if len(parts) != 4:
        return None
    try:
        return tuple(map(int, parts))
    except (TypeError, ValueError):
        return None


# ─── Normalisation des lignes en items géométriques ──────────────────────


def normalize_items(doc) -> list:
    """
    Transforme `doc["lines"]` en liste d'items "{text, x1, x2, y1, y2}".
    Texte b64-décodé et strippé. Lignes vides ignorées.
    """
    items = []
    for ln in doc.get("lines", []):
        txt = _decode_text(ln.get("text", "")).strip()
        if not txt:
            continue
        pos = ln.get("position") or {}
        items.append(
            {
                "text": txt,
                "x1": pos.get("x0", 0),
                "x2": pos.get("x1", 0),
                "y1": pos.get("y0", 0),
                "y2": pos.get("y1", 0),
            }
        )
    return items


# ─── Distance / scoring helpers ──────────────────────────────────────────


def _center(obj):
    return ((obj["x1"] + obj["x2"]) / 2, (obj["y1"] + obj["y2"]) / 2)


def _distance(a, b) -> float:
    ax, ay = _center(a)
    bx, by = _center(b)
    return ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5


def _direction_penalty(anchor, item, direction) -> int:
    if not direction:
        return 0
    if direction == "right" and item["x1"] <= anchor["x2"]:
        return 1000
    if direction == "left" and item["x2"] >= anchor["x1"]:
        return 1000
    if direction == "below" and item["y1"] <= anchor["y2"]:
        return 1000
    if direction == "above" and item["y2"] >= anchor["y1"]:
        return 1000
    return 0


def _vertical_penalty(anchor, item) -> float:
    ay = (anchor["y1"] + anchor["y2"]) / 2
    by = (item["y1"] + item["y2"]) / 2
    return abs(ay - by) * 5


# ─── Extractors par stratégie ────────────────────────────────────────────


def extract_from_zone(doc, regexp, zone) -> str | None:
    """
    Cherche dans la zone géométrique. FIX : utilise normalize_items au
    lieu de l'ancien doc.words qui n'existait pas (AttributeError silencieuse).
    """
    if not regexp or not zone:
        return None

    x1, y1, x2, y2 = zone
    for item in normalize_items(doc):
        if not (x1 <= item["x2"] <= x2 and y1 <= item["y2"] <= y2):
            continue
        m = regexp.search(item["text"])
        if m:
            return m.group(0)
    return None


def regex_search(doc, regexp) -> str | None:
    """
    Regex sur toutes les lignes de la page. FIX : voir extract_from_zone.
    """
    if not regexp:
        return None
    for item in normalize_items(doc):
        m = regexp.search(item["text"])
        if m:
            return m.group(0)
    return None


def extract_barcode(doc, bcr_type: str | None, regexp) -> str | None:
    """
    Match un barcode déjà extrait dans doc["barcodes"] (posé par cab_extract
    Lambda ou par extract_barcodes_from_pdf en mode lazy).

    Comportement (du plus strict au plus tolerant) :
      1. Correspondance EXACTE de format (case-insensitive, sans espaces :
         "Code 128" == "code128") — comportement historique.
      2. FALLBACK tolerant : si un bcr_type est configure mais qu'AUCUN barcode
         de la page ne correspond exactement a ce format, on retient quand meme
         un barcode present (en respectant la regex si fournie).

    POURQUOI le fallback : le `bcr_type` configure dans le doctype (ex "Code128")
    est une INDICATION de l'operateur ("ce champ vient d'un code-barres"), pas un
    filtre strict. Le format REEL renvoye par zxingcpp peut differer du libelle
    saisi (ex "CODE_128", "ITF", "Code39", variations de casse/underscore selon
    la version de la lib ou le type de code-barres effectivement imprime). Sans
    ce fallback, le decoupage par separateur fonctionne (il ne filtre pas le
    format, cf doc_splitter) MAIS la VALEUR n'est jamais extraite ni persistee
    comme index — symptome "coupe bien mais n'enregistre pas le no_recommande".
    """
    if not bcr_type:
        return None

    barcodes = doc.get("barcodes", []) or []
    if not barcodes:
        return None

    target = bcr_type.lower().replace(" ", "").replace("_", "")

    # 1. Correspondance EXACTE de format.
    for bc in barcodes:
        fmt = (bc.get("format") or "").lower().replace(" ", "").replace("_", "")
        if fmt != target:
            continue
        value = bc.get("value", "")
        if regexp and not regexp.search(value):
            continue
        if value:
            return value

    # 2. FALLBACK tolerant : aucun format exact -> on accepte un barcode present.
    #    On respecte la regex si fournie (permet de cibler le bon barcode quand
    #    il y en a plusieurs de formats differents sur la page).
    for bc in barcodes:
        value = bc.get("value", "")
        if not value:
            continue
        if regexp and not regexp.search(value):
            continue
        return value

    return None


def extract_anchor_robust(
    doc, regexp, anchors: str | None, direction=None, max_distance: int = 0
) -> str | None:
    """
    Cherche les ancres (anchors séparés par '|'), puis le candidat regex
    le plus proche en respectant direction et max_distance.
    Score = distance euclidienne + direction_penalty + vertical_penalty.
    """
    # On exige un anchor. La regexp est facultative : un champ "anchor seul"
    # (regexp vide) extrait le TEXTE QUI SUIT l'anchor (reste de ligne ou item
    # voisin dans la direction) — cas frequent "Label : valeur" ou la valeur
    # n'a pas de format regulier (ex: numero, nom d'autorite).
    if not anchors:
        return None

    items = normalize_items(doc)
    if not items:
        return None

    anchor_terms = _split_anchor_terms(anchors)
    if not anchor_terms:
        return None

    anchor_candidates = []
    for item in items:
        low = item["text"].lower()
        for a in anchor_terms:
            if a in low:
                anchor_candidates.append(item)
                break

    if not anchor_candidates:
        return None

    # ── Cas ANCHOR SANS REGEXP : extraire le texte suivant l'ancre ───────
    if not regexp:
        return _extract_anchor_no_regex(
            items, anchor_candidates, anchor_terms, direction, max_distance
        )

    # ── Priorite 1 : valeur DANS le meme item que l'ancre ────────────────
    # Cas tres frequent en OCR ligne : "Label : valeur" sur un seul item
    # (qu'il vienne d'un OCR fusionne {0,0,100,100} OU d'un OCR positionne
    # ou chaque ligne = un item "Fin de validite : 31.03.2029"). La recherche
    # multi-items ci-dessous EXCLUT l'item-ancre, donc elle raterait la valeur
    # co-localisee et irait piocher un mauvais candidat dans un autre item
    # (ex: "000" pris dans un numero, ou la date du mauvais champ). On tente
    # donc l'intra-item EN PREMIER, en privilegiant l'ancre la plus specifique.
    intra = _extract_anchor_intraline(items, regexp, anchor_terms, direction, max_distance)
    if intra:
        return intra

    # ── Priorite 2 : valeur dans un AUTRE item (label/valeur sur 2 lignes) ─
    best_score = 1e9
    best_value = None

    for anchor in anchor_candidates:
        for item in items:
            if item is anchor:
                continue
            m = regexp.search(item["text"])
            if not m:
                continue
            d = _distance(anchor, item)
            if max_distance and d > max_distance:
                continue
            score = (
                d + _direction_penalty(anchor, item, direction) + _vertical_penalty(anchor, item)
            )
            if score < best_score:
                best_score = score
                best_value = m.group(0)

    return best_value


def _extract_anchor_intraline(items, regexp, anchor_terms, direction, max_distance):
    """
    Extraction ancre/valeur CO-LOCALISEES dans un meme item (cas "Label :
    valeur", tres frequent en OCR ligne). On itere ANCRE PAR ANCRE, de la plus
    SPECIFIQUE (longue) a la plus generique, et pour chaque ancre on balaie
    tous les items. Des qu'une ancre donne une valeur, on la retourne : cela
    garantit que "Fin de validite" (specifique) gagne sur "validite :"
    (generique) qui matcherait aussi la ligne de debut de validite.
    """
    terms = sorted([t for t in (anchor_terms or []) if t], key=len, reverse=True)
    dir_norm = (direction or "right").strip().lower()
    # max_distance est exprime en "%" dans la config ; au sein d'un item on
    # raisonne en caracteres. Facteur prudent (et 0 = pas de borne).
    char_budget = (max_distance or 0) * 3 if max_distance else 0

    for a in terms:  # ancre la plus specifique d'abord
        best = None
        best_d = 1e9
        for item in items:  # balaie tous les items pour CETTE ancre
            text = item.get("text", "")
            low = text.lower()
            start = 0
            while True:
                pos = low.find(a, start)
                if pos == -1:
                    break
                anchor_end = pos + len(a)
                for m in regexp.finditer(text):
                    if dir_norm == "right" and m.start() < anchor_end:
                        continue
                    if dir_norm == "left" and m.start() >= pos:
                        continue
                    d = abs(m.start() - anchor_end)
                    if char_budget and d > char_budget:
                        continue
                    if d < best_d:
                        best_d = d
                        best = m.group(0)
                start = anchor_end
        if best:  # cette ancre a donne une valeur -> on la garde
            return best
    return None


# Caracteres de separation a retirer en tete de la valeur extraite apres une
# ancre (": ", "- ", etc.) et bornes de nettoyage.
_LEADING_SEP = " \t:：;,-—–=.\u00a0"

# Detecte le debut d'un NOUVEAU label de type "Mot(s) :" pour borner la valeur
# extraite d'un item fusionne (OCR mono-ligne {0,0,100,100}) ou plusieurs
# champs "Label : valeur" se suivent sur la meme ligne. Sans cette borne,
# "Num. de carte : 000000007255342 Delivree par : ..." renverrait toute la fin.
_NEXT_LABEL_RE = re.compile(r"\s+[A-Za-zÀ-ÿ.]+(?:\s+[A-Za-zÀ-ÿ.]+){0,3}\s*:")


def _clean_anchor_value(text: str, bound_next_label: bool = False) -> str:
    """Nettoie la valeur suivant une ancre : retire les separateurs de tete
    (": ", "- "...) et les espaces. Si `bound_next_label`, coupe aussi la
    valeur au prochain "Label :" (utile sur une ligne fusionnee multi-champs)."""
    if not text:
        return ""
    v = text.lstrip(_LEADING_SEP).strip()
    if bound_next_label and v:
        m = _NEXT_LABEL_RE.search(v)
        if m:
            v = v[: m.start()].strip()
    return v


def _extract_anchor_no_regex(items, anchor_candidates, anchor_terms, direction, max_distance):
    """
    Extraction quand un champ a un ANCHOR mais PAS de regexp : on renvoie le
    TEXTE QUI SUIT l'ancre.

    Deux cas, dans l'ordre :
      1. INTRA-ITEM : l'ancre et la valeur sont dans le meme item
         ("Num. de carte : 000000007255342"). On prend le reste du texte
         apres l'ancre (en privilegiant l'ancre la plus specifique). Nettoye.
      2. MULTI-ITEMS : l'item-ancre ne contient que le label ("Delivree par :")
         et la valeur est dans un item VOISIN dans la `direction`
         ("CONSEIL DEPARTEMENTAL 33"). On choisit l'item le mieux place
         (distance + penalite de direction), en l'excluant lui-meme.
    """
    terms = sorted([t for t in (anchor_terms or []) if t], key=len, reverse=True)

    # ── Cas 1 : reste de ligne dans le meme item ─────────────────────────
    for a in terms:
        for item in items:
            text = item.get("text", "")
            low = text.lower()
            pos = low.find(a)
            if pos == -1:
                continue
            tail = text[pos + len(a) :]
            val = _clean_anchor_value(tail, bound_next_label=True)
            if val:
                return val

    # ── Cas 2 : item voisin dans la direction ────────────────────────────
    dir_norm = (direction or "right").strip().lower()
    best_score = 1e9
    best_value = None

    for anchor in anchor_candidates:
        for item in items:
            if item is anchor:
                continue
            val = _clean_anchor_value(item.get("text", ""))
            if not val:
                continue
            d = _distance(anchor, item)
            if max_distance and d > max_distance:
                continue
            # Penalite de direction : on veut l'item du bon cote de l'ancre.
            pen = _direction_penalty(anchor, item, dir_norm)
            vpen = _vertical_penalty(anchor, item)
            score = d + pen + vpen
            if score < best_score:
                best_score = score
                best_value = val

    return best_value


def extract_field(doc, field: dict) -> str | None:
    """
    Stratégies essayées dans l'ordre (premier match gagne) :
      1. Barcode  (si bcr_type)
      2. Anchor   (si anchors + regexp)
      3. Zone     (si page_zone/zone + regexp)
      4. Regex global (si regexp)
      5. default_value (sinon)

    Applique la blacklist sur la valeur retournée si black_words défini.
    Retourne `default_value` (peut être "") si rien n'a matché.
    """
    regexp = compile_regexp(field.get("regexp"))
    anchors = field.get("anchors")
    direction = field.get("direction")
    bcr_type = field.get("bcr_type")
    zone = parse_zone(field.get("zone") or field.get("page_zone"))
    black_words = field.get("black_words")
    default_val = field.get("default_value") or ""

    try:
        max_distance = int(field.get("max_distance") or 0)
    except (TypeError, ValueError):
        max_distance = 0

    # 1. Barcode (rapide, pas de regex nécessaire)
    value = extract_barcode(doc, bcr_type, regexp)
    if value:
        return apply_blacklist(value, black_words) or default_val

    # 2. Anchor + regex
    value = extract_anchor_robust(doc, regexp, anchors, direction, max_distance)
    if value:
        return apply_blacklist(value, black_words) or default_val

    # 3. Zone + regex
    if zone:
        value = extract_from_zone(doc, regexp, zone)
        if value:
            return apply_blacklist(value, black_words) or default_val

    # 4. Regex globale
    value = regex_search(doc, regexp)
    if value:
        return apply_blacklist(value, black_words) or default_val

    return default_val


# ─── Multi-page extraction sur l'OCR JSON complet ────────────────────────


def _collect_all_text_lines(pages_doc) -> list:
    """Rassemble toutes les lignes de texte (decodees) de toutes les pages."""
    lines = []
    for _, page in (pages_doc or {}).items():
        if not isinstance(page, dict):
            continue
        for item in normalize_items(page):
            lines.append(item["text"])
    return lines


def _mrz_subfield_for(field: dict) -> str | None:
    """
    Determine quel sous-champ MRZ ce field vise.
    Priorite : `anchors`="mrz:document_number" > nom de champ usuel.
    Retourne une cle de MRZ_SUBFIELDS ou None.
    """
    try:
        from .fcl_mrz_parser import MRZ_SUBFIELDS
    except ImportError:
        return None  # MRZ non porté pour l'instant

    raw = (field.get("anchors") or "").strip().lower()
    if raw.startswith("mrz:"):
        sub = raw.split(":", 1)[1].strip()
        return sub if sub in MRZ_SUBFIELDS else None

    # Fallback : deviner depuis le field_name (conventions FR/EN courantes).
    name = (field.get("field_name") or "").lower()
    guess = {
        "document_number": [
            "numero_passeport",
            "numero_document",
            "num_doc",
            "document_number",
            "passport_number",
            "numero_titre",
        ],
        "surname": ["nom_titulaire", "nom", "surname", "lastname"],
        "name": ["prenoms_titulaire", "prenoms", "prenom", "name", "given"],
        "birth_date": ["date_naissance", "birth", "naissance"],
        "expiry_date": ["date_expiration", "expiry", "expiration"],
        "nationality": ["nationalite", "nationality"],
        "country": ["pays_emission", "pays", "country", "etat_emetteur"],
        "sex": ["sexe", "sex", "gender"],
        "document_type": ["type_document", "document_type"],
    }
    for sub, keys in guess.items():
        if any(k in name for k in keys):
            return sub
    return None


def extract_field_from_pages(pages_doc, field: dict, mrz_cache: dict = None) -> str | None:
    """
    Variante multi-pages : itère sur chaque page de l'OCR JSON et retourne
    le premier match.

    Stratégie MRZ : si field["strategy"] == "mrz" (ou anchors commence par
    "mrz:"), on lit la MRZ du document (ICAO 9303, validee par checksum) et on
    renvoie le sous-champ vise. On NE renvoie la valeur MRZ que si la MRZ est
    valide (checksums OK) — sinon on retombe sur l'extraction classique
    (anchors/zone/regex) pour ce champ.

    `mrz_cache` : dict optionnel partage entre champs d'un meme document pour
    ne parser la MRZ qu'une seule fois (cle "mrz" -> resultat parse_mrz).

    Format réel attendu pour `pages_doc` (tel qu'écrit par FclPdfFile.to_json
    et lu par s3obj.infos) :

        {
          "1": {"lines": [...], "barcodes": [...], "images": [...]},
          ...
        }
    """
    if not isinstance(pages_doc, dict) or not pages_doc:
        return field.get("default_value") or ""

    # ── Stratégie MRZ (prioritaire pour les documents d'identite) ────────
    strategy = (field.get("strategy") or "").strip().lower()
    anchors = (field.get("anchors") or "").strip().lower()
    if strategy == "mrz" or anchors.startswith("mrz:"):
        sub = _mrz_subfield_for(field)
        if sub:
            mrz = _get_mrz(pages_doc, mrz_cache)
            # On ne fait confiance a la MRZ que si les checksums sont OK.
            if mrz and mrz.get("valid") and mrz.get(sub):
                return mrz[sub]
        # MRZ absente/invalide/sous-champ vide -> on continue en extraction
        # classique ci-dessous (filet de securite).

    try:
        sorted_pages = sorted(pages_doc.items(), key=lambda kv: int(kv[0]))
    except (ValueError, TypeError):
        sorted_pages = list(pages_doc.items())

    for _, page in sorted_pages:
        if not isinstance(page, dict):
            continue
        value = extract_field(page, field)
        if value and value != (field.get("default_value") or ""):
            return value

    return field.get("default_value") or ""


def _get_mrz(pages_doc, mrz_cache):
    """Parse la MRZ une seule fois par document (via cache si fourni)."""
    if mrz_cache is not None and "mrz" in mrz_cache:
        return mrz_cache["mrz"]
    try:
        from .fcl_mrz_parser import parse_mrz_from_text
    except ImportError:
        return {}  # MRZ non porté pour l'instant
    result = parse_mrz_from_text(_collect_all_text_lines(pages_doc))
    if mrz_cache is not None:
        mrz_cache["mrz"] = result
    return result
