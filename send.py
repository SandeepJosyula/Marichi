#!/usr/bin/env python3
"""
MARICHI Sender  v0.3  — multi-transport

Transmission modes:
  --mode visual  (A) — Full-screen pixel frames via screen + camera  [default]
  --mode audio   (C) — MFSK acoustic modem via speaker + microphone
  --mode qr      (D) — QR-code animation via screen + camera (phone-compatible)
  --mode all         — All three simultaneously (maximum reliability)

Usage:
    python send.py <file>                              # visual, timer mode
    python send.py <file> --ack-cam 0                 # visual, ACK mode
    python send.py <file> --mode audio                # acoustic modem
    python send.py <file> --mode audio --baud 600     # faster audio
    python send.py <file> --mode qr                   # QR stream, timer
    python send.py <file> --mode qr   --fps 5 --ack-cam 0  # QR + ACK
    python send.py <file> --mode all  --ack-cam 0     # all channels + ACK

Choosing a mode:
  visual  → Two laptops facing each other with cameras  (fastest: ~1-3 MB/s)
  qr      → Phone or tablet as receiver                 (~6-22 KB/s)
  audio   → No cameras, audio-only hardware             (~50-200 bytes/s)
  all     → Redundancy / maximum reliability            (all channels)
"""

import argparse
import sys
import os
import threading


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


def _run_visual(args):
    _apply_visual_config(args)
    from marichi.sender import Sender
    s = Sender(args.file,
               block_size=args.block,
               hold_ms=args.hold,
               ack_cam=args.ack_cam)
    s.run()


def _run_audio(args):
    from marichi.transport.audio_modem import AudioSender, BAUD_RATE
    baud = args.baud if args.baud > 0 else BAUD_RATE
    s = AudioSender(args.file,
                    baud=baud,
                    ack_mode=(args.ack_cam >= 0))
    s.run()


def _run_qr(args):
    from marichi.transport.qr_stream import QRSender
    fps = args.fps if args.fps > 0 else 3
    s = QRSender(args.file,
                 fps=fps,
                 ack_cam=args.ack_cam)
    s.run()


def _run_all(args):
    """Run visual + audio + QR simultaneously in separate threads."""
    print("\n[MARICHI — ALL MODES]  Starting visual + audio + QR simultaneously.")
    print("  Each mode transmits independently.")
    print("  Receiver can use any one or all modes.\n")

    errors = {}

    def visual_worker():
        try:
            _run_visual(args)
        except Exception as e:
            errors["visual"] = str(e)
            print(f"\n[ALL] Visual mode error: {e}")

    def audio_worker():
        try:
            _run_audio(args)
        except Exception as e:
            errors["audio"] = str(e)
            print(f"\n[ALL] Audio mode error: {e}")

    def qr_worker():
        try:
            _run_qr(args)
        except Exception as e:
            errors["qr"] = str(e)
            print(f"\n[ALL] QR mode error: {e}")

    threads = [
        threading.Thread(target=visual_worker, name="visual", daemon=True),
        threading.Thread(target=audio_worker,  name="audio",  daemon=True),
        threading.Thread(target=qr_worker,     name="qr",     daemon=True),
    ]
    for t in threads:
        t.start()
    try:
        for t in threads:
            t.join()
    except KeyboardInterrupt:
        print("\n[ALL] Aborted.")

    if errors:
        print(f"\n[ALL] Errors: {errors}")


def main():
    parser = argparse.ArgumentParser(
        description="MARICHI (मरीचि) — Multi-Transport Sender  v0.3",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    parser.add_argument("file", help="Path to file to transmit")

    # Mode selection
    parser.add_argument("--mode", "-m",
                        choices=["visual", "audio", "qr", "all",
                                 "a", "c", "d"],   # single-letter aliases
                        default="visual",
                        help="Transport mode: visual(A) audio(C) qr(D) all. Default: visual")

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

    args = parser.parse_args()

    # Normalise single-letter aliases
    alias = {"a": "visual", "c": "audio", "d": "qr"}
    args.mode = alias.get(args.mode, args.mode)

    if not os.path.exists(args.file):
        print(f"ERROR: File not found: {args.file}")
        sys.exit(1)

    dispatch = {
        "visual": _run_visual,
        "audio":  _run_audio,
        "qr":     _run_qr,
        "all":    _run_all,
    }
    dispatch[args.mode](args)


if __name__ == "__main__":
    main()
