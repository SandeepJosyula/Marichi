"""
MARICHI — Core frame encoder / decoder.

encode_frame(payload, frame_no, total_frames, session_id) → BGR np.ndarray
decode_frame(bgr_img)  → (payload_bytes, frame_no, total_frames, session_id) | None
"""

from __future__ import annotations
import struct
import zlib
import numpy as np
import cv2
import reedsolo

from . import config as C

# ── Reed-Solomon codec (shared instance) ─────────────────────────────────────
_rs = reedsolo.RSCodec(C.ECC_NSYM)


# ═══════════════════════════════════════════════════════════════════════════════
#  LOW-LEVEL: bytes  ↔  cell array  ↔  BGR pixel image
# ═══════════════════════════════════════════════════════════════════════════════

def _bytes_to_cells(data: bytes, n_cells: int) -> np.ndarray:
    """Pack bytes into a 1-D array of colour indices (0-3, 2 bits each).
    Pads with zeros if data < n_cells * 2 bits."""
    bits_needed = n_cells * C.BITS_PER_CELL
    # zero-pad to required bit length
    padded = data + b'\x00' * ((bits_needed // 8) - len(data) + 1)
    arr = np.frombuffer(padded, dtype=np.uint8)
    # Unpack 8 bits → 4 × 2-bit symbols per byte
    cells = np.unpackbits(arr)[:bits_needed].reshape(-1, 2)
    return (cells[:, 0] << 1 | cells[:, 1]).astype(np.uint8)   # 0-3


def _cells_to_bytes(cells: np.ndarray) -> bytes:
    """Unpack 1-D array of colour indices (0-3) → bytes."""
    bits = np.zeros(len(cells) * 2, dtype=np.uint8)
    bits[0::2] = (cells >> 1) & 1
    bits[1::2] = cells & 1
    # pad to full byte boundary
    pad = (8 - len(bits) % 8) % 8
    bits = np.concatenate([bits, np.zeros(pad, dtype=np.uint8)])
    return np.packbits(bits).tobytes()


def _cells_to_pixels(cells_2d: np.ndarray) -> np.ndarray:
    """Map a 2-D cell array (dtype uint8, values 0-3) to a BGR pixel image.
    Each cell becomes a BLOCK_SIZE × BLOCK_SIZE block."""
    h, w = cells_2d.shape
    img = C.PALETTE_BGR[cells_2d]          # (h, w, 3)
    # Repeat each cell into BLOCK_SIZE×BLOCK_SIZE pixels
    img = np.repeat(np.repeat(img, C.BLOCK_SIZE, axis=0), C.BLOCK_SIZE, axis=1)
    return img.astype(np.uint8)


def _pixels_to_cells(region: np.ndarray, cols: int, rows: int) -> np.ndarray:
    """Down-sample a BGR pixel region (already perspective-corrected) to a
    (rows × cols) cell array by averaging each block then nearest-colour lookup."""
    # Resize to exact cell grid
    resized = cv2.resize(region, (cols * C.BLOCK_SIZE, rows * C.BLOCK_SIZE),
                         interpolation=cv2.INTER_AREA)
    # Average each BLOCK_SIZE×BLOCK_SIZE patch
    h, w = rows, cols
    arr = resized.reshape(h, C.BLOCK_SIZE, w, C.BLOCK_SIZE, 3).mean(axis=(1, 3))
    arr = arr.astype(np.float32)   # (rows, cols, 3)

    # Nearest colour in palette (Euclidean distance in BGR)
    palette_f = C.PALETTE_BGR.astype(np.float32)   # (4, 3)
    diff = arr[:, :, np.newaxis, :] - palette_f[np.newaxis, np.newaxis, :, :]  # (R,C,4,3)
    dists = (diff ** 2).sum(axis=3)     # (R, C, 4)
    return dists.argmin(axis=2).astype(np.uint8)   # (R, C)


# ═══════════════════════════════════════════════════════════════════════════════
#  CORNER MARKER  (finder pattern — 15×15 cells, QR-style)
# ═══════════════════════════════════════════════════════════════════════════════

def _draw_marker(canvas: np.ndarray, cx: int, cy: int) -> None:
    """Draw a 15×15-cell corner finder marker at top-left cell (cx, cy).
    Pattern: 7-ring outer white, 5-ring black, 3-ring white, 1-cell black centre.
    """
    M = C.MARKER_SIZE   # 15
    marker = np.ones((M, M), dtype=np.uint8)   # white=1

    # outer black ring (1 cell thick)
    marker[:1, :] = 0;  marker[-1:, :] = 0
    marker[:, :1] = 0;  marker[:, -1:] = 0
    # 1-cell white ring (already 1)
    # inner black ring
    marker[2:M-2, 2:M-2] = 0
    # inner white ring
    marker[3:M-3, 3:M-3] = 1
    # centre black
    marker[5:M-5, 5:M-5] = 0

    for r in range(M):
        for c in range(M):
            x0 = (cx + c) * C.BLOCK_SIZE
            y0 = (cy + r) * C.BLOCK_SIZE
            colour = C.PALETTE_BGR[marker[r, c]]
            canvas[y0:y0+C.BLOCK_SIZE, x0:x0+C.BLOCK_SIZE] = colour


def _draw_all_markers(canvas: np.ndarray) -> None:
    """Place 4 finder markers in frame corners (inside the border)."""
    M = C.MARKER_SIZE
    B = C.BORDER
    Cx = C.CELLS_X
    Cy = C.CELLS_Y
    _draw_marker(canvas, B,            B)             # TL
    _draw_marker(canvas, Cx-B-M,       B)             # TR
    _draw_marker(canvas, B,            Cy-B-M)        # BL
    _draw_marker(canvas, Cx-B-M,       Cy-B-M)        # BR


# ═══════════════════════════════════════════════════════════════════════════════
#  HEADER encoding (24 bytes into top HEADER_ROWS rows of data region)
# ═══════════════════════════════════════════════════════════════════════════════

def _encode_header(session_id: bytes, frame_no: int,
                   total_frames: int, payload_len: int) -> bytes:
    """Pack 24-byte header: magic(4) + session(8) + frame_no(4) +
    total(4) + payload_len(4)."""
    sid = (session_id + b'\x00' * 8)[:8]
    return (C.HEADER_MAGIC
            + sid
            + struct.pack('>I', frame_no)
            + struct.pack('>I', total_frames)
            + struct.pack('>I', payload_len))


def _decode_header(raw: bytes) -> tuple[bytes, int, int, int] | None:
    """Returns (session_id, frame_no, total_frames, payload_len) or None."""
    if len(raw) < C.HEADER_BYTES:
        return None
    if raw[:4] != C.HEADER_MAGIC:
        return None
    sid        = raw[4:12]
    frame_no   = struct.unpack('>I', raw[12:16])[0]
    total      = struct.unpack('>I', raw[16:20])[0]
    plen       = struct.unpack('>I', raw[20:24])[0]
    return sid, frame_no, total, plen


# ═══════════════════════════════════════════════════════════════════════════════
#  ECC  encode / decode
# ═══════════════════════════════════════════════════════════════════════════════

def _ecc_encode(payload: bytes) -> bytes:
    """Encode payload into ECC blocks. Output length = N_ECC_CHUNKS * CHUNK_ENC."""
    out = bytearray()
    # Pad payload to fill all chunks
    needed = C.N_ECC_CHUNKS * C.CHUNK_RAW
    padded = payload.ljust(needed, b'\x00')
    for i in range(C.N_ECC_CHUNKS):
        chunk = padded[i*C.CHUNK_RAW:(i+1)*C.CHUNK_RAW]
        out.extend(bytes(_rs.encode(chunk)))
    return bytes(out)


def _ecc_decode(encoded: bytes) -> bytes | None:
    """Decode ECC blocks. Returns raw payload bytes or None on uncorrectable error."""
    out = bytearray()
    try:
        for i in range(C.N_ECC_CHUNKS):
            block = encoded[i*C.CHUNK_ENC:(i+1)*C.CHUNK_ENC]
            decoded, _, _ = _rs.decode(block)
            out.extend(bytes(decoded))
    except reedsolo.ReedSolomonError:
        return None
    return bytes(out)


# ═══════════════════════════════════════════════════════════════════════════════
#  PUBLIC API
# ═══════════════════════════════════════════════════════════════════════════════

def encode_frame(payload: bytes,
                 frame_no: int,
                 total_frames: int,
                 session_id: bytes) -> np.ndarray:
    """
    Build a full SCREEN_H × SCREEN_W BGR image for display.

    payload      : up to PAYLOAD_PER_FRAME bytes of data for this frame
    frame_no     : 0-based index
    total_frames : total number of frames in session
    session_id   : 8-byte unique session identifier

    Returns BGR ndarray ready for cv2.imshow / cv2.imwrite.
    """
    # 1. Prepare canvas (all-black)
    canvas = np.zeros((C.SCREEN_H, C.SCREEN_W, 3), dtype=np.uint8)

    # 2. Draw outer border (black — already black)

    # 3. Draw corner markers
    _draw_all_markers(canvas)

    # 4. Encode header into HEADER_ROWS rows of the data region
    header_bytes = _encode_header(session_id, frame_no, total_frames, len(payload))
    hdr_cells_needed = C.DATA_COLS * C.HEADER_ROWS
    hdr_cell_arr = _bytes_to_cells(header_bytes, hdr_cells_needed)
    hdr_2d = hdr_cell_arr.reshape(C.HEADER_ROWS, C.DATA_COLS)

    # Paint header rows
    y_hdr_start = (C.BORDER + C.MARKER_SIZE) * C.BLOCK_SIZE
    for r in range(C.HEADER_ROWS):
        for cc in range(C.DATA_COLS):
            x0 = (C.DATA_X0 + cc) * C.BLOCK_SIZE
            y0 = y_hdr_start + r * C.BLOCK_SIZE
            colour = C.PALETTE_BGR[hdr_2d[r, cc]]
            canvas[y0:y0+C.BLOCK_SIZE, x0:x0+C.BLOCK_SIZE] = colour

    # 5. ECC-encode payload and place in data region
    ecc_encoded = _ecc_encode(payload)
    total_raw_needed = C.N_ECC_CHUNKS * C.CHUNK_ENC
    data_cells_needed = (total_raw_needed * 8) // C.BITS_PER_CELL
    data_cell_arr = _bytes_to_cells(ecc_encoded, data_cells_needed)
    data_2d = data_cell_arr.reshape(C.DATA_ROWS, C.DATA_COLS)

    # Paint data cells
    for r in range(C.DATA_ROWS):
        y0 = C.DATA_Y0 * C.BLOCK_SIZE + r * C.BLOCK_SIZE
        for cc in range(C.DATA_COLS):
            x0 = (C.DATA_X0 + cc) * C.BLOCK_SIZE
            colour = C.PALETTE_BGR[data_2d[r, cc]]
            canvas[y0:y0+C.BLOCK_SIZE, x0:x0+C.BLOCK_SIZE] = colour

    return canvas


def encode_frame_fast(payload: bytes,
                      frame_no: int,
                      total_frames: int,
                      session_id: bytes) -> np.ndarray:
    """
    Vectorised (fast) version of encode_frame using numpy array indexing.
    Same output — significantly faster for large frames.
    """
    canvas = np.zeros((C.SCREEN_H, C.SCREEN_W, 3), dtype=np.uint8)
    _draw_all_markers(canvas)

    # Header region
    header_bytes = _encode_header(session_id, frame_no, total_frames, len(payload))
    hdr_cells_needed = C.DATA_COLS * C.HEADER_ROWS
    hdr_cell_arr = _bytes_to_cells(header_bytes, hdr_cells_needed)
    hdr_2d = hdr_cell_arr.reshape(C.HEADER_ROWS, C.DATA_COLS)
    hdr_pixels = _cells_to_pixels(hdr_2d)   # (HEADER_ROWS*BS, DATA_COLS*BS, 3)
    y0h = (C.BORDER + C.MARKER_SIZE) * C.BLOCK_SIZE
    y1h = y0h + C.HEADER_ROWS * C.BLOCK_SIZE
    x0d = C.DATA_X0 * C.BLOCK_SIZE
    x1d = C.DATA_X1 * C.BLOCK_SIZE
    canvas[y0h:y1h, x0d:x1d] = hdr_pixels

    # Data region
    ecc_encoded = _ecc_encode(payload)
    total_raw_needed = C.N_ECC_CHUNKS * C.CHUNK_ENC
    data_cells_needed = (total_raw_needed * 8) // C.BITS_PER_CELL
    data_cell_arr = _bytes_to_cells(ecc_encoded, data_cells_needed)
    data_2d = data_cell_arr.reshape(C.DATA_ROWS, C.DATA_COLS)
    data_pixels = _cells_to_pixels(data_2d)
    y0d = C.DATA_Y0 * C.BLOCK_SIZE
    y1d = C.DATA_Y1 * C.BLOCK_SIZE
    canvas[y0d:y1d, x0d:x1d] = data_pixels

    return canvas


def _find_screen_corners(bgr: np.ndarray) -> np.ndarray | None:
    """
    Detect the 4 corner marker centres in a camera-captured image.
    Returns a (4,2) float32 array of (x,y) corners in TL,TR,BL,BR order,
    or None if detection fails.

    Strategy: threshold to B/W, find large filled rectangles at corners.
    """
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    # Adaptive threshold → binary
    th = cv2.adaptiveThreshold(gray, 255,
                                cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                cv2.THRESH_BINARY_INV, 51, 10)
    # Find contours
    cnts, _ = cv2.findContours(th, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not cnts:
        return None

    h, w = bgr.shape[:2]
    # Sort by area descending, keep plausible rectangles
    rects = []
    for c in cnts:
        area = cv2.contourArea(c)
        if area < 100:
            continue
        x, y, cw, ch = cv2.boundingRect(c)
        aspect = cw / max(ch, 1)
        if 0.5 < aspect < 2.0:
            rects.append((x, y, cw, ch, area))

    if len(rects) < 4:
        return None

    # Find the rectangle closest to each of the 4 corners
    corners_ref = [(0, 0), (w, 0), (0, h), (w, h)]
    selected = []
    for rx, ry in corners_ref:
        best = min(rects, key=lambda r: (r[0]+r[2]//2 - rx)**2 + (r[1]+r[3]//2 - ry)**2)
        cx_m = best[0] + best[2] // 2
        cy_m = best[1] + best[3] // 2
        selected.append([cx_m, cy_m])

    return np.array(selected, dtype=np.float32)   # TL,TR,BL,BR


def decode_frame(bgr: np.ndarray) -> tuple[bytes, int, int, bytes] | None:
    """
    Decode a camera-captured image.

    Returns (payload_bytes, frame_no, total_frames, session_id) or None on failure.

    Steps:
      1. Detect corner markers → homography warp to canonical frame
      2. Extract header cells → parse metadata
      3. Extract data cells → ECC decode → payload
      4. Verify payload_len
    """
    h_img, w_img = bgr.shape[:2]

    # ── Perspective correction ────────────────────────────────────────────────
    corners = _find_screen_corners(bgr)
    if corners is not None:
        # Destination corners at expected screen positions (normalised to img size)
        scale_x = w_img / C.SCREEN_W
        scale_y = h_img / C.SCREEN_H

        dst = np.array([
            [C.BORDER * C.BLOCK_SIZE * scale_x,         C.BORDER * C.BLOCK_SIZE * scale_y],
            [(C.CELLS_X - C.BORDER) * C.BLOCK_SIZE * scale_x, C.BORDER * C.BLOCK_SIZE * scale_y],
            [C.BORDER * C.BLOCK_SIZE * scale_x,         (C.CELLS_Y - C.BORDER) * C.BLOCK_SIZE * scale_y],
            [(C.CELLS_X - C.BORDER) * C.BLOCK_SIZE * scale_x, (C.CELLS_Y - C.BORDER) * C.BLOCK_SIZE * scale_y],
        ], dtype=np.float32)

        M, _ = cv2.findHomography(corners, dst, cv2.RANSAC, 5.0)
        if M is not None:
            bgr = cv2.warpPerspective(bgr, M, (w_img, h_img))

    # Resize to canonical screen size for cell extraction
    canonical = cv2.resize(bgr, (C.SCREEN_W, C.SCREEN_H), interpolation=cv2.INTER_LINEAR)

    # ── Extract header region ─────────────────────────────────────────────────
    y0h = (C.BORDER + C.MARKER_SIZE) * C.BLOCK_SIZE
    y1h = y0h + C.HEADER_ROWS * C.BLOCK_SIZE
    x0d = C.DATA_X0 * C.BLOCK_SIZE
    x1d = C.DATA_X1 * C.BLOCK_SIZE

    hdr_region = canonical[y0h:y1h, x0d:x1d]
    hdr_cells  = _pixels_to_cells(hdr_region, C.DATA_COLS, C.HEADER_ROWS)
    hdr_bytes  = _cells_to_bytes(hdr_cells.flatten())[:C.HEADER_BYTES]
    parsed     = _decode_header(hdr_bytes)
    if parsed is None:
        return None
    session_id, frame_no, total_frames, payload_len = parsed

    # ── Extract data region ───────────────────────────────────────────────────
    y0d = C.DATA_Y0 * C.BLOCK_SIZE
    y1d = C.DATA_Y1 * C.BLOCK_SIZE
    data_region = canonical[y0d:y1d, x0d:x1d]
    data_cells  = _pixels_to_cells(data_region, C.DATA_COLS, C.DATA_ROWS)
    raw_bytes   = _cells_to_bytes(data_cells.flatten())
    total_enc   = C.N_ECC_CHUNKS * C.CHUNK_ENC
    encoded_data = raw_bytes[:total_enc]

    # ── ECC decode ────────────────────────────────────────────────────────────
    decoded = _ecc_decode(encoded_data)
    if decoded is None:
        return None

    # Trim to declared payload length
    payload = decoded[:payload_len]
    return payload, frame_no, total_frames, session_id
