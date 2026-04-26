"""
MARICHI — Receiver

Opens camera → captures frames → decodes → reassembles file.
Tracks which frame IDs are received; stops when all frames collected.

Usage (via CLI wrapper):
    python receive.py <output_path> [--cam CAM_INDEX] [--timeout SECS]
"""

from __future__ import annotations
import os
import time
import hashlib
import numpy as np
import cv2
from tqdm import tqdm

from . import config as C
from .frame_codec import decode_frame


class Receiver:
    def __init__(self, output_path: str,
                 cam_index: int  = C.CAM_INDEX,
                 timeout_s: int  = 7200):    # 2-hour default timeout
        self.output_path = output_path
        self.cam_index   = cam_index
        self.timeout_s   = timeout_s

        # State built from first decoded frame
        self.session_id:    bytes | None = None
        self.total_frames:  int          = 0
        self.received:      dict[int, bytes] = {}   # frame_no → payload

        print(f"[MARICHI RECEIVER]")
        print(f"  output   : {output_path}")
        print(f"  camera   : device {cam_index}")
        print(f"  timeout  : {timeout_s}s")
        C.print_stats()

    # ── Camera helpers ─────────────────────────────────────────────────────────

    def _open_camera(self) -> cv2.VideoCapture:
        cap = cv2.VideoCapture(self.cam_index)
        if not cap.isOpened():
            raise RuntimeError(f"Cannot open camera {self.cam_index}")
        # Request max resolution
        cap.set(cv2.CAP_PROP_FRAME_WIDTH,  1920)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)
        cap.set(cv2.CAP_PROP_FPS, 30)
        return cap

    # ── Main capture loop ──────────────────────────────────────────────────────

    def run(self) -> str | None:
        """
        Capture frames until all received.
        Returns path of written output file, or None on timeout.
        """
        cap = self._open_camera()
        print("\n[RECEIVER] Camera open. Scanning for MARICHI frames ...")
        print("           Aim camera at sender screen.")
        print("           Press  Q  to abort.\n")

        pbar         = None
        start_time   = time.time()
        last_log     = start_time
        total_cap    = 0   # total camera frames grabbed
        decode_ok    = 0
        decode_fail  = 0

        try:
            while True:
                # ── Timeout check ─────────────────────────────────────────────
                if time.time() - start_time > self.timeout_s:
                    print("\n[RECEIVER] Timeout reached.")
                    break

                ret, frame = cap.read()
                if not ret:
                    continue
                total_cap += 1

                # ── Show live preview (small) ─────────────────────────────────
                preview = cv2.resize(frame, (640, 360))
                cv2.putText(preview,
                            f"Got {len(self.received)}/{self.total_frames or '?'}",
                            (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)
                cv2.imshow("MARICHI RX", preview)
                key = cv2.waitKey(1) & 0xFF
                if key == ord('q') or key == 27:
                    print("\n[RECEIVER] Aborted by user.")
                    break

                # ── Decode ────────────────────────────────────────────────────
                result = decode_frame(frame)
                if result is None:
                    decode_fail += 1
                    continue
                decode_ok += 1

                payload, frame_no, total_frames, session_id = result

                # First successful decode → initialise session
                if self.session_id is None:
                    self.session_id   = session_id
                    self.total_frames = total_frames
                    pbar = tqdm(total=total_frames, unit='frame',
                                desc='Receiving')
                    print(f"\n[SESSION]  id={session_id.hex()}  "
                          f"total_frames={total_frames}")

                # Ignore frames from different sessions
                if session_id != self.session_id:
                    continue

                # Store if not already received
                if frame_no not in self.received:
                    self.received[frame_no] = payload
                    if pbar:
                        pbar.update(1)

                # Periodic log
                now = time.time()
                if now - last_log >= 5.0:
                    pct = 100 * len(self.received) / max(self.total_frames, 1)
                    rate = decode_ok / (now - start_time)
                    print(f"\r[RX]  {len(self.received)}/{self.total_frames}"
                          f"  ({pct:.1f}%)  cap={total_cap}  ok={decode_ok}"
                          f"  fail={decode_fail}  {rate:.1f} fr/s",
                          end='', flush=True)
                    last_log = now

                # ── Done? ─────────────────────────────────────────────────────
                if (self.total_frames > 0
                        and len(self.received) >= self.total_frames):
                    print(f"\n[RECEIVER] All {self.total_frames} frames received!")
                    if pbar:
                        pbar.close()
                    break

        finally:
            cap.release()
            cv2.destroyAllWindows()

        if len(self.received) == 0:
            print("[RECEIVER] No frames decoded.")
            return None

        return self._assemble()

    # ── Assemble received frames into output file ──────────────────────────────

    def _assemble(self) -> str:
        """Concatenate payloads in frame order → write output file."""
        print(f"\n[ASSEMBLING]  {len(self.received)} frames ...")

        # Report missing frames
        missing = set(range(self.total_frames)) - set(self.received.keys())
        if missing:
            print(f"  ⚠️  MISSING frames: {sorted(missing)[:20]}"
                  f"{'...' if len(missing)>20 else ''}")
            print(f"     {len(missing)} frames missing — output will be INCOMPLETE")

        out = bytearray()
        for i in range(self.total_frames):
            chunk = self.received.get(i, b'')   # missing frame → zeroes
            out.extend(chunk)

        os.makedirs(os.path.dirname(os.path.abspath(self.output_path)), exist_ok=True)
        with open(self.output_path, 'wb') as f:
            f.write(out)

        sha = hashlib.sha256(out).hexdigest()
        print(f"[ASSEMBLED]  {len(out):,} bytes → {self.output_path}")
        print(f"             SHA-256: {sha}")
        print(f"             Frames received: {len(self.received)}/{self.total_frames}")
        if missing:
            print(f"             ❌ {len(missing)} frames missing — run validator to confirm damage")
        else:
            print(f"             ✅ All frames received — run validator for final confirmation")
        return self.output_path
