"""
MARICHI — Option D: QR-Code Stream

Transmits data as a rapid stream of QR codes displayed on screen.
The receiver captures QR codes with any camera — including a phone camera.

Physical setup:
  Sender laptop  → displays QR codes full-screen
  Receiver       → any camera scanning the screen (laptop webcam OR phone)

Why QR mode:
  ✓  Works with phones (standard QR scanner, no special app)
  ✓  Works with tablets and older cameras
  ✓  Human-readable frame counter (printed in QR quiet zone margin)
  ✓  Standard QR error correction (separate from MARICHI's RS ECC)

Payload capacity per QR code:
  Version  20, ECC L : ~692  bytes raw → ~519  bytes after MARICHI RS ECC
  Version  30, ECC L : ~1456 bytes raw → ~1092 bytes after MARICHI RS ECC
  Version  40, ECC L : ~2953 bytes raw → ~2214 bytes after MARICHI RS ECC  ← default

Throughput guide (QR v40, ECC L, MARICHI RS ECC):
  3 fps :  ~6.6 KB/s  →  1 MB ≈  2.6 min  |  100 MB ≈  4.3 hrs
  5 fps :  ~11  KB/s  →  1 MB ≈  1.6 min  |  100 MB ≈  2.6 hrs
  10 fps:  ~22  KB/s  →  1 MB ≈  0.8 min  |  100 MB ≈  1.3 hrs

ACK system:
  Same camera-based ACK as Option A (visual mode).
  Receiver's screen flashes BLUE (ACK) or YELLOW (NACK).
  Without ACK: sender cycles all frames until stopped.

Dependencies:
  pip install qrcode[pil] opencv-python reedsolo
"""

from __future__ import annotations
import os
import sys
import time
import math
import zlib
import hashlib
import struct
import secrets
import base64
import numpy as np
from typing import Optional

try:
    import cv2
except ImportError:
    cv2 = None

try:
    import qrcode
    from qrcode.constants import ERROR_CORRECT_L, ERROR_CORRECT_M
    from PIL import Image
except ImportError:
    qrcode = None
    Image = None

try:
    from reedsolo import RSCodec
except ImportError:
    RSCodec = None

# ── Constants ─────────────────────────────────────────────────────────────────

QR_VERSION   = 40          # Max QR version (highest data capacity)
QR_ECC       = None        # Set to ERROR_CORRECT_L at init when qrcode available
QR_BOX_SIZE  = 2           # Pixels per QR module (smaller = more QR on screen)
QR_BORDER    = 2           # Quiet zone modules

# With version 40 + ERROR_CORRECT_L: 2953 bytes capacity
# After MARICHI RS ECC (128 raw → 160 enc, ratio 0.8): ~2362 bytes payload
QR_MAX_BINARY_BYTES = 2953   # qrcode v40 ECC L binary mode capacity
QR_ECC_NSYM         = 32
QR_CHUNK_RAW        = 128
QR_CHUNK_ENC        = QR_CHUNK_RAW + QR_ECC_NSYM   # 160

# Max payload per QR frame (fit into QR_MAX_BINARY_BYTES after ECC encoding)
# n_chunks = QR_MAX_BINARY_BYTES // QR_CHUNK_ENC = 2953 // 160 = 18 chunks
QR_N_CHUNKS         = QR_MAX_BINARY_BYTES // QR_CHUNK_ENC    # 18
QR_PAYLOAD_PER_FRAME = QR_N_CHUNKS * QR_CHUNK_RAW            # 2304 bytes

QR_FPS_DEFAULT      = 3     # frames per second default (conservative)
QR_HOLD_MS_DEFAULT  = 1000 // QR_FPS_DEFAULT   # ms per QR display

# Header structure (embedded before ECC payload in QR data)
QR_MAGIC   = b'\xCA\xFE\xBB\x44'
# magic(4) + session(8) + frame_no(4) + total(4) + crc32(4) + pay_len(2) = 26 bytes
QR_HDR_FMT = '>4s8sIIIH'
QR_HDR_LEN = 26

# ACK window colours (same as visual mode)
ACK_GREEN  = (0,   200,   0)
ACK_BLUE   = (220,  60,   0)
ACK_YELLOW = (0,   220, 220)
ACK_GRAY   = (80,   80,  80)

ACK_SIGNAL_MS = 2000   # ms — longer than visual mode (camera needs scan time)
ACK_WAIT_S    = 30.0   # sender waits up to 30s for ACK per QR frame

# ── Reed-Solomon ECC ──────────────────────────────────────────────────────────

def _get_rs():
    if RSCodec is None:
        raise RuntimeError("reedsolo not installed — run: pip install reedsolo")
    return RSCodec(QR_ECC_NSYM)

def rs_encode(data: bytes) -> bytes:
    rs = _get_rs()
    out = bytearray()
    for i in range(0, len(data), QR_CHUNK_RAW):
        chunk = data[i:i + QR_CHUNK_RAW]
        out.extend(bytes(rs.encode(chunk)))
    return bytes(out)

def rs_decode(data: bytes) -> Optional[bytes]:
    rs = _get_rs()
    out = bytearray()
    for i in range(0, len(data), QR_CHUNK_ENC):
        chunk = data[i:i + QR_CHUNK_ENC]
        if len(chunk) < QR_CHUNK_ENC:
            return None
        try:
            decoded, _, _ = rs.decode(chunk)
            out.extend(bytes(decoded))
        except Exception:
            return None
    return bytes(out)

# ── Frame encoding / decoding ─────────────────────────────────────────────────

def encode_qr_frame(payload: bytes,
                    frame_no: int,
                    total_frames: int,
                    session_id: bytes) -> bytes:
    """Return raw bytes to embed in QR code."""
    crc32 = zlib.crc32(payload) & 0xFFFFFFFF
    header = struct.pack(QR_HDR_FMT,
                         QR_MAGIC,
                         session_id[:8],
                         frame_no,
                         total_frames,
                         crc32,
                         len(payload))
    ecc_payload = rs_encode(payload)
    return header + ecc_payload


def decode_qr_frame(raw: bytes) -> Optional[dict]:
    """
    Decode raw bytes extracted from a QR code.
    Returns dict or None on error.
    """
    if len(raw) < QR_HDR_LEN:
        return None
    try:
        magic, session, frame_no, total_frames, crc32, pay_len = \
            struct.unpack(QR_HDR_FMT, raw[:QR_HDR_LEN])
    except struct.error:
        return None

    if magic != QR_MAGIC:
        return None
    if pay_len > QR_PAYLOAD_PER_FRAME:
        return None

    ecc_data = raw[QR_HDR_LEN:]
    decoded  = rs_decode(ecc_data)
    if decoded is None:
        return None

    payload    = decoded[:pay_len]
    actual_crc = zlib.crc32(payload) & 0xFFFFFFFF
    crc_ok     = (actual_crc == crc32)

    return {
        "payload":      payload,
        "frame_no":     frame_no,
        "total_frames": total_frames,
        "session_id":   session,
        "crc_ok":       crc_ok,
    }

# ── QR image generation ───────────────────────────────────────────────────────

def _make_qr_image(data: bytes, frame_no: int, total_frames: int,
                   screen_w: int = 1920, screen_h: int = 1080) -> np.ndarray:
    """
    Generate a black QR code on white background, scaled to fill the screen.
    Includes a text label at the bottom (frame counter).
    """
    if qrcode is None or Image is None:
        raise RuntimeError("qrcode/Pillow not installed — run: pip install qrcode[pil]")

    # Base64-encode so qrcode library can handle arbitrary binary
    # Actually qrcode supports raw binary mode; use that for max capacity
    qr = qrcode.QRCode(
        version=QR_VERSION,
        error_correction=ERROR_CORRECT_L,
        box_size=QR_BOX_SIZE,
        border=QR_BORDER,
    )
    qr.add_data(data)
    qr.make(fit=False)

    img_pil = qr.make_image(fill_color="black", back_color="white")
    img_np  = np.array(img_pil.convert('RGB'))

    # Flip R↔B for OpenCV BGR
    img_bgr = img_np[:, :, ::-1].copy()

    # Scale to fill screen (nearest-neighbour keeps QR crisp)
    scale    = min(screen_w / img_bgr.shape[1], screen_h / img_bgr.shape[0])
    new_w    = int(img_bgr.shape[1] * scale)
    new_h    = int(img_bgr.shape[0] * scale)
    resized  = cv2.resize(img_bgr, (new_w, new_h), interpolation=cv2.INTER_NEAREST)

    # Embed in black canvas
    canvas = np.zeros((screen_h, screen_w, 3), dtype=np.uint8)
    y_off  = (screen_h - new_h) // 2
    x_off  = (screen_w - new_w) // 2
    canvas[y_off:y_off + new_h, x_off:x_off + new_w] = resized

    # Label
    label = f"MARICHI QR  Frame {frame_no+1}/{total_frames}"
    cv2.putText(canvas, label,
                (20, screen_h - 20),
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 200, 0), 2)

    return canvas


# ── ACK display (for receiver screen) ────────────────────────────────────────

class QRACKDisplay:
    """Colored ACK window on receiver screen (same concept as visual mode)."""

    def __init__(self, width: int = 600, height: int = 300):
        self.w = width
        self.h = height
        self.win = "MARICHI QR-ACK"
        cv2.namedWindow(self.win, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(self.win, width, height)
        self._show(ACK_GRAY, "Waiting for QR session...")

    def _show(self, color: tuple, text: str, frame_no: int = 0, total: int = 0) -> None:
        img = np.full((self.h, self.w, 3), color, dtype=np.uint8)
        cv2.putText(img, text,
                    (20, self.h // 2 - 20),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
        if total > 0:
            pct  = 100 * frame_no // total
            bar_w = int((self.w - 40) * pct / 100)
            cv2.rectangle(img, (20, self.h - 40), (20 + bar_w, self.h - 20),
                          (255, 255, 255), -1)
            cv2.putText(img, f"{pct}%  {frame_no}/{total}",
                        (20, self.h - 50),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)
        cv2.imshow(self.win, img)
        cv2.waitKey(1)

    def ack(self, frame_no: int = 0, total: int = 0) -> None:
        self._show(ACK_BLUE, f"✔ QR Frame {frame_no+1} — ACK", frame_no, total)
        cv2.waitKey(ACK_SIGNAL_MS)

    def nack(self, frame_no: int = 0, total: int = 0) -> None:
        self._show(ACK_YELLOW, f"✘ QR CRC MISMATCH — NACK", frame_no, total)
        cv2.waitKey(ACK_SIGNAL_MS)

    def processing(self, frame_no: int = 0, total: int = 0) -> None:
        self._show(ACK_GREEN, "Scanning QR...", frame_no, total)

    def close(self) -> None:
        cv2.destroyWindow(self.win)


# ── ACK detector (reads receiver's ACK window color, same as visual mode) ────

class QRACKDetector:
    """
    Reads the receiver's QR-ACK window color via camera.
    Same BLUE/YELLOW detection logic as Option A's ACKDetector.
    """
    ACK_HSV_BLUE   = ((100, 80, 80), (130, 255, 255))
    ACK_HSV_YELLOW = ((20,  80, 80), (35,  255, 255))
    MIN_COVERAGE   = 0.15
    CONFIRM_FRAMES = 2

    def __init__(self, cam_index: int):
        if cv2 is None:
            raise RuntimeError("opencv-python not installed")
        self.cam_index = cam_index
        self._cap      = cv2.VideoCapture(cam_index)
        self._sig: Optional[str] = None
        self._confirm  = 0
        self._last_sig: Optional[str] = None

    def read(self) -> Optional[str]:
        """Non-blocking: read one camera frame and return "ACK"/"NACK"/None."""
        ret, frame = self._cap.read()
        if not ret:
            return None
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        n   = hsv.shape[0] * hsv.shape[1]

        lo, hi = [np.array(x) for x in self.ACK_HSV_BLUE]
        if np.count_nonzero(cv2.inRange(hsv, lo, hi)) / n > self.MIN_COVERAGE:
            sig = "ACK"
        else:
            lo, hi = [np.array(x) for x in self.ACK_HSV_YELLOW]
            if np.count_nonzero(cv2.inRange(hsv, lo, hi)) / n > self.MIN_COVERAGE:
                sig = "NACK"
            else:
                sig = None

        # Require CONFIRM_FRAMES consecutive frames
        if sig == self._last_sig:
            self._confirm += 1
        else:
            self._confirm = 1
        self._last_sig = sig

        if self._confirm >= self.CONFIRM_FRAMES:
            return sig
        return None

    def release(self) -> None:
        self._cap.release()


# ── Sender ────────────────────────────────────────────────────────────────────

class QRSender:
    """
    Option D sender — displays data as a stream of QR codes.

    Usage:
        QRSender("/path/to/file.zip").run()
        QRSender("/path/to/file.zip", fps=5, ack_cam=0).run()
    """

    def __init__(self, filepath: str,
                 fps: int = QR_FPS_DEFAULT,
                 ack_cam: int = -1,
                 screen_w: int = 1920,
                 screen_h: int = 1080):
        if cv2 is None:
            raise RuntimeError("opencv-python not installed")
        if qrcode is None:
            raise RuntimeError("qrcode not installed — run: pip install qrcode[pil]")

        self.filepath   = filepath
        self.fps        = fps
        self.hold_ms    = max(50, 1000 // fps)
        self.ack_cam    = ack_cam
        self.screen_w   = screen_w
        self.screen_h   = screen_h
        self.session_id = secrets.token_bytes(8)

        with open(filepath, 'rb') as f:
            self.data = f.read()

        self.sha256       = hashlib.sha256(self.data).hexdigest()
        self.total_bytes  = len(self.data)
        self.total_frames = max(1, math.ceil(self.total_bytes / QR_PAYLOAD_PER_FRAME))
        eff_bps           = QR_PAYLOAD_PER_FRAME * fps

        print(f"\n[MARICHI QR SENDER — Option D]")
        print(f"  file        : {os.path.basename(filepath)}")
        print(f"  size        : {self.total_bytes:,} B  ({self.total_bytes/1024/1024:.2f} MB)")
        print(f"  SHA-256     : {self.sha256}")
        print(f"  session     : {self.session_id.hex()}")
        print(f"  frames      : {self.total_frames}")
        print(f"  QR version  : {QR_VERSION}  ECC: L  payload: {QR_PAYLOAD_PER_FRAME:,} B/frame")
        print(f"  fps         : {fps}  hold: {self.hold_ms}ms")
        print(f"  ACK mode    : {'camera ' + str(ack_cam) if ack_cam >= 0 else 'DISABLED (timer)'}")
        print(f"  eff. rate   : ~{eff_bps/1024:.1f} KB/s")
        print(f"  ETA         : {self.total_bytes/eff_bps/60:.1f} min")

    def _build_frames(self) -> list[np.ndarray]:
        print(f"\n[BUILDING {self.total_frames} QR FRAMES]")
        frames = []
        for i in range(self.total_frames):
            s   = i * QR_PAYLOAD_PER_FRAME
            e   = min(s + QR_PAYLOAD_PER_FRAME, self.total_bytes)
            raw = encode_qr_frame(self.data[s:e], i,
                                  self.total_frames, self.session_id)
            img = _make_qr_image(raw, i, self.total_frames,
                                 self.screen_w, self.screen_h)
            frames.append(img)
            if (i + 1) % 5 == 0 or (i + 1) == self.total_frames:
                print(f"\r  built {i+1}/{self.total_frames}", end='', flush=True)
        print(f"\n[BUILD OK]  {len(frames)} QR frames")
        return frames

    def run(self) -> None:
        frames   = self._build_frames()
        n        = self.total_frames
        ack_det  = QRACKDetector(self.ack_cam) if self.ack_cam >= 0 else None

        cv2.namedWindow("MARICHI QR TX", cv2.WND_PROP_FULLSCREEN)
        cv2.setWindowProperty("MARICHI QR TX", cv2.WND_PROP_FULLSCREEN,
                              cv2.WINDOW_FULLSCREEN)

        print(f"\n[SENDER] Displaying QR stream.")
        if ack_det:
            print(f"         ACK camera {self.ack_cam} watching receiver's ACK window.")
        else:
            print(f"         Timer mode — cycling at {self.fps} fps.")
        print(f"         Press Q / ESC to quit.\n")

        try:
            if ack_det:
                self._run_ack_mode(frames, ack_det)
            else:
                self._run_timer_mode(frames)
        finally:
            if ack_det:
                ack_det.release()
            cv2.destroyAllWindows()

    def _run_timer_mode(self, frames: list[np.ndarray]) -> None:
        idx   = 0
        cycle = 0
        t0    = time.time()
        while True:
            cv2.imshow("MARICHI QR TX", frames[idx])
            key = cv2.waitKey(self.hold_ms) & 0xFF
            if key in (ord('q'), 27):
                break
            idx += 1
            if idx >= len(frames):
                idx = 0
                cycle += 1
                elapsed = time.time() - t0
                rate    = (self.total_bytes * cycle) / elapsed / 1024
                print(f"\r[CYCLE {cycle:4d}]  {elapsed:.0f}s  {rate:.1f} KB/s", end='', flush=True)

    def _run_ack_mode(self, frames: list[np.ndarray], ack_det: QRACKDetector) -> None:
        n               = len(frames)
        retries: dict[int, int] = {}
        acked           = 0
        idx             = 0

        while idx < n:
            cv2.imshow("MARICHI QR TX", frames[idx])
            key = cv2.waitKey(50) & 0xFF
            if key in (ord('q'), 27):
                print("\n[SENDER] Aborted.")
                return

            sig       = ack_det.read()
            frame_start = getattr(self, '_frame_start', time.time())
            if idx not in retries:
                self._frame_start = time.time()
                frame_start = self._frame_start

            if sig == "ACK":
                acked += 1
                print(f"\r  ✅ QR Frame {idx+1:4d}/{n} ACK'd  (retries={retries.get(idx,0)})")
                idx += 1
                self._frame_start = time.time()

            elif sig == "NACK":
                retries[idx] = retries.get(idx, 0) + 1
                print(f"\r  ❌ QR Frame {idx+1:4d}/{n} NACK  (retry #{retries[idx]})")

            elif time.time() - frame_start > ACK_WAIT_S:
                print(f"\r  ⚠️  QR Frame {idx+1:4d}/{n} no ACK in {ACK_WAIT_S:.0f}s — advancing")
                idx += 1
                self._frame_start = time.time()

        print(f"\n\n[SENDER] All {n} QR frames transmitted!")

        # Green completion screen
        done = np.full((self.screen_h, self.screen_w, 3), ACK_GREEN, dtype=np.uint8)
        cv2.putText(done, "QR TRANSFER COMPLETE",
                    (self.screen_w // 2 - 320, self.screen_h // 2),
                    cv2.FONT_HERSHEY_SIMPLEX, 2.0, (255, 255, 255), 4)
        cv2.imshow("MARICHI QR TX", done)
        cv2.waitKey(3000)


# ── Receiver ──────────────────────────────────────────────────────────────────

class QRReceiver:
    """
    Option D receiver — scans QR codes from camera.

    Works with:
      • Laptop webcam aimed at sender's screen
      • Phone camera using the companion web QR scanner (Phase 2)

    Usage:
        r = QRReceiver("output.bin", cam_index=0)
        result = r.run()
    """

    def __init__(self, output_path: str,
                 cam_index: int = 0,
                 timeout_s: int = 7200,
                 show_ack: bool = True):
        if cv2 is None:
            raise RuntimeError("opencv-python not installed")

        self.output_path = output_path
        self.cam_index   = cam_index
        self.timeout_s   = timeout_s
        self.show_ack    = show_ack

        self.session_id:   Optional[bytes] = None
        self.total_frames: int             = 0
        self.received:     dict[int, bytes] = {}
        self.cksum_fails:  int             = 0

        # OpenCV built-in QR detector (no extra library needed)
        self._qr_detector = cv2.QRCodeDetector()

        print(f"\n[MARICHI QR RECEIVER — Option D]")
        print(f"  output      : {output_path}")
        print(f"  camera      : device {cam_index}")
        print(f"  timeout     : {timeout_s}s")
        print(f"  ACK display : {'enabled' if show_ack else 'disabled'}")

    def _open_camera(self) -> cv2.VideoCapture:
        cap = cv2.VideoCapture(self.cam_index)
        if not cap.isOpened():
            raise RuntimeError(f"Cannot open camera {self.cam_index}")
        cap.set(cv2.CAP_PROP_FRAME_WIDTH,  1920)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)
        cap.set(cv2.CAP_PROP_AUTOFOCUS, 1)
        return cap

    def run(self) -> Optional[str]:
        cap = self._open_camera()
        ack = QRACKDisplay(700, 350) if self.show_ack else None

        print(f"\n[RECEIVER] Camera open.")
        print(f"           Aim camera at sender's QR display.")
        if ack:
            print(f"           ACK window open — position so sender's camera sees it.")
        print(f"           Press Q / ESC to abort.\n")

        start_time   = time.time()
        total_cap    = 0
        decode_ok    = 0
        decode_fail  = 0

        try:
            while True:
                if time.time() - start_time > self.timeout_s:
                    print("\n[RECEIVER] Timeout.")
                    break

                ret, frame = cap.read()
                if not ret:
                    continue
                total_cap += 1

                # Show live preview
                preview = cv2.resize(frame, (640, 360))
                n_recv  = len(self.received)
                tot_str = str(self.total_frames) if self.total_frames else "?"
                cv2.putText(preview, f"QR RX  {n_recv}/{tot_str}",
                            (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 255, 0), 2)
                cv2.imshow("MARICHI QR RX — Camera", preview)

                key = cv2.waitKey(1) & 0xFF
                if key in (ord('q'), 27):
                    print("\n[RECEIVER] Aborted.")
                    break

                # Periodically show GREEN (processing)
                if ack and total_cap % 10 == 0:
                    ack.processing(n_recv, self.total_frames or 0)

                # Decode QR code from frame
                data, bbox, _ = self._qr_detector.detectAndDecode(frame)
                if not data or bbox is None:
                    decode_fail += 1
                    continue

                # QR gives us a string — we encoded binary via latin-1
                try:
                    raw = data.encode('latin-1')
                except Exception:
                    decode_fail += 1
                    continue

                result = decode_qr_frame(raw)
                if result is None:
                    decode_fail += 1
                    if ack and decode_fail % 20 == 0:
                        ack.nack(n_recv, self.total_frames or 0)
                        cv2.waitKey(ACK_SIGNAL_MS)
                    continue

                decode_ok += 1

                frame_no     = result["frame_no"]
                total_frames = result["total_frames"]
                session_id   = result["session_id"]
                payload      = result["payload"]
                crc_ok       = result["crc_ok"]

                # Session init
                if self.session_id is None:
                    self.session_id   = session_id
                    self.total_frames = total_frames
                    print(f"\n[SESSION]  id={session_id.hex()}  frames={total_frames}")

                if session_id != self.session_id:
                    continue

                already_have = frame_no in self.received

                if not already_have:
                    if crc_ok:
                        self.received[frame_no] = payload
                        n_recv = len(self.received)
                        pct    = 100 * n_recv // max(total_frames, 1)
                        print(f"\n  ✅ QR Frame {frame_no+1}/{total_frames}  ({pct}%)  "
                              f"CRC32={zlib.crc32(payload)&0xFFFFFFFF:08X}")
                        if ack:
                            ack.ack(frame_no, total_frames)
                            cv2.waitKey(ACK_SIGNAL_MS)
                    else:
                        self.cksum_fails += 1
                        print(f"\n  ⚠️  QR Frame {frame_no}: CRC MISMATCH — NACK")
                        if ack:
                            ack.nack(frame_no, total_frames)
                            cv2.waitKey(ACK_SIGNAL_MS)
                else:
                    # Duplicate — still ACK so sender advances
                    if ack:
                        ack.ack(frame_no, total_frames)
                        cv2.waitKey(ACK_SIGNAL_MS // 2)

                if self.total_frames > 0 and len(self.received) >= self.total_frames:
                    print(f"\n[RECEIVER] All {self.total_frames} QR frames received!")
                    if ack:
                        ack._show(ACK_BLUE, f"ALL {self.total_frames} QR FRAMES COMPLETE ✔",
                                  self.total_frames, self.total_frames)
                        cv2.waitKey(3000)
                    break

        finally:
            cap.release()
            if ack:
                ack.close()
            cv2.destroyAllWindows()

        if not self.received:
            print("[RECEIVER] No QR frames decoded.")
            return None

        return self._assemble()

    def _assemble(self) -> str:
        print(f"\n[ASSEMBLING]  {len(self.received)}/{self.total_frames} frames ...")
        missing = set(range(self.total_frames)) - set(self.received.keys())
        if missing:
            print(f"  ⚠️  MISSING: {sorted(missing)[:10]}"
                  f"{'...' if len(missing) > 10 else ''} ({len(missing)} frames)")

        out = bytearray()
        for i in range(self.total_frames):
            out.extend(self.received.get(i, b''))

        os.makedirs(os.path.dirname(os.path.abspath(self.output_path)), exist_ok=True)
        with open(self.output_path, 'wb') as f:
            f.write(out)

        sha      = hashlib.sha256(out).hexdigest()
        missing_n = len(missing) if missing else 0
        verdict  = "✅ COMPLETE" if not missing_n else f"⚠️  {missing_n} MISSING"
        print(f"[ASSEMBLED]  {len(out):,} B → {self.output_path}")
        print(f"             SHA-256     : {sha}")
        print(f"             Frames recv : {len(self.received)}/{self.total_frames}")
        print(f"             CRC fails   : {self.cksum_fails}")
        print(f"             Status      : {verdict}")
        return self.output_path
