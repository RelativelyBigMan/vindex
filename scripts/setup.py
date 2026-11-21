#!/usr/bin/env python
"""One-step setup that works the same on Windows 11, macOS and Linux.

    Windows:        py scripts\\setup.py            (add --bootstrap to create the platform wallets too)
    macOS / Linux:  python3 scripts/setup.py

It only uses the standard library, so it runs before anything is installed. Safe to rerun:
an existing .venv is reused and an existing .env is never overwritten.

Steps: virtual environment -> pip install -> .env with a fresh secret key -> browser libraries
(scripts/fetch_vendor.py) -> database migrations -> optionally bootstrap and preflight.

If you ran an older version: delete db.sqlite3 and rerun with --bootstrap (the migrations were reset).
"""
import argparse
import os
import secrets
import sqlite3
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


def old_database():
    """True when db.sqlite3 comes from an older version whose migrations were later reset (core 0002_leases)."""
    path = ROOT / "db.sqlite3"
    if not path.exists():
        return False
    db = sqlite3.connect(path)
    try:
        return db.execute("SELECT 1 FROM django_migrations WHERE app = 'core' AND name = '0002_leases'").fetchone() is not None
    except sqlite3.Error:  # no django_migrations table yet: a new database
        return False
    finally:
        db.close()


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
    parser = argparse.ArgumentParser(description="Set up Vindex for local development.")
    parser.add_argument("--bootstrap", action="store_true",
                        help="Also create the platform wallets on the XRPL test network and run preflight (needs internet, about 2 minutes).")
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
    env = (ROOT / ".env").read_text(encoding="utf-8")
    keys = dict(line.split("=", 1) for line in env.splitlines() if "=" in line and not line.lstrip().startswith("#"))
    if not keys.get("XAMAN_API_KEY", "").strip():
        print("NOTE: set XAMAN_API_KEY and XAMAN_API_SECRET in .env (free at https://apps.xumm.dev), or nobody can sign in.")

    say("Browser libraries (static/vendor)")
    run("scripts/fetch_vendor.py")

    say("Database")
    if old_database():
        sys.exit("\ndb.sqlite3 was made by an older version of Vindex, and the database migrations have been reset since.\n"
                 "Delete db.sqlite3 and rerun this script with --bootstrap (or run manage.py migrate and manage.py bootstrap).\n"
                 "It only holds Testnet data; the platform wallets are created again.")
    run("manage.py", "migrate")

    if args.bootstrap:
        say("Platform wallets and test tokens on the XRPL test network")
        run("manage.py", "bootstrap")
        say("Preflight: does token escrow work on this network?")
        run("manage.py", "preflight")

    python = os.path.relpath(VENV_PYTHON, ROOT)  # .venv\Scripts\python.exe on Windows, .venv/bin/python elsewhere
    say("Done. Next, each in its own terminal, from the project folder:")
    if not args.bootstrap:
        print(f"  {python} manage.py bootstrap          (once: platform wallets on the test network)")
    print(f"  {python} manage.py runserver          (then open http://127.0.0.1:8000)")
    print(f"  {python} manage.py run_bot            (returns deposits after their return date)")


if __name__ == "__main__":
    main()
