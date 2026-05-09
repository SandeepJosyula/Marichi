# MARICHI (मरीचि) — Makefile
# Usage: make help

PYTHON   := .venv/bin/python
PIP      := .venv/bin/pip
RECEIVER := receiver.html
PORT     ?= 7777
FPS      ?= 3
MODE     ?= qr
EMAIL    ?=

.PHONY: help install send receive bootstrap phone-email phone-https dist wheels clean

# ─── Help ─────────────────────────────────────────────────────────────────────
help:
	@echo ""
	@echo "  MARICHI (मरीचि)  — Zero-loss air-gap file transfer"
	@echo ""
	@echo "  ── Phone Setup (one-time) ─────────────────────────────────────────"
	@echo "  make phone-email  EMAIL=you@gmail.com   Email receiver.html to yourself"
	@echo "  make phone-https                        HTTPS server (needs same Wi-Fi)"
	@echo ""
	@echo "  ── File Transfer (after phone is set up) ──────────────────────────"
	@echo "  make send FILE=notes.pdf                Send file via QR (default)"
	@echo "  make send FILE=notes.pdf MODE=visual    Send via full-screen pixel frames"
	@echo "  make send FILE=notes.pdf MODE=audio     Send via acoustic modem"
	@echo "  make send FILE=notes.pdf FPS=5          QR at 5 fps"
	@echo ""
	@echo "  ── Receive (laptop-to-laptop) ─────────────────────────────────────"
	@echo "  make receive OUT=got.pdf                Receive file (visual + audio auto)"
	@echo ""
	@echo "  ── Package / Distribution ─────────────────────────────────────────"
	@echo "  make install                            Set up this Mac (creates .venv)"
	@echo "  make wheels                             Download deps as offline wheels"
	@echo "  make dist                               Create marichi-dist.zip to share"
	@echo "  make clean                              Remove .venv and build artefacts"
	@echo ""

# ─── Install ──────────────────────────────────────────────────────────────────
install: $(PYTHON)

$(PYTHON):
	@bash install.sh

# ─── Send ─────────────────────────────────────────────────────────────────────
send: install
ifndef FILE
	$(error FILE is not set. Usage: make send FILE=path/to/file)
endif
ifeq ($(MODE),qr)
	$(PYTHON) send.py "$(FILE)" --mode qr --web-qr --fps $(FPS)
else
	$(PYTHON) send.py "$(FILE)" --mode $(MODE)
endif

# ─── Receive (laptop-to-laptop) ───────────────────────────────────────────────
receive: install
ifndef OUT
	$(error OUT is not set. Usage: make receive OUT=output_file)
endif
	$(PYTHON) receive.py "$(OUT)" --mode auto

# ─── Phone setup: email route (VPN-safe, no server needed) ───────────────────
phone-email: install
ifndef EMAIL
	$(error EMAIL is not set. Usage: make phone-email EMAIL=you@example.com)
endif
	@echo ""
	@echo "  Opening Mail with receiver.html attached…"
	@echo "  Send the email → open on your phone → tap Start Camera."
	@echo ""
	@echo "  ⚠  If camera is blocked (file:// restriction), follow the on-screen"
	@echo "     instructions inside the app — a chrome://flags fix is shown."
	@echo ""
	@open "mailto:$(EMAIL)?subject=MARICHI%20Receiver%20App&body=Open%20the%20attached%20file%20in%20Chrome%20on%20your%20phone.%0A%0AIf%20the%20camera%20does%20not%20start%2C%20follow%20the%20on-screen%20instructions%20(chrome%3A%2F%2Fflags%20fix%20is%20shown%20automatically).&attachment=$(CURDIR)/$(RECEIVER)"
	@echo "  Done — compose window should be open. Attach receiver.html manually if"
	@echo "  it did not attach automatically, then press Send."
	@echo ""
	@echo "  File path to attach:  $(CURDIR)/$(RECEIVER)"
	@echo ""

# ─── Phone setup: HTTPS server (needs phone on same Wi-Fi, VPN off) ──────────
phone-https: install
	@echo "  Starting HTTPS bootstrap server on port $(PORT)…"
	@echo "  Disconnect VPN on this Mac first, then scan the QR on your phone."
	@echo ""
	$(PYTHON) bootstrap_receiver.py --https --port $(PORT)

# ─── Offline wheels (run this OUTSIDE Walmart network to cache deps) ──────────
wheels: install
	@echo "  Downloading dependency wheels for offline install…"
	@mkdir -p wheels
	$(PIP) download -r requirements.txt -d wheels --quiet
	@echo "  Wheels saved to: wheels/"
	@echo "  Include this directory in your dist package for offline Walmart installs."

# ─── Distribution zip ─────────────────────────────────────────────────────────
dist:
	@echo "  Building distribution package…"
	@rm -f marichi-dist.zip
	@zip -r marichi-dist.zip . \
		--exclude "*.venv/*" \
		--exclude "*__pycache__/*" \
		--exclude "*.git/*" \
		--exclude "*.pyc" \
		--exclude "*.DS_Store" \
		--exclude "marichi-dist.zip" \
		--exclude "pip.conf" \
		-q
	@echo ""
	@echo "  ✅ Created: marichi-dist.zip  ($$(du -sh marichi-dist.zip | cut -f1))"
	@echo ""
	@echo "  Share marichi-dist.zip — recipient runs:"
	@echo "    unzip marichi-dist.zip && bash install.sh"
	@echo ""

# ─── Clean ────────────────────────────────────────────────────────────────────
clean:
	rm -rf .venv wheels marichi-dist.zip
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null; true
	find . -name "*.pyc" -delete 2>/dev/null; true
	@echo "  Clean ✓"
