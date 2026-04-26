"""
MARICHI — Receiver  (v0.2 — ACK signal display + three-way checksum)

ACK protocol:
  🟢 GREEN   → displayed while decoding current frame
  🔵 BLUE    → frame decoded + header CRC32 ✅ + strip CRC32 ✅ → sender advances
  🟡 YELLOW  → decode failed OR any checksum mismatch → sender retries
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
from .ack import ACKDisplay, ACKSignal


class Receiver:
    def __init__(self, output_path: str,
                 cam_index: int  = C.CAM_INDEX,
                 timeout_s: int  = 7200,
                 show_ack: bool  = True):
        """
        output_path : file to write
        cam_index   : camera for reading sender screen
        timeout_s   : max wait seconds
        show_ack    : show ACK window (required for ACK mode; can disable for headless)
        """
        self.output_path = output_path
        self.cam_index   = cam_index
        self.timeout_s   = timeout_s
        self.show_ack    = show_ack

        self.session_id:   bytes | None = None
        self.total_frames: int          = 0
        self.received:     dict[int, bytes] = {}
        self.cksum_failures: list[int]      = []   # frame_nos where cksum failed

        print(f"\n[MARICHI RECEIVER  v0.2]")
        print(f"  output      : {output_path}")
        print(f"  camera      : device {cam_index}")
        print(f"  timeout     : {timeout_s}s")
        print(f"  ACK display : {'enabled' if show_ack else 'disabled'}")
        C.print_stats()

    # ── Camera setup ───────────────────────────────────────────────────────────

    def _open_camera(self) -> cv2.VideoCapture:
        cap = cv2.VideoCapture(self.cam_index)
        if not cap.isOpened():
            raise RuntimeError(f"Cannot open camera {self.cam_index}")
        cap.set(cv2.CAP_PROP_FRAME_WIDTH,  1920)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)
        cap.set(cv2.CAP_PROP_FPS, 30)
        return cap

    # ── Main loop ──────────────────────────────────────────────────────────────

    def run(self) -> str | None:
        """Capture → decode → ACK loop. Returns output path or None."""
        cap  = self._open_camera()
        ack  = ACKDisplay(960, 540) if self.show_ack else None

        print(f"\n[RECEIVER] Camera open.")
        print(f"           Aim camera at sender screen.")
        if ack:
            print(f"           ACK window open — position so sender's camera sees it.")
        print(f"           Press  Q / ESC  to abort.\n")

        if ack:
            ack.show(ACKSignal.NONE, extra="Waiting for MARICHI session...")

        pbar         = None
        start_time   = time.time()
        last_log     = start_time
        total_cap    = 0
        decode_ok    = 0
        decode_fail  = 0
        cksum_fail   = 0

        try:
            while True:
                # ── Timeout ───────────────────────────────────────────────────
                if time.time() - start_time > self.timeout_s:
                    print("\n[RECEIVER] Timeout.")
                    break

                ret, cam_frame = cap.read()
                if not ret:
                    continue
                total_cap += 1

                # ── ACK window keepalive ──────────────────────────────────────
                if ack:
                    ack.tick()

                # ── Show live preview ─────────────────────────────────────────
                preview = cv2.resize(cam_frame, (480, 270))
                n_recv  = len(self.received)
                tot_str = str(self.total_frames) if self.total_frames else '?'
                pct     = f"{100*n_recv//max(self.total_frames,1)}%" if self.total_frames else ""
                cv2.putText(preview, f"{n_recv}/{tot_str} {pct}",
                            (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0,255,0), 2)
                cv2.imshow("MARICHI RX — Camera", preview)
                key = cv2.waitKey(1) & 0xFF
                if key in (ord('q'), 27):
                    print("\n[RECEIVER] Aborted.")
                    break

                # ── Signal GREEN: starting decode ─────────────────────────────
                if ack and total_cap % 5 == 0:   # refresh every 5 frames
                    ack.show(ACKSignal.PROCESSING,
                             frame_no=n_recv,
                             total=self.total_frames or None,
                             extra=f"decoding... cap={total_cap} ok={decode_ok}")
                    ack.tick()

                # ── Decode frame ──────────────────────────────────────────────
                result = decode_frame(cam_frame)

                if result is None:
                    decode_fail += 1
                    # Signal YELLOW on persistent failures
                    if ack and decode_fail % 10 == 0:
                        ack.show(ACKSignal.ERROR,
                                 extra=f"decode fail #{decode_fail} — adjust camera")
                        ack.tick()
                        cv2.waitKey(C.ACK_SIGNAL_MS)   # hold YELLOW briefly
                    continue

                payload, frame_no, total_frames, session_id, checksum_ok = result
                decode_ok += 1

                # ── Session init on first successful decode ───────────────────
                if self.session_id is None:
                    self.session_id   = session_id
                    self.total_frames = total_frames
                    pbar = tqdm(total=total_frames, unit='fr', desc='Receiving')
                    print(f"\n[SESSION]  id={session_id.hex()}  frames={total_frames}")

                if session_id != self.session_id:
                    continue   # different session — ignore

                # ── Checksum result → ACK signal ──────────────────────────────
                already_have = frame_no in self.received

                if not already_have:
                    if checksum_ok:
                        # ✅ Three-way checksum passed → store + signal BLUE
                        self.received[frame_no] = payload
                        if pbar:
                            pbar.update(1)

                        if ack:
                            import zlib
                            crc_hex = f"CRC32: {zlib.crc32(payload)&0xFFFFFFFF:08X}"
                            ack.show(ACKSignal.SUCCESS,
                                     frame_no=frame_no,
                                     total=total_frames,
                                     extra=f"✔ {crc_hex}  strip=✔  header=✔")
                            ack.tick()
                            cv2.waitKey(C.ACK_SIGNAL_MS)   # hold BLUE for sender to see

                    else:
                        # ❌ Checksum mismatch → signal YELLOW (NACK)
                        cksum_fail += 1
                        self.cksum_failures.append(frame_no)
                        if ack:
                            ack.show(ACKSignal.ERROR,
                                     frame_no=frame_no,
                                     total=total_frames,
                                     extra=f"✘ CRC MISMATCH fr#{frame_no}  cksum_fails={cksum_fail}")
                            ack.tick()
                            cv2.waitKey(C.ACK_SIGNAL_MS)   # hold YELLOW for sender to see
                        print(f"\n  ⚠️  Frame {frame_no}: checksum MISMATCH — sender will retry")

                else:
                    # Already have this frame — still ACK blue so sender moves on
                    if ack:
                        ack.show(ACKSignal.SUCCESS,
                                 frame_no=frame_no,
                                 total=total_frames,
                                 extra=f"(duplicate — already stored)")
                        ack.tick()
                        cv2.waitKey(C.ACK_SIGNAL_MS // 2)

                # ── Periodic log ──────────────────────────────────────────────
                now = time.time()
                if now - last_log >= 5.0:
                    pct_done = 100 * len(self.received) / max(self.total_frames, 1)
                    print(f"\r[RX] {len(self.received)}/{self.total_frames}"
                          f"  {pct_done:.1f}%  ok={decode_ok}"
                          f"  fail={decode_fail}  cksum_err={cksum_fail}",
                          end='', flush=True)
                    last_log = now

                # ── All frames received? ──────────────────────────────────────
                if (self.total_frames > 0
                        and len(self.received) >= self.total_frames):
                    print(f"\n[RECEIVER] All {self.total_frames} frames received!")
                    if pbar:
                        pbar.close()
                    # Final BLUE flash
                    if ack:
                        ack.show(ACKSignal.SUCCESS,
                                 extra=f"ALL {self.total_frames} FRAMES COMPLETE ✔")
                        ack.tick()
                        cv2.waitKey(3000)
                    break

        finally:
            cap.release()
            if ack:
                ack.close()
            cv2.destroyAllWindows()

        if not self.received:
            print("[RECEIVER] No frames decoded.")
            return None

        return self._assemble()

    # ── Assembly ───────────────────────────────────────────────────────────────

    def _assemble(self) -> str:
        print(f"\n[ASSEMBLING]  {len(self.received)}/{self.total_frames} frames ...")

        missing = set(range(self.total_frames)) - set(self.received.keys())
        if missing:
            print(f"  ⚠️  MISSING: {sorted(missing)[:10]}"
                  f"{'...' if len(missing)>10 else ''} ({len(missing)} frames)")
        if self.cksum_failures:
            print(f"  ⚠️  CHECKSUM FAILURES (re-received frames): "
                  f"{self.cksum_failures[:10]}")

        out = bytearray()
        for i in range(self.total_frames):
            out.extend(self.received.get(i, b''))

        os.makedirs(os.path.dirname(os.path.abspath(self.output_path)), exist_ok=True)
        with open(self.output_path, 'wb') as f:
            f.write(out)

        sha = hashlib.sha256(out).hexdigest()
        print(f"[ASSEMBLED]  {len(out):,} B → {self.output_path}")
        print(f"             SHA-256     : {sha}")
        print(f"             Frames recv : {len(self.received)}/{self.total_frames}")
        print(f"             Cksum fails : {len(self.cksum_failures)}")
        verdict = "✅ COMPLETE" if not missing else f"⚠️  {len(missing)} MISSING"
        print(f"             Status      : {verdict}")
        return self.output_path
