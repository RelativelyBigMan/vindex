#!/usr/bin/env python
"""Run safeDeposit on this computer so that other people can open it. Works on Windows, macOS and Linux.

    python scripts/serve.py              same Wi-Fi / network: others open http://<this computer's address>:8000
    python scripts/serve.py --tunnel     anyone with the link: a public https address through Cloudflare Tunnel

(On Windows: py scripts\\serve.py ...)   Stop everything with Ctrl+C.

One command starts the website and the return bot, with settings that are safe to show other people:
debug pages off, only the needed host names accepted. Nothing is written to .env; the settings apply
to this run only.

--tunnel needs the free `cloudflared` program installed (https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/).
It needs no account. The address is random and changes on every run.
"""
import argparse
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENV_PYTHON = ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
TUNNEL_URL = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")


def lan_address():
    """This computer's address on the local network (no data is sent; the socket only picks a route)."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("192.0.2.1", 9))  # a reserved documentation address: nothing is contacted
        return probe.getsockname()[0]
    except OSError:
        return None
    finally:
        probe.close()


def main():
    parser = argparse.ArgumentParser(description="Serve safeDeposit to other people from this computer.")
    parser.add_argument("--tunnel", action="store_true", help="Also open a public https address with cloudflared.")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--no-bot", action="store_true", help="Don't start the return bot.")
    parser.add_argument("--debug", action="store_true", help="Keep Django's debug pages on (never with --tunnel).")
    args = parser.parse_args()

    python = str(VENV_PYTHON if VENV_PYTHON.exists() else sys.executable)
    if not (ROOT / ".env").exists():
        sys.exit("No .env file yet. Run the setup first: python scripts/setup.py --demo")
    if args.tunnel and args.debug:
        sys.exit("--debug shows internal details to visitors; it can't be combined with --tunnel.")
    cloudflared = shutil.which("cloudflared")
    if args.tunnel and not cloudflared:
        sys.exit("cloudflared isn't installed. Get it from\n"
                 "  https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/\n"
                 "then run this again. Without it, drop --tunnel to share on your local network only.")

    address = lan_address()
    env = dict(os.environ)
    env["DJANGO_DEBUG"] = "1" if args.debug else "0"
    hosts = ["localhost", "127.0.0.1", "[::1]"] + ([address] if address else [])
    if args.tunnel:
        # Requests arrive from the tunnel over https, with the visitor's address in a header.
        hosts.append(".trycloudflare.com")
        env["DJANGO_CSRF_TRUSTED_ORIGINS"] = "https://*.trycloudflare.com"
        env["DJANGO_BEHIND_PROXY"] = "1"
    env["DJANGO_ALLOWED_HOSTS"] = ",".join(hosts)
    # With a tunnel only the tunnel talks to the server, so it listens on this computer alone.
    bind = "127.0.0.1" if args.tunnel else "0.0.0.0"

    children = []

    def start(name, command, **kwargs):
        process = subprocess.Popen(command, cwd=ROOT, env=env, **kwargs)
        children.append((name, process))
        return process

    # --insecure: let the development server deliver the static files although debug is off.
    start("website", [python, "manage.py", "runserver", f"{bind}:{args.port}", "--noreload", "--insecure"])
    if not args.no_bot:
        start("return bot", [python, "manage.py", "run_bot"])

    print("\n" + "=" * 72)
    print(f"  On this computer:      http://127.0.0.1:{args.port}")
    if not args.tunnel:
        if address:
            print(f"  On the same network:   http://{address}:{args.port}")
            print("  (Others must be on the same Wi-Fi or network. Your firewall may ask to allow Python.)")
            print("  Over plain http, the Copy buttons and the photo Verify check don't work for them;")
            print("  use --tunnel for an https address where everything works.")
        else:
            print("  No network address found: is this computer connected to a network?")
    else:
        print("  Public address:        starting the tunnel...")
    print("  Stop with Ctrl+C.")
    print("=" * 72 + "\n", flush=True)

    if args.tunnel:
        tunnel = start("tunnel", [cloudflared, "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{args.port}"],
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

        def watch():
            announced = False
            for line in tunnel.stdout:
                found = TUNNEL_URL.search(line)
                if found and not announced:
                    announced = True
                    print("\n" + "=" * 72)
                    print(f"  Public address:  {found.group(0)}")
                    print("  Anyone with this link can open the site and create an account. It stops working")
                    print("  when you press Ctrl+C, and it is different on every run.")
                    print("=" * 72 + "\n", flush=True)

        threading.Thread(target=watch, daemon=True).start()

    def stop(*_):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop)  # closing the terminal or a service manager also stops the children

    try:
        while True:
            for name, process in children:
                code = process.poll()
                if code is not None:
                    print(f"\nThe {name} stopped (exit code {code}). Stopping the rest.")
                    raise KeyboardInterrupt
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        for _name, process in children:
            if process.poll() is None:
                process.terminate()
        for _name, process in children:
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
        print("Stopped.")


if __name__ == "__main__":
    main()
