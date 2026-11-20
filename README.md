# decelsus

Escrow for rental deposits. The tenant's deposit (a stablecoin on the XRP Ledger testnet) is held for the length of the lease, then released by a **2-of-3 vote** between tenant, owner and an independent arbiter.

This repo is **frontend only** so far. It's laid out the way Django expects, so a Django project can use it without moving files.

## Layout

```
templates/
  base.html                  global page template (blocks: title, meta_description, body_class, extra_css, content, extra_js)
  partials/                  header, footer, messages, status badge
  escrow/                    home, dashboard, lease_create, lease_detail
  legal/                     terms, privacy (placeholders, need legal review)
static/
  css/global.css             global stylesheet: design tokens, light/dark, components
  js/app.js                  global behaviour (wallet UI, confirms, copy, amount validation)
  js/wallet.js               wallet adapter interface (no wallet wired in yet)
```

New pages: `{% extends "base.html" %}` and fill `{% block content %}`.

## What Django needs to provide later

**Settings**

```python
TEMPLATES[0]["DIRS"] = [BASE_DIR / "templates"]
STATICFILES_DIRS = [BASE_DIR / "static"]
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
