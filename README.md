# Mill City Job Tracker

Job profitability, crew hours and collections for Mill City Cabinetry. Spec: `docs/JOB_TRACKER_SPEC.md`.

## What works now

- Personal logins with roles: owner, crew, designer, rep. Sessions last 30 days.
- Owner: jobs, the 15-line profit waterfall, costs and change orders per line, team logins and pay rates.
- Crew: log their own hours against a job picked from a list; they see their own hours and pay, never job dollars. Hours go submitted → approved → paid; the owner approves each person's week at once, and only approved hours count on a job. Entries lock once approved.
- My pay (crew and designer): earnings by week, split into waiting, owed and paid.
- Pay rates are copied onto each hours entry, so a rate change never rewrites past jobs.
- Crew log assembly (boxes, modifications, extra cost) and submit reimbursements with a receipt photo; owner approves, rejects with a reason, and marks paid. Only approved entries count on a job. Fuel and other no-job reimbursements hit no job.
- Designer logs each design (name, date, revisions, charge) before a job exists; charges go through the same approve → pay flow, and the designer never sees close rates. Owner sees suggested matches on each job and links them with one tap; linked charges land on line 8. Designs page shows close rate per designer.
- A login locks for 15 minutes after 5 wrong passwords.
- Payments per job: received or pending, method, type, card fee (a job cost on line 10), optional screenshot. Each job shows collected and balance due.
- Collections: collected by rep by day / week (Sunday to Saturday) / month / quarter / year, pending payments, balances still owed, sold by rep, payment methods, and commission due on closed, paid-in-full jobs with a Mark paid button.
- Quick log (owner): type, dictate or drop a screenshot; Claude drafts the entry (payment, crew hours or job cost), you check it and save. Needs `ANTHROPIC_API_KEY` set on the app.
- Ready-to-order intake: `POST /api/intake/email` (header `x-intake-token`) takes an email's subject, body, message and thread id and the estimate PDF. New jobs are created, change orders / add-ons attach to their job, vendor replies on the same thread fill the material total, duplicates are ignored, and near-miss names wait on the Jobs page for the owner. The rep is whoever with a rep login is on To/CC (the sender is skipped); nobody = the owner's own job. Needs `INTAKE_TOKEN` and `ANTHROPIC_API_KEY`.
- Every create, edit and delete is recorded in an audit log (who, when, old and new value).

## Run locally

```bash
# backend (needs Postgres)
cd backend
pip install -r requirements.txt pytest httpx
export DATABASE_URL=postgresql+psycopg://postgres@localhost/jobtracker
python -m app.create_owner "Skyler Smith" you@example.com 'a-strong-password'
uvicorn app.main:app --reload --port 8000

# frontend (second terminal)
cd frontend && npm install && npm run dev
```

Tests: `cd backend && createdb jobtracker_test && DATABASE_URL=postgresql+psycopg://postgres@localhost/jobtracker_test pytest`

## Deploy (DigitalOcean App Platform)

1. Create a Managed Postgres database (enable daily backups).
2. Create an App from this GitHub repo; it builds from the `Dockerfile`, port 8080.
3. Environment variables: `DATABASE_URL` (the database connection string), `SESSION_SECRET` (a long random string), `ANTHROPIC_API_KEY` (for Quick log). Mark all three Encrypt.
4. Add the domain `jobs.millcitycabinetry.com` and point a CNAME at the app.
5. Create the owner login once from the app's console: `python -m app.create_owner "Skyler Smith" email 'password'`.

## Changing the database

The app applies migrations on every start. To change the schema: edit `backend/app/models.py`, then from `backend/` run
`alembic revision --autogenerate -m "what changed"`, review the new file in `migrations/versions/`, and commit it.
