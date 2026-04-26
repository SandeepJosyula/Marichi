"""
MARICHI — Sender

Reads source file → pre-builds all frames → displays in a fullscreen loop
until interrupted or until receiver signals done.

Usage (via CLI wrapper):
    python send.py <file_path> [--block BLOCK_SIZE] [--hold HOLD_MS]
"""

from __future__ import annotations
import os
import time
import hashlib
import secrets
import math
import struct
import numpy as np
import cv2
from tqdm import tqdm

from . import config as C
from .frame_codec import encode_frame_fast


class Sender:
    def __init__(self, filepath: str,
                 block_size: int  = C.BLOCK_SIZE,
                 hold_ms: int     = C.FRAME_HOLD_MS):
        self.filepath   = filepath
        self.block_size = block_size
        self.hold_ms    = hold_ms
        self.session_id = secrets.token_bytes(8)

        # Read source file
        with open(filepath, 'rb') as f:
            self.data = f.read()

        self.sha256 = hashlib.sha256(self.data).hexdigest()
        self.total_bytes = len(self.data)

        # Compute number of frames needed
        self.total_frames = math.ceil(self.total_bytes / C.PAYLOAD_PER_FRAME)
        if self.total_frames == 0:
            self.total_frames = 1

        print(f"[MARICHI SENDER]")
        print(f"  file       : {os.path.basename(filepath)}")
        print(f"  size       : {self.total_bytes:,} bytes ({self.total_bytes/1024/1024:.2f} MB)")
        print(f"  SHA-256    : {self.sha256}")
        print(f"  session    : {self.session_id.hex()}")
        print(f"  frames     : {self.total_frames}")
        print(f"  payload/fr : {C.PAYLOAD_PER_FRAME:,} B")
        C.print_stats()

    def build_frames(self) -> list[np.ndarray]:
        """Pre-encode all frames. Returns list of BGR images."""
        print(f"\n[BUILDING {self.total_frames} FRAMES ...]")
        frames = []
        for i in tqdm(range(self.total_frames), unit='frame'):
            start = i * C.PAYLOAD_PER_FRAME
            end   = min(start + C.PAYLOAD_PER_FRAME, self.total_bytes)
            payload = self.data[start:end]
            img = encode_frame_fast(payload, i, self.total_frames, self.session_id)
            frames.append(img)
        print(f"[BUILD COMPLETE]  {len(frames)} frames ready")
        return frames

    def _build_manifest_frame(self) -> np.ndarray:
        """Frame 0 always carries session manifest: SHA-256 + filename + total_bytes."""
        manifest = (self.sha256.encode()
                    + b'|' + os.path.basename(self.filepath).encode()
                    + b'|' + str(self.total_bytes).encode())
        return encode_frame_fast(manifest, 0, self.total_frames, self.session_id)

    def run(self) -> None:
        """Build frames and enter display loop."""
        frames = self.build_frames()

        print("\n[SENDER] Opening fullscreen display window.")
        print("         Press  Q  to quit.")
        print(f"         Cycling through {self.total_frames} frames at ~{1000//self.hold_ms} fps.\n")

        cv2.namedWindow("MARICHI", cv2.WND_PROP_FULLSCREEN)
        cv2.setWindowProperty("MARICHI", cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)

        idx = 0
        cycle = 0
        start_time = time.time()
        try:
            while True:
                cv2.imshow("MARICHI", frames[idx])
                key = cv2.waitKey(self.hold_ms) & 0xFF
                if key == ord('q') or key == 27:   # Q or ESC
                    print("\n[SENDER] Stopped by user.")
                    break

                idx += 1
                if idx >= self.total_frames:
                    idx = 0
                    cycle += 1
                    elapsed = time.time() - start_time
                    rate = (self.total_bytes * cycle) / elapsed / 1024 / 1024
                    print(f"\r[CYCLE {cycle:4d}]  elapsed={elapsed:.0f}s  "
                          f"effective={rate:.2f} MB/s", end='', flush=True)
        finally:
            cv2.destroyAllWindows()
            print("\n[SENDER] Done.")
