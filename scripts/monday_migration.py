#!/usr/bin/env python3
"""Plan Monday migration offline; apply only to an explicitly verified target."""
import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def read_json(path):
    return json.loads(Path(path).read_text())


def secure_json(path, value):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        os.fchmod(handle.fileno(), 0o600)
        json.dump(value, handle, indent=2, ensure_ascii=False, default=str)
        handle.write("\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("plan", "apply"))
    parser.add_argument("--export", required=True)
    parser.add_argument("--mapping", required=True)
    parser.add_argument("--output", required=True, help="Private JSON report path, normally .migration/")
    parser.add_argument("--check-target", action="store_true", help="Read configured target DB to validate IDs and prior imports")
    parser.add_argument("--target-id", help="Must equal MIGRATION_TARGET_ID when applying")
    parser.add_argument("--backup-reference", help="Verified restore/backup artifact reference, required when applying")
    parser.add_argument("--expected-source-sha256", help="Reviewed export file hash, required when applying")
    parser.add_argument("--expected-mapping-sha256", help="Reviewed owner/job mapping file hash, required when applying")
    args = parser.parse_args()
    source_bytes = Path(args.export).read_bytes()
    source_hash = hashlib.sha256(source_bytes).hexdigest()
    mapping_bytes = Path(args.mapping).read_bytes()
    mapping_hash = hashlib.sha256(mapping_bytes).hexdigest()
    export, mapping = json.loads(source_bytes), json.loads(mapping_bytes)
    # Offline planning does not connect to the configured database.
    if args.action == "plan" and not args.check_target:
        os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
    from app.monday_import import apply_plan, build_plan
    if args.action == "apply":
        if not os.environ.get("DATABASE_URL"):
            parser.error("Apply requires an explicitly configured DATABASE_URL")
        if not args.target_id or args.target_id != os.environ.get("MIGRATION_TARGET_ID"):
            parser.error("Apply target ID must exactly match verified MIGRATION_TARGET_ID")
        if not args.backup_reference:
            parser.error("Apply requires the verified backup reference")
        if args.expected_source_sha256 != source_hash:
            parser.error("Export hash differs from the reviewed source SHA-256")
        if args.expected_mapping_sha256 != mapping_hash:
            parser.error("Owner/job mapping hash differs from the reviewed mapping SHA-256")
    if args.action == "apply" or args.check_target:
        if not os.environ.get("DATABASE_URL"):
            parser.error("Target checks require an explicitly configured DATABASE_URL")
        from app.db import SessionLocal
        from app import audit  # noqa: F401 - preserve real actor/source audit records
        with SessionLocal() as db:
            if args.action == "apply":
                result = apply_plan(db, export, mapping, target_id=args.target_id, backup_reference=args.backup_reference)
                db.commit()
            else:
                result = build_plan(export, mapping, db)
    else:
        result = build_plan(export, mapping)
    if "entries" in result:
        result["entries"] = [{k: v for k, v in row.items() if k != "source_item"} for row in result["entries"]]
    result["source_file_sha256"] = source_hash
    result["mapping_file_sha256"] = mapping_hash
    secure_json(args.output, result)
    print(json.dumps({"action": args.action, "summary": result["summary"], "source_file_sha256": source_hash,
                      "mapping_file_sha256": mapping_hash,
                      "report": str(Path(args.output).resolve())}, indent=2))


if __name__ == "__main__":
    main()
