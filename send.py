#!/usr/bin/env python3
"""
MARICHI Sender  v0.3  — multi-transport

Transmission modes (sender and receiver are now fully independent):
  --mode visual          (A) — Full-screen pixel frames via screen + camera  [default]
  --mode audio           (C) — MFSK acoustic modem via speaker + microphone
  --mode qr              (D) — QR-code animation via screen + camera (phone-compatible)
  --mode all                 — All three simultaneously with a SHARED session ID
  --mode visual,audio        — Visual + audio simultaneously, shared session ID
  --mode visual,qr           — Visual + QR simultaneously, shared session ID
  --mode audio,qr            — Audio + QR simultaneously, shared session ID
  (any comma-separated combination of: visual, audio, qr)

Session independence:
  When multiple modes are specified (e.g. --mode visual,audio), all transports
  share ONE session_id.  The receiver can be on any channel and will correctly
  assemble the file from whichever channel it sees first.

  The receiver does NOT need to match the sender's mode.
  Use  python receive.py --mode auto  to listen on visual + audio simultaneously.

Usage:
    python send.py <file>                              # visual, timer mode
    python send.py <file> --ack-cam 0                 # visual, ACK mode
    python send.py <file> --mode audio                # acoustic modem
    python send.py <file> --mode audio --baud 600     # faster audio
    python send.py <file> --mode qr                   # QR stream, timer
    python send.py <file> --mode qr   --fps 5 --ack-cam 0  # QR + ACK
    python send.py <file> --mode visual,audio         # send on two channels, shared session
    python send.py <file> --mode all  --ack-cam 0     # all three channels + ACK

Choosing a mode:
  visual      → Two laptops facing each other with cameras  (fastest: ~1-3 MB/s)
  qr          → Phone or tablet as receiver                 (~6-22 KB/s)
  audio       → No cameras, audio-only hardware             (~50-200 bytes/s)
  visual,audio→ Redundancy; receiver picks whichever arrives first
  all         → Maximum redundancy / reliability            (all channels)
"""

import argparse
import sys
import os
import secrets
import threading
from typing import Optional


def _apply_visual_config(args):
    import marichi.config as cfg
    cfg.BLOCK_SIZE    = args.block
    cfg.CELLS_X       = cfg.SCREEN_W // args.block
    cfg.CELLS_Y       = cfg.SCREEN_H // args.block
    cfg.FRAME_HOLD_MS = args.hold
    cfg.DATA_X0 = cfg.BORDER + cfg.MARKER_SIZE
    cfg.DATA_X1 = cfg.CELLS_X - cfg.BORDER - cfg.MARKER_SIZE
    cfg.DATA_Y0 = cfg.BORDER + cfg.MARKER_SIZE + cfg.HEADER_ROWS
    cfg.DATA_Y1 = cfg.CELLS_Y - cfg.BORDER - cfg.MARKER_SIZE
    cfg.CKSUM_Y0 = cfg.DATA_Y1 - cfg.CHECKSUM_ROWS
    cfg.CKSUM_Y1 = cfg.DATA_Y1
    cfg.DATA_COLS  = cfg.DATA_X1 - cfg.DATA_X0
    cfg.DATA_ROWS  = cfg.DATA_Y1 - cfg.DATA_Y0 - cfg.CHECKSUM_ROWS
    cfg.DATA_CELLS = cfg.DATA_COLS * cfg.DATA_ROWS
    cfg.BYTES_RAW_PER_FRAME = (cfg.DATA_CELLS * cfg.BITS_PER_CELL) // 8
    cfg.N_ECC_CHUNKS      = cfg.BYTES_RAW_PER_FRAME // cfg.CHUNK_ENC
    cfg.PAYLOAD_PER_FRAME = cfg.N_ECC_CHUNKS * cfg.CHUNK_RAW
    cfg.ACK_CAM_INDEX     = args.ack_cam


def _run_visual(args, session_id: Optional[bytes] = None):
    _apply_visual_config(args)
    from marichi.sender import Sender
    s = Sender(args.file,
               block_size=args.block,
               hold_ms=args.hold,
               ack_cam=args.ack_cam,
               session_id=session_id)
    s.run()


def _run_audio(args, session_id: Optional[bytes] = None):
    from marichi.transport.audio_modem import AudioSender, BAUD_RATE
    baud = args.baud if args.baud > 0 else BAUD_RATE
    s = AudioSender(args.file,
                    baud=baud,
                    ack_mode=(args.ack_cam >= 0),
                    session_id=session_id)
    s.run()


def _run_qr(args, session_id: Optional[bytes] = None):
    from marichi.transport.qr_stream import QRSender
    fps = args.fps if args.fps > 0 else 3
    web_mode = getattr(args, 'web_qr', False)
    s = QRSender(args.file,
                 fps=fps,
                 ack_cam=args.ack_cam,
                 session_id=session_id,
                 web_mode=web_mode)
    s.run()


def _run_multi(args, modes: list):
    """
    Run two or more transport modes simultaneously with a single shared session_id.

    All channels encode the same file with the same 8-byte session token.
    The receiver can be on any one channel (or use --mode auto to listen on all)
    and will correctly assemble the file from whichever channel it receives first.
    """
    shared_sid = secrets.token_bytes(8)

    print(f"\n[MARICHI — MULTI-MODE]  channels: {', '.join(modes)}")
    print(f"  shared session : {shared_sid.hex()}")
    print(f"  The receiver does NOT need to use the same mode(s).")
    print(f"  Use: python receive.py <output> --mode auto   (listens on visual + audio)")
    print(f"  Or:  python receive.py <output> --mode <any single mode>\n")

    runners = {
        "visual": lambda: _run_visual(args, session_id=shared_sid),
        "audio":  lambda: _run_audio(args,  session_id=shared_sid),
        "qr":     lambda: _run_qr(args,     session_id=shared_sid),
    }

    errors = {}

    def make_worker(mode_name):
        def worker():
            try:
                runners[mode_name]()
            except Exception as e:
                errors[mode_name] = str(e)
                print(f"\n[MULTI] {mode_name} channel error: {e}")
        return worker

    threads = [
        threading.Thread(target=make_worker(m), name=m, daemon=True)
        for m in modes
    ]
    for t in threads:
        t.start()
    try:
        for t in threads:
            t.join()
    except KeyboardInterrupt:
        print("\n[MULTI] Aborted.")

    if errors:
        print(f"\n[MULTI] Channel errors: {errors}")


def _parse_modes(raw: str, parser: argparse.ArgumentParser) -> list:
    """
    Parse a comma-separated mode string into a validated list of mode names.

    Accepts:
      - Single modes: "visual", "audio", "qr"
      - Aliases:      "a" → "visual", "c" → "audio", "d" → "qr"
      - Shorthand:    "all" → ["visual", "audio", "qr"]
      - Combos:       "visual,audio", "visual,qr", "audio,qr", "visual,audio,qr"
    """
    alias = {"a": "visual", "c": "audio", "d": "qr"}
    valid = {"visual", "audio", "qr"}

    parts = [p.strip() for p in raw.split(',')]

    # Handle "all" anywhere in the list
    if "all" in parts:
        return ["visual", "audio", "qr"]

    modes = []
    for p in parts:
        normalised = alias.get(p, p)
        if normalised not in valid:
            parser.error(
                f"Unknown mode: {p!r}. "
                f"Valid modes: visual (a), audio (c), qr (d), all, "
                f"or comma-separated combinations (e.g. visual,audio)."
            )
        if normalised not in modes:   # deduplicate while preserving order
            modes.append(normalised)

    if not modes:
        parser.error("No valid modes specified.")

    return modes


def main():
    parser = argparse.ArgumentParser(
        description="MARICHI (मरीचि) — Multi-Transport Sender  v0.3",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    parser.add_argument("file", help="Path to file to transmit")

    # Mode selection — accepts single mode OR comma-separated combo
    parser.add_argument(
        "--mode", "-m",
        default="visual",
        metavar="MODE[,MODE...]",
        help=(
            "Transport mode(s). Single: visual(a) | audio(c) | qr(d) | all.  "
            "Combo: comma-separated, e.g. visual,audio  (shared session ID, "
            "receiver can use any channel).  Default: visual"
        ),
    )

    # Visual mode options
    parser.add_argument("--block",   "-b", type=int, default=2,
                        help="[visual] Pixels per cell (1=fast, 4=robust). Default: 2")
    parser.add_argument("--hold",    "-t", type=int, default=80,
                        help="[visual] Frame hold ms in timer mode. Default: 80")
    parser.add_argument("--ack-cam", "-a", type=int, default=-1,
                        dest="ack_cam",
                        help="Camera index for ACK detection, all modes. Default: -1")

    # Audio mode options
    parser.add_argument("--baud",    type=int, default=0,
                        help="[audio] Baud rate: 300, 600, 1200. Default: 300")

    # QR mode options
    parser.add_argument("--fps",     type=int, default=0,
                        help="[qr] QR frames per second (1–10). Default: 3")
    parser.add_argument("--web-qr",  action="store_true", default=False,
                        dest="web_qr",
                        help="[qr] Use phone-compatible base64 QR encoding for receiver.html. "
                             "Slightly lower throughput (1664 vs 2304 bytes/frame) but works "
                             "on ALL phone browsers without encoding issues.")

    args = parser.parse_args()

    if not os.path.exists(args.file):
        print(f"ERROR: File not found: {args.file}")
        sys.exit(1)

    modes = _parse_modes(args.mode, parser)

    if len(modes) == 1:
        # Single-channel — original path, no shared session overhead
        dispatch = {
            "visual": lambda: _run_visual(args),
            "audio":  lambda: _run_audio(args),
            "qr":     lambda: _run_qr(args),
        }
        dispatch[modes[0]]()
    else:
        # Multi-channel — shared session_id across all senders
        _run_multi(args, modes)


if __name__ == "__main__":
    main()
