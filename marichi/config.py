"""
MARICHI — Frame geometry, colour palette, ECC parameters.
All tunable constants live here.
"""
import numpy as np

# ── Display resolution (sender screen) ────────────────────────────────────────
SCREEN_W = 1920
SCREEN_H = 1080

# ── Data cell size (screen pixels per logical cell) ───────────────────────────
# 2 = good balance of speed vs camera robustness (~1.9 MB/s at 20 fps)
# 1 = maximum speed (~7.6 MB/s) — requires high-quality camera
# 4 = maximum robustness — slower
BLOCK_SIZE = 2

CELLS_X = SCREEN_W // BLOCK_SIZE   # 960
CELLS_Y = SCREEN_H // BLOCK_SIZE   # 540

# ── 4-colour palette (2 bits per cell) ───────────────────────────────────────
# Stored as BGR for OpenCV. Chosen for maximum separation in colour space.
PALETTE_BGR = np.array([
    [  0,   0,   0],   # 0 = Black
    [255, 255, 255],   # 1 = White
    [  0,   0, 220],   # 2 = Red   (BGR order)
    [220,   0,   0],   # 3 = Blue  (BGR order)
], dtype=np.uint8)

BITS_PER_CELL = 2   # log2(4)

# ── Frame layout geometry (in cells) ─────────────────────────────────────────
#
#  ┌──────────────────────────────────────────────────────────────────┐
#  │ BORDER (BLACK)                                                    │
#  │  ┌────────────────────────────────────────────────────────────┐  │
#  │  │ MK_TL │         HEADER ROWS (metadata)          │ MK_TR │  │  │
#  │  │       ├────────────────────────────────────────┤        │  │  │
#  │  │       │                                        │        │  │  │
#  │  │       │         DATA CELLS                     │        │  │  │
#  │  │       │                                        │        │  │  │
#  │  │ MK_BL │                                        │ MK_BR  │  │  │
#  │  └────────────────────────────────────────────────────────────┘  │
#  └──────────────────────────────────────────────────────────────────┘

BORDER      = 3     # silent black outer border (cells)
MARKER_SIZE = 15    # corner finder marker side  (cells) — QR-style
HEADER_ROWS = 4     # cell-rows at top for session metadata

# Data region bounds (in cells, excluding border + markers + header)
DATA_X0 = BORDER + MARKER_SIZE
DATA_X1 = CELLS_X - BORDER - MARKER_SIZE
DATA_Y0 = BORDER + MARKER_SIZE + HEADER_ROWS
DATA_Y1 = CELLS_Y - BORDER - MARKER_SIZE

DATA_COLS  = DATA_X1 - DATA_X0   # 960 - 3 - 15 - 3 - 15 = 924
DATA_ROWS  = DATA_Y1 - DATA_Y0   # 540 - 3 - 15 - 4 - 3 - 15 = 500
DATA_CELLS = DATA_COLS * DATA_ROWS

# Raw byte capacity of data region per frame
BYTES_RAW_PER_FRAME = (DATA_CELLS * BITS_PER_CELL) // 8

# ── Reed-Solomon ECC ──────────────────────────────────────────────────────────
ECC_NSYM  = 32    # parity bytes per chunk (corrects up to 16 byte-errors)
CHUNK_RAW = 128   # raw data bytes per RS chunk
CHUNK_ENC = CHUNK_RAW + ECC_NSYM   # 160 bytes encoded

# How many full ECC chunks fit in the data region?
N_ECC_CHUNKS      = BYTES_RAW_PER_FRAME // CHUNK_ENC
PAYLOAD_PER_FRAME = N_ECC_CHUNKS * CHUNK_RAW   # effective payload bytes

# ── Header (fits in HEADER_ROWS above data, same DATA_COLS width) ─────────────
# Layout (24 bytes flat):
#   4B magic | 8B session_id | 4B frame_no | 4B total_frames | 4B payload_len
HEADER_MAGIC   = b'\xCA\xFE\xBA\xBE'
HEADER_BYTES   = 24   # total header size

# ── Timing ────────────────────────────────────────────────────────────────────
FRAME_HOLD_MS = 80    # ms to display each frame (≈12.5 fps)
CAM_INDEX     = 0     # camera device index for receiver

# ── Derived at import time ────────────────────────────────────────────────────
def print_stats() -> None:
    rate_bps = (PAYLOAD_PER_FRAME * 1000) / FRAME_HOLD_MS
    print(f"MARICHI config  block={BLOCK_SIZE}px  cells={CELLS_X}×{CELLS_Y}")
    print(f"  data region : {DATA_COLS}×{DATA_ROWS} = {DATA_CELLS:,} cells")
    print(f"  raw/frame   : {BYTES_RAW_PER_FRAME:,} B")
    print(f"  ECC chunks  : {N_ECC_CHUNKS} × {CHUNK_RAW}B raw → {CHUNK_ENC}B enc")
    print(f"  payload/frame: {PAYLOAD_PER_FRAME:,} B  ({PAYLOAD_PER_FRAME/1024:.1f} KB)")
    print(f"  at {1000//FRAME_HOLD_MS} fps  → {rate_bps/1024/1024:.2f} MB/s theoretical")
    hrs_25gb = (25 * 1024**3) / rate_bps / 3600
    print(f"  25 GB ETA   : {hrs_25gb:.1f} hours")
