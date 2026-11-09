# Communications pilot

The Communications screen is part of the existing authenticated workspace. It uses
existing lead access rules and does not alter Job Tracker finances. No credentials
or live messages are included in the repository. No background campaigns run.

## Gmail: company accounts

Create a Google Cloud project **inside the company Workspace organization**.
Configure Google Auth Platform with an Internal audience, enable Gmail API, and
create a Web application OAuth client with this exact redirect:

`https://jobs.millcitycabinetry.com/api/workspace/communications/gmail/callback`

Requested scopes: `openid`, `email`, `gmail.send`, `gmail.readonly`. Users grant
access individually. Each verified company mailbox must match that user's existing
Job Tracker login email. Correct an account email deliberately if needed; do not
silently connect somebody else's mailbox. Reading is restricted in the app to
conversations involving the selected accessible lead and the user's own mailbox.
Only plain text is displayed; HTML/attachments are not rendered in this pilot.

Encrypted hosting settings:

- `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`
- `GOOGLE_WORKSPACE_DOMAIN=millcitycabinetry.com`
- `COMMUNICATIONS_PUBLIC_URL=https://jobs.millcitycabinetry.com`
- `COMMUNICATIONS_ENCRYPTION_KEY`: a Fernet key generated with
  `python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'`

Keep the encryption key backed up securely; never put it in Git, chat, or a customer
record. Losing or replacing it requires reconnecting mailboxes. Refresh tokens are
encrypted in the database and redacted from the audit log. The OAuth state is
session-bound, expires after 10 minutes, and is single-use in the database.
Disconnect revokes the Google grant before removing local access.

## Twilio: outbound browser calling and two-way texts

Recover the existing Twilio account before purchasing a number or creating new
billing. Choose a US/Canada number with Voice and SMS. Register business SMS
(A2P 10DLC or toll-free verification) and establish the approved consent flow.

Encrypted hosting settings:
`TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_API_KEY`,
`TWILIO_API_SECRET`, `TWILIO_TWIML_APP_SID`, `TWILIO_PHONE_NUMBER`.
Only set `TWILIO_SMS_ENABLED=1` after number registration and controlled testing.

Configure the TwiML application Voice URL (POST):
`https://jobs.millcitycabinetry.com/api/workspace/communications/phone/voice`

Configure the number's incoming message URL (POST):
`https://jobs.millcitycabinetry.com/api/workspace/communications/phone/inbound`

Status callbacks and call completion URLs are set by the app. Use the exact public
origin; incoming signatures are checked against it. Tokens expire after 10 minutes.
Calls use an account-bound, single-call intent with the destination stored on the
server. Calls are capped at one hour. The pilot restricts destinations to US/Canada.
An authorized call still incurs provider charges. No call recording is enabled.
Incoming voice routing, voicemail, and transfers are not implemented in this pilot.
Do not port the main company number until incoming routing has been implemented.

Texts require recorded permission. STOP blocks the number globally. Incoming texts
attach to the most recent outbound conversation on that number. Unknown senders
remain in Twilio; an unassigned inbox is not implemented. Use explicit opt-in
recovery through the provider after STOP; the app cannot override an opt-out.

## Customer campaigns

Resend is for subscribed recipients only. Its policy prohibits scraped/purchased
list outreach. Prospect drafts are separate and cannot use this provider.
Verify a sending domain and configure SPF/DKIM, choose the From mailbox, and
supply the real postal address. Connect a separate prospect provider before
activating researched-list campaigns.

Encrypted settings: `RESEND_API_KEY`, `RESEND_WEBHOOK_SECRET`,
`CAMPAIGN_FROM_EMAIL`, `CAMPAIGN_POSTAL_ADDRESS`, `COMMUNICATIONS_SIGNING_KEY`.
The signing key must be a long random secret and remain stable for unsubscribe links.

Configure signed provider events for email.delivered, email.bounced,
email.complained, email.failed, and email.suppressed at:
`https://jobs.millcitycabinetry.com/api/workspace/communications/campaigns/events`

Campaign drafts hold up to 50 selected leads. Review shows duplicates, missing
permission, suppression, and prior delivery attempts. Each button dispatches at
most five reviewed eligible recipients, allowing bounded requests and explicit
continuation. A changed audience/permission invalidates the review token. Messages
contain the postal address, unsubscribe link, and one-click headers. GET only shows
a confirmation page so link scanners cannot unsubscribe contacts; POST suppresses
further email. Signed bounce/complaint events also suppress the address globally.
The UI distinguishes provider acceptance from actual delivery.

## Failure handling and launch validation

A database receipt is committed before sending. Repeating the same command cannot
send twice. If the provider result is uncertain, its receipt stays `unknown` and
is not automatically retried. Check the provider before intentionally composing
another message. A process crash can leave `sending`; treat this as uncertain too.

Before enabling use: connect one consenting employee mailbox, send an explicitly
authorized test email, load its reply; authorize a test call and opted-in text,
check replies/STOP; authorize a small subscriber campaign and verify delivery,
unsubscribe and bounce callbacks. No customer sends are part of installation.

Automated tests use fake providers and disposable SQLite. They verify access,
OAuth state replay and mailbox identity, encryption, idempotency, uncertain sends,
SMS permission/STOP, signed callbacks, fixed call destinations, campaign review,
duplicate/suppressed recipients, unsubscribe semantics, and schema rollback.
Live provider verification remains separate from these tests.
