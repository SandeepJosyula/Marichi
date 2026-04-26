"""
MARICHI — Sender  (v0.2 — ACK-aware with auto-advance)

Two modes:
  • ACK mode   (--ack-cam N): sender watches receiver's ACK window via webcam.
                              Auto-advances frame only on BLUE (SUCCESS) signal.
                              Re-shows same frame on YELLOW (ERROR).
  • Timer mode (no --ack-cam): original time-based cycling (fallback).
"""

from __future__ import annotations
import os
import time
import hashlib
import secrets
import math
import numpy as np
import cv2
from tqdm import tqdm

from . import config as C
from .frame_codec import encode_frame_fast
from .ack import ACKDetector, ACKSignal


class Sender:
    def __init__(self, filepath: str,
                 block_size: int  = C.BLOCK_SIZE,
                 hold_ms: int     = C.FRAME_HOLD_MS,
                 ack_cam: int     = -1):
        """
        filepath  : path to file to transmit
        block_size: pixels per data cell (1=fast, 4=robust)
        hold_ms   : ms per frame in timer mode (ignored if ack_cam >= 0)
        ack_cam   : camera index for ACK detection (-1 = disabled)
        """
        self.filepath   = filepath
        self.block_size = block_size
        self.hold_ms    = hold_ms
        self.ack_cam    = ack_cam
        self.session_id = secrets.token_bytes(8)

        with open(filepath, 'rb') as f:
            self.data = f.read()

        self.sha256      = hashlib.sha256(self.data).hexdigest()
        self.total_bytes = len(self.data)
        self.total_frames = max(1, math.ceil(self.total_bytes / C.PAYLOAD_PER_FRAME))

        print(f"\n[MARICHI SENDER  v0.2]")
        print(f"  file        : {os.path.basename(filepath)}")
        print(f"  size        : {self.total_bytes:,} B  ({self.total_bytes/1024/1024:.2f} MB)")
        print(f"  SHA-256     : {self.sha256}")
        print(f"  session     : {self.session_id.hex()}")
        print(f"  frames      : {self.total_frames}")
        print(f"  ACK mode    : {'camera ' + str(ack_cam) if ack_cam >= 0 else 'DISABLED (timer)'}")
        C.print_stats()

    # ── Frame building ─────────────────────────────────────────────────────────

    def build_frames(self) -> list[np.ndarray]:
        print(f"\n[BUILDING {self.total_frames} FRAMES — includes CRC32 + checksum strip]")
        frames = []
        for i in tqdm(range(self.total_frames), unit='fr'):
            s = i * C.PAYLOAD_PER_FRAME
            e = min(s + C.PAYLOAD_PER_FRAME, self.total_bytes)
            frames.append(encode_frame_fast(self.data[s:e], i,
                                            self.total_frames, self.session_id))
        print(f"[BUILD OK]  {len(frames)} frames  "
              f"(SHA-256 embedded per frame + checksum strip)")
        return frames

    # ── Main run ───────────────────────────────────────────────────────────────

    def run(self) -> None:
        frames  = self.build_frames()
        n       = self.total_frames
        ack_det = None

        if self.ack_cam >= 0:
            ack_det = ACKDetector(self.ack_cam)
            ack_det.start()

        print(f"\n[SENDER] Opening fullscreen window.")
        if ack_det:
            print(f"         ACK camera {self.ack_cam} monitoring receiver screen.")
            print(f"         Will auto-advance on BLUE ACK, re-show on YELLOW NACK.")
        else:
            print(f"         Timer mode: cycling at ~{1000//self.hold_ms} fps.")
        print(f"         Press  Q / ESC  to quit.\n")

        cv2.namedWindow("MARICHI TX", cv2.WND_PROP_FULLSCREEN)
        cv2.setWindowProperty("MARICHI TX", cv2.WND_PROP_FULLSCREEN,
                              cv2.WINDOW_FULLSCREEN)

        if ack_det:
            self._run_ack_mode(frames, ack_det)
        else:
            self._run_timer_mode(frames)

        if ack_det:
            ack_det.stop()
        cv2.destroyAllWindows()
        print("\n[SENDER] Done.")

    # ── Timer mode (original) ──────────────────────────────────────────────────

    def _run_timer_mode(self, frames: list[np.ndarray]) -> None:
        idx   = 0
        cycle = 0
        t0    = time.time()
        while True:
            cv2.imshow("MARICHI TX", frames[idx])
            key = cv2.waitKey(self.hold_ms) & 0xFF
            if key in (ord('q'), 27):
                break
            idx += 1
            if idx >= len(frames):
                idx = 0
                cycle += 1
                elapsed = time.time() - t0
                rate    = (self.total_bytes * cycle) / elapsed / 1024 / 1024
                print(f"\r[CYCLE {cycle:4d}]  {elapsed:.0f}s  {rate:.2f} MB/s", end='', flush=True)

    # ── ACK mode (auto-advance) ────────────────────────────────────────────────

    def _run_ack_mode(self, frames: list[np.ndarray],
                      ack_det: ACKDetector) -> None:
        """
        Display protocol:
          1. Show frame N
          2. Wait for ACK (poll detector every 50 ms)
          3. BLUE  → advance to N+1
          4. YELLOW → re-show N (NACK)
          5. Q/ESC → quit
          6. Timeout (30 s) without ACK → advance anyway + warn
        """
        idx             = 0
        n               = len(frames)
        ack_timeout_s   = 30
        retries: dict[int, int] = {}
        start           = time.time()
        acked           = 0

        pbar = tqdm(total=n, unit='fr', desc='Transmitted')

        while idx < n:
            cv2.imshow("MARICHI TX", frames[idx])

            # Overlay: frame number + retry count
            overlay = frames[idx].copy()
            rc  = retries.get(idx, 0)
            txt = f"Frame {idx+1}/{n}  retries={rc}  acked={acked}"
            cv2.putText(overlay, txt, (20, 40),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 0), 2)
            cv2.imshow("MARICHI TX", overlay)

            frame_start = time.time()
            advanced    = False

            while not advanced:
                key = cv2.waitKey(50) & 0xFF
                if key in (ord('q'), 27):
                    print("\n[SENDER] Aborted.")
                    return

                sig = ack_det.get_latest()

                if sig == ACKSignal.SUCCESS:
                    # Receiver confirmed this frame ✅
                    acked += 1
                    pbar.update(1)
                    print(f"\r  ✅ Frame {idx+1:4d}/{n} ACK'd  "
                          f"(retries={retries.get(idx,0)})", end='')
                    idx     += 1
                    advanced = True

                elif sig == ACKSignal.ERROR:
                    # Receiver reported error → re-show same frame ❌
                    retries[idx] = retries.get(idx, 0) + 1
                    print(f"\r  ❌ Frame {idx+1:4d}/{n} NACK  "
                          f"(retry #{retries[idx]})", end='')
                    # Re-display same frame — outer loop handles it
                    frame_start = time.time()

                elif time.time() - frame_start > ack_timeout_s:
                    # No ACK received in timeout — advance with warning
                    print(f"\r  ⚠️  Frame {idx+1:4d}/{n} no ACK in {ack_timeout_s}s "
                          f"— advancing anyway", end='')
                    pbar.update(1)
                    idx     += 1
                    advanced = True

        pbar.close()
        elapsed = time.time() - start
        print(f"\n\n[SENDER] All {n} frames transmitted!")
        print(f"  total time : {elapsed:.0f}s")
        print(f"  effective  : {self.total_bytes / elapsed / 1024 / 1024:.2f} MB/s")
        print(f"  ACK'd      : {acked}/{n}")
        print(f"  timeouts   : {n - acked}")

        # Show green "ALL DONE" screen for 3 seconds
        done_img = np.full((C.SCREEN_H, C.SCREEN_W, 3), C.ACK_COLOR_GREEN, dtype=np.uint8)
        cv2.putText(done_img, "TRANSFER COMPLETE",
                    (C.SCREEN_W//2 - 300, C.SCREEN_H//2),
                    cv2.FONT_HERSHEY_SIMPLEX, 2.0, (255, 255, 255), 4)
        cv2.imshow("MARICHI TX", done_img)
        cv2.waitKey(3000)
