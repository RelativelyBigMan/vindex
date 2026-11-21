#!/usr/bin/env python
"""Drive scenarios A, B and C through the JSON API only (demo mode), printing statuses and explorer links.

    python manage.py runserver            # terminal 1
    python manage.py run_bot              # terminal 2
    python scripts/api_walkthrough.py     # terminal 3   [--base URL] [--currency EUR] [--scenario A|B|C|all] [--poke-bot]

A  normal end:      lock -> wait -> the bot returns the deposit automatically        -> RETURNED
B  early move-out:  lock -> tenant releases -> tenant + landlord approve 100% back   -> SETTLED
C  damage dispute:  lock -> landlord claims -> landlord proposes 850/150, arbiter approves -> SETTLED

A is started first; B and C run while it waits. --poke-bot calls POST /api/demo/run-bot/ while
waiting, for when run_bot isn't running. Each run costs the tenant 150 test tokens (scenario C).
"""
import argparse
import sys
import time

import requests

DEPOSIT = "1000"


class Failed(Exception):
    pass


class Api:
    """One browser-like session: keeps the cookies and sends the CSRF token on every POST."""

    def __init__(self, base):
        self.base = base.rstrip("/")
        self.http = requests.Session()
        self.get("/api/demo/personas/")  # sets the CSRF cookie

    def _result(self, response):
        try:
            data = response.json()
        except ValueError:
            raise Failed(f"{response.request.method} {response.url} -> HTTP {response.status_code}, not JSON")
        if response.status_code >= 400:
            raise Failed(f"{response.request.method} {response.url} -> HTTP {response.status_code} {data.get('code')}: {data.get('error')}")
        return data

    def get(self, path):
        return self._result(self.http.get(self.base + path, timeout=120))

    def post(self, path, body=None):
        headers = {"X-CSRFToken": self.http.cookies.get("csrftoken", ""), "Referer": self.base + "/"}
        return self._result(self.http.post(self.base + path, json=body or {}, headers=headers, timeout=180))

    def act_as(self, persona):
        self.post("/api/demo/persona/", {"party_id": persona["party_id"]})


class Walkthrough:
    def __init__(self, args):
        self.args = args
        self.api = Api(args.base)
        personas = self.api.get("/api/demo/personas/")["personas"]
        try:
            self.who = {role: next(p for p in personas if p["role"] == role) for role in ("tenant", "landlord", "arbiter")}
        except StopIteration:
            raise Failed("demo personas are missing; run bootstrap_demo first")
        self.seen = {}
        self.results = {}

    def say(self, tag, text, link=None):
        print(f"[{tag}] {text}" + (f"\n      {link}" if link else ""), flush=True)

    def show(self, tag, state):
        """Print the status when it changes and every timeline event once, oldest first."""
        seen = self.seen.setdefault(state["id"], {"status": None, "events": set()})
        for event in reversed(state["events"]):
            if event["id"] not in seen["events"]:
                seen["events"].add(event["id"])
                self.say(tag, f"  event: {event['summary']}", event["explorer_url"])
        label = state["status"] + (f" ({state['setup_step']})" if state["status"] == "DRAFT" and state["setup_step"] else "")
        if label != seen["status"]:
            seen["status"] = label
            self.say(tag, f"status: {label}")
        return state

    def wait_for(self, tag, lease_id, wanted, timeout, poke_bot=False):
        deadline = time.time() + timeout
        while True:
            state = self.show(tag, self.api.get(f"/api/leases/{lease_id}/"))
            if state["status"] == wanted:
                return state
            if state["status"] == "FAILED":
                raise Failed(f"lease failed: {state['error']}")
            if time.time() > deadline:
                raise Failed(f"still {state['status']} after {timeout} s, expected {wanted}")
            if poke_bot and state["seconds_to_cancel_after"] == 0:
                self.api.post("/api/demo/run-bot/")
            time.sleep(3)

    def expect(self, tag, condition, text):
        if not condition:
            raise Failed(text)
        self.say(tag, f"ok  {text}")

    def open_deposit(self, tag, end_in_minutes):
        self.api.act_as(self.who["tenant"])
        lease_id = self.api.post("/api/leases/", {
            "landlord_id": self.who["landlord"]["party_id"], "arbiter_id": self.who["arbiter"]["party_id"],
            "currency": self.args.currency, "amount": DEPOSIT, "end_in_minutes": end_in_minutes, "grace_minutes": 1,
        })["lease_id"]
        self.say(tag, f"lease created: {self.api.base}/api/leases/{lease_id}/")
        state = self.wait_for(tag, lease_id, "SETTLEMENT_READY", 180)
        settlement = state["settlement"]
        self.expect(
            tag, settlement.get("quorum") == 2 and settlement.get("master_disabled") and len(settlement.get("signers", [])) == 3,
            f"settlement account {settlement['address']}: 2 of 3 signers, master key disabled (read live)",
        )
        self.expect(tag, state["actions"] == ["lock_deposit", "upload_evidence"], "the tenant may now lock the deposit")
        state = self.show(tag, self.api.post(f"/api/leases/{lease_id}/escrow/", {"mode": "demo"}))
        self.expect(tag, state["status"] == "ACTIVE" and state["escrow"]["sequence"], f"deposit locked, escrow sequence {state['escrow']['sequence']}")
        return lease_id

    def settle(self, tag, lease_id, proposal, approver, tenant_amount, landlord_amount):
        self.expect(
            tag, (proposal["tenant_amount"], proposal["landlord_amount"], proposal["signatures_count"]) == (tenant_amount, landlord_amount, 1),
            f"open proposal {tenant_amount} / {landlord_amount}, signed by {proposal['signed_by'][0]['role']}",
        )
        self.api.act_as(self.who[approver])
        state = self.api.get(f"/api/leases/{lease_id}/")
        self.expect(tag, f"sign:{proposal['id']}" in state["actions"], f"the {approver} may approve it")
        signed = self.api.post(f"/api/proposals/{proposal['id']}/sign/")
        state = self.show(tag, signed["lease"])
        self.expect(tag, signed["status"] == "EXECUTED" and state["status"] == "SETTLED", f"approved by the {approver}: paid out and settled")
        self.expect(tag, state["settlement"]["balance"] == "0", "settlement account is empty")

    # ---- scenarios ----
    def scenario_a_start(self):
        print("\nA. Normal end (started; B and C run while it waits)")
        return self.open_deposit("A", end_in_minutes=1)

    def scenario_a_finish(self, lease_id):
        print("\nA. Normal end (continued): waiting for the bot")
        state = self.api.get(f"/api/leases/{lease_id}/")
        self.say("A", f"automatic return in about {state['seconds_to_cancel_after'] + 10} s (ledger clock)")
        state = self.wait_for("A", lease_id, "RETURNED", 420, poke_bot=self.args.poke_bot)
        self.expect("A", state["escrow"]["exists"] is False, "escrow is gone; the deposit is back with the tenant")
        self.expect("A", state["events"][0]["kind"] == "returned" and state["events"][0]["tx_hash"], "the return is on the timeline with its transaction")

    def scenario_b(self):
        print("\nB. Early move-out")
        lease_id = self.open_deposit("B", end_in_minutes=30)
        state = self.show("B", self.api.post(f"/api/leases/{lease_id}/release/", {"reason": "early_move_out", "mode": "demo"}))
        self.expect("B", state["status"] == "IN_SETTLEMENT" and state["settlement"]["balance"] == DEPOSIT, f"settlement account holds {DEPOSIT}")
        self.settle("B", lease_id, state["proposals"][0], "landlord", DEPOSIT, "0")

    def scenario_c(self):
        print("\nC. Damage dispute")
        lease_id = self.open_deposit("C", end_in_minutes=30)
        self.api.act_as(self.who["landlord"])
        state = self.show("C", self.api.post(f"/api/leases/{lease_id}/release/", {"reason": "damage_claim", "mode": "demo"}))
        self.expect("C", state["status"] == "IN_SETTLEMENT" and not state["proposals"], "in settlement, no default proposal")
        created = self.api.post(f"/api/leases/{lease_id}/proposals/", {"tenant_amount": "850", "landlord_amount": "150", "reason": "Broken window"})
        state = self.show("C", created["lease"])
        self.settle("C", lease_id, state["proposals"][0], "arbiter", "850", "150")

    def run(self, name, fn):
        if self.results.get(name) not in (None, "PASS"):
            return None
        try:
            value = fn()
            self.results[name] = "PASS"
            return value if value is not None else True
        except Failed as exc:
            self.results[name] = f"FAIL: {exc}"
            print(f"[{name}] FAILED {exc}")
        except requests.RequestException as exc:
            self.results[name] = f"FAIL: can't reach the server ({exc})"
            print(f"[{name}] FAILED {exc}")
        return None


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base", default="http://127.0.0.1:8000")
    parser.add_argument("--currency", choices=["EUR", "USD"], default="EUR")
    parser.add_argument("--scenario", choices=["A", "B", "C", "all"], default="all")
    parser.add_argument("--poke-bot", action="store_true", help="call /api/demo/run-bot/ while waiting (when run_bot isn't running)")
    args = parser.parse_args()
    try:
        walk = Walkthrough(args)
    except (Failed, requests.RequestException) as exc:
        sys.exit(f"Can't start: {exc}\nIs `python manage.py runserver` running at {args.base}?")
    me = walk.api.get("/api/me/")
    print(f"Server: {args.base} | network: {me['network']} | deposit: {DEPOSIT} {args.currency}")
    for role, persona in walk.who.items():
        print(f"  {role:<9} {persona['name']:<24} {persona['explorer_url']}")

    wanted = ["A", "B", "C"] if args.scenario == "all" else [args.scenario]
    pending = walk.run("A", walk.scenario_a_start) if "A" in wanted else None
    if "B" in wanted:
        walk.run("B", walk.scenario_b)
    if "C" in wanted:
        walk.run("C", walk.scenario_c)
    if pending:
        walk.run("A", lambda: walk.scenario_a_finish(pending))

    names = {"A": "normal end, automatic return", "B": "early move-out, 100% back to the tenant", "C": "damage dispute, 850 / 150 split"}
    names = {key: names[key] for key in wanted}
    print("\nRESULT  SCENARIO")
    for key, text in names.items():
        result = walk.results.get(key, "FAIL: not run")
        print(f"{'PASS' if result == 'PASS' else 'FAIL'}    {key}: {text}" + ("" if result == "PASS" else f"  ({result[6:]})"))
    sys.exit(0 if all(walk.results.get(k) == "PASS" for k in names) else 1)


if __name__ == "__main__":
    main()
