#!/usr/bin/env python
"""Download the pinned browser builds into static/vendor/ and check their SHA-256.

    python scripts/fetch_vendor.py

Why a script: the browser code is self-hosted (no CDN at run time, so a strict CSP holds and no
visitor IP reaches a third party). xrpl.js (ISC licence) is committed. @gemwallet/api is NOT
committed: its licence allows personal and educational use but asks for GemWallet's permission
before public or commercial distribution, so every checkout fetches its own copy from npm.
"""
import hashlib
import io
import sys
import tarfile
import urllib.request
from pathlib import Path

VENDOR = Path(__file__).resolve().parent.parent / "static" / "vendor"
FILES = [
    # (file in static/vendor, npm tarball, path inside the tarball, sha256, lines to drop from the end)
    ("gemwallet-api-3.8.0.js", "https://registry.npmjs.org/@gemwallet/api/-/api-3.8.0.tgz",
     "package/umd/gemwallet-api.js", "b677480fb46322197aa945575f32192136fcaa0aa4bdf402b7291cc982b8bbfe", 0),
    # The source-map comment (last line) is dropped: the map isn't shipped.
    ("xrpl-5.3.0.min.js", "https://registry.npmjs.org/xrpl/-/xrpl-5.3.0.tgz",
     "package/build/xrpl-latest-min.js", "4da2289595d03e1827e596f69c4a7540a95a1cde737a5f0cd113ced2d2f0c01c", 1),
]


def main():
    VENDOR.mkdir(parents=True, exist_ok=True)
    failed = False
    for name, url, member, expected, drop in FILES:
        target = VENDOR / name
        if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() == expected:
            print(f"ok       {name}")
            continue
        print(f"fetching {name} from {url}")
        with urllib.request.urlopen(url, timeout=60) as response:  # noqa: S310 (fixed https URL)
            archive = tarfile.open(fileobj=io.BytesIO(response.read()), mode="r:gz")
        data = archive.extractfile(member).read()
        if drop:
            data = b"\n".join(data.split(b"\n")[:-drop - 1]) + b"\n" if data.endswith(b"\n") else b"\n".join(data.split(b"\n")[:-drop]) + b"\n"
        digest = hashlib.sha256(data).hexdigest()
        if digest != expected:
            print(f"REFUSED  {name}: sha256 {digest} is not the pinned {expected}")
            failed = True
            continue
        target.write_bytes(data)
        print(f"saved    {name}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
