#!/usr/bin/env python
"""One-step setup that works the same on Windows 11, macOS and Linux.

    Windows:        py scripts\\setup.py            (add --demo to create the Testnet demo wallets too)
    macOS / Linux:  python3 scripts/setup.py

It only uses the standard library, so it runs before anything is installed. Safe to rerun:
an existing .venv is reused and an existing .env is never overwritten.

Steps: virtual environment -> pip install -> .env with a fresh secret key -> browser libraries
(scripts/fetch_vendor.py) -> database migrations -> optionally bootstrap_demo and preflight.
"""
import argparse
import os
import secrets
import subprocess
import sys
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WINDOWS = os.name == "nt"
VENV = ROOT / ".venv"
# The one real difference between the platforms: where a virtual environment keeps its Python.
VENV_PYTHON = VENV / ("Scripts/python.exe" if WINDOWS else "bin/python")
MIN_PYTHON = (3, 11)


def say(text):
    print(f"\n== {text}", flush=True)


def run(*args):
    """Run a command with the virtual environment's Python, from the project root; stop on failure."""
    command = [str(VENV_PYTHON), *args]
    result = subprocess.run(command, cwd=ROOT)  # a list, never a shell string: paths with spaces are safe
    if result.returncode != 0:
        sys.exit(f"\nSetup stopped: `{' '.join(args)}` failed (exit code {result.returncode}). Fix the error above and rerun.")


def make_env_file():
    target, example = ROOT / ".env", ROOT / ".env.example"
    if target.exists():
        print(".env exists: left as it is")
        return
    lines = []
    for line in example.read_text(encoding="utf-8").splitlines():
        if line.startswith("DJANGO_SECRET_KEY="):
            line = f"DJANGO_SECRET_KEY={secrets.token_urlsafe(50)}"  # generated here, never printed
        lines.append(line)
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(".env created with a new random DJANGO_SECRET_KEY (git-ignored; never commit it)")


def main():
    parser = argparse.ArgumentParser(description="Set up safeDeposit for local development.")
    parser.add_argument("--demo", action="store_true",
                        help="Also create the demo wallets on the XRPL test network and run preflight (needs internet, about 2 minutes).")
    args = parser.parse_args()

    if sys.version_info < MIN_PYTHON:
        sys.exit(f"Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]} or newer is needed; this is {sys.version.split()[0]}. "
                 "Install it from https://www.python.org/downloads/ and rerun.")

    say("Virtual environment (.venv)")
    if VENV_PYTHON.exists():
        print("exists: reused")
    else:
        venv.EnvBuilder(with_pip=True).create(VENV)
        print("created")

    say("Python packages")
    run("-m", "pip", "install", "--disable-pip-version-check", "-r", "requirements.txt")

    say("Configuration (.env)")
    make_env_file()

    say("Browser libraries (static/vendor)")
    run("scripts/fetch_vendor.py")

    say("Database")
    run("manage.py", "migrate")

    if args.demo:
        say("Demo wallets, test tokens and the arbiter credential on the XRPL test network")
        run("manage.py", "bootstrap_demo")
        say("Preflight: does token escrow work on this network?")
        run("manage.py", "preflight")

    python = os.path.relpath(VENV_PYTHON, ROOT)  # .venv\Scripts\python.exe on Windows, .venv/bin/python elsewhere
    say("Done. Next, each in its own terminal, from the project folder:")
    if not args.demo:
        print(f"  {python} manage.py bootstrap_demo     (once: demo wallets on the test network)")
    print(f"  {python} manage.py runserver          (then open http://127.0.0.1:8000)")
    print(f"  {python} manage.py run_bot            (returns deposits after their return date)")
    print(f"  {python} manage.py demo_scenarios     (stages the three demo leases)")


if __name__ == "__main__":
    main()
