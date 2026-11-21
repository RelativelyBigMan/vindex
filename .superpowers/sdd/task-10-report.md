# Task 10 report

Commit: 60d15e5 (not pushed).

## Implemented
Steps 1-9 of the brief: demo/stage/arbiter pages deleted; home CTA + home.js; dashboard with faucet card; agreement form + create.js; invite.html/invite.js; lease page (agreement card, Xaman actions, faucet-based lock step, cancelled outcome, retry payout); verify.js credential check removed.

## Checks
- node --check on every file in static/js and static/js/pages: clean.
- Step 10 grep over static, templates, core (py/js/html): no matches except core/tests.py (asserts the old URLs are gone) and a help string in run_bot.py mentioning /api/demo/run-bot/ (stale text, harmless).
- manage.py test: OK, 125 tests.
- Smoke: / 200, /leases/ 302, /legal/terms/ 200 (also /leases/new/ 302, /invite/x/ 404). Server stopped.

## Deviations
- invite.js: added an early branch for a used invite with a lease object lacking terms (no `amount`): hides the terms card and shows "This invite has already been used." (the brief's code would have crashed).
- home.html: also removed the "verified by a credential" sentence and the demo/"View my leases" links in the bottom CTA (reworded to the real flow).
- verify.js: also removed the arbiter_credentials ledger request, the LSF_ACCEPTED constant and the hexToText import. verify.html had no credential wording.
- Brief line ranges included code-fence lines; stripped them.

## Concerns
- No browser click-through done (no Xaman key); only syntax and HTTP smoke.
- verify.js still queries account_lines for tenant/landlord addresses that may be null for unaccepted agreements (wrapped in optional()).
- run_bot.py help string still mentions the removed demo endpoint.

## Task 10 fix report
- lease.js: deadline only updated when seconds_to_cancel_after is a number, else null; open-role cards use party--ROLE class.
- actions.js: anchorEvidence({quiet}) option; only uploadEvidence passes quiet.
- invite.js: sign-in try/catch with toast; decline shows closed message.
- Copy: footer, dashboard title, lease_detail back-links; home Get started gated on signed_in with data-home-signin button (home.js now uses querySelectorAll).
- Checks: node --check OK on 4 JS files; 125 tests pass.
