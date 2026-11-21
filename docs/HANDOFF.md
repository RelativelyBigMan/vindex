# safeDeposit — handoff for a new AI session

Read this first, then `CLAUDE.md` (project rules). This file maps the code so you don't need to explore it file by file. State as of 2026-11-21, branch `real-accounts-testnet` (start your own branch from it).

## 1. What the app is

A Django web app that protects rental security deposits on the **XRP Ledger Testnet**. Real users sign in and sign every action with the **Xaman** phone wallet. The server never holds user keys.

- A lease has three parties: tenant, landlord, independent arbiter.
- The tenant locks the deposit in a **token escrow** (XLS-85, test EUR/USD tokens) whose destination is a per-lease **settlement account**. That account has a 2-of-3 SignerList (tenant, landlord, arbiter) and its own master key disabled.
- **Normal end:** after `CancelAfter` (lease end + grace) the platform bot submits EscrowCancel and the deposit goes back to the tenant automatically.
- **Early move-out / damage claim:** tenant or landlord asks; the **platform bot** submits EscrowFinish with the secret fulfillment (the secret never leaves the server). The money moves to the settlement account, and any 2 of the 3 parties approve a split by **Xaman multisignatures**. The server combines them and submits the payouts.
- Evidence photos: stored privately; only their SHA-256 goes on the ledger in a memo (signed by the uploader in Xaman).
- Hackathon project (IXH26); Testnet only, not production.

## 2. Run it (Windows)

```powershell
cd C:\path\to\decelsus          # manage.py lives HERE, not in the parent folder
py scripts\setup.py --bootstrap  # venv, packages, .env, vendor JS, migrate, platform wallets, preflight
.venv\Scripts\python manage.py runserver   # http://127.0.0.1:8000
.venv\Scripts\python manage.py run_bot     # second terminal: returns deposits, expires agreements
```

- `.env` needs `XAMAN_API_KEY` / `XAMAN_API_SECRET` from https://apps.xumm.dev (free developer app). Never commit `.env`. Without keys, sign-in is disabled (home page says so).
- The Xaman app must be on Testnet (Settings → Advanced → Node).
- `.claude/launch.json` uses `.venv/bin/python` (macOS/Linux path); on Windows the interpreter is `.venv\Scripts\python`.
- Old database from the demo version? Delete `db.sqlite3` and rerun `bootstrap` (migrations were reset).
- Browsers cache ES modules: hard-reload (Ctrl+Shift+R) after JS changes.

## 3. Checks

| Command | What | Network |
|---|---|---|
| `.venv\Scripts\python manage.py test` | 142 unit/API tests, all ledger + Xaman calls mocked | no |
| `manage.py check`, `manage.py makemigrations --check` | config / migration sanity | no |
| `manage.py preflight` | token escrow round trip with throw-away wallets | Testnet, ~1 min |
| `manage.py smoke_test --scenario all` | scenarios A/B/C on the ledger, throw-away wallets, no Xaman | Testnet, ~15 min |
| `python scripts\xaman_multisign_spike.py rA rB` | two real Xaman accounts multisign a payment (passed 2026-11-21) | Testnet + phone |
| `node --check static\js\...` | JS syntax | no |

## 4. File map

```
config/settings.py        env (XRPL_NETWORK, DJANGO_*, XAMAN_API_KEY/SECRET), CSP middleware, context processor
ledger/                   everything that talks to the XRPL or Xaman; no Django request objects
  client.py               network config, refuses mainnet hosts, JsonRpcClient factory, explorer URLs
  services.py             every ledger operation as a plain function (see §6)
  xaman.py                Xaman REST client: create_payload / get_payload / cancel_payload / qr_data_uri
  conditions.py           PREIMAGE-SHA-256 condition + fulfillment
  errors.py               LedgerError(engine_result, message) + friendly messages for engine results
core/
  models.py               PlatformWallet, Party, Currency, Lease, Invite, SettlementProposal, Evidence, LedgerEvent, XamanRequest, FaucetGrant
  agreements.py           pre-ledger flow: drafts, invites, accept/decline, arbiter approval, cancel
  lifecycle.py            guarded status transitions on the ledger, the bot pass, allowed_actions
  signing.py              one Xaman request per user action; refresh() finishes it
  faucet.py               Testnet faucet: activate account, test tokens, once per 24 h
  api.py                  JSON API (contract in §7), error mapping, serialisation
  views.py, urls.py       HTML page shells + routes
  middleware.py           strict CSP + security headers
  context.py              `escrow_config` (network, currencies, xaman_configured, signed_in) embedded in every page
  management/commands/    bootstrap, preflight, run_bot, smoke_test
  tests.py                LedgerMockedTestCase base + all core tests
templates/                base.html (Xaman dialog, name dialog), partials/, escrow/{home,dashboard,lease_create,lease_detail,invite,verify}.html, legal/
static/js/                vanilla ES modules, no build step
  api.js                  fetch wrapper: CSRF header, {error, code} → ApiError, toasts unless quiet
  ui.js                   el(), fill(), busy(), toast(), money(), fmtTime(), statusBadge(), renderIfChanged(), copyButton(), ROLE_LABEL
  session.js              config (from #escrow-config), loadMe(), currentMe(), onMe(), signOut(); event "me:changed"
  xaman.js                signWithXaman(start, {title}) and signSteps(path): QR dialog + polling
  actions.js              signIn, lockDeposit, releaseDeposit, proposeSplit, approveSplit, retryPayout, addTrustLine, uploadEvidence, anchorEvidence, verifyEvidence
  app.js                  global boot: header identity, startSignIn (safe ?next=), name prompt, copy buttons
  parts.js                partyCard, proposalCard, setupChecklist, splitBar, syncEvents
  ledger.js               browser-side ledger reads via self-hosted xrpl.js (verify page, evidence check), loadVendor
  splitform.js, dec.js    split form; decimal ↔ BigInt micro-units
  poll.js, fx.js, sound.js  polling helper, visual effects, click sounds
  pages/                  home, dashboard (agreement groups + faucet card), create, invite, lease (main screen), verify (public check)
static/css/global.css     "whitepaper" theme; section 10 = app components (.qr for the Xaman dialog)
static/vendor/            xrpl-5.3.0.min.js (committed)
scripts/                  setup.py, fetch_vendor.py, xaman_multisign_spike.py
docs/superpowers/         design spec and implementation plan for the Xaman migration (long; read only if needed)
```

## 5. Data model and statuses

- **Party** = an account: `address` (unique), `display_name`, `xaman_user_token` (push), `label` property. No role: the role comes from each lease.
- **Lease**: `tenant` / `landlord` / `arbiter` (nullable until accepted), `created_by`, `arbiter_accepted`, `tenant_approved_arbiter`, `landlord_approved_arbiter`, currency, `amount` (Decimal), title (off-chain), `end_at`, `grace_minutes`, `cancel_after` (= end + grace, recomputed on save), settlement address/seed (Testnet only), `condition_hex`, `fulfillment_hex` (SECRET), escrow owner/sequence, status, setup_step, release_reason, error.
- **Lease.status**: `PENDING_ACCEPTANCE → DRAFT (setup running) → SETTLEMENT_READY → ACTIVE → RETURNED` or `ACTIVE → IN_SETTLEMENT → SETTLED`; `FAILED` on errors; `CANCELLED` before the deposit is locked.
- **Invite**: lease, role, url-safe token (single use), accepted_by / accepted_at / declined_at, revoked.
- **SettlementProposal**: amounts, reason, proposed_by, status (OPEN, EXECUTED, SUPERSEDED, EXPIRED), `prepared_txs` (unsigned payouts with fixed Sequence/Fee/LastLedgerSequence), `signatures` = `{party_id: [signed tx JSON or null per prepared tx]}`, `executed_hashes`.
- **XamanRequest**: our UUID (`id`, the only id the browser sees), `xaman_uuid`, `purpose` (signin, escrow_create, payout_sign, evidence_anchor, trustset), links to party/lease/proposal/evidence, `payload_index`, `currency_code`, `deeplink`, `pushed`, `status` (open → signed → finishing → done, or rejected / expired / failed), `signed_at`, `error`, `result`.
- **FaucetGrant**: party, kind (`activate`, `tokens:EUR`, `tokens:USD`), created_at.
- **LedgerEvent**: one timeline line per on-ledger action, with tx hash. Every ledger action must write one.

## 6. Key flows (where the logic lives)

**Sign-in** — `api.signin` → `signing.start_signin()` (Xaman `SignIn` payload, stored request id in the session) → browser polls `GET /api/xaman/requests/<id>/` → `signing.refresh()` → `_finish_signin` creates/gets the Party → API rotates the session key and sets `party_id`. Only the browser session that started a sign-in can complete it. First sign-in → name dialog (`POST /api/me/`). Throttled to 10 per minute.

**Agreement** — `agreements.create_draft(creator, role, …)` creates the lease in `PENDING_ACCEPTANCE` plus invites for the other side and the arbiter. Optional `arbiter_address` names the arbiter (counts as the creator's approval). `accept_invite(token, party, expected_arbiter)` fills the role; a side accepting with an arbiter already named approves it (guarded by `expected_arbiter`, else 409 `terms_changed`). `change_arbiter` resets approvals and reissues the arbiter invite. `_maybe_start` → `lifecycle.start_setup` when all three accepted and both sides approved.

**Settlement account setup** — `lifecycle.start_setup` → background thread `_run_setup` → `services.create_settlement_account` (fund from treasury → TrustSet → SignerListSet quorum 2 → disable master), one LedgerEvent per step → `SETTLEMENT_READY`.

**Lock** — `POST /api/leases/<id>/escrow/` → `signing.start_escrow` (tenant only; returns the existing open request instead of creating a second one) → Xaman signs and submits the EscrowCreate → `_finish_escrow` waits for validation → `lifecycle.register_wallet_escrow` verifies every field on the ledger → `ACTIVE`.

**Release** — `POST /api/leases/<id>/release/ {reason}` → `lifecycle.release` → bot wallet submits EscrowFinish → `IN_SETTLEMENT`. Early move-out auto-proposes 100% to the tenant.

**Approve a split** — `POST /api/leases/<id>/proposals/` → `lifecycle.propose` (prepares payouts, no signatures). Each party: `POST /api/proposals/<id>/sign/` repeatedly (JS `signSteps`), one Xaman multisign request per prepared payout → `_finish_payout` → `lifecycle.add_signature` → `services.verify_multisig_blob` (right signer, own key, valid signature, identical tx body). Two complete signers → `_execute` submits the payouts (`services.execute_multisigned`). Expired payouts (`tefMAX_LEDGER`) are re-prepared and need fresh approvals; a half-paid split never pays twice; `POST /api/proposals/<id>/retry/` resubmits.

**Bot** (`run_bot` → `lifecycle.bot_pass` every 10 s) — cancels pending agreements past their return date, fails setups stuck in DRAFT for 10+ minutes, closes or adopts unlocked leases past their return date, returns escrows (EscrowCancel) after CancelAfter + 10 s, reconciles escrows that vanished, returns escrows locked after a cancellation, and cancels duplicate escrows of a lease.

**Faucet** — `faucet.activate` (50 test XRP from treasury, only for accounts not on the ledger), TrustSet via Xaman (`signing.start_trustline`), `faucet.grant_tokens` (5,000 per currency, needs the trust line). Each once per 24 h per account.

## 7. API contract (JSON; CSRF header on every POST)

Errors are `{error, code}`: 400 bad input / ledger refusal, 401 `signed_out`, 404 not a party / not found, 409 not allowed now, 500 server or Xaman/ledger unreachable.

```
GET/POST /api/me/                         account or {"signed_in": false}; POST {display_name}
POST /api/auth/signin/ | /api/auth/signout/
GET  /api/xaman/requests/<id>/            {request_id, purpose, status, error, qr (data: URI), deeplink, pushed}
POST /api/xaman/requests/<id>/cancel/     keeps a request the user already signed
GET  /api/faucet/   POST /api/faucet/activate/ | trustline/ {currency} | tokens/ {currency}
GET  /api/invites/<token>/                terms (reduced once used); POST .../accept/ {expected_arbiter} | .../decline/
GET/POST /api/leases/                     list (?status=) | create {role, title, currency, amount, end_date | end_in_minutes, grace_minutes, arbiter_address}
GET  /api/leases/<id>/                    full state: parties, open_roles, agreement, invites, settlement (live), escrow, proposals, events, evidence, actions
GET  /api/leases/<id>/public/ | settlement/
POST /api/leases/<id>/cancel/ | arbiter/ {address} | approve-arbiter/ | escrow/ | release/ {reason} | proposals/ | evidence/ (multipart)
POST /api/proposals/<id>/sign/ | retry/
GET  /api/evidence/<id>/file/   POST /api/evidence/<id>/anchor/
```

The UI shows buttons only from `actions` (from `lifecycle.allowed_actions`); the API enforces the same list. Vocabulary: `change_arbiter`, `approve_arbiter`, `cancel`, `lock_deposit`, `release_early`, `claim_damage`, `propose`, `sign:<proposal_id>`, `retry:<proposal_id>`, `upload_evidence`, `share_verify`.

## 8. Rules that must not break

- Testnet/Devnet only; `ledger/client.py` refuses mainnet hosts. Every Xaman payload sets `force_network`.
- Never store, log or send user keys. Only platform wallets (treasury, bot, issuer_eur, issuer_usd) and Testnet settlement seeds are in the DB.
- The fulfillment never leaves the server (not in JSON, pages, logs, or Xaman payloads).
- The browser never sees the Xaman uuid; QR codes are rendered server-side (segno → `data:` URI).
- Strict CSP: no CDNs, no new browser hosts, no inline scripts or styles, never `innerHTML`; write text with `ui.el` / `textContent`; never pass `null` to `replaceChildren` (use `ui.fill`). A new external host needs an entry in `core/middleware.py`.
- Amounts: `Decimal` on the server, BigInt micro-units in the browser. Deadlines use the ledger's close time, never the local clock.
- Two transactions from one account must not be built at once: send through `services.submit()` (per-address lock).
- Memos are public and permanent: ids and hashes only, never names.
- Tests never touch the network: mock `ledger.services` functions and `ledger.xaman` (see `LedgerMockedTestCase` and `SigningTests`/`ApiTests` setups in `core/tests.py`).
- Commit messages end with a `Co-Authored-By:` line for the AI model that helped.

## 9. Known gaps and polish ideas

Not yet done:
- **No full browser click-through yet** with three Xaman accounts (tenant, landlord, arbiter). The backend flows are tested with mocks; the multisign spike and smoke tests passed on Testnet.
- No email notifications: invite links are shared by hand.

Known minor issues (safe to polish):
- Faucet uses one in-process lock held across network calls (fine for one process).
- SQLite: `select_for_update` is a no-op; transitions rely on conditional updates. `"OPTIONS": {"transaction_mode": "IMMEDIATE"}` would harden it.
- The cancelled-lease safety net in the bot re-queries every pass (unbounded but small).
- The duplicate-escrow cancel in the bot runs only on the pass that returns the lease (a failed cancel isn't retried).
- `proposal_sign` answers `done` on a closed proposal if the party had already signed it.
- Busy label stays "Releasing and preparing the split…" while the Xaman approval is open (damage claim).
- `XamanCancelled` is exported from `xaman.js` but nothing distinguishes it.
- No tests yet for the anchor and trust-line finishers or the own-key-mismatch branch of `verify_multisig_blob`.
- `templates/legal/*` are placeholders.

UI polish candidates: mobile layout of the Xaman dialog and lease page, empty states, clearer progress during multi-payout approvals ("signature 1 of 2"), a "copy invite link" toast, showing which Xaman account is expected in the dialog.
