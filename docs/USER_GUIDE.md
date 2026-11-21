# safeDeposit user guide

How to use the website: what every page does and everything you can do on it. For installation see the
[README](../README.md); for how the code fits together see `CLAUDE.md`.

Everything here runs on the XRP Ledger **Testnet** with test tokens. Nothing has real value.

## Contents

1. [The idea in one minute](#1-the-idea-in-one-minute)
2. [Who you are on the site](#2-who-you-are-on-the-site)
3. [What is on every page](#3-what-is-on-every-page)
4. [The pages](#4-the-pages)
5. [The life of a deposit](#5-the-life-of-a-deposit)
6. [Walkthroughs](#6-walkthroughs)
7. [Using your own wallet (GemWallet)](#7-using-your-own-wallet-gemwallet)
8. [Evidence photos](#8-evidence-photos)
9. [Messages, waiting times and errors](#9-messages-waiting-times-and-errors)
10. [Privacy: what is public and what is not](#10-privacy-what-is-public-and-what-is-not)
11. [Troubleshooting](#11-troubleshooting)
12. [Words used](#12-words-used)

---

## 1. The idea in one minute

A rental deposit normally sits in one side's bank account. Here it is locked on the XRP Ledger instead.

- Three parties are named on every deposit: the **tenant**, the **landlord** and an independent **arbiter**.
- The tenant locks the deposit in an **escrow**. While it is locked, nobody can spend it.
- If nothing goes wrong, the ledger gives it back to the tenant after the lease end plus a grace period. Nobody has to
  approve that.
- If the tenant moves out early, or the landlord claims damage, the deposit moves to a **settlement account**. Money
  leaves that account only when **two of the three** parties approve the same split.

safeDeposit prepares the transactions and shows what is happening. It cannot take or redirect a deposit.

## 2. Who you are on the site

There are no passwords. You act as one party at a time, in one of two ways.

### Demo personas (the default)

Three ready-made people with test wallets:

| Persona | Role |
|---|---|
| Giulia Rossi | Tenant |
| Marco Bianchi | Landlord |
| Bari Housing Mediation | Arbiter (holds a verified-arbiter credential) |

You start as Giulia. Switch at any time with the three buttons in the **demo bar** at the bottom of every page, or on
the **Demo personas** page. Switching is instant and the page you are on refreshes for the new person. For these
personas the server signs transactions with their Testnet keys, so you never see a wallet popup.

### Your own wallet

Click **Connect wallet** in the header to act as your own GemWallet account instead. See
[section 7](#7-using-your-own-wallet-gemwallet).

### What each role can do

| Action | Tenant | Landlord | Arbiter |
|---|:-:|:-:|:-:|
| Start a new deposit | yes | no | no |
| Lock the deposit | yes | no | no |
| Move out early (release the escrow) | yes | no | no |
| Claim damage (release the escrow) | no | yes | no |
| Propose a split | yes | yes | yes |
| Approve a split | yes | yes | yes |
| Upload evidence photos | yes | yes | no |
| View evidence photos | yes | yes | yes |
| Inspect the settlement account, share the verification link | yes | yes | yes |

The site only shows you the buttons you are allowed to use right now, and the server enforces the same rules.

## 3. What is on every page

**Testnet banner** (top): a reminder that only test funds are involved.

**Header**

- **safeDeposit** logo: back to the home page.
- **My leases**, **New deposit**, **Arbiter**: the three main pages.
- **Speaker button**: turns the soft click sounds on or off. The choice is remembered in this browser only.
- **Persona badge**: shows who you are acting as and in which role, in that role's colour (purple tenant, blue
  landlord, red arbiter). Clicking it opens the Demo personas page.
- **Connect wallet**: links a GemWallet account. Once linked, the header shows the wallet's address and a
  **Disconnect** button instead.

**Demo bar** (bottom, only in demo mode)

- **Giulia / Marco / Bari**: act as that persona. The active one is highlighted green.
- **Run bot now**: runs the return bot once. Use it if the background bot is not running and a deposit is past its
  return date. It tells you how many deposits it returned, or that none is due yet.
- **Testnet**: the network in use.
- **Personas** and **Stage**: shortcuts to those two pages.

**Notices**: short messages appear at the bottom right and disappear after a few seconds. Click the × to dismiss one.

**Footer**: what safeDeposit is and is not, a statement that it never asks for a seed or private key, and links to
Terms and Privacy.

## 4. The pages

### 4.1 Home (`/`)

An introduction written like a short paper: the tagline, how it works in three steps (Lock, Live, Return), a table of
the three outcomes, and the settlement rule. **Try the demo** opens the Demo personas page. **Protect a deposit** opens
the new-deposit form. **View my leases** opens your list.

### 4.2 Demo personas (`/demo/`)

One card per persona, showing:

- role, name and shortened wallet address;
- **Copy address** and an **Explorer** link to the account on the public ledger explorer;
- live balances in XRP and the two test tokens (EUR and USD);
- for the arbiter, a **Verified arbiter** badge read live from the ledger.

Buttons:

- **Act as …** on the other two cards switches persona.
- **Top up test funds** on your own card sends 5,000 of each test token, and some XRP if the wallet is running low. It
  takes about 10 seconds and can be used once every 30 seconds.

Below the cards are three suggestions to try, a button to start a deposit and a link to the presenter view.

### 4.3 New deposit (`/leases/new/`)

Only a tenant can start a deposit. If you are acting as someone else, a notice offers **Act as the tenant**.

**The form**

| Field | What to enter |
|---|---|
| Property name | Optional label, up to 120 characters. Kept private; never written to the ledger. |
| Deposit | The amount, for example `1000`. Up to 6 decimal places. |
| Currency | EUR (test) or USD (test). |
| Lease end | **In a few minutes (demo)** with a number of minutes, or **On a date**. |
| Grace period (minutes) | Time after the lease end in which a damage claim is still possible. After it, the deposit returns automatically. |
| Landlord | Pick the demo landlord, or choose **Paste an XRP Ledger address…** and enter any existing Testnet address. |
| Arbiter | Pick a listed arbiter (only arbiters with a verified credential are listed), or paste an address. |
| Terms checkbox | Required. |

Things to know:

- The lease end plus the grace period must be at least 90 seconds away, so there is time to lock the deposit.
- Tenant, landlord and arbiter must be three different accounts.
- A pasted landlord or arbiter address counts as a signer on the ledger and can receive money, but cannot approve
  splits from this site. The other two parties decide.
- A pasted arbiter address without a verified-arbiter credential shows a warning. You can still use it.
- Never enter a seed phrase, secret or private key anywhere. Only public addresses (starting with `r`).

**After you click Create settlement account**

The form is replaced by a live checklist. Each line is a real ledger transaction and gets a **View transaction** link
when it is validated:

1. Settlement account created
2. Trust line to the deposit token
3. Tenant, landlord and arbiter set as signers (2 of 3)
4. Account's own key disabled

This takes about 20 seconds. Then the **Sign & lock deposit** button appears. Clicking it locks the deposit (about 4
seconds) and takes you to the deposit's page.

If you reload during setup, the page picks up where it was. If setup fails, nothing was locked; **Try again** brings
the form back with your details.

### 4.4 My leases (`/leases/`)

A table of every deposit where the person you are acting as is the tenant, landlord or arbiter:

- **Property**: the name (or a short id) and "tenant → landlord". Click it to open the deposit.
- **Your role** in that deposit.
- **Automatic return**: the date and time after which it goes back to the tenant.
- **Deposit**: amount and currency.
- **Status**: see [section 5](#5-the-life-of-a-deposit).

The list refreshes every few seconds and changes when you switch persona. The **New deposit** button is shown to
tenants only.

### 4.5 Deposit page (`/leases/<id>/`)

The main screen. It refreshes itself every 3 seconds; you never need to reload. From top to bottom:

**Heading**: property name, amount, lease end, your role, and the status badge.

**Result banner** (only at the end)

- *Deposit returned to (tenant)*: the automatic return happened.
- *Deposit settled*: who approved, a bar showing the split, and links to the payout transactions.
- *This deposit didn't start*: setup failed or the deposit was never locked in time; with a **Start again** button.

**Party cards**: tenant, landlord and arbiter, each with name, address, **Copy address** and **Explorer**. Your own
card is marked **You**. The arbiter's card shows **Verified arbiter** or **Unverified**.

**Setting up on the ledger** (only before the deposit is locked): the same checklist as on the new-deposit page. The
tenant sees **Sign & lock deposit**; the others see that they are waiting for the tenant.

**Deposit escrow card**

- the amount and a badge: *Not locked yet*, *Locked on the ledger*, *Released to settlement* or *Returned to tenant*;
- while locked, a large **countdown** to the automatic return. When it reaches zero it says the return date has
  passed; the bot returns the deposit about 10 seconds later, and a **Run bot now** button is offered;
- details: destination (the settlement account), the automatic return date, the escrow's owner and number with an
  explorer link, and the token and its issuer.

**Settlement account card**, marked *read live from the ledger*

- the three signers with their roles;
- *Required: 2 of 3 signatures*;
- *Own key: Disabled*, meaning nobody can spend from the account alone;
- how much it currently holds;
- **Inspect ledger data** opens a window with what the ledger itself reports (key disabled, quorum, signers, trust
  line, balance) and the raw data;
- an explorer link.

**What you can do**: only the actions open to you right now.

| Button | Who and when | What happens |
|---|---|---|
| **I'm moving out early** | Tenant, while locked and before the return date | After a confirmation, the deposit moves to the settlement account and a proposal of 100% back to the tenant is created with the tenant's approval. |
| **Claim damage** | Landlord, while locked and before the return date | Opens the split form. Submitting moves the deposit to the settlement account and proposes your split with your approval. |
| **Propose a split** / **Propose a different split** | Any party, in settlement | Opens the split form. A new proposal replaces the open one and counts as your approval. |
| **Share verification link** | Any party, once locked | Copies the public check link. **Open the public check** opens it. |

**The split form**: two amounts (to the tenant, to the landlord) that must add up to exactly the deposit. Typing one
fills in the other; the slider sets the landlord's share in percent; a bar previews the split. A reason is required
and is shown to the other parties (it is not written to the ledger). The submit button stays disabled until the
amounts add up.

**Proposed split**: one card per proposal with the amounts, a split bar, the reason, who proposed it, **Approvals: 1
of 2** with names, and who is still needed. If you may approve, you see **Approve this split (demo key)**. The second
approval pays the deposit out immediately. If a payout was interrupted, the button reads **Retry the payout**.

**Evidence**: see [section 8](#8-evidence-photos).

**Ledger timeline**: every step, newest first, with the time, a plain description and a **View on explorer** link.
New entries slide in as they happen.

If you open a deposit while acting as someone who is not one of its three parties, the page says so and asks you to
switch persona.

### 4.6 Arbiter desk (`/arbiter/`)

For the arbiter: every deposit currently in settlement, each with

- the property name (a link to the deposit), amount, parties and whether it is a damage claim or an early move-out;
- the open proposal with **Approve this split**;
- the evidence photos for that deposit, with their fingerprints and ledger links. Clicking a photo opens the deposit,
  where it can be verified.

If you are not acting as an arbiter, a notice offers **Act as the arbiter**. An empty desk says no deposits are
waiting for a decision.

### 4.7 Public check (`/verify/<id>/`)

A page anyone can open, with no login, to check a deposit **without trusting safeDeposit**. Your browser asks a public
ledger server directly; safeDeposit only tells it which addresses to look at.

It shows a checklist with a tick, a cross or a note for each point:

- the amount is locked in an escrow;
- the escrow belongs to the tenant, so an automatic return goes to the tenant;
- an early release can only go to the settlement account;
- it is the agreed token from the agreed issuer;
- releasing needs a secret, and there is no time-based release;
- the date after which it returns automatically;
- the settlement account's own key is disabled;
- spending from it needs 2 of 3 signatures;
- the three signers are exactly the tenant, landlord and arbiter;
- the arbiter holds an accepted verified-arbiter credential;
- how many evidence fingerprints are anchored.

Below are the escrow and settlement account details with explorer links, the raw ledger data (click to expand), and
**Check again**. If the escrow no longer exists, the page explains whether the money is in the settlement account or
has already been returned or paid out.

Typical use: a landlord checks the deposit before handing over the keys.

### 4.8 Presenter view (`/demo/stage/`)

A projector-friendly screen for live demos, showing three staged deposits side by side. They are created with the
`demo_scenarios` command (see the README).

| Column | What it shows | Button |
|---|---|---|
| **A, Normal end** | A countdown to the automatic return, then "returned by the ledger" | None needed. If the return is 20 seconds overdue, **Run bot now** appears. |
| **B, Early move-out** | A locked deposit | **Tenant moves out**, then **Landlord approves the refund** |
| **C, Damage dispute** | An 850 / 150 split already approved by the landlord | **Arbiter approves** |

Each column also shows the status, approval progress, the latest timeline entry, and links to the deposit and its
public check. The buttons act as the right persona for you.

### 4.9 Terms and Privacy (`/legal/terms/`, `/legal/privacy/`)

Placeholder texts listing what the final legal pages must cover. They need review by a lawyer before any real use.

### 4.10 Error pages

A missing page shows a 404 page with a link home. A server error shows a page stating that the fault is on the site's
side and that a locked deposit stays where it is.

### 4.11 Admin (`/admin/`)

Django's admin, for the people running the site (needs a superuser account). It lists parties, leases, proposals,
evidence and timeline entries. Keys and the escrow secret are never shown there.

## 5. The life of a deposit

| Status badge | Meaning | What can happen next |
|---|---|---|
| **Being set up** | The settlement account is being created (about 20 s). | Becomes *Ready to lock*, or *Failed*. |
| **Ready to lock** | Waiting for the tenant to lock the deposit. | Tenant locks it. If the return date passes first, it becomes *Failed*. |
| **Locked** | The deposit is in escrow. Nobody can spend it. | Automatic return, early move-out, or damage claim. |
| **Returned to tenant** | The ledger gave it back after the return date. | Final. |
| **In settlement** | The deposit is in the 2-of-3 account. | Proposals and approvals, until two parties approve one split. |
| **Settled** | Paid out as approved. | Final. |
| **Failed** | Setup failed or it was never locked in time. No money was locked. | Start again. |

Two rules about time:

- Early move-out and damage claims are possible only **before** the automatic return date. After it, the only thing
  that can happen to a locked deposit is its return to the tenant.
- The return is submitted by a bot about 10 seconds after the date. Anyone could submit it; the ledger checks the
  date, not the sender.

## 6. Walkthroughs

### A. Normal end

1. Act as **Giulia**. Open **New deposit**.
2. Enter 1000, ends in 2 minutes, grace 1. Tick the terms. **Create settlement account**.
3. Wait for the four ticks. **Sign & lock deposit**.
4. Watch the countdown. Shortly after it reaches zero, a notice says the deposit was returned and the page shows the
   green result banner.

### B. Early move-out

1. As **Giulia**, create and lock a deposit that ends in 20 minutes.
2. Click **I'm moving out early** and confirm. A proposal of 100% to the tenant appears with 1 of 2 approvals.
3. Switch to **Marco** in the demo bar. Click **Approve this split**.
4. Status becomes **Settled** and the payout link appears.

### C. Damage claim

1. As **Giulia**, create and lock a deposit.
2. Switch to **Marco**. Click **Claim damage**, set 150 to the landlord, write a reason, submit.
3. Switch to **Bari**. Open **Arbiter** and click **Approve this split**.
4. Status becomes **Settled**: 850 to the tenant, 150 to the landlord.

Giulia could instead **Propose a different split**; then Marco or Bari would need to approve hers.

## 7. Using your own wallet (GemWallet)

A tenant or landlord can sign with the GemWallet browser extension instead of a demo persona.

**Connecting**

1. Install GemWallet and switch it to **Testnet**.
2. Click **Connect wallet**. Approve sharing your address.
3. The first time, choose whether this wallet is a tenant, landlord or arbiter. This cannot be changed later for that
   address.
4. Approve one **message signature**. It proves the wallet is yours. No transaction is made and no key leaves the
   wallet.

The header then shows your address. **Disconnect** returns you to the demo persona.

**Locking a deposit with your wallet**

On the deposit page the site first checks your wallet:

- no trust line for the token → **Add EUR trust line in wallet** (or USD; one wallet popup);
- not enough tokens → **Top up test tokens**;
- then **Sign & lock deposit** opens a wallet popup with the escrow transaction.

After you approve, the site waits a few seconds for the ledger and accepts the deposit only after checking the
transaction there.

**Releasing with your wallet**: **I'm moving out early** and **Claim damage** work the same way, with one popup.

**Limits**

- GemWallet cannot produce the kind of signature a 2-of-3 payout needs. A wallet user can **propose** a split, but
  cannot approve one; the other two parties approve it.
- If the wallet is on another network or another account than the one you connected, the site tells you and does
  nothing.

## 8. Evidence photos

Tenant and landlord can attach photos to a deposit from the moment the settlement account is ready until it is
settled.

**Uploading**: under **Evidence** on the deposit page, choose what the photo shows (move-in condition, move-out
condition, damage), pick a file and click **Upload and anchor**. JPEG, PNG or WebP, up to 5 MB. It takes about 5
seconds.

**What happens**: the photo is stored privately. Its fingerprint (a SHA-256 hash) is written to the ledger in a public
transaction. The fingerprint cannot be turned back into the photo, but it proves later that the photo has not been
changed or swapped.

**Each photo card shows**: the picture, its kind, who uploaded it and when, the start of its fingerprint, and
**Anchored on XRPL** (a link to the transaction). If anchoring failed, the uploader sees **Anchor on the ledger** to
retry.

**Checking a photo**

- **Verify**: your browser computes the photo's fingerprint itself and compares it with the one in the ledger
  transaction. *Match* means it is the photo that was anchored.
- **Verify an altered copy**: a demonstration. It changes one byte of the photo in memory and checks again, which
  gives *Mismatch*. The stored photo is not touched.

The arbiter sees every deposit's photos on the Arbiter desk.

## 9. Messages, waiting times and errors

**Waiting times.** Every action that touches the ledger takes a few seconds, because the ledger confirms it. A button
shows a spinner and text such as *Submitting to the XRPL (about 4 s)* and cannot be clicked twice.

| Action | Typical time |
|---|---|
| Setting up a settlement account | about 20 s |
| Locking, releasing, approving | about 4 to 10 s |
| Uploading and anchoring a photo | about 5 s |
| Top up test funds | about 10 s |

**Notices** tell you when the status changes, for example *Deposit returned to Giulia Rossi automatically*.

**Common messages**

| Message | What to do |
|---|---|
| *The XRP Ledger test network can't be reached right now. Nothing was changed; try again in a moment.* | Wait and click again. |
| *Another action on this deposit is still being processed.* | Wait a few seconds. |
| *The two amounts must add up to exactly …* | Fix the split so it equals the amount held. |
| *(Tenant) holds … but the deposit is … Top up test funds first.* | Use **Top up test funds** on the Demo personas page. |
| *It's too late to release this deposit…* | The return date has passed; it goes back to the tenant automatically. |
| *You've already approved this split.* | Another party must approve. |
| *This persona was topped up a moment ago.* | Wait 30 seconds. |
| *Connection lost. Showing the last known state; retrying…* | The site cannot reach its server; it keeps retrying by itself. |

If an approval is interrupted halfway (for example by a network failure), nothing is lost: the button becomes **Retry
the payout** and finishes the job without paying anything twice.

## 10. Privacy: what is public and what is not

| Public on the ledger, permanently | Private, on the safeDeposit server |
|---|---|
| Wallet addresses | Property name |
| Amounts and dates of the escrow and payouts | Reasons given for a split |
| The 2-of-3 signer list | Evidence photos (visible to the three parties only) |
| Photo fingerprints, with the deposit's id and the kind of photo | Persona names |
| The arbiter's credential | |

The site loads no third-party fonts, scripts or trackers, and uses only the cookies it needs to work. It never asks
for a seed phrase or private key.

## 11. Troubleshooting

- **The persona list is empty or says "run bootstrap_demo".** The demo wallets have not been created. Run the setup
  script with `--demo` (see the README).
- **The countdown reached zero but nothing happens.** The background bot is not running. Click **Run bot now**; it
  works from about 10 seconds after the return date.
- **A page looks broken after an update.** Do a hard reload (Ctrl+Shift+R, or Cmd+Shift+R on a Mac); browsers keep old
  scripts.
- **"This deposit isn't visible to the persona you are acting as."** Switch to its tenant, landlord or arbiter.
- **Connect wallet says GemWallet wasn't found.** Install the extension, or keep using the demo personas.
- **The public check can't read the ledger.** The public Testnet server may be busy. Click **Check again**.
- **Balances are missing on a persona card.** The ledger could not be reached at that moment. Reload the page.

## 12. Words used

- **Escrow**: a lock on the ledger holding the deposit. It can only be released to the settlement account, or returned
  to the tenant after the return date.
- **Settlement account**: a ledger account created for one deposit. Its own key is switched off; it pays out only
  with two of the three parties' signatures.
- **Grace period**: time after the lease end during which a damage claim is still possible.
- **Automatic return date**: lease end plus grace period.
- **Proposal / split**: a suggestion of how much goes to the tenant and how much to the landlord.
- **Approval**: a party's signature on a proposal. Two are needed.
- **Trust line**: a wallet's permission to hold a particular token.
- **Fingerprint (SHA-256)**: a short code computed from a file. Any change to the file changes the code.
- **Credential**: a statement on the ledger, issued by a registry and accepted by the arbiter, that the arbiter is
  verified.
- **Bot**: a small program that submits the automatic return once the date has passed.
- **Explorer**: a public website that shows ledger accounts and transactions.
- **Testnet**: a practice copy of the XRP Ledger where tokens have no value.
