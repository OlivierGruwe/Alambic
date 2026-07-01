"""alambic_core.ai.image_preprocess — prétraitement d'image pour l'OCR.

Améliore fortement la qualité de l'OCR (surtout Tesseract) sur les documents
difficiles : fonds peu contrastés, texte pâle, scans bruités. Fournit :

- des PROFILS de nettoyage (niveaux de gris, débruitage, binarisation adaptative) ;
- un SCORING d'un résultat OCR (confiance cumulée des mots fiables) pour choisir
  le meilleur profil quand on en teste plusieurs ;
- une correction de ROTATION : orientation (0/90/180/270 via OSD Tesseract) et
  désalignement fin (deskew par angle des lignes de texte).

La stratégie multi-profils (tester plusieurs nettoyages et garder le meilleur)
est calibrée sur de vrais documents : sur une carte grise pâle, elle fait passer
l'OCR de ~4 mots à ~130 mots fiables.

Toutes les fonctions dégradent proprement : si OpenCV/Tesseract manquent ou
échouent, on renvoie l'image d'origine (l'OCR se poursuit sans prétraitement).
"""

from __future__ import annotations

import logging

logger = logging.getLogger("alambic.ai.preprocess")

# Confiance minimale et longueur minimale d'un mot pour compter dans le score
# (filtre le bruit qu'un profil trop agressif pourrait produire).
_SCORE_MIN_CONF = 40.0
_SCORE_MIN_LEN = 2

# Deskew : on ne corrige que si l'angle détecté dépasse ce seuil (évite de
# tourner inutilement un document déjà droit) mais reste sous un plafond (au-delà,
# c'est un problème d'orientation, pas de désalignement).
_DESKEW_MIN_ANGLE = 0.3
_DESKEW_MAX_ANGLE = 20.0

# OSD : confiance minimale pour appliquer une rotation d'orientation (prudent :
# on ne retourne le document que si Tesseract est sûr de son orientation).
_OSD_MIN_CONFIDENCE = 2.0


# Garde-fou taille d'image : au-delà de ce nombre de pixels, une image est
# redimensionnée avant tout traitement. Évite qu'une image géante (ex.
# 12000×12000 = 144 M px) monopolise un worker (le débruitage OpenCV scale mal)
# ou déclenche la protection « decompression bomb » de PIL. 24 M px ≈ 4900×4900,
# déjà bien au-delà de ce dont l'OCR a besoin (une page A4 à 300 DPI ≈ 8-9 M px).
MAX_IMAGE_PIXELS = 24_000_000


def guard_image_size(image, *, max_pixels: int = MAX_IMAGE_PIXELS):
    """Redimensionne une image PIL si elle dépasse max_pixels, en conservant le
    ratio. Renvoie (image, was_resized). Ne décode pas les pixels tant que ce
    n'est pas nécessaire : la décision se prend sur .size (métadonnées).

    Le redimensionnement préserve le document (on garde l'info, quitte à perdre
    un peu de finesse) plutôt que de le rejeter. L'OCR reste efficace : au-delà
    de ~40 M px, la résolution excède déjà ce dont Tesseract a besoin.
    """
    width, height = image.size
    pixels = width * height
    if pixels <= max_pixels or pixels <= 0:
        return image, False

    import math

    scale = math.sqrt(max_pixels / pixels)
    new_w = max(1, int(width * scale))
    new_h = max(1, int(height * scale))
    # Resampling rapide et de bonne qualité pour du texte.
    resized = image.resize((new_w, new_h))
    return resized, True


def guard_image_bytes(data: bytes, *, max_pixels: int = MAX_IMAGE_PIXELS,
                      filename: str = "") -> bytes:
    """Garde-fou sur des OCTETS image : si l'image dépasse max_pixels, renvoie
    des octets PNG réduits ; sinon renvoie les octets d'origine inchangés.

    Universel : à appliquer avant N'IMPORTE QUEL moteur OCR (Tesseract local
    comme EdenAI distant). Pour EdenAI c'est doublement utile : on réduit le
    volume envoyé sur le réseau (upload plus rapide) et on évite de payer l'OCR
    d'une image inutilement énorme. Ne lève jamais : en cas de souci, renvoie les
    octets d'origine (le moteur décidera).
    """
    try:
        import io

        from PIL import Image

        with Image.open(io.BytesIO(data)) as probe:
            w, h = probe.size
            if w * h <= max_pixels:
                return data  # sous le seuil : rien à faire

            # Relever la limite PIL le temps de NOTRE redimensionnement contrôlé.
            prev = Image.MAX_IMAGE_PIXELS
            Image.MAX_IMAGE_PIXELS = None
            try:
                probe.load()
                resized, was = guard_image_size(probe, max_pixels=max_pixels)
                if not was:
                    return data
                out = io.BytesIO()
                resized.convert("RGB").save(out, format="PNG")
                logger.info(
                    "Garde-fou image (%s) : %dx%d réduit à %dx%d avant OCR",
                    filename or "?", w, h, resized.size[0], resized.size[1],
                )
                return out.getvalue()
            finally:
                Image.MAX_IMAGE_PIXELS = prev
    except Exception as exc:  # noqa: BLE001
        logger.debug("Garde-fou image indisponible (%s) : octets inchangés", exc)
        return data


def _to_gray(image_bgr):
    import cv2

    if image_bgr.ndim == 2:
        return image_bgr
    return cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)


def profile_raw(image_bgr):
    """Profil « brut » : simple passage en niveaux de gris (aucun nettoyage)."""
    return _to_gray(image_bgr)


def profile_adaptive(image_bgr, *, block_size: int = 51, c: int = 25, denoise: bool = True):
    """Profil « adaptatif » : débruitage + binarisation à seuil local gaussien.

    C'est le profil le plus efficace sur les documents à fond variable (cartes
    grises, scans pliés) : le seuil est calculé par zone, ce qui gère les fonds
    inégaux là où un seuil global (Otsu) échoue. block_size impair obligatoire.
    """
    import cv2

    gray = _to_gray(image_bgr)
    if denoise:
        gray = cv2.fastNlMeansDenoising(gray, h=10)
    bs = block_size if block_size % 2 == 1 else block_size + 1
    return cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, bs, c
    )


def profile_otsu(image_bgr):
    """Profil « Otsu » : binarisation à seuil global. Efficace sur documents à
    fort contraste et fond uniforme (photocopies nettes)."""
    import cv2

    gray = _to_gray(image_bgr)
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return binary


# Registre des profils disponibles (nom → fonction). L'ordre reflète un ordre de
# préférence raisonnable ; le scoring tranche de toute façon.
PROFILES = {
    "adaptive": profile_adaptive,
    "otsu": profile_otsu,
    "raw": profile_raw,
}


def score_ocr_dict(data: dict) -> float:
    """Score d'un résultat pytesseract.image_to_data : somme des confiances des
    mots fiables. Récompense à la fois le nombre de mots ET leur qualité, tout en
    filtrant le bruit (confiance et longueur minimales)."""
    total = 0.0
    for word, conf in zip(data.get("text", []), data.get("conf", []), strict=False):
        w = (word or "").strip()
        try:
            c = float(conf)
        except (ValueError, TypeError):
            continue
        if len(w) >= _SCORE_MIN_LEN and c >= _SCORE_MIN_CONF:
            total += c
    return total


def deskew_angle(gray) -> float:
    """Estime l'angle de désalignement (degrés) via les coordonnées des pixels
    de texte. Renvoie 0 si non déterminé ou hors plage raisonnable."""
    import cv2
    import numpy as np

    # Inverse (texte blanc sur fond noir) pour que les pixels de texte soient non nuls.
    inv = cv2.bitwise_not(gray)
    _, binary = cv2.threshold(inv, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    coords = np.column_stack(np.where(binary > 0))
    if coords.shape[0] < 50:
        return 0.0
    angle = cv2.minAreaRect(coords)[-1]
    # minAreaRect renvoie [-90, 0[ : normaliser vers un petit angle signé.
    if angle < -45:
        angle = 90 + angle
    if abs(angle) < _DESKEW_MIN_ANGLE or abs(angle) > _DESKEW_MAX_ANGLE:
        return 0.0
    return float(angle)


def rotate_image(image, angle: float):
    """Tourne une image d'un angle (degrés) autour de son centre, fond blanc."""
    import cv2

    h, w = image.shape[:2]
    center = (w // 2, h // 2)
    matrix = cv2.getRotationMatrix2D(center, angle, 1.0)
    border = 255 if image.ndim == 2 else (255, 255, 255)
    return cv2.warpAffine(
        image, matrix, (w, h), flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_CONSTANT, borderValue=border,
    )


def detect_orientation(image_bgr, langs: str = "fra+eng") -> int:
    """Détecte l'orientation du texte (0/90/180/270°) via l'OSD de Tesseract.

    L'OSD est peu fiable sur une image brute peu contrastée : on le lance donc sur
    une version NETTOYÉE (binarisation adaptative), ce qui rend la détection
    robuste. La rotation est ensuite appliquée à l'image d'origine par l'appelant.

    Renvoie l'angle de rotation à appliquer pour remettre le document à l'endroit
    (0 si déjà droit ou si la détection n'est pas fiable — prudent)."""
    try:
        import pytesseract

        # OSD sur l'image nettoyée : bien plus fiable que sur le brut.
        cleaned = profile_adaptive(image_bgr)
        osd = pytesseract.image_to_osd(cleaned, output_type=pytesseract.Output.DICT)
    except Exception as exc:  # noqa: BLE001
        logger.debug("OSD indisponible ou échoué : %s", exc)
        return 0

    rotate = int(osd.get("rotate", 0) or 0)
    conf = float(osd.get("orientation_conf", 0) or 0)
    if rotate in (90, 180, 270) and conf >= _OSD_MIN_CONFIDENCE:
        return rotate
    return 0


def correct_rotation(image_bgr, *, langs: str = "fra+eng",
                     fix_orientation: bool = True, fix_skew: bool = True):
    """Corrige l'orientation (90/180/270) puis le désalignement fin d'une image.

    Conservateur : n'applique une correction que si la détection est fiable.
    Renvoie (image_corrigée, infos) où infos décrit ce qui a été appliqué.
    """
    info = {"orientation": 0, "skew": 0.0}
    out = image_bgr

    # 1. Orientation grossière (OSD) : remet le document à l'endroit.
    if fix_orientation:
        rot = detect_orientation(out, langs)
        if rot:
            # OSD donne l'angle horaire à corriger ; rotate_image tourne en
            # trigonométrique, d'où le signe négatif.
            out = rotate_image(out, -rot)
            info["orientation"] = rot

    # 2. Désalignement fin (deskew) sur l'image remise droite.
    if fix_skew:
        gray = _to_gray(out)
        ang = deskew_angle(gray)
        if ang:
            out = rotate_image(out, ang)
            info["skew"] = round(ang, 2)

    return out, info
