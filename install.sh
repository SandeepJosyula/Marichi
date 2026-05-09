#!/usr/bin/env bash
# MARICHI (मरीचि) — One-liner Mac Installer
# Usage: bash install.sh
# Works on macOS 12+ with Python 3.9+. No internet required if wheels/ dir exists.
set -euo pipefail

PYTHON=${PYTHON:-python3}
VENV=.venv
WHEELS_DIR="$(dirname "$0")/wheels"

banner() { echo; echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"; echo "  $*"; echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"; }

banner "MARICHI Installer"

# ── Python version check ──────────────────────────────────────────────────────
PY_VER=$($PYTHON -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null) || {
    echo "ERROR: python3 not found. Install Python 3.9+ and retry."
    exit 1
}
MAJOR=$(echo "$PY_VER" | cut -d. -f1)
MINOR=$(echo "$PY_VER" | cut -d. -f2)
if [ "$MAJOR" -lt 3 ] || ([ "$MAJOR" -eq 3 ] && [ "$MINOR" -lt 9 ]); then
    echo "ERROR: Python $PY_VER found but 3.9+ is required."
    exit 1
fi
echo "  Python $PY_VER ✓"

# ── Virtual environment ───────────────────────────────────────────────────────
if [ ! -d "$VENV" ]; then
    echo "  Creating virtual environment…"
    $PYTHON -m venv "$VENV"
fi
echo "  Venv: $VENV ✓"

# ── pip upgrade ───────────────────────────────────────────────────────────────
"$VENV/bin/pip" install --upgrade pip --quiet

# ── Install dependencies ──────────────────────────────────────────────────────
echo "  Installing dependencies…"
if [ -d "$WHEELS_DIR" ]; then
    echo "  (offline mode — using bundled wheels)"
    "$VENV/bin/pip" install --no-index --find-links "$WHEELS_DIR" -r requirements.txt --quiet
else
    "$VENV/bin/pip" install -r requirements.txt --quiet
fi

# ── Smoke test ────────────────────────────────────────────────────────────────
"$VENV/bin/python" - <<'EOF'
import marichi, numpy, reedsolo, tqdm, cv2, qrcode, flask
print(f"  marichi {marichi.__version__} ✓")
EOF

echo
echo "  Installation complete!"
echo
echo "  ┌──────────────────────────────────────────────────────────┐"
echo "  │  QUICK START                                             │"
echo "  │                                                          │"
echo "  │  Set up phone (one-time):                                │"
echo "  │    make phone-email EMAIL=you@gmail.com                  │"
echo "  │    (opens Mail with receiver.html — send to yourself)    │"
echo "  │                                                          │"
echo "  │  Send a file to phone:                                   │"
echo "  │    make send FILE=photo.jpg                              │"
echo "  │                                                          │"
echo "  │  Full help:                                              │"
echo "  │    make help                                             │"
echo "  └──────────────────────────────────────────────────────────┘"
echo
