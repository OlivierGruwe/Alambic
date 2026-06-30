"""
Seed des tables de référence Alambic depuis les CSV exportés de FlowerScan.

Étape 1 (ce script) : accounts + doctypes. Les configs (regroupement JSONB)
viendront dans un second temps.

Transformations appliquées :
  - account_id / doctype_id  → id (clé primaire alambic_core)
  - address1..5              → bloc JSONB address {line1..line5}
  - json_content (base64)    → décodé en texte JSON lisible
  - dates "JJ/MM/AAAA HH:MM:SS" → ignorées (created_at/updated_at gérés par la DB)
  - secrets (edenai_secret_key, keys) → laissés VIDES (ressaisie via UI, décision)
  - active "true"/"false"    → booléen

Idempotent : si un id existe déjà, on met à jour plutôt que de dupliquer.

Usage (infra docker-compose debout + variables .env chargées) :
    uv run python -m alambic_workers.seed.load_reference
ou via Makefile : make seed
"""

from __future__ import annotations

import base64
import csv
from pathlib import Path

from alambic_core.db.session import get_sessionmaker, init_core
from alambic_core.models import Account, Doctype

SEEDS_DIR = Path(__file__).resolve().parent.parent.parent / "seeds"


def _bool(value: str) -> bool:
    return str(value).strip().lower() in ("true", "1", "yes", "on")


def _decode_json_content(raw: str) -> str:
    """json_content est du JSON encodé en base64 dans l'export. On le décode."""
    if not raw:
        return ""
    try:
        return base64.b64decode(raw).decode("utf-8")
    except Exception:
        # Si ce n'est pas du base64 valide, on garde la valeur brute.
        return raw


def _address_block(row: dict) -> dict:
    """Regroupe address1..5 en un bloc JSONB, en ignorant les lignes vides."""
    block = {}
    for i in range(1, 6):
        val = (row.get(f"address{i}") or "").strip()
        if val:
            block[f"line{i}"] = val
    return block


def seed_accounts(session) -> int:
    path = SEEDS_DIR / "accounts.csv"
    count = 0
    with open(path, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            acc_id = row["account_id"]
            acc = session.get(Account, acc_id)
            if acc is None:
                acc = Account(id=acc_id)
                session.add(acc)
            acc.account_name = row.get("account_name", "")
            acc.active = _bool(row.get("active", "false"))
            acc.address = _address_block(row)
            acc.zip = row.get("zip", "")
            acc.town = row.get("town", "")
            acc.country = row.get("country", "")
            # Secrets laissés vides (décision : ressaisie via UI).
            acc.edenai_secret_key = ""
            acc.keys = ""
            count += 1
    return count


def seed_doctypes(session) -> int:
    path = SEEDS_DIR / "doctypes.csv"
    count = 0
    with open(path, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            doc_id = row["doctype_id"]
            dt = session.get(Doctype, doc_id)
            if dt is None:
                dt = Doctype(id=doc_id)
                session.add(dt)
            dt.doctype_name = row.get("doctype_name", "")
            dt.account_id = row.get("account_id") or None
            dt.is_public = _bool(row.get("is_public", "false"))
            dt.json_content = _decode_json_content(row.get("json_content", ""))
            count += 1
    return count


def main() -> None:
    init_core()  # lit ALAMBIC_DATABASE_URL + ALAMBIC_SECRET_KEY
    Sess = get_sessionmaker()
    with Sess() as session:
        n_acc = seed_accounts(session)
        n_doc = seed_doctypes(session)
        session.commit()
    print(f"Seed terminé : {n_acc} compte(s), {n_doc} doctype(s).")


if __name__ == "__main__":
    main()
