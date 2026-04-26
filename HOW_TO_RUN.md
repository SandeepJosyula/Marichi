# How to Run MARICHI (मरीचि)  v0.2
**Zero-Loss Visual Modem — with ACK Feedback + Three-Way Checksum**

---

## What's New in v0.2

| Feature | v0.1 | v0.2 |
|---------|------|------|
| Transmission mode | Timer-based cycling | **ACK-driven auto-advance** |
| Checksum | None | **CRC32 in header + independent checksum strip** |
| Feedback | None | **Green / Blue / Yellow ACK window on receiver** |
| Retry on error | Never | **Auto-retry on Yellow (NACK)** |
| Data loss guarantee | ECC only | **ECC + CRC32 + strip + ACK confirmation** |

---

## How the ACK Protocol Works

```
SENDER MACHINE                              RECEIVER MACHINE
┌──────────────────────────────┐           ┌─────────────────────────────┐
│                              │           │                             │
│  ┌────────────────────────┐  │           │  ┌─────────────────────┐   │
│  │  DATA FRAME (full scr) │  │           │  │  CAMERA PREVIEW     │   │
│  │  Frame 47 / 200        │  │ ←──────── │  │  (reading sender)   │   │
│  │  [pixel matrix]        │  │  data cam │  └─────────────────────┘   │
│  │  [checksum strip]      │  │           │                             │
│  └────────────────────────┘  │           │  ┌─────────────────────┐   │
│                              │           │  │  ACK WINDOW         │   │
│  ┌────────────────────────┐  │           │  │                     │   │
│  │  ACK CAMERA PREVIEW    │  │ ──────────→ │  🟢 PROCESSING      │   │
│  │  (watching receiver    │  │  ack cam  │  🔵 BLUE = frame OK   │   │
│  │   ACK window)          │  │           │  🟡 YELLOW = retry    │   │
│  └────────────────────────┘  │           │  └─────────────────────┘   │
└──────────────────────────────┘           └─────────────────────────────┘
```

### Signal Meaning

| Receiver shows | Sender sees | Sender action |
|----------------|-------------|---------------|
| 🟢 **GREEN** | "Processing" | Wait — receiver is decoding |
| 🔵 **BLUE** | "ACK" | ✅ Advance to next frame |
| 🟡 **YELLOW** | "NACK" | ❌ Re-show same frame (retry) |

### Three-Way Checksum Validation

For every frame, the receiver runs **three independent checks**:

```
Frame received
    │
    ├─ 1. ECC decode          → Reed-Solomon corrects camera noise
    │                              (fails → YELLOW immediately)
    │
    ├─ 2. Header CRC32 check  → CRC32(decoded payload) == CRC32 in header
    │                              (mismatch → YELLOW)
    │
    └─ 3. Strip CRC32 check   → CRC32 in checksum strip == header CRC32
                                   (mismatch → YELLOW)

All three pass → 🔵 BLUE ACK sent
Any one fails  → 🟡 YELLOW NACK sent → sender retries
```

The checksum strip is visually encoded as **2 extra rows** at the bottom of every data frame — independent of the main ECC data. It contains the CRC32 × 8 repetitions + RS ECC. Immune to partial row corruption.

---

## Physical Setup

### Dual-Camera Configuration (ACK Mode — Recommended)

```
        ┌───────────────────────────────────────────────────┐
        │                    TOP VIEW                        │
        │                                                    │
        │    SENDER laptop              RECEIVER laptop      │
        │   ┌──────────────┐           ┌──────────────┐    │
        │   │              │ ◄── 30-60cm ──►           │    │
        │   │  DATA FRAME  │           │  ACK WINDOW  │    │
        │   │  on screen   │           │  blue/green/ │    │
        │   │              │           │  yellow flash│    │
        │   └──────────────┘           └──────────────┘    │
        │     📷 webcam A                   📷 webcam B     │
        │    (at top of lid)               (at top of lid)  │
        │    → points at                   → points at      │
        │      receiver screen               sender screen  │
        │                                                    │
        └───────────────────────────────────────────────────┘
```

Both laptop webcams (built into the screen lids) naturally point toward each other when the laptops face each other.

**Physical checklist:**
- [ ] Place laptops facing each other, 30–60 cm apart
- [ ] Sender screen: **full brightness** (F12 or display settings)
- [ ] Receiver's ACK window: large, positioned so sender's webcam sees it clearly
- [ ] Tilt lids slightly toward each other to maximise camera angle
- [ ] Diffuse room lighting (avoid glare/reflections on sender screen)
- [ ] Both cameras in focus (tap to lock autofocus if on phone)

### Single-Camera Setup (Timer Mode — No ACK)

If you only have one camera (receiver reads sender, no ACK feedback):
- Use timer mode: sender cycles through all frames on a fixed interval
- Receiver captures until all frames seen
- No auto-advance, no retry — the sender loops until you stop it

---

## Step-by-Step Run Guide

### STEP 1 — Install (new machine)
```bash
git clone https://gecgithub01.walmart.com/n0j02yt/marichi.git
cd marichi
pip install -r requirements.txt
```

---

### STEP 2 — Start Receiver FIRST

On the **receiver** machine, open a terminal and run:

```bash
# Default: camera 0, block size 2, shows ACK window
python receive.py received_output.zip

# Specify camera and block size
python receive.py received_output.zip --cam 0 --block 2

# Timer mode (no ACK window, headless)
python receive.py received_output.zip --no-ack
```

The receiver opens:
- A small **camera preview window** (what the camera sees)
- A large **ACK window** (green/blue/yellow — starts dark gray "WAITING")

> ⚠️ **Always start the receiver before the sender.** The receiver needs to be ready to display ACK before the sender starts watching.

---

### STEP 3 — Start Sender

On the **sender** machine:

```bash
# ACK mode (recommended — sender watches receiver's ACK window)
python send.py /path/to/myfile.zip --ack-cam 0

# Specify camera index for ACK detection
python send.py /path/to/myfile.zip --ack-cam 1

# Timer mode fallback (no ACK camera)
python send.py /path/to/myfile.zip
```

**What you'll see on sender:**
```
[MARICHI SENDER  v0.2]
  file        : myfile.zip
  size        : 524,288,000 B  (500.00 MB)
  SHA-256     : 3d4f...a9b2
  session     : 7c3a1f9b0e2d4a6c
  frames      : 5690
  ACK mode    : camera 1

[BUILDING 5690 FRAMES — includes CRC32 + checksum strip]
100%|████████████████| 5690/5690 [02:14<00:00]

[SENDER] Opening fullscreen window.
         ACK camera 1 monitoring receiver screen.
         Will auto-advance on BLUE ACK, re-show on YELLOW NACK.
  ✅ Frame    1/5690 ACK'd  (retries=0)
  ✅ Frame    2/5690 ACK'd  (retries=0)
  ❌ Frame    3/5690 NACK  (retry #1)       ← YELLOW from receiver
  ✅ Frame    3/5690 ACK'd  (retries=1)     ← re-shown, now OK
  ✅ Frame    4/5690 ACK'd  (retries=0)
  ...
```

---

### STEP 4 — Watch Progress

**Receiver ACK window colours:**

| Colour | Meaning | Duration |
|--------|---------|---------|
| 🔘 Dark gray | Waiting for first frame | Until first decode |
| 🟢 **Solid green** | Decoding in progress | Continuous while scanning |
| 🔵 **Solid blue** | Frame verified — ACK sent | 1.5 seconds (configurable) |
| 🟡 **Solid yellow** | Checksum failed — NACK | 1.5 seconds |

**Progress bar on receiver:**
```
Receiving:  47%|█████████████▌               | 2675/5690 [08:32<09:45]
```

---

### STEP 5 — Completion

**Receiver** prints:
```
[RECEIVER] All 5690 frames received!
[ASSEMBLING]  5690/5690 frames ...
[ASSEMBLED]  524,288,000 B → received_output.zip
             SHA-256     : 3d4f...a9b2
             Frames recv : 5690/5690
             Cksum fails : 0
             Status      : ✅ COMPLETE

✅  Received: received_output.zip
    Validate: python validate.py <original> received_output.zip
```

**Sender** screen turns solid **GREEN** "TRANSFER COMPLETE" for 3 seconds, then closes.

---

### STEP 6 — Validate

```bash
python validate.py /path/to/original/myfile.zip received_output.zip
```

**Perfect output:**
```
════════════════════════════════════════════════════════════
  MARICHI VALIDATION REPORT
════════════════════════════════════════════════════════════
  Original size      : 524,288,000 bytes
  Received size      : 524,288,000 bytes
  Size check         : ✅ MATCH

  Original SHA-256   : 3d4f...a9b2
  Received SHA-256   : 3d4f...a9b2
  Hash check         : ✅ MATCH

  Differences        : NONE ✅

  VERDICT            : 🟢  PERFECT
════════════════════════════════════════════════════════════
```

Exit code `0` = zero data loss confirmed.

---

## CLI Reference — v0.2

### `send.py`
```
python send.py <file> [options]

  --block / -b  INT   Pixels per cell (1=fast, 4=robust). Default: 2
  --hold  / -t  INT   Frame hold ms in timer mode.       Default: 80
  --ack-cam/-a  INT   Camera index for ACK detection.    Default: -1 (disabled)

Examples:
  python send.py data.zip                        # timer mode, no ACK
  python send.py data.zip --ack-cam 0            # ACK via camera 0
  python send.py data.zip --block 1 --ack-cam 1  # high-speed + ACK cam 1
  python send.py data.zip --block 4              # robust timer mode
```

### `receive.py`
```
python receive.py <output> [options]

  --cam    / -c  INT   Camera device index.              Default: 0
  --block  / -b  INT   Must match sender's --block.      Default: 2
  --timeout/ -t  INT   Max wait seconds.                 Default: 7200
  --ack-ms        INT   ACK flash duration ms.           Default: 1500
  --no-ack             Disable ACK window (headless).

Examples:
  python receive.py out.zip                      # standard
  python receive.py out.zip --cam 1 --block 2    # different camera
  python receive.py out.zip --ack-ms 2000        # slower ACK for older cameras
  python receive.py out.zip --no-ack             # headless / terminal only
```

### `validate.py`
```
python validate.py <original> <received>

  Exit 0 = PERFECT (bit-identical)
  Exit 1 = any difference
```

---

## Troubleshooting

| Problem | Cause | Fix |
|---------|-------|-----|
| Sender stuck on same frame | ACK cam not seeing BLUE | Position ACK window closer to sender cam |
| All frames show YELLOW | Camera too far / block too small | Use `--block 4`, improve lighting |
| Receiver sees no frames | Camera not on sender screen | Move closer; try `--cam 1` |
| NACK loop (never ACKs) | Lighting / angle issue | Adjust sender screen brightness or camera angle |
| ACK cam false positives | Room has blue/yellow lights | Dim ambient lights; close blinds |
| `Cannot open camera` | Wrong index | Run camera list check below |
| Sender times out (30s) | ACK too slow | Reduce `--ack-ms` on receiver |

**List cameras:**
```bash
python -c "import cv2; [print(f'cam {i}:', 'OK' if cv2.VideoCapture(i).isOpened() else 'not found') for i in range(5)]"
```

---

## Splitting 25 GB Files

```bash
# Split on sender
split -b 4G bigfile.tar.gz chunk_

# Send each
python send.py chunk_aa --ack-cam 0
python send.py chunk_ab --ack-cam 0
# ...

# Receive each
python receive.py chunk_aa_recv --cam 0
python receive.py chunk_ab_recv --cam 0
# ...

# Reassemble
cat chunk_aa_recv chunk_ab_recv ... > bigfile_received.tar.gz

# Final validation
python validate.py bigfile.tar.gz bigfile_received.tar.gz
```

---

## Performance Estimates

| Block | ACK Mode | Effective | 25 GB |
|-------|----------|-----------|-------|
| `--block 1` | ACK | ~3–5 MB/s | 1.5–2.5 hrs |
| `--block 2` *(default)* | ACK | ~1–2 MB/s | 3.5–7 hrs |
| `--block 2` | Timer | ~1.9 MB/s | ~3.7 hrs |
| `--block 4` | ACK | ~400 KB/s | ~18 hrs |

> ACK mode is slightly slower than timer mode because it waits for each frame to be confirmed before advancing. But it guarantees **zero data loss** — every byte is confirmed before the sender moves on.

---

## Quick Reference Card

```
ALWAYS START RECEIVER FIRST, THEN SENDER.

RECEIVER:  python receive.py <output>  --cam 0 --block 2
SENDER:    python send.py    <file>    --ack-cam 0 --block 2
VALIDATE:  python validate.py <original> <received>

Block rule: --block on receiver MUST equal --block on sender.

ACK signals:
  🟢 GREEN  = processing (wait)
  🔵 BLUE   = ACK — sender advances
  🟡 YELLOW = NACK — sender retries same frame

Checksum: 3-way verification per frame
  1. Reed-Solomon ECC decode
  2. CRC32(payload) == header CRC32
  3. CRC32(payload) == checksum strip CRC32
All three must pass for BLUE. Any failure = YELLOW.

Verdict codes:
  🟢 PERFECT       → exit 0, zero data loss
  🟡 DEGRADED      → exit 1, < 5% bytes wrong
  🔴 SIZE_MISMATCH → exit 1, frames missed
  🔴 CORRUPT       → exit 1, > 5% bytes wrong
```

---

*MARICHI (मरीचि) v0.2 — "ray of light; a mirage"*
*🌀 Magic applied with Sandeep Josyula's VASS !! 🪄*

---
*Built from VASS knowledge under tutelege of Shri. Sandeep Josyula, and awesomeness!!*
