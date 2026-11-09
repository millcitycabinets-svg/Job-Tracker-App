# Hosted workspace pilot

Release preparation, November 9, 2026. **This document does not confirm deployment or a completed Monday import.** Record the deployed commit, migration revision, backup reference and smoke-test results below before describing the workspace as live.

The intended address is [jobs.millcitycabinetry.com](https://jobs.millcitycabinetry.com/), using the existing DigitalOcean `coral-app` service and its managed PostgreSQL database. The observed hosting configuration was one $12/month app service plus a $15.15/month database; this is a snapshot, not a future invoice or an additional service order. No new hosting service is required by this release plan.

## What the release includes

- Team sign-in through existing Job Tracker accounts. Accounts and passwords are retained; the session-fingerprint change requires existing users to sign in again after deployment. Signing in or out in another tab clears stale screens; API requests are bound to the account shown in the tab so an old draft cannot be submitted as a different signed-in user.
- Personal sales opportunities and an explicit shared lead pool, with saved table/board layouts, columns and home widgets. Admin sees all leads; a salesperson sees owned personal leads and shared leads.
- Design and Estimate requests sent to Operations immediately, without a deposit prerequisite. Operations receives customer/work context, not sales notes or commissions. Repeated stage saves reuse open work; completed work retains its history.
- Won opportunities in Prepping / Ready to Order until an existing Job Tracker job is explicitly linked. Fulfillment scheduling and financial records stay with the existing Job Tracker data.
- Reviewed CSV contact import, described below. Importing contacts creates New Lead prospects; it does not create financial jobs or send messages.

Email synchronization, browser calling, SMS, campaigns and outreach sequences remain unconnected. Buttons must stay disabled until those services are configured and tested. The marketing file library and cabinet-design portal connection remain pending. Logging a call outcome records a note; it does not place a call.

## Uploading contacts

1. In **Sales**, select **Upload CSV** beside **New lead**. **Download template** supplies these columns: `name,customer,email,phone,address,notes,source_url`.
2. Keep **Personal leads** for private ownership, or explicitly choose **Shared lead pool**. Admin can choose an active sales/admin owner. Shared lead viewers can read sales notes; Operations does not receive those notes.
3. Choose a CSV file of at most **5 MiB / 5,000 contact rows**. Headers are matched automatically. Each usable contact needs a name or company and an email or phone number.
4. Review **Ready to import**, **Duplicates to skip** and **Invalid rows**. The table shows up to the first 100 reviewed rows. Duplicate checks use email/phone matches against earlier valid file rows and existing leads the importing user may see; they do not reveal another rep’s private records.
5. Expand **Match CSV columns** to correct unfamiliar headers or leave unwanted fields unmapped. After changing columns, pool or owner, select **Update preview**. The original file remains available while remapping or correcting an error.
6. Select **Import valid contacts** only after reviewing the preview. Wait for **Import complete**, then check the actual created/skipped counts. Invalid and duplicate rows are skipped. If the request fails, **Retry import** reuses the same command identity to avoid repeating a completed import.

Source URLs are stored as provenance and displayed as text during review; the importer does not visit them. Imported records remain prospects in **New Lead**, with no marketing subscription or consent inferred. To correct invalid rows, edit the original CSV and upload it again. A new preview rechecks visible duplicates.

Local verification used fictional contacts: a four-row file created two contacts, skipped one duplicate and one invalid row; reupload offered zero new contacts. Unrecognized headers were remapped successfully, and a shared-pool import created one contact with the selected owner. Template download and a mobile layout were checked. Backend CSV tests include same-command replay and concurrent import protection. These results are local evidence, not production acceptance.

Frontend account-isolation checks: from `frontend/`, run `node --test tests/api-session.test.mjs`. These cover stale-tab requests, identity headers on workspace and legacy finance routes, 401 invalidation, old responses, cross-tab signals and interrupted photo preparation. Production browser checks below are still required.

## Monday migration status

The reviewed source inventory and target ownership map are stored privately. Use the current approved manifest and reconciliation report for proposed and completed import totals.

The uncertain Active backlog stays with Sales. Ordered records must not be matched by name guesses or converted into new financial jobs. Source ownership exceptions remain in private admin review until resolved. Preserve source identifiers and history, and verify imported records against the source before any Monday cutover. See [MONDAY-MIGRATION.md](MONDAY-MIGRATION.md) for scope, attachment/history coverage, holds and the rehearsal process.

## Production smoke checklist

Use existing authorized accounts and approved test records. Record evidence without copying customer details into public logs or screenshots. This checklist does not run migrations or authorize financial test transactions.

- [ ] Confirm the intended commit is deployed to the existing service, HTTPS works, the health check passes, and the displayed environment does not say “Local pilot” or contain fictional pilot accounts/data.
- [ ] Record the verified backup/restore evidence, schema revision and before/after financial reconciliation. Existing users, jobs, payments, costs and commissions match the pre-release baseline.
- [ ] Refresh previously open tabs to load the release, verify a previous session is rejected after the session-fingerprint change, then sign in with an existing account. Check logout, re-login and account switching, including stale tabs. No previous user’s drafts or records should appear or submit under a different user.
- [ ] Check Admin, two distinct Sales users, Operations, Design and Crew. Rep B cannot search or directly open Rep A’s private lead; shared leads are visible to both until claimed. Operations/Design cannot open full sales records or owner finance screens. Crew retains its existing tools.
- [ ] Create one approved test prospect, request Design then Estimate, and verify the scoped Operations work and returned result. Same-stage saves do not duplicate work. Stale updates report a conflict.
- [ ] Mark the approved test prospect Won and verify Prepping / Ready to Order. Test a tracker link only against an approved existing test job; no financial job or monetary value is created by the CRM action. Check schedules through the appropriate role without altering real customer commitments.
- [ ] Check dashboard collections/balances against Job Tracker; personal commissions remain private. Monthly goals use received payments in the current month. Change one personal view preference and confirm it persists only for that user.
- [ ] Download the CSV template. Upload a small approved fictional fixture with valid, duplicate and invalid rows; review, commit, verify actual counts, then reupload to check duplicate prevention. Test custom-header remapping and confirm the selected ownership/pool. Keep such records clearly identified for controlled cleanup.
- [ ] Confirm all unconnected communications and file-library actions remain honestly unavailable. No logged activity claims delivery, no import claims marketing consent, and no source URL is fetched by import.
- [ ] Compare the approved Monday manifest with committed counts, ownership, Operations visibility and Ordered holds. Unresolved records stay held; cutover remains pending until reconciliation is signed off.

Release record: deployed commit **pending** · schema revision **pending** · backup/restore reference **pending** · smoke-test date/operator **pending** · Monday committed/rejected/held counts **pending** · remaining issues **pending**.
