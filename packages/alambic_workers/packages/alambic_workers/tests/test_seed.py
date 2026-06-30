"""
Tests des transformations du seed (load_reference).

Couvre la logique pure de transformation CSV → modèle : conversion booléenne,
décodage base64 du json_content, regroupement des adresses. Sans DB.
"""

from __future__ import annotations

import base64
import json

from alambic_workers.seed.load_reference import (
    _address_block,
    _bool,
    _decode_json_content,
)


def test_bool_conversion():
    assert _bool("true")
    assert _bool("True")
    assert _bool("1")
    assert _bool("yes")
    assert not _bool("false")
    assert not _bool("")
    assert not _bool("0")


def test_decode_json_content_base64():
    original = json.dumps({"document_type": "facture", "fields": []})
    encoded = base64.b64encode(original.encode("utf-8")).decode("ascii")
    decoded = _decode_json_content(encoded)
    assert json.loads(decoded)["document_type"] == "facture"


def test_decode_json_content_empty():
    assert _decode_json_content("") == ""


def test_decode_json_content_not_base64_kept_as_is():
    # Une valeur déjà en clair (non base64) est conservée telle quelle.
    raw = '{"already": "json"}'
    # base64 décoderait sans lever mais produirait des octets invalides ;
    # la fonction retombe sur la valeur brute si le décodage utf-8 échoue.
    result = _decode_json_content(raw)
    assert "already" in result or result == raw


def test_address_block_groups_non_empty():
    row = {
        "address1": "10 rue de la Paix",
        "address2": "",
        "address3": "BP 42",
        "address4": "",
        "address5": "",
    }
    block = _address_block(row)
    assert block == {"line1": "10 rue de la Paix", "line3": "BP 42"}


def test_address_block_empty():
    row = {f"address{i}": "" for i in range(1, 6)}
    assert _address_block(row) == {}
