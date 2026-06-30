"""Tests d'intégration de la relève des boîtes mail (IMAP)."""

from __future__ import annotations

import email.mime.multipart
import email.mime.text
import os
import tempfile
from contextlib import suppress
from unittest.mock import MagicMock, patch

from cryptography.fernet import Fernet
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles


@compiles(JSONB, "sqlite")
def _jsonb_sqlite(element, compiler, **kw):  # noqa: ANN001
    return "JSON"


def _setup():
    dbfile = tempfile.NamedTemporaryFile(suffix=".db", delete=False).name  # noqa: SIM115
    os.environ["ALAMBIC_DATABASE_URL"] = f"sqlite:///{dbfile}"
    os.environ["ALAMBIC_SECRET_KEY"] = Fernet.generate_key().decode()

    from alambic_core.db.base import Base
    from alambic_core.db.session import get_engine, get_sessionmaker, init_core

    init_core()
    import alambic_core.models  # noqa: F401

    Base.metadata.create_all(get_engine())
    return dbfile, get_sessionmaker(), get_engine()


def _raw_mail(sender="Factures <factures@arondor.com>", subject="Facture"):
    m = email.mime.multipart.MIMEMultipart()
    m["From"] = sender
    m["Subject"] = subject
    m.attach(email.mime.text.MIMEText("Voici la facture.", "plain"))
    return m.as_bytes()


def _add_config(Sess, **kw):
    from alambic_core.models import MailConfig

    defaults = dict(
        id="mc1",
        mailconfig_name="Factures",
        email_address="factures@arondor.com",
        imap_server="imap.arondor.com",
        imap_password_enc="SECRET",
        config_id="cfg1",
        account_id="acc1",
        is_active=True,
        content_mode="all",
    )
    defaults.update(kw)
    with Sess() as s:
        s.add(MailConfig(**defaults))
        s.commit()


def test_poll_ingests_mail_with_policy():
    """Un mail relevé est déposé et ingéré avec la politique de contenu."""
    dbfile, Sess, engine = _setup()
    from alambic_workers.tasks.mail_poll import poll_mailboxes

    _add_config(Sess, content_mode="attachments", filter_attachment_extensions=".pdf")

    fake_client = MagicMock()
    fake_client.fetch_mails.return_value = [{"id": "1", "content": _raw_mail()}]

    captured = {}

    def _fake_start(**kwargs):
        captured.update(kwargs)
        return {"transactionId": "trx-x"}

    with (
        patch("alambic_workers.tasks.mail_poll.ImapClient", return_value=fake_client),
        patch("alambic_workers.tasks.mail_poll.storage.put_object", return_value=None),
        patch(
            "alambic_workers.tasks.mail_poll.storage.build_upload_key",
            return_value="__uploads__/acc1/cfg1/MAIL/mail_1.eml",
        ),
        patch("alambic_workers.tasks.mail_poll.start_ingestion", side_effect=_fake_start),
    ):
        summary = poll_mailboxes.run()

    assert summary["fetched"] == 1
    assert summary["ingested"] == 1
    # La politique de contenu a bien été transmise à l'ingestion.
    assert captured["metadata"]["mail_policy"]["content_mode"] == "attachments"
    assert captured["metadata"]["mail_policy"]["filter_attachment_extensions"] == ".pdf"
    # Action post-traitement appliquée.
    fake_client.apply_post_action.assert_called_with("1", "seen", "ARCHIVE")
    engine.dispose()
    with suppress(OSError):
        os.unlink(dbfile)


def test_poll_skips_non_whitelisted_sender():
    """Un mail d'un expéditeur hors whitelist n'est pas ingéré."""
    dbfile, Sess, engine = _setup()
    from alambic_workers.tasks.mail_poll import poll_mailboxes

    _add_config(Sess, sender_whitelist="*@arondor.com")

    fake_client = MagicMock()
    fake_client.fetch_mails.return_value = [
        {"id": "1", "content": _raw_mail(sender="spam@autre.com")},
    ]

    with (
        patch("alambic_workers.tasks.mail_poll.ImapClient", return_value=fake_client),
        patch("alambic_workers.tasks.mail_poll.start_ingestion") as start,
    ):
        summary = poll_mailboxes.run()

    assert summary["skipped"] == 1
    assert summary["ingested"] == 0
    start.assert_not_called()
    # Le mail rejeté est tout de même marqué traité (pas de re-relève infinie).
    fake_client.apply_post_action.assert_called_with("1", "seen", "ARCHIVE")
    engine.dispose()
    with suppress(OSError):
        os.unlink(dbfile)


def test_poll_inactive_config_ignored():
    """Une config mail inactive n'est pas relevée."""
    dbfile, Sess, engine = _setup()
    from alambic_workers.tasks.mail_poll import poll_mailboxes

    _add_config(Sess, is_active=False)

    with patch("alambic_workers.tasks.mail_poll.ImapClient") as client:
        summary = poll_mailboxes.run()

    assert summary["configs"] == 0
    client.assert_not_called()
    engine.dispose()
    with suppress(OSError):
        os.unlink(dbfile)


def test_poll_one_mailbox_failure_isolated():
    """Une boîte en échec n'empêche pas les autres d'être relevées."""
    dbfile, Sess, engine = _setup()
    from alambic_workers.tasks.mail_poll import poll_mailboxes

    _add_config(Sess, id="mc1", email_address="a@arondor.com")
    _add_config(Sess, id="mc2", email_address="b@arondor.com")

    ok_client = MagicMock()
    ok_client.fetch_mails.return_value = [{"id": "1", "content": _raw_mail()}]

    def _client_factory(params):
        if params.email == "a@arondor.com":
            raise OSError("IMAP down")
        return ok_client

    with (
        patch("alambic_workers.tasks.mail_poll.ImapClient", side_effect=_client_factory),
        patch("alambic_workers.tasks.mail_poll.storage.put_object", return_value=None),
        patch("alambic_workers.tasks.mail_poll.storage.build_upload_key", return_value="k"),
        patch(
            "alambic_workers.tasks.mail_poll.start_ingestion",
            return_value={"transactionId": "t"},
        ),
    ):
        summary = poll_mailboxes.run()

    # Une boîte en erreur, une qui ingère.
    assert summary["configs"] == 2
    assert summary["errors"] == 1
    assert summary["ingested"] == 1
    engine.dispose()
    with suppress(OSError):
        os.unlink(dbfile)
