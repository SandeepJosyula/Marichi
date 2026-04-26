"""
MARICHI — ACK System  (v0.2)

ACKSignal  : enum for the three possible signals
ACKDisplay : shown on RECEIVER screen → sender's camera reads it
ACKDetector: runs on SENDER, watches its webcam for receiver's ACK flashes

Physical setup:
  ┌──────────────────┐         ┌──────────────────────┐
  │  SENDER           │         │  RECEIVER             │
  │                   │         │                       │
  │  [DATA SCREEN]    │◄──────►│  [ACK WINDOW]         │
  │  Data frames      │  cams   │  Green / Blue / Yellow│
  │  displayed here   │  face   │  flashed here         │
  │                   │  each   │                       │
  │  📷 ACK cam       │  other  │  📷 Data cam          │
  │  (watches recv.   │         │  (reads sender screen)│
  │   ACK window)     │         │                       │
  └──────────────────┘         └──────────────────────┘
"""

from __future__ import annotations
import threading
import queue
import time
from enum import Enum

import numpy as np
import cv2

from . import config as C


# ── Signal enum ────────────────────────────────────────────────────────────────

class ACKSignal(Enum):
    NONE       = 'none'
    PROCESSING = 'green'    # receiver is currently decoding this frame
    SUCCESS    = 'blue'     # frame decoded + all checksums verified ✅
    ERROR      = 'yellow'   # decode failed or checksum mismatch    ❌


# ── Display colours (BGR) and labels ──────────────────────────────────────────

_DISPLAY: dict[ACKSignal, tuple[tuple, str]] = {
    ACKSignal.NONE:       ((20,  20,  20),  "WAITING"),
    ACKSignal.PROCESSING: (C.ACK_COLOR_GREEN,  "● PROCESSING"),
    ACKSignal.SUCCESS:    (C.ACK_COLOR_BLUE,   "✔ ACK — FRAME OK"),
    ACKSignal.ERROR:      (C.ACK_COLOR_YELLOW, "✘ NACK — RETRY"),
}

# ── HSV detection ranges (what sender's camera must recognise) ─────────────────

_HSV_RANGES: dict[ACKSignal, tuple] = {
    ACKSignal.PROCESSING: C.ACK_HSV_GREEN,
    ACKSignal.SUCCESS:    C.ACK_HSV_BLUE,
    ACKSignal.ERROR:      C.ACK_HSV_YELLOW,
}


# ═══════════════════════════════════════════════════════════════════════════════
#  ACKDisplay — shown on RECEIVER machine
# ═══════════════════════════════════════════════════════════════════════════════

class ACKDisplay:
    """
    Full-screen (or large window) colored panel shown on the receiver.
    The sender's webcam reads this window to detect ACK signals.

    Must be called from the main thread (macOS OpenCV requirement).
    """

    WIN_NAME = "MARICHI_ACK"

    def __init__(self, width: int = 960, height: int = 540):
        self.w = width
        self.h = height
        self._canvas = np.zeros((height, width, 3), dtype=np.uint8)
        self._current_signal: ACKSignal = ACKSignal.NONE
        cv2.namedWindow(self.WIN_NAME, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(self.WIN_NAME, width, height)
        self.show(ACKSignal.NONE)

    # ── Public ─────────────────────────────────────────────────────────────────

    def show(self, signal: ACKSignal,
             frame_no: int | None  = None,
             total: int | None     = None,
             extra: str            = "") -> None:
        """
        Update the ACK window with the given signal color.

        signal   : ACKSignal enum value
        frame_no : frame index being processed (optional, shown in window)
        total    : total frames (optional)
        extra    : extra status string (e.g. checksum hex)
        """
        color, label = _DISPLAY[signal]
        self._canvas[:] = color

        # Main label (large, centred)
        cv2.putText(self._canvas, label,
                    (self.w // 2 - 200, self.h // 2 - 20),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.4, (255, 255, 255), 3,
                    cv2.LINE_AA)

        # Frame counter
        if frame_no is not None and total is not None:
            progress = f"Frame {frame_no + 1} / {total}"
            cv2.putText(self._canvas, progress,
                        (20, self.h - 60),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 255, 255), 2)
            # Progress bar
            bar_w = int(self.w * (frame_no + 1) / max(total, 1))
            cv2.rectangle(self._canvas,
                          (0, self.h - 20), (bar_w, self.h),
                          (255, 255, 255), -1)

        # Extra info (checksum, etc.)
        if extra:
            cv2.putText(self._canvas, extra,
                        (20, self.h - 90),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)

        cv2.imshow(self.WIN_NAME, self._canvas)
        self._current_signal = signal

    def tick(self) -> None:
        """Call cv2.waitKey(1) to keep the window alive — call from main loop."""
        cv2.waitKey(1)

    def close(self) -> None:
        try:
            cv2.destroyWindow(self.WIN_NAME)
        except Exception:
            pass

    @property
    def current(self) -> ACKSignal:
        return self._current_signal


# ═══════════════════════════════════════════════════════════════════════════════
#  ACKDetector — runs on SENDER, watches webcam for receiver's ACK flashes
# ═══════════════════════════════════════════════════════════════════════════════

class ACKDetector:
    """
    Background thread that monitors a camera (sender's webcam) and detects
    ACK color flashes coming from the receiver's ACK window.

    Usage:
        det = ACKDetector(cam_index=1)
        det.start()
        # in sender loop:
        sig = det.get_latest()   # non-blocking, returns ACKSignal
        det.stop()
    """

    def __init__(self, cam_index: int,
                 poll_interval: float = 0.05):   # 20 fps polling
        self.cam_index     = cam_index
        self.poll_interval = poll_interval
        self._queue: queue.Queue[ACKSignal] = queue.Queue(maxsize=20)
        self._stop_evt = threading.Event()
        self._thread:  threading.Thread | None = None
        self._last:    ACKSignal = ACKSignal.NONE

    def start(self) -> None:
        """Start background detection thread."""
        self._stop_evt.clear()
        self._thread = threading.Thread(target=self._loop,
                                        name="ACKDetector",
                                        daemon=True)
        self._thread.start()
        print(f"[ACK DETECTOR] Started on camera {self.cam_index}")

    def stop(self) -> None:
        """Stop background thread gracefully."""
        self._stop_evt.set()
        if self._thread:
            self._thread.join(timeout=3)

    def get_latest(self) -> ACKSignal:
        """
        Non-blocking read: returns the most recent ACKSignal seen,
        or ACKSignal.NONE if nothing detected yet.
        Drains the queue keeping only the latest.
        """
        latest = ACKSignal.NONE
        while not self._queue.empty():
            try:
                latest = self._queue.get_nowait()
            except queue.Empty:
                break
        if latest != ACKSignal.NONE:
            self._last = latest
        return latest

    def peek_last(self) -> ACKSignal:
        """Return most recently confirmed signal without draining queue."""
        return self._last

    # ── Background loop ────────────────────────────────────────────────────────

    def _loop(self) -> None:
        cap = cv2.VideoCapture(self.cam_index)
        if not cap.isOpened():
            print(f"[ACK DETECTOR] ⚠️  Cannot open camera {self.cam_index} "
                  f"— ACK detection disabled")
            return

        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        consecutive: dict[ACKSignal, int] = {s: 0 for s in ACKSignal}
        CONFIRM_FRAMES = 3   # must see same signal N frames in a row

        try:
            while not self._stop_evt.is_set():
                ret, frame = cap.read()
                if not ret:
                    time.sleep(0.1)
                    continue

                detected = self._detect_color(frame)

                for sig in ACKSignal:
                    if sig == detected:
                        consecutive[sig] += 1
                    else:
                        consecutive[sig] = 0

                if consecutive.get(detected, 0) >= CONFIRM_FRAMES:
                    if self._queue.full():
                        try:
                            self._queue.get_nowait()
                        except queue.Empty:
                            pass
                    self._queue.put(detected)
                    # Reset so we don't fire repeatedly for the same hold
                    consecutive[detected] = 0

                time.sleep(self.poll_interval)
        finally:
            cap.release()

    def _detect_color(self, bgr: np.ndarray) -> ACKSignal:
        """Detect dominant ACK colour in a BGR frame. Returns ACKSignal or NONE."""
        hsv      = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
        total_px = bgr.shape[0] * bgr.shape[1]

        for signal, (lo, hi) in _HSV_RANGES.items():
            mask     = cv2.inRange(hsv, np.array(lo), np.array(hi))
            coverage = np.count_nonzero(mask) / total_px
            if coverage >= C.ACK_MIN_COVERAGE:
                return signal

        return ACKSignal.NONE
