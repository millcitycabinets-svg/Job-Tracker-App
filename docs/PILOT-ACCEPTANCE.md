# Local pilot acceptance

This increment extends the actual Job Tracker backend in an isolated local database. It is not connected to the production Job Tracker database or to Monday. Its seed data is artificial. The displayed environment must say `local-pilot` and financial summaries must identify Job Tracker as their source.

## Automated checks

Run from the repository root after installing the repository's development dependencies. Do not source an existing app's `.env` file or use a copied production database URL.

```sh
WORKSPACE_TEST_RUN_DIR="$(mktemp -d "${TMPDIR:-/tmp}/mill-city-workspace-tests.XXXXXX")"
DATABASE_URL="sqlite:///$WORKSPACE_TEST_RUN_DIR/legacy-suite.sqlite" \
  WORKSPACE_ENVIRONMENT=local-pilot HTTPS_ONLY=0 \
  PYTHONPATH=backend .venv/bin/pytest -q backend/tests/test_workspace.py
```

`test_workspace.py` requires an explicitly configured SQLite URL before importing the app. Every test then overrides `get_db` with its own pytest temporary SQLite database, with foreign-key checks enabled. Tests authenticate through the real login route and session cookie. They do not override the current user, call `drop_all`, load provider credentials, send communications, or run application lifespan migrations.

The inherited `test_app.py` **does drop and recreate the configured database**. Run it only with a new disposable SQLite URL, including when running the complete suite:

```sh
DATABASE_URL="sqlite:///$WORKSPACE_TEST_RUN_DIR/legacy-suite.sqlite" \
  WORKSPACE_ENVIRONMENT=local-pilot HTTPS_ONLY=0 \
  PYTHONPATH=backend .venv/bin/pytest -q backend/tests
```

Coverage in the workspace suite:

- Anonymous requests and inactive sessions fail authentication.
- Rep A and Rep B see their own leads and the shared pool; list/search/direct-ID mutation paths cannot expose the other rep's private lead.
- Operations/design access only projected work requests, with no private sales notes. Crew cannot access CRM or work queues. New roles retain no owner financial permissions.
- Shared leads require explicit claim, including when the current rep created the shared lead. Competing simultaneous claims produce one winner; replay does not create extra audit entries. Admin reassignment updates open work context and immediately revokes the former owner's record and replay access.
- Design and Estimate immediately create work without a payment or financial job. Same-stage saves and command replay do not duplicate work. Stale and guessed future revisions conflict. Open requests are reused; completed requests retain history and explicit stage reentry produces a new work version.
- Completion requires a result; Sales can read it, and Sales cannot edit the operations result. Assignment and filters are validated. Changes to sales contact/scope fields refresh open work and its revision; completed context remains a historical snapshot.
- Tracker links use an explicit, authorized existing job, prevent duplicate links, and preserve financial ownership and values. CRM stage, amount, notes and completion actions leave the financial tables unchanged.
- The pre-order operations queue is separate from Design/Estimate work and financial jobs. A won opportunity without a tracker link belongs in Prepping/Ready to Order; a pending lead does not. Operations receives only projected customer/work fields. Queue scheduling uses its own revision and must not change sales notes, CRM value or financial records. Linking the tracker job or returning the opportunity to follow-up removes it from this queue and invalidates stale operations writes.
- Financial summaries use actual seeded tracker payments and costs. Pending payments do not count as collected; current-month collections exclude prior-month receipts and other reps' jobs; unknown costs remain unknown; personal commissions are scoped to their rep. Shared totals contain no private commission data or lead identifiers.
- Operations/admin may update fulfillment status and installation dates through the dedicated scheduling route. Rep/designer/crew attempts fail. Scheduling rejects stale state, forbidden financial/ownership fields and attempts to close jobs; it preserves all monetary fields. The original tracker delete route rejects linked jobs with a conflict.
- Preferences persist across login and remain per-user. Unknown keys, invalid enums/dates, nonfinite/negative money and forbidden financial fields are rejected.
- Activity logging creates a record only. Provider channels remain explicitly unconfigured and do not claim delivery.

## Migration smoke checks

Schema creation in the API fixture is not a migration test. Test the migration chain separately against fresh local files. Never run these commands against the existing live Job Tracker.

```sh
WORKSPACE_MIGRATION_RUN_DIR="$(mktemp -d "${TMPDIR:-/tmp}/mill-city-workspace-migrations.XXXXXX")"
DATABASE_URL="sqlite:///$WORKSPACE_MIGRATION_RUN_DIR/fresh.sqlite" \
  PYTHONPATH=backend .venv/bin/python -m app.migrate
DATABASE_URL="sqlite:///$WORKSPACE_MIGRATION_RUN_DIR/fresh.sqlite" \
  PYTHONPATH=backend .venv/bin/python -m app.migrate
DATABASE_URL="sqlite:///$WORKSPACE_MIGRATION_RUN_DIR/fresh.sqlite" \
  PYTHONPATH=backend .venv/bin/alembic -c backend/alembic.ini current
```

Both fresh upgrade and repeated upgrade must succeed at the workspace migration head. Inspect the resulting schema for the workspace opportunity, request, activity, command-receipt and preference tables. Verify uniqueness for the linked tracker job, command ID, per-user preferences, and an open request of each kind per opportunity.

Also exercise an upgrade from the previous financial schema:

```sh
DATABASE_URL="sqlite:///$WORKSPACE_MIGRATION_RUN_DIR/upgrade.sqlite" \
  PYTHONPATH=backend .venv/bin/alembic -c backend/alembic.ini upgrade 0007
```

Seed only artificial existing users/jobs/payments into this disposable database and save their table-level values. Then upgrade to head, verify the new workspace tables exist, and compare the original financial values exactly. Repeat the upgrade and confirm no changes. A SQLite smoke test does not establish PostgreSQL deployment readiness: before production rollout, run the same migration path and concurrent-update checks against disposable PostgreSQL of the intended version and prove a backup restore.

## Browser walkthrough

Use separate browser sessions for two sales users and one operations user. Admin is for setup and review, not a substitute for checking role restrictions.

1. Log in as Rep A, create a private prospect, personalize columns/widgets, and reload. Verify the data and preferences persist. Rep B must not find that prospect by name or direct URL.
2. Have admin add a shared lead. Both reps can see it. Claim it as Rep A; Rep B loses access and a stale claim fails clearly.
3. Move a prospect to Design, then inspect Operations immediately. The request contains customer contact information and work scope, but no private sales notes. No payment/deposit or tracker job is needed.
4. Save the same stage again and replay a pending save. Confirm one open request. Submit a stale edit from another tab; it must report a conflict rather than overwrite newer work.
5. Operations assigns the request, completes it with a result, and Sales sees that result. After an explicit leave/reentry into Design, a new revision appears while the completed work remains available.
6. Link an existing authorized tracker job by explicit ID. Compare invoice, collections, balance and eligible commission with the original tracker. Changing the CRM amount or stage must not change tracker figures. Another rep's job must not be linkable by a salesperson.
   Before linking, mark a prospect Won and verify it appears in the pre-order queue. Operations can move it from Prepping to Ready to Order and set its target date without seeing private sales notes or commission details. After linking, verify it leaves that queue and appears through its existing tracker record. A stale pre-order editor must not modify the linked job.
7. Check the personal dashboard with both reps. Team collected totals may match; personal commissions and private lead details must not cross accounts. Check Operations cannot open owner finance routes. The monthly goal compares with this month's received payments, not lifetime receipts or pending payments.
8. In Operations, update a fulfillment status and installation date. Confirm the tracker reflects those fields, all money and ownership remain unchanged, and a stale tab cannot overwrite the schedule. Sales and design users must not gain these scheduling permissions. Closure remains with the tracker owner workflow.
9. Inspect communications: actions in this increment are clearly labeled recorded notes/call outcomes. Every email/campaign/call/text provider connection remains marked unconfigured until a real integration and delivery test passes.

## Release boundary

This pilot proves the local workflow increment. It does not complete Gmail synchronization, marketing delivery, cold-campaign sending, browser calling, SMS, historical Monday migration, production hosting, backup recovery or production access review. Those remain part of the requested product and need separate acceptance results. Logging an email/call/text is not a communication integration; a link to a provider dashboard does not meet the requirement that daily communication occurs inside the app.

Before a real migration decision, record the automated test output, migration heads, role walkthrough results, provider tests and the exact outstanding gaps. November 20 is a decision deadline; it does not turn an unverified pilot into a complete replacement. No Monday cancellation, live data import, external send or paid subscription is authorized by running these local tests.

## Results

Final local verification on November 9, 2026:

- **82 tests passed in 66.41 seconds** in the combined backend suite: 29 existing finance/auth tests, 52 workspace cases, and one migration-preservation test. The only warnings are Alembic configuration deprecations.
- `npm run build` passed with 24 modules. `git diff --check` passed.
- Migration checks exercised fresh databases, adoption of both legacy schema generations, and preservation of actual delivery, payment, opportunity, request, and activity rows through migration `0009`, repeated startup, downgrade and reapplication. The populated pilot uncovered a SQLite foreign-key issue with batch table replacement; `0009` now adds columns directly with inline constraints, and the strengthened migration checks pass.
- Independent review covered authentication, private/shared lead boundaries, operations projections, receipts/replay, conflicting edits, financial preservation, and account switching. Findings were fixed and regression-tested. This is not a comprehensive production security audit.
- Browser walkthrough: created a Design lead as Sales, completed its work as Operations, read the returned result as Sales, changed a pre-order stage/date and a tracked fulfillment status/date, created a fictional financial job/payment, and linked it from CRM. The received $5,000 payment raised Morgan's dashboard collections from $55,000 to $60,000; the dashboard balance became $24,000 across Morgan's jobs.
- Browser role and persistence checks: crew retained only its original tools; switching back restored the workspace shell; a second salesperson could not see the first person's private lead; the monthly $75,000 goal and board layout survived reload; the second salesperson retained a separate table view.

The pilot runs at `http://127.0.0.1:8765` with persistent fictional data under ignored `.pilot/`. Its original PostgreSQL production database was not connected or changed. PostgreSQL migration rehearsal against a sanitized production copy, hosted team access, Monday import/reconciliation, provider delivery tests, design-tool integration and file storage remain outstanding before launch.
