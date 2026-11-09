# Mill City workspace pilot

This is the actual Job Tracker backend with an additive sales CRM and operations workspace. The local pilot uses **fictional jobs and payments in its own persistent database**. It does not read or modify jobs.millcitycabinetry.com, Monday, Gmail, or a phone/email provider. Closing the browser does not erase records.

## Start

From this repository:

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
cd frontend
npm ci
npm run build
cd ..
.venv/bin/python scripts/run_pilot.py
```

Open **http://127.0.0.1:8765**. The server binds only to this computer. `scripts/run_pilot.py` overrides inherited database/session settings and removes intake/AI credentials. Database and session files are stored under ignored `.pilot/`. Back up that directory before experiments you want to keep. The launcher is intentionally not a production deployment command.

## Local test accounts

All accounts below use the public **local-only test password `MillCityPilot!20`**. Never create these accounts on a shared/production server.

| Login | Test role |
|---|---|
| admin@pilot.millcity.test | Admin: sales, operations, original Job Tracker finance screens |
| sales@pilot.millcity.test | Morgan: personal sales, shared lead pool, own financial totals |
| sales2@pilot.millcity.test | Taylor: separate personal leads and financial totals |
| operations@pilot.millcity.test | Back-office requests and scheduling; no private sales notes or commissions |
| design@pilot.millcity.test | Design/estimate work and original design entry tools |
| crew@pilot.millcity.test | Original crew Job Tracker tools; no CRM access |

## Try the handoff

1. Sign in as Sales. Create a lead and set its stage to **Design** or **Estimate**, with a clear work scope.
2. Sign out and sign in as Operations. Open the queued work, set an assignee/date, and move it to In Progress. Complete it with a result.
3. Return to Sales. Open that opportunity and see the result. Editing the same stage does not create another request; leaving and reentering the stage after completion requests a new version.
4. Claim a shared prospect. Sales 2 can no longer access it once it becomes Sales 1's private lead.
5. Open Sample — Maple House and its linked Job Tracker job. As Admin, record a fictional received payment in Job Tracker, then return Home to verify the collections/balance totals update from those same records.
6. Change personal columns, board/table view, home widgets, and monthly goal. Reload and verify the saved view.
7. Mark an unlinked sale **Won**. It appears under Operations → **Prepping & ready to order**. Operations can set its stage and due date without creating financial entries. Linking a Job Tracker record removes it from this queue; its fulfillment progress comes from Job Tracker.

The browser walkthrough left **Pilot walkthrough — Birch Kitchen** as a completed design request, won opportunity, and linked financial test job, with a fictional $5,000 payment. This is useful for tracing the whole handoff. The sample Maple House was advanced to Assembled with a November 18 installation date, and Canyon Residence was moved to Ready to Order. All are test records.

Communications provider cards are configuration placeholders. Notes and call outcomes can be logged, but calls, texts, email delivery, reply sync, and campaigns are not connected in this pilot. No Send button reports success without a provider.

## Before production

Validate the pilot with sales and operations; back up and verify the current production database and deployed commit; rehearse the additive migration against a sanitized PostgreSQL copy; reconcile Monday groups/statuses and imports; configure HTTPS and real user accounts; connect Google/phone/email providers and verify end-to-end delivery. Production rollout must preserve current financial data and existing authentication. The pilot is intended for testing before November 20, not as evidence that migration or provider setup is complete.
