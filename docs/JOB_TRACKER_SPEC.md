# Mill City Job Tracker — Build Spec v1

Owner: Skyler Smith · Date: 2026-10-24 · Design record: ADR-001 Job Profitability Tracking

## Goal

Show every job's true profit, from contract to net, and every rep's collections, with data entered by the people who do the work. Tracking starts clean from go-live; no historical import.

## Hosting

- Separate app from the dealer portal. Own repo, own database, own subdomain: `jobs.millcitycabinetry.com`.
- Stack: FastAPI + React + Postgres (same as the dealer portal, so one skill set maintains both).
- Host: DigitalOcean App Platform for the app, DigitalOcean Managed Postgres with daily backups. Money data must never live on a single unmanaged disk.
- Mobile-first web app, installable to the home screen (PWA). Crew uses it on phones.
- Secrets: Anthropic API key (reads screenshots and text callouts), Gmail OAuth for millcitycabinets@gmail.com (ready-to-order scan), session secret.

## Roles and privacy

Every login is personal. Every entry records who made it and when. Nobody sees another person's pay.

| Role | People | Can see | Can do |
| --- | --- | --- | --- |
| Owner | Skyler | Everything | All entry, approvals, rates, users, reports |
| Designer | Jen | Her own design log | Log designs: name, date, revisions, charge |
| Crew | Omar, Jorge, Mauricio, assembly, delivery | Active job names only (no dollars, no customers' totals), their own entries | Log hours, log assembly, submit reimbursements |
| Rep (phase 2) | Murphy, Delani, Drew, Garner | Their own jobs, collections, commission | Read only at first |

Crew never sees pay rates or job dollars. Their own reimbursements show their own amounts and status.

## The waterfall (per job)

| # | Line | Source |
| --- | --- | --- |
| 1 | Original contract | Ready-to-order email invoice total |
| 2 | Change orders | Change-order / add-on ready-to-order emails, or owner entry |
| 3 | Adjusted revenue | 1 + 2 |
| 4 | Materials | Manufacturer total from ready-to-order email + additional material (expenses, reimbursements) |
| 5 | Assembly | Crew assembly log: boxes × $20 + modifications × $50 + extra modification cost |
| 6 | Install labor | Crew hours × person's rate (Omar $40, Jorge $24.50, Mauricio $20) |
| 7 | Countertops | Dollar entries: slabs/materials + fabrication/install (Abraham). Job also stores a countertop invoice amount for countertop margin |
| 8 | Design | Designer log entries matched to this job |
| 9 | Subcontractors | Delivery, other installers, other subs |
| 10 | Card processing fees | Fee on each card payment |
| 11 | Unexpected | Owner entry, reason required |
| 12 | Referral payout | Amount negotiated before start, entered on the job |
| 13 | Job profit | 3 − (4..12). Commission base |
| 14 | Commission | Rep rate × line 13 (Murphy 40%; Delani, Drew, Garner 30%) |
| 15 | Net to Mill City | 13 − 14, plus margin % of adjusted revenue |

Every figure is calculated from entries; nothing on the waterfall is typed directly.

## Data model

- `users`: name, role, login, active, hourly_rate (crew), commission_rate (rep).
- `jobs`: name, customer, rep_id, manufacturer, invoice_total, countertop_invoice, material_total, boxes, modifications, install_date, status (Ordered, Assembled, Delivered, Installed, Punch, Closed), frozen flag, referral_amount, closed_at.
- `rto_emails`: gmail message id (unique), job_id, parsed fields, kind (new / change order / add-on), raw text.
- `labor_entries`: user_id, job_id, date, hours, rate_at_entry, total. Rate copied at entry time so later rate changes don't rewrite history.
- `assembly_entries`: user_id, job_id, date, boxes, modifications, extra_cost, note, status (submitted, approved, paid).
- `design_entries`: user_id, design_name (free text), date, revisions, charge, job_id (null until matched).
- `expenses`: job_id or null (overhead), waterfall_line, type, payee, amount, note, receipt_url.
- `reimbursements`: user_id, amount, purpose, job_id or null, receipt_url (required), status (submitted, approved, paid), paid_at. An approved reimbursement tied to a job also creates its expense line.
- `payments`: job_id, amount, method, status (pending, received), expected_date, received_date, payment_type, processing_fee, screenshot_url.
- `unmatched`: raw input, best-guess job ids, resolved_job_id.
- `audit_log`: user, table, row, field, old value, new value, timestamp. Written on every create, edit, delete, approve.

## Screens

### Owner

1. **Inbox (smart input):** one box. Drop a screenshot, type, or dictate. The server sends it to Claude, which returns a structured entry; the app shows a confirmation card ("Payment · $4,200 · Dustin Selden · Drew · Check"). One tap saves it to the right table. Unsure entries go to Unmatched.
2. **Jobs:** list with status, rep, adjusted revenue, projected profit, balance due. Filter by status and rep.
3. **Job detail:** the 15-line waterfall, each line expandable to its entries; edit any entry (audited).
4. **Collections:** collected by rep by day / week (Sunday–Saturday) / month / quarter / year; cash in by day; method mix; pending and on its way; outstanding by rep; sold vs collected.
5. **Approvals:** submitted reimbursements and assembly entries; approve, then mark paid.
6. **Design pipeline:** all design entries, matched and unmatched; close rate by designer.
7. **Unmatched:** anything that couldn't be tied to a job, with suggested matches.
8. **Two-week outlook:** jobs by install date with expected labor, material outstanding, and expected cash in.
9. **Settings:** users, roles, rates.

### Crew

1. **Log hours:** job dropdown (active jobs only), date (defaults to today), hours. Submit.
2. **Log assembly:** job dropdown, boxes, modifications, extra cost and note. Shows the job's expected box count from the ready-to-order email for a quick sanity check.
3. **My reimbursements:** new reimbursement (amount, what for, job or "none" for things like fuel, receipt photo required) and a list of their own with status.

Crew entries are editable for 3 days, then locked; owner can still edit.

### Designer

1. **My designs:** add a design (name, date, revisions, charge); list of her own entries with matched job when one exists.

## Automations

### Ready-to-order scan

Runs on the server at 9 am, 1 pm and 5 pm Arizona time.

1. Search Gmail for ready-to-order emails sent to manufacturers since the last run.
2. Skip any message id already in `rto_emails`.
3. Parse with Claude: job name, rep, invoice total, manufacturer, material total, boxes, modifications.
4. Match:
   - Contains "change order" or "add-on" → attach to existing job (invoice to line 2, material to line 4).
   - Job name matches an existing job → attach.
   - No match → create job.
   - Unsure → Unmatched.
5. Missing material total → leave blank; the next run re-checks that thread and fills it.

### Design matching

When a job is created, suggest design entries whose names resemble the job name. Owner confirms with one tap; confirmed design charges land on line 8. Unmatched designs stay in the pipeline.

### Commission

A job becomes payable when: paid in full, no open change orders or requests, not frozen. Owner closes it; commission shows as due immediately.

## Build order

1. Repo, hosting, database, logins and roles, audit log.
2. Jobs and the waterfall (owner manual entry first).
3. Crew screens: hours, assembly, reimbursements, approvals.
4. Designer screen and design matching.
5. Payments and collections reports.
6. Smart input (Claude parsing of screenshots and text).
7. Ready-to-order Gmail scan.
8. Two-week outlook.

## Acceptance checks

- Omar logs 13 hours on Dustin Selden → job line 6 shows $520; Omar's screen shows 13 hours and no dollars.
- Jorge cannot see Omar's entries, any job's dollars, or any reimbursement but his own.
- A $180 Home Depot reimbursement tied to a job adds $180 to that job's line 4 once approved and appears as owed until marked paid. A fuel reimbursement with no job hits no job.
- Changing Omar's rate does not change past entries.
- The same ready-to-order email processed twice creates one job.
- A change-order email attaches to its existing job.
- Every edit appears in the audit log with old and new values.

## Out of scope for v1

Rep logins, credit card statement tracking, QuickBooks sync, historical data import, commission on negative-profit jobs.
