# safeDeposit

safeDeposit (repo: decelsus) holds rental deposits in escrow. The tenant's deposit (a stablecoin on the XRP Ledger testnet) is held for the length of the lease, then released by a **2-of-3 vote** between tenant, owner and an independent arbiter.

The Django backend is being added in patches (see `CLAUDE.md` for the spec and progress). Patch 1 is in: the project skeleton, the XRPL Testnet wallets and test tokens, and a preflight check. The pages below aren't wired to URLs yet.

## Setup

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env            # then set DJANGO_SECRET_KEY to a long random string
.venv/bin/python manage.py migrate
.venv/bin/python manage.py bootstrap_demo   # wallets, personas, test EUR/USD on Testnet (safe to rerun)
.venv/bin/python manage.py preflight        # proves token escrow works on the network
.venv/bin/python manage.py test ledger
```

If preflight says token escrow is disabled, set `XRPL_NETWORK=devnet` in `.env` and rerun `bootstrap_demo`.
Demo keys live in `db.sqlite3` (git-ignored). They are Testnet keys with no value; the app refuses to start against mainnet.

## Layout

```
templates/
  base.html                  global page template (blocks: title, meta_description, body_class, extra_css, content, extra_js)
  partials/                  header, footer, messages, status badge, official XRP symbol
  escrow/                    home, dashboard, lease_create, lease_detail
  legal/                     terms, privacy (placeholders, need legal review)
static/
  css/global.css             "whitepaper" theme: XRP brand palette + LaTeX typesetting, light/dark, animations
  fonts/                     SD Modern (subset of Latin Modern, GUST Font License; see README-fonts.txt)
  js/app.js                  global behaviour (wallet UI, confirms, copy, amount validation)
  js/fx.js                   decorative motion (scroll reveals, count-ups, ripples); off for reduced motion
  js/sound.js                soft synthesised click sounds with a mute toggle (no audio files)
  js/wallet.js               wallet adapter interface (no wallet wired in yet)
```

New pages: `{% extends "base.html" %}` and fill `{% block content %}`.

Whitepaper building blocks for templates: `.sec` + `.sec__title` (auto-numbered sections),
`.defn` / `.defn--theorem` (numbered definitions), `.equation` + `.math` (numbered display maths;
add `.equation--type` to type it in), `.stats`, `.titleblock`, `.abstract`.

## Branding rules (XRP Brand Toolkit 2026)

- The XRP "X" symbol (`partials/_xrp_symbol.html`) is the official path, unmodified. Black or white only,
  at least 20px tall, with clear space of 0.25 x its height. Use it only for "Built on the XRP Ledger",
  never inside the safeDeposit logo.
- Palette: white / Grey 5 `#F0F3F7` backgrounds, Black `#141414` text, Green `#21E46B` accents
  (green fills take black text; green *text* uses Dark Green `#078139` for contrast).
- The footer states the project isn't affiliated with Ripple or the XRP Ledger Foundation. Keep that line.

## What Django needs to provide later

**Settings**

```python
TEMPLATES[0]["DIRS"] = [BASE_DIR / "templates"]
STATICFILES_DIRS = [BASE_DIR / "static"]
# Hashed filenames so browsers never keep a stale global.css/app.js after a deploy:
STORAGES = {"staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.ManifestStaticFilesStorage"}}
```

**URL names** used by the templates:

| name | args | method |
|---|---|---|
| `home` | | GET |
| `escrow:dashboard` | | GET |
| `escrow:lease_create` | | GET, POST |
| `escrow:lease_detail` | `lease.id` | GET |
| `escrow:lease_deposit` | `lease.id` | POST |
| `escrow:lease_vote` | `lease.id` | POST (`decision` = `tenant` \| `owner`) |
| `legal:terms`, `legal:privacy` | | GET |

**Context**
- `escrow_config` (best from a context processor):
  `{"network": {"id", "name", "is_testnet"}, "currency": {"code", "issuer", "decimals"}}`
- `leases`: list for the dashboard.
- `lease`: for the detail page, with `parties` (role, role_display, address, vote), `votes_for_tenant`, `votes_for_owner`, `votes_required`, `status` (`awaiting_deposit` | `active` | `voting` | `released_tenant` | `released_owner`), and optionally `transactions`.
- `form`: the create form's field names are `property_name`, `property_address`, `start_date`, `end_date`, `deposit_amount`, `tenant_address`, `owner_address`, `arbiter_address`, `accept_terms`.

## Security notes for the backend

- Keep `CsrfViewMiddleware` and `XFrameOptionsMiddleware` on. Every form already includes `{% csrf_token %}`.
- The frontend has no inline scripts or styles, so a strict CSP works: `default-src 'self'; frame-ancestors 'none'` (e.g. with `django-csp`).
- Re-validate everything server-side: use `Decimal` for amounts, check address format, require all three addresses to be different, and require `end_date > start_date`.
- Never accept seeds, secrets or private keys. Signing happens only in the user's wallet.
- Keep personal lease data (property address) off-chain. Store only a hash on-chain.
- Never commit `.wallets.json`. It's already in `.gitignore`.
