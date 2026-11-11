# Website inquiries into shared leads

New consultation requests from millcitycabinetry.com are collected once per minute. The shared sales view refreshes once per minute, so an open screen may take up to two minutes to show a new inquiry. Measuring-guide downloads and historical inquiries are excluded. Existing website confirmation/notification behavior is unchanged.

Salespeople open Shared lead pool and claim an inquiry. It then appears under their My opportunities. Shared inquiries do not count as a salesperson's personal pipeline. The existing role permissions continue to apply.

## Configuration

Website: CRM_FEED_TOKEN (secret) and CRM_FEED_START_MS (activation epoch milliseconds).
Workspace: WEBSITE_FEED_TOKEN (encrypted), matching the website token. No other hosting setting changes are required. The polling worker starts only when configured and is disabled in local-pilot mode.

The website exposes a bearer-authenticated, read-only /api/crm-feed endpoint. It returns at most 100 consultation records per page using a monotonic SQLite rowid cursor. The workspace checks up to 20 pages per minute. Stored receipts make retries idempotent; a repeated inquiry never overwrites or reassigns a claimed lead. Lead, activity, and receipt are committed together. An outage preserves inquiries on the website and retries in 60 seconds. Logs contain only counts or a generic failure message, never contact payloads or credentials.

The additive migration 0013 creates workspace_website_leads. Do not delete receipts or website inquiry records during routine cleanup; they maintain replay protection and cursor continuity. Imported inquiries authorize project follow-up only; no campaign/SMS permission is granted and no operations job is created.

## Verification

Backend suite: 145 passed. Website suite: 14 passed, TypeScript check and production build passed. Workspace production build passed. Published website feed rejects anonymous requests (401) and accepts the configured token (200). Final hosted import verification requires the matching encrypted setting and an approved test inquiry.
