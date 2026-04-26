# MARICHI (मरीचि)
**Zero-Loss Visual Modem — Air-Gap Data Transfer via Camera**

> Encode any file as full-screen colour pixel frames → capture with camera → decode with zero data loss.

---

## Quick Start

### Install
```bash
pip install opencv-python numpy reedsolo tqdm Pillow
# or:
pip install -r requirements.txt
```

### Send (source laptop — full-screen display)
```bash
python send.py /path/to/file.zip
```

### Receive (target laptop or phone — camera at screen)
```bash
python receive.py received_file.zip --cam 0
```

### Validate (compare original vs received)
```bash
python validate.py /path/to/file.zip received_file.zip
```

Zero data loss → exit code 0 + `PERFECT` verdict.

---

## How It Works

```
SOURCE LAPTOP                           TARGET LAPTOP / PHONE
┌─────────────────────┐                ┌─────────────────────────┐
│  send.py            │                │  receive.py             │
│  1. Read file       │  Camera aims   │  1. Open camera         │
│  2. Split → frames  │ ─────────────► │  2. Capture BGR frames  │
│  3. ECC encode      │  at screen     │  3. Detect corners      │
│  4. Pixel-map cells │                │  4. Perspective warp    │
│  5. Full-screen     │                │  5. Cell → byte decode  │
│     display loop    │                │  6. ECC correct         │
│                     │                │  7. Reassemble file     │
└─────────────────────┘                └─────────────────────────┘
                                       Then:
                                         python validate.py orig recv
```

---

## Frame Anatomy

```
┌──────────────────────────────────────────────────────────────┐
│  BORDER (black, 3 cells)                                      │
│  ┌──────┬─────────────────────────────────────┬──────┐       │
│  │ MK◄──┤   HEADER ROWS (session metadata)    │──►MK │       │
│  │  TL  ├─────────────────────────────────────┤  TR  │       │
│  │      │                                     │      │       │
│  │      │         DATA CELLS                  │      │       │
│  │      │   (ECC-encoded payload, 2 bits/cell)│      │       │
│  │ MK   │                                     │  MK  │       │
│  │  BL  └─────────────────────────────────────┘  BR  │       │
│  └──────┴─────────────────────────────────────┴──────┘       │
└──────────────────────────────────────────────────────────────┘
```

- **4 colours** (Black, White, Red, Blue) = 2 bits per cell
- **Reed-Solomon ECC** per 128-byte chunk — corrects up to 16 byte errors
- **Corner markers** = QR-style finder patterns for perspective correction
- **Header** = session ID + frame number + total frames + payload length

---

## Performance

| Block size | Theoretical | Practical | 25 GB ETA |
|------------|-------------|-----------|-----------|
| `--block 1` | 7.6 MB/s   | ~3–5 MB/s | ~1.5–2.5 hrs |
| `--block 2` *(default)* | 1.9 MB/s | ~1–2 MB/s | ~3.5–7 hrs |
| `--block 4` | 0.5 MB/s   | ~400 KB/s | ~18 hrs |

> Tip: Use `--block 1` with a good 1080p camera in good lighting for maximum speed.

---

## CLI Reference

### `send.py`
```
python send.py <file> [options]

Options:
  --block / -b  INT   Pixels per cell (1=fast, 4=robust). Default: 2
  --hold  / -t  INT   Frame hold ms (lower=faster). Default: 80
```

### `receive.py`
```
python receive.py <output> [options]

Options:
  --cam     / -c  INT   Camera device index. Default: 0
  --block   / -b  INT   Must match sender's --block. Default: 2
  --timeout / -t  INT   Max seconds. Default: 7200
```

### `validate.py`
```
python validate.py <original> <received>

Exit codes:
  0 = PERFECT (bit-identical)
  1 = differences found
```

---

## Tips for Best Results

1. **Lighting**: Even, diffuse light. Avoid reflections on sender screen.
2. **Alignment**: Camera should face screen directly (tilt < 30°). Perspective correction handles minor angles.
3. **Distance**: Fill the camera frame with the sender screen. 30–60 cm is ideal.
4. **Screen brightness**: Sender screen at full brightness.
5. **Camera focus**: Ensure autofocus locks onto the screen.
6. **Mobile phone**: Use rear camera (higher resolution than front).

---

## For Mobile Receivers

The Python `receive.py` script can run on any laptop. For mobile:
- Install Python via **Termux** (Android) or use the iPhone **Pythonista** app
- Or: a browser-based PWA receiver is planned for Phase 2

---

*MARICHI (मरीचि) — "ray of light; a mirage"*
*🌀 Magic applied with Sandeep Josyula's VASS !! 🪄*

---
*Built from VASS knowledge under tutelege of Shri. Sandeep Josyula, and awesomeness!!*
