"""
MARICHI — Core frame encoder / decoder  (v0.2 — with CRC32 + checksum strip)

encode_frame_fast(payload, frame_no, total_frames, session_id) → BGR np.ndarray
decode_frame(bgr_img) → (payload, frame_no, total_frames, session_id, crc_ok) | None
"""

from __future__ import annotations
import struct
import zlib
import numpy as np
import cv2
import reedsolo

from . import config as C

# ── Reed-Solomon codecs ───────────────────────────────────────────────────────
_rs_data  = reedsolo.RSCodec(C.ECC_NSYM)          # for main data chunks
_rs_cksum = reedsolo.RSCodec(C.CKSUM_ECC_NSYM)    # for checksum strip


# ═══════════════════════════════════════════════════════════════════════════════
#  LOW-LEVEL: bytes ↔ cell array ↔ BGR pixel image
# ═══════════════════════════════════════════════════════════════════════════════

def _bytes_to_cells(data: bytes, n_cells: int) -> np.ndarray:
    """Pack bytes into 1-D array of colour indices (0-3, 2 bits each). Zero-pads."""
    bits_needed = n_cells * C.BITS_PER_CELL
    n_bytes = (bits_needed + 7) // 8
    padded = (data + b'\x00' * n_bytes)[:n_bytes]
    arr  = np.frombuffer(padded, dtype=np.uint8)
    bits = np.unpackbits(arr)[:bits_needed].reshape(-1, 2)
    return (bits[:, 0] << 1 | bits[:, 1]).astype(np.uint8)


def _cells_to_bytes(cells: np.ndarray) -> bytes:
    """Unpack 1-D colour-index array (0-3) → bytes."""
    flat = cells.flatten()
    bits = np.zeros(len(flat) * 2, dtype=np.uint8)
    bits[0::2] = (flat >> 1) & 1
    bits[1::2] =  flat       & 1
    pad  = (8 - len(bits) % 8) % 8
    bits = np.concatenate([bits, np.zeros(pad, dtype=np.uint8)])
    return np.packbits(bits).tobytes()


def _cells_to_pixels(cells_2d: np.ndarray) -> np.ndarray:
    """Map (rows × cols) cell array to BGR pixel image via BLOCK_SIZE repeat."""
    img = C.PALETTE_BGR[cells_2d]
    return np.repeat(np.repeat(img, C.BLOCK_SIZE, axis=0),
                     C.BLOCK_SIZE, axis=1).astype(np.uint8)


def _pixels_to_cells(region: np.ndarray, cols: int, rows: int) -> np.ndarray:
    """Down-sample a BGR pixel region to a (rows × cols) cell array."""
    resized = cv2.resize(region, (cols * C.BLOCK_SIZE, rows * C.BLOCK_SIZE),
                         interpolation=cv2.INTER_AREA)
    arr = resized.reshape(rows, C.BLOCK_SIZE, cols, C.BLOCK_SIZE, 3).mean(axis=(1, 3))
    diff = arr[:, :, np.newaxis, :].astype(np.float32) - C.PALETTE_BGR.astype(np.float32)
    return diff.pow(2).sum(axis=3).argmin(axis=2).astype(np.uint8) \
        if hasattr(diff, 'pow') \
        else ((diff ** 2).sum(axis=3)).argmin(axis=2).astype(np.uint8)


# ═══════════════════════════════════════════════════════════════════════════════
#  CORNER MARKERS  (QR-style finder patterns, 15×15 cells)
# ═══════════════════════════════════════════════════════════════════════════════

def _draw_marker(canvas: np.ndarray, cx: int, cy: int) -> None:
    M = C.MARKER_SIZE   # 15
    pat = np.ones((M, M), dtype=np.uint8)  # 1=white
    pat[:1, :] = 0;  pat[-1:, :] = 0       # outer black ring
    pat[:, :1] = 0;  pat[:, -1:] = 0
    pat[2:M-2, 2:M-2] = 0                  # inner black ring
    pat[3:M-3, 3:M-3] = 1                  # inner white ring
    pat[5:M-5, 5:M-5] = 0                  # centre black
    for r in range(M):
        for c in range(M):
            colour = C.PALETTE_BGR[pat[r, c]]
            x0 = (cx + c) * C.BLOCK_SIZE
            y0 = (cy + r) * C.BLOCK_SIZE
            canvas[y0:y0+C.BLOCK_SIZE, x0:x0+C.BLOCK_SIZE] = colour


def _draw_all_markers(canvas: np.ndarray) -> None:
    M, B = C.MARKER_SIZE, C.BORDER
    _draw_marker(canvas, B,            B)
    _draw_marker(canvas, C.CELLS_X-B-M, B)
    _draw_marker(canvas, B,            C.CELLS_Y-B-M)
    _draw_marker(canvas, C.CELLS_X-B-M, C.CELLS_Y-B-M)


# ═══════════════════════════════════════════════════════════════════════════════
#  HEADER  (28 bytes: magic + session + frame_no + total + payload_len + crc32)
# ═══════════════════════════════════════════════════════════════════════════════

def _encode_header(session_id: bytes, frame_no: int,
                   total_frames: int, payload_len: int,
                   crc32_val: int) -> bytes:
    sid = (session_id + b'\x00' * 8)[:8]
    return (C.HEADER_MAGIC
            + sid
            + struct.pack('>I', frame_no)
            + struct.pack('>I', total_frames)
            + struct.pack('>I', payload_len)
            + struct.pack('>I', crc32_val))   # NEW: CRC32


def _decode_header(raw: bytes) -> tuple[bytes, int, int, int, int] | None:
    """Returns (session_id, frame_no, total_frames, payload_len, crc32) or None."""
    if len(raw) < C.HEADER_BYTES or raw[:4] != C.HEADER_MAGIC:
        return None
    sid    = raw[4:12]
    fno    = struct.unpack('>I', raw[12:16])[0]
    total  = struct.unpack('>I', raw[16:20])[0]
    plen   = struct.unpack('>I', raw[20:24])[0]
    crc32v = struct.unpack('>I', raw[24:28])[0]
    return sid, fno, total, plen, crc32v


# ═══════════════════════════════════════════════════════════════════════════════
#  CHECKSUM STRIP  (bottom CHECKSUM_ROWS of data area)
#  Encodes: crc32(4B) + frame_no(4B) + total_frames(4B) = 12 bytes
#  Repeated CKSUM_REPEAT times + ECC → immune to partial row corruption
# ═══════════════════════════════════════════════════════════════════════════════

def _encode_checksum_strip(crc32_val: int,
                           frame_no: int,
                           total_frames: int) -> np.ndarray:
    """Return a (CHECKSUM_ROWS × DATA_COLS) cell array for the checksum strip."""
    payload_12 = (struct.pack('>I', crc32_val)
                  + struct.pack('>I', frame_no)
                  + struct.pack('>I', total_frames))
    repeated   = payload_12 * C.CKSUM_REPEAT               # 96 bytes
    encoded    = bytes(_rs_cksum.encode(repeated))          # 112 bytes
    strip_capacity = (C.DATA_COLS * C.CHECKSUM_ROWS * C.BITS_PER_CELL) // 8
    padded     = (encoded + b'\x00' * strip_capacity)[:strip_capacity]
    cells      = _bytes_to_cells(padded, C.DATA_COLS * C.CHECKSUM_ROWS)
    return cells.reshape(C.CHECKSUM_ROWS, C.DATA_COLS)


def _decode_checksum_strip(strip_region: np.ndarray) -> tuple[int, int, int] | None:
    """
    Decode checksum strip pixel region → (crc32, frame_no, total_frames) or None.
    strip_region: BGR image of CHECKSUM_ROWS * BLOCK_SIZE × DATA_COLS * BLOCK_SIZE
    """
    cells  = _pixels_to_cells(strip_region, C.DATA_COLS, C.CHECKSUM_ROWS)
    raw    = _cells_to_bytes(cells.flatten())
    needed = C.CKSUM_BLOCK_ENC   # 112 bytes
    try:
        decoded, _, _ = _rs_cksum.decode(raw[:needed])
        decoded = bytes(decoded)
    except reedsolo.ReedSolomonError:
        # Fall back: majority vote across repetitions
        chunk = 12
        votes = [raw[i*chunk:(i+1)*chunk] for i in range(C.CKSUM_REPEAT)]
        from collections import Counter
        majority = bytes(Counter(b[j] for b in votes if len(b) > j).most_common(1)[0][0]
                         for j in range(chunk))
        decoded = majority

    if len(decoded) < 12:
        return None
    crc32v    = struct.unpack('>I', decoded[0:4])[0]
    frame_no  = struct.unpack('>I', decoded[4:8])[0]
    total     = struct.unpack('>I', decoded[8:12])[0]
    return crc32v, frame_no, total


# ═══════════════════════════════════════════════════════════════════════════════
#  ECC  encode / decode  (main data)
# ═══════════════════════════════════════════════════════════════════════════════

def _ecc_encode(payload: bytes) -> bytes:
    needed = C.N_ECC_CHUNKS * C.CHUNK_RAW
    padded = payload.ljust(needed, b'\x00')
    out    = bytearray()
    for i in range(C.N_ECC_CHUNKS):
        out.extend(bytes(_rs_data.encode(padded[i*C.CHUNK_RAW:(i+1)*C.CHUNK_RAW])))
    return bytes(out)


def _ecc_decode(encoded: bytes) -> bytes | None:
    out = bytearray()
    try:
        for i in range(C.N_ECC_CHUNKS):
            block = encoded[i*C.CHUNK_ENC:(i+1)*C.CHUNK_ENC]
            dec, _, _ = _rs_data.decode(block)
            out.extend(bytes(dec))
    except reedsolo.ReedSolomonError:
        return None
    return bytes(out)


# ═══════════════════════════════════════════════════════════════════════════════
#  PUBLIC API
# ═══════════════════════════════════════════════════════════════════════════════

def encode_frame_fast(payload: bytes,
                      frame_no: int,
                      total_frames: int,
                      session_id: bytes) -> np.ndarray:
    """
    Build a full SCREEN_H × SCREEN_W BGR image.
    Includes CRC32 in header AND as a redundant checksum strip.
    """
    crc32_val = zlib.crc32(payload) & 0xFFFFFFFF
    canvas    = np.zeros((C.SCREEN_H, C.SCREEN_W, 3), dtype=np.uint8)
    _draw_all_markers(canvas)

    # ── Header region ──────────────────────────────────────────────────────────
    hdr_bytes = _encode_header(session_id, frame_no, total_frames,
                               len(payload), crc32_val)
    hdr_cells = _bytes_to_cells(hdr_bytes, C.DATA_COLS * C.HEADER_ROWS)
    hdr_2d    = hdr_cells.reshape(C.HEADER_ROWS, C.DATA_COLS)
    y0h = (C.BORDER + C.MARKER_SIZE) * C.BLOCK_SIZE
    x0d = C.DATA_X0 * C.BLOCK_SIZE
    x1d = C.DATA_X1 * C.BLOCK_SIZE
    canvas[y0h : y0h + C.HEADER_ROWS * C.BLOCK_SIZE, x0d:x1d] = _cells_to_pixels(hdr_2d)

    # ── ECC data region ────────────────────────────────────────────────────────
    ecc_enc   = _ecc_encode(payload)
    n_cells   = (C.N_ECC_CHUNKS * C.CHUNK_ENC * 8) // C.BITS_PER_CELL
    data_cell = _bytes_to_cells(ecc_enc, n_cells).reshape(C.DATA_ROWS, C.DATA_COLS)
    y0d = C.DATA_Y0 * C.BLOCK_SIZE
    y1d = C.CKSUM_Y0 * C.BLOCK_SIZE    # stop BEFORE checksum strip
    canvas[y0d:y1d, x0d:x1d] = _cells_to_pixels(data_cell)

    # ── Checksum strip ─────────────────────────────────────────────────────────
    cksum_cells = _encode_checksum_strip(crc32_val, frame_no, total_frames)
    y0c = C.CKSUM_Y0 * C.BLOCK_SIZE
    y1c = C.CKSUM_Y1 * C.BLOCK_SIZE
    canvas[y0c:y1c, x0d:x1d] = _cells_to_pixels(cksum_cells)

    return canvas


# ── Perspective detection ──────────────────────────────────────────────────────

def _find_screen_corners(bgr: np.ndarray) -> np.ndarray | None:
    """Detect the 4 corner marker centres. Returns (4,2) float32 TL/TR/BL/BR or None."""
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    th   = cv2.adaptiveThreshold(gray, 255,
                                  cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                  cv2.THRESH_BINARY_INV, 51, 10)
    cnts, _ = cv2.findContours(th, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not cnts:
        return None

    h, w  = bgr.shape[:2]
    rects = []
    for c in cnts:
        area = cv2.contourArea(c)
        if area < 100:
            continue
        x, y, cw, ch = cv2.boundingRect(c)
        if 0.5 < cw / max(ch, 1) < 2.0:
            rects.append((x, y, cw, ch))

    if len(rects) < 4:
        return None

    corners_ref = [(0, 0), (w, 0), (0, h), (w, h)]
    selected    = []
    for rx, ry in corners_ref:
        best = min(rects, key=lambda r: (r[0]+r[2]//2-rx)**2 + (r[1]+r[3]//2-ry)**2)
        selected.append([best[0]+best[2]//2, best[1]+best[3]//2])
    return np.array(selected, dtype=np.float32)


def decode_frame(bgr: np.ndarray) -> tuple[bytes, int, int, bytes, bool] | None:
    """
    Decode a camera-captured image.

    Returns (payload, frame_no, total_frames, session_id, checksum_ok) or None.

    checksum_ok=True  → CRC32 from header matches CRC32 of decoded payload
                        AND checksum strip CRC32 matches header CRC32.
    checksum_ok=False → ECC decode succeeded but checksum mismatch (data error).
    """
    h_img, w_img = bgr.shape[:2]

    # ── Perspective correction ─────────────────────────────────────────────────
    corners = _find_screen_corners(bgr)
    if corners is not None:
        sx, sy = w_img / C.SCREEN_W, h_img / C.SCREEN_H
        dst = np.array([
            [C.BORDER * C.BLOCK_SIZE * sx,          C.BORDER * C.BLOCK_SIZE * sy],
            [(C.CELLS_X-C.BORDER)*C.BLOCK_SIZE*sx,  C.BORDER * C.BLOCK_SIZE * sy],
            [C.BORDER * C.BLOCK_SIZE * sx,           (C.CELLS_Y-C.BORDER)*C.BLOCK_SIZE*sy],
            [(C.CELLS_X-C.BORDER)*C.BLOCK_SIZE*sx,   (C.CELLS_Y-C.BORDER)*C.BLOCK_SIZE*sy],
        ], dtype=np.float32)
        M, _ = cv2.findHomography(corners, dst, cv2.RANSAC, 5.0)
        if M is not None:
            bgr = cv2.warpPerspective(bgr, M, (w_img, h_img))

    canonical = cv2.resize(bgr, (C.SCREEN_W, C.SCREEN_H), interpolation=cv2.INTER_LINEAR)
    x0d, x1d  = C.DATA_X0 * C.BLOCK_SIZE, C.DATA_X1 * C.BLOCK_SIZE

    # ── Header ─────────────────────────────────────────────────────────────────
    y0h = (C.BORDER + C.MARKER_SIZE) * C.BLOCK_SIZE
    y1h = y0h + C.HEADER_ROWS * C.BLOCK_SIZE
    hdr_cells  = _pixels_to_cells(canonical[y0h:y1h, x0d:x1d],
                                  C.DATA_COLS, C.HEADER_ROWS)
    hdr_bytes  = _cells_to_bytes(hdr_cells.flatten())[:C.HEADER_BYTES]
    parsed     = _decode_header(hdr_bytes)
    if parsed is None:
        return None
    session_id, frame_no, total_frames, payload_len, hdr_crc32 = parsed

    # ── ECC data ───────────────────────────────────────────────────────────────
    y0d  = C.DATA_Y0 * C.BLOCK_SIZE
    y1d  = C.CKSUM_Y0 * C.BLOCK_SIZE
    data_cells = _pixels_to_cells(canonical[y0d:y1d, x0d:x1d],
                                  C.DATA_COLS, C.DATA_ROWS)
    raw_bytes  = _cells_to_bytes(data_cells.flatten())
    decoded    = _ecc_decode(raw_bytes[:C.N_ECC_CHUNKS * C.CHUNK_ENC])
    if decoded is None:
        return None
    payload = decoded[:payload_len]

    # ── Checksum strip (independent CRC32 verification) ───────────────────────
    y0c  = C.CKSUM_Y0 * C.BLOCK_SIZE
    y1c  = C.CKSUM_Y1 * C.BLOCK_SIZE
    strip_result = _decode_checksum_strip(canonical[y0c:y1c, x0d:x1d])

    # ── Three-way checksum validation ─────────────────────────────────────────
    # 1. CRC32 of decoded payload
    computed_crc32 = zlib.crc32(payload) & 0xFFFFFFFF
    # 2. Header CRC32 must match computed
    header_ok  = (computed_crc32 == hdr_crc32)
    # 3. Checksum strip CRC32 must match computed
    strip_ok   = False
    if strip_result is not None:
        strip_crc32, strip_fno, _ = strip_result
        strip_ok = (strip_crc32 == computed_crc32) and (strip_fno == frame_no)

    checksum_ok = header_ok and strip_ok

    return payload, frame_no, total_frames, session_id, checksum_ok
