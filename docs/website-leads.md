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

## Sales team notifications

A persistent in-app notice appears across the workspace for salespeople and administrators while website inquiries remain unclaimed. It checks every 30 seconds and on window focus. View inquiries opens the latest five leads directly for claiming; larger queues link to the shared pool. Claimed, Won, and Lost leads are excluded, and other roles cannot access the alert endpoint. Existing unclaimed inquiries appear on sign-in, so alerts do not depend on catching a brief toast. These are in-app notices, not background push, email, or SMS notifications when the app is closed.

## Phone push alerts

Salespeople and admins can open Phone alerts in the top bar, enable this device, and send a test. On iOS/iPadOS 16.4+, add the site to the Home Screen first and open that installed app; Chrome on Android supports browser push. Each device must grant notification permission. No Twilio account or SMS service is used.

The push worker runs every 30 seconds independently of the browser. It sends a generic notice for new, still-unclaimed website inquiries after a device opted in. Tapping opens Sales with the shared pool selected. Device cursors advance only after successful provider acceptance, or when all intervening inquiries have been claimed/closed. Failed sends retry; expired subscriptions are removed. A crash after provider acceptance but before cursor commit can resend; the notification tag collapses notices. Delivery is best effort and subject to connectivity and phone Focus settings; a five-minute TTL limits stale queued notices. A claim cannot retract an already delivered phone notification. Existing in-app alerts remain available for missed pushes.

VAPID keys are generated once and encrypted in the database using the existing COMMUNICATIONS_ENCRYPTION_KEY. Subscriptions are encrypted, role scoped, limited to 10 per account, and restricted to known Apple/Google/Mozilla push hosts; redirects are disabled. Deactivated or non-sales accounts are removed by the worker. Logout unsubscribes the current device. No customer name, email, phone, or project information appears in a push payload. Preserve the encryption key and push configuration table across deployments.
