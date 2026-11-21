# Real accounts with Xaman on Testnet — design

Date: 2026-11-21. Branch: `real-accounts-testnet`.

## Goal

Turn safeDeposit from a demo with server-held persona keys into an app real people can use on the XRPL
Testnet. The home page explains the app and offers sign-in / account creation. All demo-specific
features are removed. The server holds no user keys.

## Decisions

| Question | Decision |
|---|---|
| Wallet | **Xaman only**, via its server-side REST API (API key + secret in `.env`). GemWallet is removed: it can't multisign. |
| Accounts | Signing in with Xaman for the first time creates the account. No passwords. |
| Arbiter | Chosen per agreement: tenant and landlord both approve the arbiter, the arbiter accepts the role. |
| Agreement | Invite links; all three parties must accept before anything goes on the ledger. |
| Test funds | In-app Testnet faucet: activation XRP, trust line, test tokens. |
| Credential | VERIFIED_ARBITER credential, credential issuer and badge are dropped. |
| Escrow release | The platform bot submits EscrowFinish on request of the tenant or landlord; the fulfillment never leaves the server. |

## 1. Accounts and sign-in

- `/` explains the app (existing whitepaper content) with one **Sign in with Xaman** button.
- Sign-in is a Xaman `SignIn` payload (`force_network: TESTNET`). The page shows a QR code (rendered
  server-side as SVG, so the browser never contacts Xaman) and an "Open in Xaman" deep link for phones.
  The browser polls our own API for the payload status.
- When the payload is signed, the server fetches the result from Xaman's API with the API secret, reads the
  account address, and stores the account in the Django session (`party_id`, as today). The session id is
  rotated on login. **Sign out** flushes the session.
- First sign-in creates the account and asks for a display name (shown to the other parties; never put on
  the ledger).
- `Party` becomes the user account: `address` (unique), `display_name`, `created_at`. Removed fields:
  `seed`, `wallet_type`, fixed `role`. A person's role comes from each lease.
- Removed: personas, demo bar, `/demo/`, `/demo/stage/`, `/arbiter/`, `demo_scenarios`, `demo_reset`, every
  `/api/demo/*` endpoint, `DEMO_MODE`, the GemWallet adapter and `static/vendor/gemwallet-api-*.js` (and its
  entry in `scripts/fetch_vendor.py`), `/api/wallet/*`, the credential issuer, `/api/credentials/*` and the
  badge.
- Kept: `run_bot`, `preflight`, `smoke_test` (admin health checks; `smoke_test` keeps generating its own
  throw-away wallets). `bootstrap_demo` is renamed `bootstrap` and creates only platform accounts:
  treasury, bot, EUR and USD issuers.

## 2. Agreement flow

1. **Draft.** A signed-in user clicks *New deposit agreement*, says whether they are the tenant or the
   landlord, and enters title, currency (test EUR / USD), amount, lease end date and grace period. The
   arbiter is optional at this point: either an XRPL address or left open to be filled by the arbiter
   invite. Nothing goes on the ledger.
2. **Invites.** The draft page shows two invite links: one for the other party, one for the arbiter. Each
   is a random, unguessable token (`secrets.token_urlsafe(32)`) bound to one role, usable once.
3. **Accepting.** Opening a link asks the person to sign in with Xaman if needed, then shows the full terms
   (amount, dates, all named parties with addresses, the settlement rule). The other party clicks
   *Accept*; the arbiter clicks *Accept the arbiter role*. A person cannot take two roles on one lease.
4. **Arbiter approval.** Tenant and landlord must both approve the arbiter. The creator's approval is
   implied when they name an arbiter address in the draft or when the arbiter accepts their invite and
   the creator confirms; the other party approves the arbiter as part of accepting (if the arbiter is
   still open when they accept, they approve it once it is filled). The arbiter can only be changed while
   the lease is `PENDING_ACCEPTANCE`; changing it resets both approvals and the arbiter's acceptance and
   issues a new arbiter invite.
5. **All accepted** (tenant, landlord and arbiter set; arbiter accepted; both approvals present): the lease
   moves to `DRAFT` and the settlement account is created in the background exactly as now (treasury
   funds it → TrustSet → SignerListSet 2-of-3 → disable master). Then the tenant can lock the deposit.
6. **Cancel / decline.** Before locking, the creator can cancel and any invited person can decline. The
   lease moves to `CANCELLED` and drops off active lists. A settlement account that was already created
   stays empty.

Data:
- `Lease.status` gains `PENDING_ACCEPTANCE` (initial) and `CANCELLED`. `tenant`, `landlord`, `arbiter`
  become nullable while pending. New fields: `created_by` (FK Party), `tenant_approved_arbiter`,
  `landlord_approved_arbiter` (bool). `scenario` and `archived` are removed.
- New `Invite` model: `lease`, `role` (landlord | tenant | arbiter), `token` (unique), `accepted_by`
  (FK Party, null), `accepted_at`, `declined_at`, `created_at`.

## 3. Signing with Xaman

Every ledger action a user takes is a Xaman payload created by the server (`ledger/xaman.py`, plain
functions over `requests`; no SDK dependency). Each payload sets `force_network: "TESTNET"` (or `DEVNET`
when `XRPL_NETWORK=devnet`) and, where the signer is known, `signers: [address]`.

A `XamanRequest` model tracks each payload: `uuid`, `purpose` (signin | escrow_create | payout_sign |
evidence_anchor | trustset), `party` (null for sign-in), `lease` / `proposal` / `evidence` (nullable FKs),
`status` (open | signed | rejected | expired), `result` (JSON), `created_at`. The browser only ever sees our
id, the QR SVG and the deep link.

API (all JSON, CSRF on POSTs):
- `POST /api/xaman/signin/` → `{request_id, qr_svg, deeplink}`
- `GET  /api/xaman/requests/<id>/` → `{status}`; when signed, the server finishes the action (log in,
  register the transaction, store the signature) before answering `signed`.

| Action | Who | Payload |
|---|---|---|
| Sign in | anyone | `SignIn`, never submitted |
| Lock deposit | tenant | EscrowCreate, `submit: true`; on `signed` the server runs the existing `verify_escrow_create_tx` on the validated ledger transaction |
| Release early / claim damage | tenant or landlord | no payload: the bot account submits EscrowFinish with the fulfillment (`lifecycle.release`, as the demo path did) |
| Approve a split | any 2 of the 3 | one payload per prepared payout, `options.multisign: true`, `submit: false`; the returned signed blob is decoded, checked (signer = the party's address, tx fields unchanged) and stored in `SettlementProposal.signatures`; with 2 signers the server combines and submits (`execute_multisigned`) |
| Anchor evidence | uploader | Payment with the memo, `submit: true`; registered with the existing `register_wallet_anchor` |
| Trust line | user | TrustSet, `submit: true` |

Errors: rejected or expired payloads show "Cancelled in Xaman — try again". Multisign payloads for a
prepared tx that hit `tefMAX_LEDGER` are rebuilt (`_prepare_again`) and re-requested. Xaman API errors map to
friendly `LedgerError`s.

**First implementation step (spike):** on Testnet, create a 2-of-3 account, sign one Payment with two
Xaman multisign payloads, combine and submit. Confirm the shape of the returned blob. Everything else builds
on this.

## 4. Pages, test funds, bot, config, tests

Pages:
- `/` home: intro + *Sign in with Xaman*; when signed in, *Go to your deposits*.
- `/leases/` dashboard: agreements grouped *Waiting for acceptance*, *Active*, *In settlement*, *Closed*,
  each with the user's role; *New deposit agreement*; the test-funds card.
- `/leases/new/` draft form. `/invite/<token>/` terms + Accept / Decline.
- `/leases/<id>/` lease page: buttons come from `allowed_actions` as today; arbiters use this page too.
- `/verify/<id>/` unchanged. Header: display name, short address, *Sign out*.
- A signed-out visitor opening any page but `/`, `/verify/<id>/`, `/invite/<token>/` and the legal pages is
  sent to `/` to sign in.

Test funds card (labelled "Testnet only"):
1. *Activate account* (only if the account doesn't exist on the ledger): treasury sends 50 test XRP.
2. *Add EUR / USD trust line*: TrustSet via Xaman.
3. *Get 5,000 test EUR / USD*: issuer sends tokens.
Rate limit: each step once per account per 24 hours (`FaucetGrant` model: party, kind, created_at).

Bot: `run_bot` unchanged: returns deposits after `CancelAfter`, expires unlocked leases past their end
(now also pending ones → `CANCELLED`), and submits EscrowFinish for releases. The *Run bot now* button is
removed. README: run the bot next to the web server.

Config: `.env` gains `XAMAN_API_KEY`, `XAMAN_API_SECRET`; `DEMO_MODE` is removed. Without the keys the home
page says Xaman isn't configured and sign-in is disabled. CSP unchanged (no new browser hosts). New
dependency: `segno` (pure-Python QR → SVG).

Database: squash `core/migrations` into a fresh `0001_initial`; existing `db.sqlite3` is deleted and
`bootstrap` re-run (demo leases and personas are discarded; nothing of value on Testnet).

Tests (no network): remove demo tests; add tests with a faked Xaman client for sign-in and session
rotation, invite / acceptance / arbiter-approval rules, multisign blob verification and combination,
faucet rate limits, `allowed_actions` per role and status, and the sign-in redirect. The spike is checked
by hand on Testnet.

Docs: README and CLAUDE.md updated (setup with Xaman keys, flows, removed features, XRPL features table
without Credentials).

## Out of scope

Mainnet, email notifications, user profiles beyond display name, GemWallet / Crossmark, Batch payouts,
EXIF stripping, legal texts.
