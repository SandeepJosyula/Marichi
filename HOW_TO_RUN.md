# How to Run MARICHI (मरीचि)
**Zero-Loss Visual Modem — Step-by-Step Operational Guide**

---

## What Is Happening Here

You have two machines. No cable. No network. Just two laptops facing each other.

```
  OLD LAPTOP (Sender)                    NEW LAPTOP / PHONE (Receiver)
  ┌─────────────────────┐                ┌─────────────────────────────┐
  │                     │                │                             │
  │  ██▓░▒█▓░█▒░▓▓█▒░  │◄──────────────│  📷 Camera points at screen │
  │  ▓░▒██▒░▓█░▒░▓█▒█  │   Camera       │                             │
  │  ░▓█▒░██▓░▒█▒░▒█▓  │   captures     │  receive.py captures,       │
  │  ▒░░▓▒█░▓▒█░▓█░▒▓  │   the screen   │  decodes, reassembles file  │
  │                     │                │                             │
  │  send.py displays   │                └─────────────────────────────┘
  │  coloured pixel     │
  │  frames in a loop   │
  └─────────────────────┘
```

The sender screen becomes a **visual modem**. Each full-screen frame carries ~90 KB of data encoded as coloured pixel cells. The receiver's camera reads those frames in real time and reassembles the original file, **bit for bit**.

---

## Prerequisites

### Both Machines
- Python 3.9 or 3.10+
- The `marichi` repo cloned

### Install (run once, outside Walmart network)
```bash
git clone https://gecgithub01.walmart.com/n0j02yt/marichi.git
cd marichi
pip install -r requirements.txt
```

**Packages installed:**
| Package | Purpose |
|---------|---------|
| `opencv-python` | Screen display + camera capture + image processing |
| `numpy` | Fast pixel array operations |
| `reedsolo` | Reed-Solomon error correction (corrects camera noise) |
| `tqdm` | Progress bars |
| `Pillow` | Image utilities |

---

## Part 1 — SENDER SETUP (Old Laptop)

### Step 1.1 — Prepare your file

Any file works: `.zip`, `.tar.gz`, a single binary, anything.

```bash
# Optional: zip multiple files into one bundle first
zip -r mydata.zip /path/to/folder/
```

### Step 1.2 — Run the sender

```bash
cd /path/to/marichi

python send.py /path/to/mydata.zip
```

**What happens:**
1. File is read and its SHA-256 is printed — **write this down** for validation later
2. All frames are pre-built in memory (this takes 1–3 minutes for large files)
3. A fullscreen black window opens on your screen
4. Coloured pixel frames begin cycling on the screen in a loop

**Example output:**
```
[MARICHI SENDER]
  file       : mydata.zip
  size       : 1,048,576 bytes (1.00 MB)
  SHA-256    : a3f2c19d...e8b7
  session    : 4f9a2b1c3d0e5f6a
  frames     : 12
  payload/fr : 92,288 B

[BUILDING 12 FRAMES ...]
100%|████████████████| 12/12 [00:04<00:00]
[BUILD COMPLETE]

[SENDER] Opening fullscreen display window.
         Press  Q  to quit.
         Cycling through 12 frames at ~12 fps.

[CYCLE    1]  elapsed=2s  effective=1.04 MB/s
[CYCLE    2]  elapsed=4s  effective=1.07 MB/s
```

The sender **keeps looping** until you press `Q`. It will loop thousands of times if needed — the receiver just needs to capture each unique frame once.

### Step 1.3 — Speed settings

| Use case | Command | Speed |
|----------|---------|-------|
| Default (balanced) | `python send.py file.zip` | ~1.1 MB/s |
| Fast (needs good camera) | `python send.py file.zip --block 1` | ~4.4 MB/s |
| Robustness (noisy camera) | `python send.py file.zip --block 4` | ~280 KB/s |
| Faster frame rate | `python send.py file.zip --hold 50` | ~1.8 MB/s |

> `--block` = pixels per coloured cell. Smaller = more data per frame but harder for camera to read.
> `--hold` = milliseconds per frame. Lower = faster cycling.

---

## Part 2 — PHYSICAL SETUP

This is the most important part. Get this right and everything else follows.

### Camera Distance and Angle

```
                    ←—— 30–60 cm ——→

  ┌──────────────┐                 ┌─────────┐
  │  SENDER      │                 │ RECEIVER│
  │  SCREEN      │                 │         │
  │              │                 │  📷     │
  │  (full       │◄────────────────│  Camera │
  │   brightness)│  Camera sees    │         │
  │              │  entire screen  │         │
  └──────────────┘                 └─────────┘
          ↑
   Screen tilted slightly
   toward camera if needed
```

**Checklist:**
- [ ] Sender screen at **full brightness** (F12 / max brightness)
- [ ] Camera fills its view with the sender screen (screen should be ~80% of camera frame)
- [ ] Camera aimed **straight on** — angle < 30° tilt (perspective correction handles minor tilt)
- [ ] **No strong light source behind the camera** (avoid window glare on sender screen)
- [ ] Room lighting: **even, diffuse** — avoid spotlights on the screen
- [ ] Camera should **not** be autofocusing in and out — tap to focus and lock if on phone

### If Using a Phone as Receiver
- Use the **rear camera** (higher resolution than front)
- Hold phone in **landscape mode**
- Use a stand or prop the phone so it is stable
- Ensure camera autofocus has locked before starting `receive.py`

---

## Part 3 — RECEIVER SETUP (New Laptop / Phone)

### Step 3.1 — Run the receiver

Open a terminal on the **target machine** (the one with the camera pointed at the sender screen):

```bash
cd /path/to/marichi

python receive.py received_output.zip
```

**What happens:**
1. Camera opens
2. A small live preview window appears showing what the camera sees
3. The receiver scans every camera frame looking for MARICHI pixel patterns
4. When it detects the first valid frame, it prints the session ID and total frame count
5. Progress bar shows frames being received
6. When all frames are captured, file is assembled and SHA-256 is printed

**Example output:**
```
[MARICHI RECEIVER]
  output   : received_output.zip
  camera   : device 0
  timeout  : 7200s

[RECEIVER] Camera open. Scanning for MARICHI frames ...
           Aim camera at sender screen.
           Press  Q  to abort.

[SESSION]  id=4f9a2b1c3d0e5f6a  total_frames=12
Receiving:  42%|████████████▌         | 5/12 [00:08<00:09]

[RECEIVER] All 12 frames received!

[ASSEMBLING]  12 frames ...
[ASSEMBLED]  1,048,576 bytes → received_output.zip
             SHA-256: a3f2c19d...e8b7
             Frames received: 12/12
             ✅ All frames received — run validator for final confirmation

✅ File saved: received_output.zip
   Run:  python validate.py <original> received_output.zip
```

### Step 3.2 — If using a different camera device

```bash
# List available cameras (0, 1, 2, ...)
python -c "import cv2; [print(f'Camera {i}: OK' if cv2.VideoCapture(i).isOpened() else f'Camera {i}: not found') for i in range(5)]"

# Use camera 1
python receive.py output.zip --cam 1
```

### Step 3.3 — block size must match sender

> ⚠️ **Critical:** The `--block` value on the receiver MUST match the sender.

```bash
# Sender ran with --block 1 ?  → Receiver must also use --block 1
python send.py file.zip --block 1
python receive.py out.zip --block 1   # same block size!
```

---

## Part 4 — VALIDATOR (Zero-Loss Verification)

Run this on **either machine** after the transfer completes.

```bash
python validate.py /path/to/original/mydata.zip received_output.zip
```

**Perfect transfer output:**
```
════════════════════════════════════════════════════════════
  MARICHI VALIDATION REPORT
════════════════════════════════════════════════════════════
  Original           : /path/to/mydata.zip
  Received           : received_output.zip

  Original size      : 1,048,576 bytes
  Received size      : 1,048,576 bytes
  Size check         : ✅ MATCH

  Original SHA-256   : a3f2c19d...e8b7
  Received SHA-256   : a3f2c19d...e8b7
  Hash check         : ✅ MATCH

  Differences        : NONE ✅

  VERDICT            : 🟢  PERFECT
════════════════════════════════════════════════════════════
```

Exit code `0` = PERFECT. Use in scripts:
```bash
python validate.py original.zip received.zip && echo "Transfer verified" || echo "DATA LOSS DETECTED"
```

**If transfer was incomplete:**
```
  VERDICT            : 🔴  SIZE_MISMATCH
```
→ Some frames were missed. Re-run receive.py (sender is still looping).

**If transfer has errors:**
```
  First diff offset  : byte 1,048,234  (0x000FFEDA)
  Byte differences   : 42
  Bit  differences   : 163
  Corruption %       : 0.000040%
  VERDICT            : 🟡  DEGRADED
```
→ ECC corrected most errors but a few slipped through. Decrease `--block` size or improve lighting and retry.

---

## Part 5 — FULL WALKTHROUGH EXAMPLE

### Transferring a 500 MB file

**On old laptop (sender):**
```bash
cd ~/IdeaProjects/marichi
python send.py ~/backup.tar.gz --block 2 --hold 80
# → Prints SHA-256: abc123...
# → Opens fullscreen display, starts cycling frames
```

**On new laptop (receiver) — camera pointed at old screen:**
```bash
cd ~/marichi
python receive.py ~/received_backup.tar.gz --cam 0 --block 2
# → Waits, detects session, starts capturing...
# → Shows progress: 3341/3341 frames (100%)
# → Assembles file, prints SHA-256: abc123...
```

**Validate on either machine:**
```bash
python validate.py ~/backup.tar.gz ~/received_backup.tar.gz
# → VERDICT: 🟢 PERFECT
```

---

## Part 6 — TIMING ESTIMATES

| File Size | block=2 (default) | block=1 (fast) |
|-----------|-------------------|----------------|
| 100 MB | ~2 min | ~40 sec |
| 1 GB | ~16 min | ~6.5 min |
| 10 GB | ~2.7 hrs | ~65 min |
| 25 GB | ~6.5 hrs | ~2.7 hrs |

> These are **theoretical** at 12 fps. Practical speed depends on camera quality and lighting.
> The sender loops continuously — receiver just needs to capture each frame once.
> You don't need to watch it. Start both, let them run, come back when done.

---

## Part 7 — TROUBLESHOOTING

| Problem | Likely Cause | Fix |
|---------|-------------|-----|
| Receiver sees no frames | Camera not seeing screen / wrong device | Move camera closer; try `--cam 1` |
| Many decode failures | Block size too small for camera | Use `--block 4` for robustness |
| Progress stalls at N frames | Some frames hard to read | Wait — sender loops forever, receiver will eventually get them |
| SIZE_MISMATCH after receive | Session interrupted early | Re-run `receive.py` — it will start a new session |
| DEGRADED verdict | A few uncorrected errors | Re-transfer just the corrupted blocks (feature: Phase 2) |
| Screen glare | Overhead light reflecting | Draw blinds / move to darker room |
| Out of memory on large files | 25 GB file fully loaded in RAM | Split with `split -b 4G bigfile.tar.gz chunk_` and send in parts |

---

## Part 8 — SPLITTING LARGE FILES (for 25 GB)

Sending 25 GB in one go requires ~25 GB of RAM to pre-build all frames. Split into chunks:

```bash
# On sender — split 25 GB into 4 GB chunks
split -b 4G bigfile.tar.gz chunk_

# Send each chunk one by one
python send.py chunk_aa
python send.py chunk_ab
# ...

# On receiver — receive each chunk
python receive.py chunk_aa_recv
python receive.py chunk_ab_recv
# ...

# Reassemble on receiver
cat chunk_aa_recv chunk_ab_recv chunk_ac_recv ... > bigfile_received.tar.gz

# Validate the whole thing
python validate.py bigfile.tar.gz bigfile_received.tar.gz
```

---

## Quick Reference Card

```
SENDER   →   python send.py    <file>          [--block 1|2|4]  [--hold 50|80|120]
RECEIVER →   python receive.py <output>        [--cam 0|1]      [--block SAME_AS_SENDER]
VALIDATE →   python validate.py <orig> <recv>

Block size guide:
  --block 1  →  fastest, needs 1080p camera, good lighting
  --block 2  →  default, balanced, works with most laptop cameras
  --block 4  →  slowest, most robust, for challenging conditions

Verdict codes:
  🟢 PERFECT        → exit 0, zero data loss, transfer complete
  🟡 DEGRADED       → exit 1, < 5% bytes wrong, re-transfer recommended
  🔴 SIZE_MISMATCH  → exit 1, frames missed, re-run receiver
  🔴 CORRUPT        → exit 1, > 5% bytes wrong, re-transfer
```

---

*MARICHI (मरीचि) v0.1 — "ray of light; a mirage"*
*🌀 Magic applied with Sandeep Josyula's VASS !! 🪄*

---
*Built from VASS knowledge under tutelege of Shri. Sandeep Josyula, and awesomeness!!*
