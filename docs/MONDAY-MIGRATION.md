# Monday migration

The importer adds source-linked sales opportunities and scoped operations work to the existing Job Tracker workspace. It never creates or edits financial jobs, payments, costs or commissions.

## Routing

- Design and Estimate create scoped operations requests.
- Prepping and Ready to Order become won opportunities awaiting an explicit existing Job Tracker link.
- The reviewed Active backlog routes to Sales Follow up with its original group and status retained.
- Ordered records require an explicit existing Job Tracker ID and supporting source evidence. Ambiguous matches remain held.
- Ownership uses verified source and target account identities. Missing or multiple owners remain private administrator review records.

## Private migration records

Exports, owner mappings, reconciliation reports, source comments, file manifests, backup references and transfer artifacts live under ignored `.migration/` and `.deployment/`. They must never be committed or copied into public logs. Files preserved locally are not automatically available through the hosted app. Do not claim a complete migration until excluded groups, discussion history, files and remaining holds are reconciled.

## Execution

Use `scripts/monday_migration.py plan --export SOURCE --mapping MAP --output REPORT` for an offline plan. Add `--check-target` with the intended database configured to validate existing IDs and source receipts without writing.

Before applying, verify a private backup, review the source and mapping SHA-256 hashes, and upgrade through migration 0011. Apply requires an explicit `DATABASE_URL`, a `--target-id` matching `MIGRATION_TARGET_ID`, `--backup-reference`, `--expected-source-sha256` and `--expected-mapping-sha256`. The CLI prints aggregate results and writes its detailed report privately.

Source identities are unique by board/item. Reruns preserve existing edits and work history; changed source snapshots require reconciliation. The batch applies in one transaction. Compare committed counts and source receipts with the approved plan, and verify existing financial data remains unchanged.

`scripts/production_backup.py` creates a consistent read-only PostgreSQL binary COPY archive with per-table checksums. Preserve its source commit and schema revision. `scripts/rehearse_workspace_migration.py` tests the additive schema change on PostgreSQL in a guarded transaction that always rolls back and verifies the original schema afterward.

`scripts/monday_assets.py` preserves authorized source files with size and checksum verification. Its metadata and output directory must remain private. Refresh expiring source URLs through the authorized Monday connector; never disable TLS verification.

## Validation

Isolated tests cover source parsing, routing, owner review, unchanged finance, rerun preservation, ordered-link evidence, incomplete exports and target/backup/hash gates. Production results belong in the private release report.
