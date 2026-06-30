"""Tests du poller de déclenchement (scrutation __uploads__ → start_ingestion)."""

from __future__ import annotations

from unittest.mock import patch

from alambic_workers.trigger import poller


def test_scan_once_triggers_and_cleans():
    objs = [{"key": "__uploads__/a/c/UI_IMPORT/f.zip", "size": 10, "last_modified": None}]
    calls = {"dl": [], "start": [], "del": []}
    with (
        patch.object(poller.storage, "list_objects", return_value=objs),
        patch.object(
            poller.storage, "download_to", side_effect=lambda b, k, d: calls["dl"].append(k) or d
        ),
        patch.object(
            poller.storage, "delete_object", side_effect=lambda b, k: calls["del"].append(k)
        ),
        patch.object(
            poller,
            "start_ingestion",
            side_effect=lambda **kw: (
                calls["start"].append(kw["object_key"]) or {"transactionId": "trx1"}
            ),
        ),
    ):
        n = poller.scan_once()
    assert n == 1
    assert calls["start"] == ["__uploads__/a/c/UI_IMPORT/f.zip"]
    assert calls["del"] == ["__uploads__/a/c/UI_IMPORT/f.zip"]


def test_scan_once_skips_folders():
    objs = [{"key": "__uploads__/a/c/UI_IMPORT/", "size": 0, "last_modified": None}]
    with (
        patch.object(poller.storage, "list_objects", return_value=objs),
        patch.object(poller, "start_ingestion") as start,
    ):
        n = poller.scan_once()
    assert n == 0
    start.assert_not_called()


def test_scan_once_continues_on_error():
    """Un fichier en erreur ne bloque pas le passage."""
    objs = [
        {"key": "__uploads__/a/c/UI_IMPORT/bad.zip", "size": 10, "last_modified": None},
        {"key": "__uploads__/a/c/UI_IMPORT/good.zip", "size": 10, "last_modified": None},
    ]
    started = []

    def _start(**kw):
        if "bad" in kw["object_key"]:
            raise RuntimeError("boom")
        started.append(kw["object_key"])
        return {"transactionId": "trx1"}

    with (
        patch.object(poller.storage, "list_objects", return_value=objs),
        patch.object(poller.storage, "download_to", side_effect=lambda b, k, d: d),
        patch.object(poller.storage, "delete_object"),
        patch.object(poller, "start_ingestion", side_effect=_start),
    ):
        n = poller.scan_once()
    # good.zip est déclenché malgré l'échec de bad.zip
    assert started == ["__uploads__/a/c/UI_IMPORT/good.zip"]
    assert n == 1
