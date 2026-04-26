"""
MARICHI — Frame geometry, colour palette, ECC parameters, ACK system constants.
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
#  │ BORDER (BLACK, 3 cells)                                          │
#  │  ┌──────┬─────────────────────────────────────────────┬──────┐  │
#  │  │ MK   │     HEADER ROWS (metadata, 4 rows)          │  MK  │  │
#  │  │  TL  ├─────────────────────────────────────────────┤  TR  │  │
#  │  │      │     DATA CELLS (ECC-encoded payload)        │      │  │
#  │  │      ├─────────────────────────────────────────────┤      │  │
#  │  │      │     CHECKSUM STRIP (CRC32 × redundant)      │      │  │
#  │  │ MK   │                                             │  MK  │  │
#  │  │  BL  └─────────────────────────────────────────────┘  BR  │  │
#  └──────────────────────────────────────────────────────────────────┘

BORDER         = 3    # silent black outer border (cells)
MARKER_SIZE    = 15   # corner finder marker side  (cells)
HEADER_ROWS    = 4    # cell-rows reserved for session metadata
CHECKSUM_ROWS  = 2    # cell-rows at bottom of data area for CRC32 strip

# Full data+checksum region bounds (in cells)
DATA_X0 = BORDER + MARKER_SIZE            # 18
DATA_X1 = CELLS_X - BORDER - MARKER_SIZE  # 942
DATA_Y0 = BORDER + MARKER_SIZE + HEADER_ROWS  # 22
DATA_Y1 = CELLS_Y - BORDER - MARKER_SIZE  # 522

DATA_COLS = DATA_X1 - DATA_X0   # 924

# ─ Checksum strip (bottom CHECKSUM_ROWS of data region) ─
CKSUM_Y0  = DATA_Y1 - CHECKSUM_ROWS   # 520  (in cells)
CKSUM_Y1  = DATA_Y1                   # 522

# ─ Actual ECC data area (above checksum strip) ─
DATA_ROWS  = DATA_Y1 - DATA_Y0 - CHECKSUM_ROWS  # 498
DATA_CELLS = DATA_COLS * DATA_ROWS               # 924 × 498 = 460,152

BYTES_RAW_PER_FRAME = (DATA_CELLS * BITS_PER_CELL) // 8  # 115,038

# ── Reed-Solomon ECC ──────────────────────────────────────────────────────────
ECC_NSYM  = 32    # parity bytes per chunk (corrects up to 16 byte-errors)
CHUNK_RAW = 128   # raw data bytes per RS chunk
CHUNK_ENC = CHUNK_RAW + ECC_NSYM   # 160 bytes encoded

N_ECC_CHUNKS      = BYTES_RAW_PER_FRAME // CHUNK_ENC   # 718
PAYLOAD_PER_FRAME = N_ECC_CHUNKS * CHUNK_RAW            # 91,904 bytes

# ── Header (28 bytes flat) ────────────────────────────────────────────────────
# Layout:
#   4B magic | 8B session_id | 4B frame_no | 4B total_frames |
#   4B payload_len | 4B crc32_payload
HEADER_MAGIC  = b'\xCA\xFE\xBA\xBE'
HEADER_BYTES  = 28   # expanded from 24 to include CRC32

# ── Checksum strip encoding ───────────────────────────────────────────────────
# Encodes: crc32(4B) + frame_no(4B) + total_frames(4B) = 12 bytes
# Repeated 8× for redundancy + 16B RS ECC = 112 bytes total
# Capacity: 924 cols × 2 rows × 2 bits = 462 bytes  (plenty)
CKSUM_PAYLOAD_BYTES = 12          # crc32 + frame_no + total_frames
CKSUM_REPEAT        = 8           # repetitions for robustness
CKSUM_ECC_NSYM      = 16          # RS parity for checksum strip
CKSUM_BLOCK_RAW     = CKSUM_PAYLOAD_BYTES * CKSUM_REPEAT   # 96 bytes
CKSUM_BLOCK_ENC     = CKSUM_BLOCK_RAW + CKSUM_ECC_NSYM    # 112 bytes

# ── ACK system ────────────────────────────────────────────────────────────────
#
# RECEIVER screen signals (sender's camera reads these):
#   GREEN  = currently processing / decoding frame
#   BLUE   = frame decoded + checksum verified  → sender advances
#   YELLOW = decode failed / checksum mismatch  → sender re-shows frame
#
# Sender uses a second camera (--ack-cam N) pointed at receiver's ACK window.

ACK_CAM_INDEX  = -1      # -1 = ACK detection disabled (time-based fallback)
ACK_SIGNAL_MS  = 1500    # how long ACK color is displayed (ms)

# ACK display colors (BGR for OpenCV)
ACK_COLOR_GREEN  = (0,   200,   0)   # processing
ACK_COLOR_BLUE   = (220,  60,   0)   # success / ACK
ACK_COLOR_YELLOW = (0,   220, 220)   # error  / NACK

# ACK detection HSV ranges (H: 0-179, S: 0-255, V: 0-255)
# These are what the SENDER'S camera must recognise on the RECEIVER'S screen
ACK_HSV_GREEN  = ((40,  80, 80), (80,  255, 255))
ACK_HSV_BLUE   = ((100, 80, 80), (130, 255, 255))
ACK_HSV_YELLOW = ((20,  80, 80), (35,  255, 255))

ACK_MIN_COVERAGE = 0.20   # at least 20% of camera frame must be ACK colour

# ── Timing ────────────────────────────────────────────────────────────────────
FRAME_HOLD_MS = 80    # ms to display each frame in time-based mode (≈12.5 fps)
CAM_INDEX     = 0     # receiver's camera device index

# ── Derived stats helper ──────────────────────────────────────────────────────
def print_stats() -> None:
    rate_bps = (PAYLOAD_PER_FRAME * 1000) / FRAME_HOLD_MS
    print(f"  config    : block={BLOCK_SIZE}px  cells={CELLS_X}×{CELLS_Y}")
    print(f"  data grid : {DATA_COLS}×{DATA_ROWS} cells (+ {CHECKSUM_ROWS} cksum rows)")
    print(f"  payload   : {PAYLOAD_PER_FRAME:,} B/frame  ({N_ECC_CHUNKS} ECC chunks)")
    print(f"  rate est  : {rate_bps/1024/1024:.2f} MB/s @ {1000//FRAME_HOLD_MS} fps")
    print(f"  25 GB ETA : {(25*1024**3)/rate_bps/3600:.1f} hrs")
