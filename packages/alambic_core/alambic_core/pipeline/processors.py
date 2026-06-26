"""alambic_core.pipeline.processors — processeurs d'extraction par type.

Portés de flowerscan_lib (fcl_zip_processor, fcl_eml_processor) :
- ZipProcessor : décompresse une archive, récursion sur chaque entrée.
- EmlProcessor : extrait corps (texte/html) + pièces jointes d'un e-mail.
- DefaultProcessor : catch-all, déplace le fichier tel quel s'il est autorisé.

Corrections vs FlowerScan : pas de sanitize ASCII (UTF-8 conservé), métadonnées
e-mail gardées proprement, gestion d'erreur par fichier (un fichier KO ne casse
pas l'extraction des autres).
"""

from __future__ import annotations

import base64
import json
import mimetypes
import os
import re
import zipfile
from email import policy as email_policy
from email.parser import BytesParser
from email.utils import getaddresses
from pathlib import Path

from .extraction import (
    ExtractedFile,
    ExtractionEngine,
    ExtractionResult,
    FileProcessor,
    _extension,
)


def _uniq_path(target: Path) -> Path:
    """Évite l'écrasement : ajoute un suffixe numérique si le nom existe déjà."""
    if not target.exists():
        return target
    stem, suffix, parent = target.stem, target.suffix, target.parent
    i = 1
    while True:
        candidate = parent / f"{stem}_{i}{suffix}"
        if not candidate.exists():
            return candidate
        i += 1


def _write_tmp(out_dir: str, name: str, payload: bytes) -> Path:
    """Écrit un payload dans un fichier temporaire, en conservant son vrai nom.

    On utilise un sous-dossier '__staging__' pour ne pas polluer le dossier de
    sortie et pour que le nom du fichier (utilisé par la récursion et la
    détection d'extension) reste exact.
    """
    staging = Path(out_dir) / "__staging__"
    staging.mkdir(exist_ok=True)
    target = _uniq_path(staging / name)
    target.write_bytes(payload)
    return target


class ZipProcessor(FileProcessor):
    """Décompresse une archive ZIP, récursion sur chaque entrée."""

    def supports(self, category: str) -> bool:
        return category == "ZIP"

    def process(
        self, engine: ExtractionEngine, path: str, out_dir: str, depth: int
    ) -> ExtractionResult:
        res = ExtractionResult()
        policy = engine.policy
        if depth > policy.max_depth:
            return res

        with zipfile.ZipFile(path) as z:
            entries = z.infolist()
            if len(entries) > policy.max_files:
                return res

            total_size = 0
            for member in entries:
                name = Path(member.filename).name
                if not name or member.is_dir():
                    continue
                try:
                    total_size += member.file_size
                    if total_size > policy.max_size:
                        break

                    payload = z.read(member)
                    tmp = _write_tmp(out_dir, name, payload)

                    if not policy.is_allowed(name):
                        res.unsupported_files.append(
                            ExtractedFile(
                                filename=name,
                                type="attachment",
                                depth=depth,
                                error_code="UNSUPPORTED_FILE",
                                message=f"Extension {_extension(name)} non autorisée",
                            )
                        )
                        tmp.unlink(missing_ok=True)
                        continue

                    # Récursion : l'entrée peut être elle-même un zip/eml.
                    sub = engine.process(str(tmp), out_dir, depth + 1)
                    if sub.ok_files or sub.error_files or sub.unsupported_files:
                        res.merge(sub)
                        tmp.unlink(missing_ok=True)
                        continue

                    target = _uniq_path(Path(out_dir) / name)
                    tmp.replace(target)
                    res.ok_files.append(
                        ExtractedFile(filename=name, path=str(target), type="zip", depth=depth)
                    )
                except Exception as exc:
                    res.error_files.append(
                        ExtractedFile(
                            filename=name,
                            type="zip",
                            depth=depth,
                            error_code=type(exc).__name__,
                            message=str(exc),
                        )
                    )
        return res


# ── Métadonnées e-mail (en-têtes utiles, sans sanitize destructeur) ─────────
_ALLOWED_HEADERS = {"message-id", "from", "to", "cc", "reply-to", "subject", "date"}
_ADDRESS_HEADERS = {"from", "to", "cc", "reply-to"}
_MAX_ADDR = 5
_MAX_SUBJECT = 200
_MAX_HEADER = 300


def _normalize_addresses(value: str) -> list[str]:
    out: list[str] = []
    for _name, email in getaddresses([value]):
        email = email.strip().lower()
        if email:
            out.append(email)
        if len(out) >= _MAX_ADDR:
            break
    return out


def _email_metadata(msg) -> dict:
    """Extrait les en-têtes utiles d'un e-mail (UTF-8 conservé)."""
    meta: dict = {}
    for k, v in msg.items():
        key = k.lower()
        if key not in _ALLOWED_HEADERS or not v:
            continue
        if key in _ADDRESS_HEADERS:
            emails = _normalize_addresses(str(v))
            if emails:
                meta[key] = json.dumps(emails, separators=(",", ":"))
        elif key == "subject":
            meta[key] = str(v).strip()[:_MAX_SUBJECT]
        else:
            meta[key] = str(v).strip()[:_MAX_HEADER]
    return meta


class EmlProcessor(FileProcessor):
    """Extrait le corps (texte/html) et les pièces jointes d'un e-mail.

    Respecte la MailContentPolicy du moteur (content_mode + filtre d'extensions
    de PJ) : en mode 'body' les PJ ne sont pas extraites, en mode 'attachments'
    le corps n'est pas extrait, et le filtre d'extensions écarte les PJ non
    désirées proprement (non écrites sur disque) — donc pas comptées en DISCARDED.
    """

    _CID_RE = re.compile(r'src=["\']cid:(.+?)["\']', re.I)

    def supports(self, category: str) -> bool:
        return category == "EML"

    def process(
        self, engine: ExtractionEngine, path: str, out_dir: str, depth: int
    ) -> ExtractionResult:
        res = ExtractionResult()
        policy = engine.policy
        mail = engine.mail_policy  # MailContentPolicy (défaut 'all')
        is_root = depth == 0
        process_body = mail.should_process_body()

        with open(path, "rb") as f:
            msg = BytesParser(policy=email_policy.default).parse(f)

        if is_root:
            res.metadatas = _email_metadata(msg)

        text_body = None
        html_body = None
        cid_map: dict[str, str] = {}

        for part in msg.walk():
            filename = None
            try:
                content_type = part.get_content_type()
                disposition = part.get_content_disposition()
                content_id = part.get("Content-ID")
                payload = part.get_payload(decode=True)
                if payload is None:
                    continue
                if len(payload) > policy.max_size:
                    continue

                # Corps (racine, sans disposition de pièce jointe).
                if is_root and not disposition:
                    if not process_body:
                        # Mode 'attachments' : on ignore le corps.
                        continue
                    charset = part.get_content_charset() or "utf-8"
                    if content_type == "text/plain" and text_body is None:
                        text_body = payload.decode(charset, errors="ignore")
                        continue
                    if content_type == "text/html" and html_body is None:
                        html_body = payload.decode(charset, errors="ignore")
                        continue

                # Image inline (cid) — inlinée dans le HTML seulement si on traite
                # le corps ; sinon inutile.
                cid = content_id.strip("<>") if content_id else None
                is_inline = is_root and (
                    disposition == "inline" or (content_id and content_type.startswith("image/"))
                )
                if is_inline and cid:
                    if process_body:
                        b64 = base64.b64encode(payload).decode()
                        cid_map[cid] = f"data:{content_type};base64,{b64}"
                    continue

                # Pièce jointe.
                filename = part.get_filename()
                if not filename:
                    ext_guess = mimetypes.guess_extension(content_type) or ".bin"
                    filename = f"attachment{ext_guess}"
                filename = os.path.basename(filename)

                # Politique mail : ce mode/filtre veut-il cette PJ ? Si non, on la
                # saute silencieusement (choix de config, pas un écartement).
                if not mail.should_process_attachment(filename):
                    continue

                if not policy.is_allowed(filename):
                    res.unsupported_files.append(
                        ExtractedFile(
                            filename=filename,
                            type="attachment",
                            depth=depth,
                            error_code="UNSUPPORTED_FILE",
                            message=f"Extension {_extension(filename)} non autorisée",
                            metadatas=res.metadatas,
                        )
                    )
                    continue

                tmp = _write_tmp(out_dir, filename, payload)

                # Récursion : une PJ peut être un zip/eml.
                sub = None
                if depth < policy.max_depth:
                    sub = engine.process(str(tmp), out_dir, depth + 1)
                if sub and (sub.ok_files or sub.error_files or sub.unsupported_files):
                    res.merge(sub)
                    tmp.unlink(missing_ok=True)
                    continue

                target = _uniq_path(Path(out_dir) / filename)
                tmp.replace(target)
                res.ok_files.append(
                    ExtractedFile(
                        filename=filename,
                        path=str(target),
                        type="attachment",
                        depth=depth,
                        metadatas=res.metadatas,
                    )
                )
            except Exception as exc:
                res.error_files.append(
                    ExtractedFile(
                        filename=filename or "",
                        type="attachment",
                        depth=depth,
                        error_code="INVALID_MIME",
                        message=str(exc),
                        metadatas=res.metadatas,
                    )
                )

        # Corps en fichiers (racine), seulement si on traite le corps.
        if is_root and process_body:
            if html_body and cid_map:
                html_body = self._CID_RE.sub(
                    lambda m: f'src="{cid_map.get(m.group(1), "cid:" + m.group(1))}"',
                    html_body,
                )
            if text_body:
                text_path = Path(out_dir, "__text_body__.txt")
                text_path.write_text(text_body, encoding="utf-8", errors="ignore")
                res.ok_files.append(
                    ExtractedFile(
                        filename=text_path.name,
                        path=str(text_path),
                        type="body_text",
                        depth=depth,
                        metadatas=res.metadatas,
                    )
                )
            if html_body:
                html_path = Path(out_dir, "__html_body__.html")
                html_path.write_text(html_body, encoding="utf-8", errors="ignore")
                res.ok_files.append(
                    ExtractedFile(
                        filename=html_path.name,
                        path=str(html_path),
                        type="body_html",
                        depth=depth,
                        metadatas=res.metadatas,
                    )
                )
        return res


class DefaultProcessor(FileProcessor):
    """Catch-all : déplace le fichier tel quel s'il est autorisé."""

    def supports(self, category: str) -> bool:
        return True

    def process(
        self, engine: ExtractionEngine, path: str, out_dir: str, depth: int
    ) -> ExtractionResult:
        res = ExtractionResult()
        policy = engine.policy
        name = Path(path).name

        if not policy.is_allowed(name):
            res.unsupported_files.append(
                ExtractedFile(
                    filename=name,
                    type="file",
                    depth=depth,
                    error_code="UNSUPPORTED_EXTENSION",
                    message=f"Extension {_extension(name)} non autorisée",
                )
            )
            return res

        target = _uniq_path(Path(out_dir) / name)
        Path(path).replace(target)
        res.ok_files.append(
            ExtractedFile(filename=name, path=str(target), type="file", depth=depth)
        )
        return res


def build_engine(policy=None, mail_policy=None) -> ExtractionEngine:
    """Construit le moteur avec la chaîne standard de processeurs.

    mail_policy : MailContentPolicy optionnelle (content_mode + filtre PJ) pour
    les imports via boîte mail. Défaut : tout extraire.
    """
    from .extraction import default_policy

    return ExtractionEngine(
        policy or default_policy(),
        processors=[ZipProcessor(), EmlProcessor(), DefaultProcessor()],
        mail_policy=mail_policy,
    )
