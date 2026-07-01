"""alambic_core.ai.tesseract_ocr — OCR local par Tesseract (souverain, gratuit).

Fournit un provider OCR alternatif à EdenAI, implémentant le même contrat
(`ocr_bytes(data, filename) -> OcrResult`) pour être interchangeable dans
PdfExtractor. Tesseract tourne en local (aucun appel externe, aucun coût), ce
qui en fait une option 100% souveraine.

Les positions de lignes sont renvoyées en POURCENTAGES de la page (comme EdenAI),
via `pytesseract.image_to_data`, pour que l'extraction structurée fonctionne à
l'identique quel que soit le provider choisi.
"""

from __future__ import annotations

import io
import logging

from alambic_core.ai.edenai_ocr import OcrResult

logger = logging.getLogger("alambic.ai.tesseract")

# Langues Tesseract par défaut (français + anglais). Surcouche possible via config.
DEFAULT_LANGS = "fra+eng"

# Confiance minimale (0-100) d'un mot pour être retenu (filtre le bruit d'OCR).
MIN_WORD_CONFIDENCE = 30


class TesseractOcr:
    """Provider OCR local basé sur Tesseract, compatible avec PdfExtractor.

    Expose `ocr_bytes(data, filename) -> OcrResult` comme DocumentOcr (EdenAI).
    Les lignes sont regroupées et positionnées en pourcentages de la page.
    """

    provider = "tesseract"

    def __init__(self, langs: str = DEFAULT_LANGS, *, preprocess_mode: str = "single",
                 rotation: bool = True):
        """preprocess_mode : « off » (aucun), « single » (un profil adaptatif),
        « multi » (teste plusieurs profils, garde le meilleur — plus lent, plus
        robuste). rotation : corrige orientation (90/180/270) + désalignement."""
        self.langs = langs or DEFAULT_LANGS
        self.preprocess_mode = preprocess_mode
        self.rotation = rotation

    def _prepare_image(self, image):
        """Applique rotation + nettoyage à une image PIL, renvoie l'image PIL à
        OCR-iser. Selon preprocess_mode, teste un ou plusieurs profils et garde le
        meilleur (au score OCR). Dégrade proprement si OpenCV/preprocess absents."""
        if self.preprocess_mode == "off" and not self.rotation:
            return image

        try:
            import cv2
            import numpy as np
            import pytesseract
            from PIL import Image

            from alambic_core.ai.image_preprocess import (
                PROFILES,
                correct_rotation,
                profile_adaptive,
                score_ocr_dict,
            )
        except ImportError as exc:  # pragma: no cover
            logger.debug("Prétraitement indisponible (%s) : image brute", exc)
            return image

        bgr = cv2.cvtColor(np.array(image.convert("RGB")), cv2.COLOR_RGB2BGR)

        # 1. Rotation (orientation + deskew) sur l'image couleur.
        if self.rotation:
            try:
                bgr, _info = correct_rotation(bgr, langs=self.langs)
            except Exception as exc:  # noqa: BLE001
                logger.debug("Correction de rotation échouée : %s", exc)

        # 2. Nettoyage.
        if self.preprocess_mode == "off":
            return Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))

        if self.preprocess_mode == "multi":
            # Teste chaque profil, garde celui au meilleur score OCR.
            best_img, best_score = None, -1.0
            for fn in PROFILES.values():
                try:
                    arr = fn(bgr)
                    d = pytesseract.image_to_data(
                        Image.fromarray(arr), lang=self.langs,
                        output_type=pytesseract.Output.DICT,
                    )
                    sc = score_ocr_dict(d)
                except Exception:  # noqa: BLE001, S112
                    continue
                if sc > best_score:
                    best_img, best_score = arr, sc
            if best_img is not None:
                return Image.fromarray(best_img)
            return image

        # single : profil adaptatif (le plus efficace en général).
        try:
            return Image.fromarray(profile_adaptive(bgr))
        except Exception as exc:  # noqa: BLE001
            logger.debug("Profil adaptatif échoué : %s", exc)
            return image

    def ocr_bytes(self, data: bytes, filename: str = "") -> OcrResult:  # noqa: ARG002
        """OCR d'une image (octets PNG/JPEG) via Tesseract. Ne lève jamais :
        en cas d'erreur, renvoie un OcrResult vide (l'extraction se poursuit)."""
        try:
            import pytesseract
            from PIL import Image
        except ImportError as exc:  # pragma: no cover — dépendances absentes
            logger.warning("Tesseract/pytesseract indisponible : %s", exc)
            return OcrResult(provider=self.provider)

        try:
            image = Image.open(io.BytesIO(data))
            # Garde-fou taille AVANT load() : une image géante est réduite avant
            # tout décodage coûteux (évite de bloquer un worker plusieurs dizaines
            # de secondes). La décision se prend sur les métadonnées (.size).
            from alambic_core.ai.image_preprocess import MAX_IMAGE_PIXELS, guard_image_size

            w, h = image.size
            if w * h > MAX_IMAGE_PIXELS:
                # Relever temporairement la limite PIL pour permettre NOTRE
                # redimensionnement contrôlé (sinon .load() lèverait avant).
                prev = Image.MAX_IMAGE_PIXELS
                Image.MAX_IMAGE_PIXELS = None
                try:
                    image, resized = guard_image_size(image)
                    if resized:
                        logger.info(
                            "Tesseract : image %dx%d réduite à %dx%d (garde-fou taille)",
                            w, h, image.size[0], image.size[1],
                        )
                finally:
                    Image.MAX_IMAGE_PIXELS = prev
            image.load()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Tesseract : image illisible (%s) : %s", filename, exc)
            return OcrResult(provider=self.provider)

        # Prétraitement (rotation + nettoyage) avant l'OCR.
        image = self._prepare_image(image)

        width, height = image.size
        if not width or not height:
            return OcrResult(provider=self.provider)

        try:
            data_dict = pytesseract.image_to_data(
                image, lang=self.langs, output_type=pytesseract.Output.DICT
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Tesseract : échec OCR (%s) : %s", filename, exc)
            return OcrResult(provider=self.provider)

        lines = self._group_lines(data_dict, width, height)
        full_text = "\n".join(ln["text"] for ln in lines)

        return OcrResult(
            text=full_text,
            lines=lines,
            provider=self.provider,
            model=f"tesseract-{self.langs}",
            cost=0.0,  # local : gratuit
        )

    def _group_lines(self, d: dict, width: int, height: int) -> list:
        """Regroupe les mots Tesseract en lignes, positions en % de la page.

        Tesseract renvoie des mots indexés par (block, paragraph, line). On
        regroupe les mots d'une même ligne, on concatène leur texte, et on prend
        la boîte englobante de la ligne convertie en pourcentages.
        """
        n = len(d.get("text", []))
        groups: dict = {}
        for i in range(n):
            word = (d["text"][i] or "").strip()
            if not word:
                continue
            try:
                conf = float(d["conf"][i])
            except (ValueError, TypeError):
                conf = -1.0
            if conf < MIN_WORD_CONFIDENCE:
                continue

            key = (d["block_num"][i], d["par_num"][i], d["line_num"][i])
            x, y, w, h = d["left"][i], d["top"][i], d["width"][i], d["height"][i]
            g = groups.setdefault(
                key, {"words": [], "x0": x, "y0": y, "x1": x + w, "y1": y + h}
            )
            g["words"].append(word)
            g["x0"] = min(g["x0"], x)
            g["y0"] = min(g["y0"], y)
            g["x1"] = max(g["x1"], x + w)
            g["y1"] = max(g["y1"], y + h)

        lines = []
        for key in sorted(groups):
            g = groups[key]
            text = " ".join(g["words"]).strip()
            if not text:
                continue
            lines.append(
                {
                    "text": text,
                    "bbox": {
                        "x0": round(g["x0"] / width * 100, 4),
                        "y0": round(g["y0"] / height * 100, 4),
                        "x1": round(g["x1"] / width * 100, 4),
                        "y1": round(g["y1"] / height * 100, 4),
                    },
                }
            )
        return lines


def tesseract_config_from_config(config) -> TesseractOcr:
    """Construit un TesseractOcr depuis une Config : langues + prétraitement.

    Options lues dans edenai_settings :
    - ocr_language : langues (« fr », « fra+eng »...) ;
    - ocr_preprocess : « off » / « single » / « multi » (défaut « single ») ;
    - ocr_rotation : bool, corrige orientation + désalignement (défaut True).
    """
    settings = (config.edenai_settings or {}) if config is not None else {}
    langs = _normalize_langs(settings.get("ocr_language") or DEFAULT_LANGS)
    mode = (settings.get("ocr_preprocess") or "single").lower()
    if mode not in ("off", "single", "multi"):
        mode = "single"
    rotation = settings.get("ocr_rotation")
    rotation = True if rotation is None else bool(rotation)
    return TesseractOcr(langs=langs, preprocess_mode=mode, rotation=rotation)


# Correspondance codes courants → codes Tesseract (ISO 639-2/T).
_LANG_MAP = {
    "fr": "fra", "fra": "fra", "french": "fra",
    "en": "eng", "eng": "eng", "english": "eng",
    "de": "deu", "deu": "deu", "es": "spa", "spa": "spa",
    "it": "ita", "ita": "ita", "nl": "nld", "nld": "nld",
    "pt": "por", "por": "por",
}


def _normalize_langs(raw: str) -> str:
    """Normalise une liste de langues (« fr,en » ou « fra+eng ») → « fra+eng »."""
    if not raw:
        return DEFAULT_LANGS
    parts = raw.replace("+", ",").split(",")
    codes = []
    for p in parts:
        code = _LANG_MAP.get(p.strip().lower())
        if code and code not in codes:
            codes.append(code)
    return "+".join(codes) if codes else DEFAULT_LANGS
