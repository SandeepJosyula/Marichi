# How to Run MARICHI (मरीचि)  v0.3
**Zero-Loss Multi-Transport Modem — Visual · Audio · QR · All**

---

## Choose Your Transport Mode

| Mode | Flag | Hardware needed | Throughput | Best for |
|------|------|-----------------|------------|---------|
| **A — Visual** *(default)* | `--mode visual` | Two cameras facing each other | **1–3 MB/s** | Laptop ↔ Laptop |
| **C — Audio** | `--mode audio` | Speaker + Microphone | ~50–200 B/s | No cameras; small files |
| **D — QR Stream** | `--mode qr` | Any camera incl. phone | **6–22 KB/s** | Phone/tablet as receiver |
| **All** | `--mode all` | Everything above | All channels | Max reliability |

> **File size guidance:**
> - 25 GB → use **visual** (`--mode visual --block 2`, 3–7 hrs)
> - 100 MB → use **qr** (`--mode qr --fps 5`, ~2.5 hrs) or visual
> - < 1 MB → any mode; **audio** works for credential/key transfer

---

## What's New in v0.3

| Feature | v0.2 | v0.3 |
|---------|------|------|
| Transmission modes | Visual only | **Visual + Audio + QR + All** |
| Phone receiver | Not supported | **✅ QR mode (Option D)** |
| Audio transmission | Not supported | **✅ MFSK-4 modem (Option C)** |
| Simultaneous channels | N/A | **✅ `--mode all`** |
| Letter aliases | N/A | **`-m a/c/d`** |

---

## Mode A — Visual (Full-Screen Pixel Frame)

The original MARICHI mode. Fastest throughput. Requires cameras on both devices.

### Physical Setup

```
        ┌───────────────────────────────────────────────────┐
        │                    TOP VIEW                        │
        │                                                    │
        │    SENDER laptop              RECEIVER laptop      │
        │   ┌──────────────┐           ┌──────────────┐    │
        │   │              │ ◄── 30-60cm ──►           │    │
        │   │  DATA FRAME  │           │  ACK WINDOW  │    │
        │   │  pixel grid  │           │  blue/green/ │    │
        │   │  + cksum row │           │  yellow flash│    │
        │   └──────────────┘           └──────────────┘    │
        │     📷 webcam A                   📷 webcam B     │
        │    (at top of lid)               (at top of lid)  │
        │    → points at                   → points at      │
        │      receiver screen               sender screen  │
        └───────────────────────────────────────────────────┘
```

### Run Visual Mode

```bash
# STEP 1 — Receiver first
python receive.py received.zip                      # cam 0, ACK on
python receive.py received.zip --cam 1 --block 2    # custom camera

# STEP 2 — Sender
python send.py myfile.zip --ack-cam 0               # ACK mode (recommended)
python send.py myfile.zip                           # timer mode (no camera)

# STEP 3 — Validate
python validate.py myfile.zip received.zip
```

### ACK Protocol (Visual)

```
Receiver shows      Sender sees    Sender action
──────────────────  ─────────────  ────────────────────────────────
🟢 Solid GREEN      Processing     Wait — receiver is decoding
🔵 Solid BLUE       ACK            ✅ Advance to next frame
🟡 Solid YELLOW     NACK           ❌ Re-show same frame (retry)
```

### Three-Way Checksum (Visual)

```
Frame received
    ├─ 1. ECC decode          → Reed-Solomon corrects camera noise
    ├─ 2. Header CRC32        → CRC32(payload) == header CRC32
    └─ 3. Strip CRC32         → independent checksum row matches
All three pass → 🔵 BLUE.  Any failure → 🟡 YELLOW + retry.
```

### Visual Performance

| Block | ACK Mode | Effective | 25 GB |
|-------|----------|-----------|-------|
| `--block 1` | ACK | ~3–5 MB/s | 1.5–2.5 hrs |
| `--block 2` *(default)* | ACK | ~1–2 MB/s | 3.5–7 hrs |
| `--block 4` | ACK | ~400 KB/s | ~18 hrs |

---

## Mode C — Audio (MFSK-4 Acoustic Modem)

Transmits data as audio tones through the laptop speaker. Receiver captures via microphone.
Works when cameras are unavailable. Best for small files (< 10 MB).

### Physical Setup

```
        ┌─────────────────────────────────────────────────┐
        │                  AUDIO MODE                      │
        │                                                  │
        │   SENDER laptop          RECEIVER laptop         │
        │   ┌──────────────┐      ┌──────────────┐        │
        │   │              │      │              │        │
        │   │  (screen off │      │  (screen off │        │
        │   │   or idle)   │      │   or idle)   │        │
        │   └──────────────┘      └──────────────┘        │
        │       🔊 speaker  ──────►  🎤 mic                │
        │                                                  │
        │   Distance: 20–50 cm, quiet room                │
        │   No line-of-sight needed for screen             │
        └─────────────────────────────────────────────────┘
```

**Physical checklist:**
- [ ] Place laptops 20–50 cm apart
- [ ] Use a quiet room (background noise degrades decode accuracy)
- [ ] Speaker volume: 80–100%
- [ ] Microphone sensitivity: default system level
- [ ] Both devices on the same flat surface (reduces echo)

### Run Audio Mode

```bash
# STEP 1 — Receiver first (microphone listens)
python receive.py received.bin --mode audio

# With custom baud rate (must match sender)
python receive.py received.bin --mode audio --baud 600

# STEP 2 — Sender (plays tones through speaker)
python send.py myfile.bin --mode audio

# Faster (louder, quieter room needed)
python send.py myfile.bin --mode audio --baud 600

# No ACK (timer mode — sender plays all frames sequentially)
python send.py myfile.bin --mode audio --baud 300
```

### Audio Protocol

```
SENDER speaker plays:
  MFSK-4 tones at 1200/1800/2400/3000 Hz
  → 2 bits per symbol
  → 300 baud × 2 = 600 bps raw
  → ~56 bytes/sec effective (after RS ECC)

RECEIVER mic listens:
  FFT peak detection per symbol window
  CRC32 + RS ECC per frame
  → ACK tone: 500 Hz (frame OK)
  → NACK tone: 750 Hz (retry)
```

### Audio Baud Rate Guide

| Baud | Data rate | Effective | 1 MB | Notes |
|------|-----------|-----------|------|-------|
| `300` *(default)* | 600 bps | ~56 B/s | ~18 min | Most reliable |
| `600` | 1200 bps | ~112 B/s | ~9 min | Good room required |
| `1200` | 2400 bps | ~225 B/s | ~5 min | Very quiet, close proximity |

> ⚠️ **Baud rate MUST match** on sender and receiver (`--baud 600` on both sides)

### Audio Troubleshooting

| Problem | Fix |
|---------|-----|
| No frames decoded | Increase speaker volume; reduce mic distance |
| High NACK rate | Use lower `--baud`; reduce background noise |
| `sounddevice not installed` | `pip install sounddevice` (outside Walmart network) |
| PortAudio error on macOS | `brew install portaudio` |
| PortAudio error on Linux | `sudo apt install libportaudio2` |

---

## Mode D — QR Stream (Phone-Compatible)

Displays a rapid stream of QR codes on screen. Receiver scans with any camera — including a phone.
No special app needed on phone (uses standard QR scan capability).

### Physical Setup

```
  Option 1: Laptop webcam as receiver
  ─────────────────────────────────────────────
        SENDER laptop            RECEIVER laptop
       ┌──────────────┐         ┌──────────────┐
       │  QR CODE     │ ◄─30cm─►│  📷 webcam   │
       │  (animated)  │         │  scanning    │
       └──────────────┘         └──────────────┘

  Option 2: Phone as receiver (most flexible)
  ─────────────────────────────────────────────
        SENDER laptop            Phone / tablet
       ┌──────────────┐         ┌────────┐
       │  QR CODE     │ ◄─30cm─►│  📷   │
       │  (animated)  │         │  cam  │
       └──────────────┘         └────────┘
       (python receive.py)      (or web scanner — Phase 2)
```

**Physical checklist:**
- [ ] Sender screen: **full brightness**
- [ ] QR display fills most of the screen (it does by default)
- [ ] Camera in focus — QR requires sharper focus than pixel mode
- [ ] Distance: 20–40 cm (QR codes need clear detail)
- [ ] Good ambient light on sender screen (no glare)

### Run QR Mode

```bash
# STEP 1 — Receiver first
python receive.py received.zip --mode qr                # cam 0, ACK on
python receive.py received.zip --mode qr --cam 1        # different camera
python receive.py received.zip --mode qr --no-ack       # headless (phone-only)

# STEP 2 — Sender
python send.py myfile.zip --mode qr                     # 3 fps, no ACK
python send.py myfile.zip --mode qr --fps 5             # faster
python send.py myfile.zip --mode qr --fps 5 --ack-cam 0 # QR + ACK feedback

# STEP 3 — Validate
python validate.py myfile.zip received.zip
```

### QR Performance

| FPS | Effective rate | 1 MB | 100 MB | Notes |
|-----|---------------|------|--------|-------|
| `1` | ~2.3 KB/s | 7.5 min | 12.5 hrs | Very slow — only if camera struggles |
| `3` *(default)* | ~6.9 KB/s | 2.5 min | 4.2 hrs | Conservative, reliable |
| `5` | ~11.5 KB/s | 1.5 min | 2.5 hrs | Good camera required |
| `10` | ~23 KB/s | 45 sec | 1.2 hrs | Fast camera + sharp focus |

Each QR frame carries **2,304 bytes** of payload (QR version 40, after RS ECC).

### QR Troubleshooting

| Problem | Fix |
|---------|-----|
| Receiver can't scan QR | Move closer (20–30 cm); increase screen brightness |
| High NACK / decode fail | Reduce `--fps` (camera needs time to focus per frame) |
| `qrcode not installed` | `pip install qrcode[pil]` (outside Walmart network) |
| Phone as receiver | Use `--no-ack` on receive; scan the QR codes manually or use a QR scanning app |

---

## Mode ALL — Simultaneous Channels

Runs Visual + Audio + QR all at once. Each channel transmits the full file independently.
Receiver uses whichever channel it's set up to receive on.

```bash
# Sender: all channels simultaneously
python send.py myfile.zip --mode all --ack-cam 0

# Receiver: choose one channel (or run 3 terminals for all three)
python receive.py recv_visual.zip --mode visual
python receive.py recv_audio.bin  --mode audio
python receive.py recv_qr.zip     --mode qr
```

> **Use case:** Maximum redundancy when environment is uncertain.
> E.g.: noisy room (audio unreliable) + camera glare (visual struggles) → QR succeeds.

---

## Full CLI Reference — v0.3

### `send.py`

```
python send.py <file> [options]

  --mode / -m   STR   Transport: visual(a) audio(c) qr(d) all. Default: visual
  --ack-cam/-a  INT   Camera for ACK detection (all modes). Default: -1 (off)

  Visual mode:
  --block / -b  INT   Pixels per cell (1=fast, 4=robust). Default: 2
  --hold  / -t  INT   Frame hold ms (timer mode only).    Default: 80

  Audio mode:
  --baud        INT   Baud rate: 300, 600, 1200.          Default: 300

  QR mode:
  --fps         INT   QR frames per second (1–10).        Default: 3

Examples:
  python send.py data.zip                           # visual, timer
  python send.py data.zip --ack-cam 0               # visual, ACK
  python send.py data.zip --mode c                  # audio (Option C)
  python send.py data.zip --mode audio --baud 600   # faster audio
  python send.py data.zip --mode d --fps 5          # QR (Option D)
  python send.py data.zip --mode qr --ack-cam 0     # QR + ACK
  python send.py data.zip --mode all                # all channels
```

### `receive.py`

```
python receive.py <output> [options]

  --mode / -m   STR   Transport: visual(a) audio(c) qr(d). Default: visual
  --timeout/-t  INT   Max wait seconds.                   Default: 7200
  --no-ack           Disable ACK window (headless mode).

  Visual / QR:
  --cam   / -c  INT   Camera device index.                Default: 0
  --block / -b  INT   Must match sender's --block.        Default: 2
  --ack-ms      INT   ACK flash duration ms.              Default: 1500

  Audio:
  --baud        INT   Must match sender's --baud.         Default: 300

Examples:
  python receive.py out.zip                         # visual, standard
  python receive.py out.zip --cam 1 --block 2       # visual, other camera
  python receive.py out.bin --mode c                # audio (Option C)
  python receive.py out.bin --mode audio --baud 600 # must match sender
  python receive.py out.zip --mode d                # QR (Option D)
  python receive.py out.zip --mode qr --no-ack      # QR, headless
```

### `validate.py`

```
python validate.py <original> <received>

  Exit 0 = PERFECT (bit-identical)
  Exit 1 = any difference
```

---

## Install

```bash
# Clone
git clone https://gecgithub01.walmart.com/n0j02yt/marichi.git
# or:
git clone https://github.com/SandeepJosyula/marichi.git

cd marichi

# Install all dependencies (do this OUTSIDE Walmart corporate network)
pip install -r requirements.txt

# Or install only what you need:
pip install numpy reedsolo tqdm             # core (all modes)
pip install opencv-python                  # visual + QR modes
pip install sounddevice                    # audio mode only
pip install qrcode[pil]                    # QR sender only
```

---

## Splitting 25 GB Files

```bash
# Split on sender (4 GB chunks)
split -b 4G bigfile.tar.gz chunk_

# Send each chunk — visual mode recommended for 25 GB
python send.py chunk_aa --ack-cam 0
python send.py chunk_ab --ack-cam 0
# ...

# Receive each
python receive.py chunk_aa_recv --cam 0
python receive.py chunk_ab_recv --cam 0
# ...

# Reassemble on receiver
cat chunk_aa_recv chunk_ab_recv ... > bigfile_received.tar.gz

# Final validation
python validate.py bigfile.tar.gz bigfile_received.tar.gz
```

---

## Quick Reference Card

```
ALWAYS START RECEIVER FIRST, THEN SENDER.

═══ MODE A — VISUAL (default, fastest) ════════════════════
RECEIVER:  python receive.py <output> --cam 0 --block 2
SENDER:    python send.py <file>      --ack-cam 0 --block 2

═══ MODE C — AUDIO (speaker/mic, no cameras) ══════════════
RECEIVER:  python receive.py <output> --mode audio
SENDER:    python send.py <file>      --mode audio
⚠️  Baud must match: add --baud 600 on BOTH sides if faster

═══ MODE D — QR STREAM (phone/tablet compatible) ══════════
RECEIVER:  python receive.py <output> --mode qr --cam 0
SENDER:    python send.py <file>      --mode qr --fps 3

═══ VALIDATE (all modes) ══════════════════════════════════
python validate.py <original> <received>

═══ ALL MODES SIMULTANEOUSLY ══════════════════════════════
SENDER:    python send.py <file> --mode all

ACK signals (Visual + QR modes):
  🟢 GREEN  = processing (wait)
  🔵 BLUE   = ACK — sender advances
  🟡 YELLOW = NACK — sender retries

ACK tones (Audio mode):
  500 Hz = ACK — sender advances
  750 Hz = NACK — sender retries

Mode selector shorthand:
  --mode a  = visual (default)
  --mode c  = audio
  --mode d  = qr

Camera list check:
  python -c "import cv2; [print(f'cam {i}:', 'OK' if cv2.VideoCapture(i).isOpened() else 'not found') for i in range(5)]"

Verdict codes (validate.py):
  🟢 PERFECT       → exit 0, zero data loss
  🟡 DEGRADED      → exit 1, < 5% bytes wrong
  🔴 SIZE_MISMATCH → exit 1, frames missed
  🔴 CORRUPT       → exit 1, > 5% bytes wrong
```

---

*MARICHI (मरीचि) v0.3 — "ray of light; a mirage"*
*🌀 Magic applied with Sandeep Josyula's VASS !! 🪄*

---
*Built from VASS knowledge under tutelege of Shri. Sandeep Josyula, and awesomeness!!*
