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
    # En attente de validation humaine (index extraits, contrôle par un opérateur
    # avant export). Renseigné en fin d'extraction ; quitté quand l'humain valide.
    PENDING_VALIDATION = "PENDING_VALIDATION"
    VALIDATED = "VALIDATED"
    EXPORTED = "EXPORTED"
    FAILED = "FAILED"
    # Type de document non reconnu : la classification n'a pas atteint le seuil de
    # confiance (classifier_confidence_level) et let_it_guess est désactivé. Ce
    # n'est PAS une erreur technique (≠ FAILED) : le document est intact, son type
    # n'a juste pas pu être déterminé. Un opérateur peut le classer manuellement.
    UNRECOGNIZED = "UNRECOGNIZED"
    # Remplacé par ses enfants (eml extrait, pdf splitté). Normal dans le
    # process, sans raison à stocker. Exclu du calcul de complétude.
    DEPRECATED = "DEPRECATED"
    # Écarté car inexploitable (vidéo, format non géré, corrompu). Avec une
    # raison (discard_reason). Non rejoué. Exclu de la complétude, mais l'info
    # remonte à la transaction (message + compteur nb_discarded).
    DISCARDED = "DISCARDED"


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


class UserRole(str, Enum):
    """Rôle d'un utilisateur de la plateforme.

    - SUPER_ADMIN : accès total, transverse à tous les comptes.
    - ADMIN       : gère son compte (configs, doctypes, utilisateurs).
    - VALIDATOR   : valide les documents de son compte (accès restreint).

    Valeurs string explicites : stables en base et lisibles, pour pouvoir
    basculer vers un fournisseur d'identité externe (Keycloak) sans réécrire.
    """

    SUPER_ADMIN = "SUPER_ADMIN"
    ADMIN = "ADMIN"
    VALIDATOR = "VALIDATOR"
