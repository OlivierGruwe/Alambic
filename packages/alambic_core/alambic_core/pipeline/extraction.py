"""alambic_core.pipeline.extraction — moteur d'extraction de fichiers.

Porté de flowerscan_lib (fcl_file_processor + processeurs zip/eml), avec
corrections :
- ExtractionEngine.process renvoie TOUJOURS un Result (jamais None) ;
- les exceptions sont loguées (pas d'except muet qui avale la cause) ;
- pas de sanitize ASCII destructeur (Garage/Postgres n'ont pas la contrainte de
  métadonnées S3 d'AWS) — on garde l'UTF-8 propre.

Architecture : une ExtractionPolicy (extensions autorisées, bornes) + une chaîne
de FileProcessor (Zip, Eml, Default). Le moteur détecte la catégorie d'un fichier
et délègue au premier processeur qui la gère. Récursif et borné (un zip dans un
eml…), avec garde-fous (max_depth, max_files, max_size).
"""

from __future__ import annotations

import logging
import os
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class ExtractedFile:
    """Un fichier issu de l'extraction (ok, en erreur, ou non supporté)."""

    filename: str
    path: str = ""
    type: str = "file"
    depth: int = 0
    error_code: str = ""
    message: str = ""
    metadatas: dict = field(default_factory=dict)


@dataclass
class ExtractionResult:
    """Résultat d'une extraction : fichiers ok / en erreur / non supportés."""

    metadatas: dict = field(default_factory=dict)
    ok_files: list[ExtractedFile] = field(default_factory=list)
    error_files: list[ExtractedFile] = field(default_factory=list)
    unsupported_files: list[ExtractedFile] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.ok_files) + len(self.error_files) + len(self.unsupported_files)

    def merge(self, other: ExtractionResult) -> None:
        self.metadatas = {**self.metadatas, **other.metadatas}
        self.ok_files += other.ok_files
        self.error_files += other.error_files
        self.unsupported_files += other.unsupported_files


@dataclass
class ExtractionPolicy:
    """Bornes et extensions autorisées pour l'extraction."""

    allowed_extensions: set[str]
    max_depth: int = 5
    max_files: int = 10000
    max_size: int = 500 * 1024 * 1024  # 500 Mo

    def __post_init__(self) -> None:
        self.allowed_extensions = self._normalize(self.allowed_extensions)

    @staticmethod
    def _normalize(exts) -> set[str]:
        out: set[str] = set()
        for e in exts or []:
            if isinstance(e, str) and e.strip():
                base = e.strip().lower().lstrip(".")
                out.add(base)
                out.add(f".{base}")
        return out

    def is_allowed(self, filename: str) -> bool:
        ext = _extension(filename)
        return ext in self.allowed_extensions


@dataclass
class MailContentPolicy:
    """Politique de traitement du contenu d'un e-mail (issue de la config mail).

    Portée de FmlMailConfig (content_mode + filter_attachment_extensions) :
    - content_mode : 'all' (corps + PJ), 'body' (corps seul), 'attachments'
      (PJ seules). Valeur inconnue → 'all'.
    - filter_attachment_extensions : extensions de PJ autorisées (ex. ".pdf,.docx").
      Vide → toutes acceptées. Ignoré en mode 'body' (aucune PJ extraite).

    Défaut : tout extraire (comportement d'un dépôt direct, sans config mail).
    """

    content_mode: str = "all"
    filter_attachment_extensions: str = ""

    def __post_init__(self) -> None:
        if self.content_mode not in ("all", "body", "attachments"):
            self.content_mode = "all"

    @property
    def accepted_extensions(self) -> list[str]:
        raw = (self.filter_attachment_extensions or "").replace(";", ",")
        return [
            e.strip() if e.strip().startswith(".") else f".{e.strip()}"
            for e in raw.split(",")
            if e.strip()
        ]

    def should_process_body(self) -> bool:
        return self.content_mode != "attachments"

    def should_process_attachment(self, filename: str) -> bool:
        if self.content_mode == "body":
            return False
        exts = self.accepted_extensions
        if not exts:
            return True
        return _extension(filename) in exts


def _extension(filename: str) -> str:
    """Extension en minuscule, avec le point (ex. '.pdf'). '' si absente."""
    return Path(filename).suffix.lower()


def detect_category(path: str) -> str:
    """Catégorie d'un fichier local : ZIP, EML, ou FILE (défaut).

    Détection par signature (magic bytes) en priorité, repli sur l'extension.
    Plus robuste que la seule extension (un .zip renommé reste détecté).
    """
    p = Path(path)
    ext = p.suffix.lower()

    # Formats TECHNIQUEMENT zip (commencent par "PK") mais qui sont des DOCUMENTS
    # à convertir, pas des archives à décompresser. Sans cette exclusion, un .docx
    # serait "extrait" en ses XML/images internes au lieu d'être converti en PDF.
    _ZIP_BASED_DOCS = {
        ".docx",
        ".xlsx",
        ".pptx",
        ".docm",
        ".xlsm",
        ".pptm",
        ".odt",
        ".ods",
        ".odp",
        ".odg",
        ".epub",
        ".jar",
        ".apk",
    }
    if ext in _ZIP_BASED_DOCS:
        return "FILE"

    try:
        with open(path, "rb") as f:
            head = f.read(8)
    except OSError:
        head = b""

    # ZIP : signature PK\x03\x04 (ou variantes vides/spanned).
    if head[:2] == b"PK" or zipfile.is_zipfile(path):
        return "ZIP"

    # EML : difficile par magic bytes ; on se fie à l'extension et à un en-tête
    # d'e-mail plausible (présence de "From:" / "Received:" / "Subject:").
    if ext == ".eml":
        return "EML"
    try:
        with open(path, "rb") as f:
            start = f.read(2048)
        lowered = start.lower()
        if any(h in lowered for h in (b"from:", b"received:", b"subject:", b"message-id:")):
            return "EML"
    except OSError:
        pass

    return "FILE"


class FileProcessor:
    """Interface d'un processeur de fichiers (zip, eml, défaut)."""

    def supports(self, category: str) -> bool:
        raise NotImplementedError

    def process(
        self, engine: ExtractionEngine, path: str, out_dir: str, depth: int
    ) -> ExtractionResult:
        raise NotImplementedError


class ExtractionEngine:
    """Détecte la catégorie d'un fichier et délègue au bon processeur.

    Récursif (un zip peut contenir un eml…) et borné par la policy.
    """

    def __init__(
        self,
        policy: ExtractionPolicy,
        processors: list[FileProcessor],
        mail_policy: "MailContentPolicy | None" = None,
    ):
        self.policy = policy
        self.processors = processors
        # Politique de contenu mail (content_mode + filtre PJ). Défaut : tout.
        self.mail_policy = mail_policy or MailContentPolicy()

    def process(self, path: str, out_dir: str, depth: int = 0) -> ExtractionResult:
        # CORRECTION FlowerScan : on renvoie TOUJOURS un Result, jamais None.
        category = detect_category(path)
        for processor in self.processors:
            if processor.supports(category):
                try:
                    return processor.process(self, path, out_dir, depth)
                except Exception as exc:
                    # CORRECTION : on loggue (pas d'except muet qui avale la cause).
                    logger.exception(
                        "Échec du processeur %s sur %s",
                        type(processor).__name__,
                        path,
                    )
                    res = ExtractionResult()
                    res.error_files.append(
                        ExtractedFile(
                            filename=os.path.basename(path),
                            type="file",
                            depth=depth,
                            error_code=type(exc).__name__,
                            message=str(exc),
                        )
                    )
                    return res
        # Aucun processeur ne gère cette catégorie → résultat vide explicite.
        return ExtractionResult()


def default_policy() -> ExtractionPolicy:
    """Policy par défaut : extensions documentaires courantes."""
    return ExtractionPolicy(
        allowed_extensions={
            "pdf",
            "png",
            "jpg",
            "jpeg",
            "tiff",
            "tif",
            "bmp",
            "gif",
            "doc",
            "docx",
            "xls",
            "xlsx",
            "ppt",
            "pptx",
            "odt",
            "ods",
            "txt",
            "html",
            "htm",
            "rtf",
            "csv",
            "eml",
        }
    )
