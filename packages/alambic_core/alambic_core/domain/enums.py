"""
alambic_core.domain.enums — énumérations métier.

Reprises À L'IDENTIQUE de flowerscan_lib (fcl_status.py, fcl_document.py).
On préserve les VALEURS string exactes : elles existent déjà dans les données
et dans la logique du pipeline. Renommer = casser l'existant.
"""

from enum import Enum


class DocumentStatus(str, Enum):
    """État global d'un document. Repris de fcl_status.DocumentStatus."""

    CREATED = "CREATED"
    DETECTED = "DETECTED"
    EXPANDED = "EXPANDED"
    CONVERTED_TO_PDF = "CONVERTED_TO_PDF"
    OCR_DONE = "OCR_DONE"
    FILE_SPLIT = "FILE_SPLIT"
    DATA_EXTRACTED_CONVENTIONAL = "DATA_EXTRACTED_CONVENTIONAL"
    DATA_EXTRACTED_AI = "DATA_EXTRACTED_AI"
    VALIDATED = "VALIDATED"
    EXPORTED = "EXPORTED"
    FAILED = "FAILED"


class DocumentProcess(str, Enum):
    """Étape du pipeline. Reprise de fcl_document.DocumentProcess.

    Conserve les noms (sérialisés en base) ; on abandonne juste les valeurs
    entières (l'ordre n'était de toute façon pas une garantie, dixit ton code).
    """

    NEWDOC = "NEWDOC"
    OFFICE_CONVERTER = "OFFICE_CONVERTER"
    IMAGECONVERTER = "IMAGECONVERTER"
    TEXTCONVERTER = "TEXTCONVERTER"
    FILEEXTRACTOR = "FILEEXTRACTOR"
    DETECT_MULTI_DOC = "DETECT_MULTI_DOC"
    OCR_READER = "OCR_READER"
    CAB_READER = "CAB_READER"
    UNLOCK_PDF = "UNLOCK_PDF"
    DOC_SPLITTER = "DOC_SPLITTER"
    DATA_EXTRACTOR = "DATA_EXTRACTOR"
    IA_AGENT = "IA_AGENT"
    CLASSIFIER = "CLASSIFIER"
    HUMAN_VALIDATION = "HUMAN_VALIDATION"
    EXPORT = "EXPORT"
    DISPATCHED = "DISPATCHED"
    # États "atteints" écrits par les workflows
    DOC_CREATED = "DOC_CREATED"
    DOC_EXTRACTED = "DOC_EXTRACTED"
    FILE_CONVERTED = "FILE_CONVERTED"
    PDF_TRUNCATED = "PDF_TRUNCATED"
    CAB_READ = "CAB_READ"
    DISPATCH_DONE = "DISPATCH_DONE"
    UNKNOWN = "UNKNOWN"


class DocumentProcessState(str, Enum):
    """État de l'étape en cours. Repris de fcl_document.DocumentProcessState."""

    STARTED = "STARTED"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    ERROR = "ERROR"
    UNKNOWN = "UNKNOWN"


class IndexType(str, Enum):
    """Type d'index. Repris de FclIndexModel.index_type."""

    METADATA = "metadata"
    EXTRACTED = "extracted"
