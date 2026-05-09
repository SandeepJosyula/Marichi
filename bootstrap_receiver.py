#!/usr/bin/env python3
"""
MARICHI Bootstrap — deliver receiver.html to your phone  (one-time setup)

This script does ONE thing: serve receiver.html over local Wi-Fi so your
phone can load it.  After the page is loaded on the phone, shut this server
down — all subsequent file transfers are 100% air-gap (phone camera only).

IMPORTANT: No file data flows through this server.  Ever.
  • The server ONLY serves the receiver.html page.
  • Once the phone has the page, the Mac and phone have ZERO network contact
    during a transfer.  Data moves exclusively through the phone camera
    reading QR codes off the Mac screen.

One-time setup:
  1. Run this script on the Mac
  2. Phone scans the QR code → opens receiver.html in browser
  3. (Optional) "Add to Home Screen" for offline access forever
  4. Stop this server — you won't need it again for this phone

Per-transfer (after setup):
  Mac:   python send.py <file> --mode qr --web-qr --fps 3
  Phone: open receiver.html → tap Start Camera → aim at Mac screen
  Done:  file downloads automatically to phone

Requirements:
  pip install flask qrcode[pil]

Usage:
  python bootstrap_receiver.py             # port 7777
  python bootstrap_receiver.py --port 8080
"""

import argparse
import logging
import os
import socket
import ssl
import subprocess
import sys
import tempfile

try:
    import qrcode as _qrcode
except ImportError:
    _qrcode = None

try:
    from flask import Flask, Response, send_file
except ImportError:
    print("ERROR: flask not installed.  Run:  pip install flask")
    sys.exit(1)

RECEIVER_HTML = os.path.join(os.path.dirname(os.path.abspath(__file__)), "receiver.html")

if not os.path.exists(RECEIVER_HTML):
    print(f"ERROR: receiver.html not found at {RECEIVER_HTML}")
    sys.exit(1)

app = Flask(__name__)

# Suppress werkzeug request logs
logging.getLogger("werkzeug").setLevel(logging.WARNING)


@app.route("/")
def index():
    return send_file(RECEIVER_HTML, mimetype="text/html")


@app.route("/health")
def health():
    return "ok"


def _local_ip() -> str:
    """
    Return the best local IP for phone access — prefer Wi-Fi (en0) over VPN tunnels.
    On macOS, VPN creates utun* interfaces whose IPs (172.x.x.x) are unreachable
    from phones on the local network.  We explicitly check en0 first.
    """
    import re

    # macOS: try en0 (Wi-Fi) first
    for iface in ("en0", "en1", "en2"):
        try:
            out = subprocess.check_output(
                ["ifconfig", iface], stderr=subprocess.DEVNULL
            ).decode()
            m = re.search(r'\binet (\d+\.\d+\.\d+\.\d+)', out)
            if m and not m.group(1).startswith("127."):
                return m.group(1)
        except Exception:
            pass

    # Fallback: first non-loopback, non-VPN address found via socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("192.168.1.1", 80))
        ip = s.getsockname()[0]
        s.close()
        if not ip.startswith("127."):
            return ip
    except Exception:
        pass

    return "127.0.0.1"


def _print_qr(url: str) -> None:
    if _qrcode is None:
        print(f"\n  Open on phone:  {url}\n")
        return
    qr = _qrcode.QRCode(border=1)
    qr.add_data(url)
    qr.make(fit=True)
    m = qr.modules
    rows, cols = len(m), len(m[0]) if m else 0
    for r in range(0, rows, 2):
        top = m[r]
        bot = m[r + 1] if r + 1 < rows else [False] * cols
        print("  " + "".join(
            "█" if t and b else "▀" if t else "▄" if b else " "
            for t, b in zip(top, bot)
        ))
    print()


def _generate_cert(ip: str):
    """
    Generate a temporary self-signed TLS certificate valid for 1 day.
    Requires openssl CLI (pre-installed on macOS).
    Returns (cert_path, key_path) — files live in a temp dir until process exits.
    """
    tmpdir = tempfile.mkdtemp(prefix="marichi_ssl_")
    cert   = os.path.join(tmpdir, "cert.pem")
    key    = os.path.join(tmpdir, "key.pem")
    try:
        subprocess.run(
            [
                "openssl", "req", "-x509", "-newkey", "rsa:2048",
                "-keyout", key, "-out", cert,
                "-days", "1", "-nodes",
                "-subj", f"/CN={ip}",
            ],
            check=True,
            capture_output=True,
        )
    except FileNotFoundError:
        print("ERROR: openssl not found. Install it or use --no-https for plain HTTP.")
        sys.exit(1)
    except subprocess.CalledProcessError as exc:
        print(f"ERROR: openssl failed:\n{exc.stderr.decode()}")
        sys.exit(1)
    return cert, key


def main():
    parser = argparse.ArgumentParser(
        description="MARICHI Bootstrap — deliver receiver.html to phone (one-time only)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--port", "-p", type=int, default=7777)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument(
        "--https", "-s",
        action="store_true",
        default=False,
        help=(
            "Serve over HTTPS using a temporary self-signed certificate. "
            "Required when the phone opens receiver.html from local storage "
            "(e.g. downloaded from email) and camera access is blocked. "
            "Chrome will show an SSL warning — tap Advanced → Proceed."
        ),
    )
    args = parser.parse_args()

    ip  = _local_ip()

    ssl_ctx = None
    if args.https:
        print("  Generating self-signed TLS cert … ", end="", flush=True)
        cert, key = _generate_cert(ip)
        ssl_ctx = (cert, key)
        print("done")
        url = f"https://{ip}:{args.port}"
    else:
        url = f"http://{ip}:{args.port}"

    sz = os.path.getsize(RECEIVER_HTML)

    print("\n" + "═" * 62)
    print("  MARICHI Bootstrap Server — one-time phone setup")
    print("═" * 62)
    print(f"\n  Serving : receiver.html  ({sz:,} bytes)")
    print(f"  URL     : {url}")
    if args.https:
        print()
        print("  ┌─────────────────────────────────────────────────────┐")
        print("  │  HTTPS mode (self-signed cert)                      │")
        print("  │  Chrome will show a security warning — that is OK.  │")
        print("  │  Tap  Advanced  →  Proceed to <IP> (unsafe)         │")
        print("  │  Camera access will work after you proceed.         │")
        print("  └─────────────────────────────────────────────────────┘")
    print()
    print("  ┌─────────────────────────────────────────────────────┐")
    print("  │  This server ONLY delivers the receiver app.        │")
    print("  │  Zero file data ever passes through it.             │")
    print("  │  After the phone loads the page → stop this server. │")
    print("  └─────────────────────────────────────────────────────┘")
    print()
    print("  HOW TO USE")
    print("  ──────────")
    print("  ① Connect phone to the same Wi-Fi as this Mac")
    print("  ② Scan the QR code below with your phone camera")
    print("  ③ receiver.html opens in your phone browser")
    if args.https:
        print("  ③b Chrome shows SSL warning → tap Advanced → Proceed")
    print("  ④ (Optional) tap Share → Add to Home Screen (works offline)")
    print("  ⑤ Stop this server — never needed again for this phone")
    print()
    print("  TO SEND A FILE (after setup, no Wi-Fi needed):")
    print("  ─────────────────────────────────────────────")
    print("    Mac:   python send.py <file> --mode qr --web-qr --fps 3")
    print("    Phone: open receiver.html → tap 📷 Start Camera")
    print("           aim phone camera at Mac screen")
    print("           file downloads when all frames received\n")

    _print_qr(url)
    print(f"  Listening on {url}  (Ctrl+C to stop)\n")

    app.run(host=args.host, port=args.port,
            debug=False, use_reloader=False, threaded=True,
            ssl_context=ssl_ctx)


if __name__ == "__main__":
    main()
