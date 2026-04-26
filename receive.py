#!/usr/bin/env python3
"""
MARICHI Receiver  v0.3  — multi-transport

Receive modes (must match sender's --mode):
  --mode visual  (A) — pixel frame capture via camera         [default]
  --mode audio   (C) — MFSK acoustic modem via microphone
  --mode qr      (D) — QR-code stream via camera (phone-compatible)

Usage:
    python receive.py <output>                         # visual (cam 0, ACK on)
    python receive.py <output> --cam 1                 # different camera
    python receive.py <output> --mode audio            # acoustic modem
    python receive.py <output> --mode audio --baud 600 # must match sender baud
    python receive.py <output> --mode qr               # QR stream receiver
    python receive.py <output> --mode qr  --no-ack     # QR headless
    python receive.py <output> --no-ack                # any mode, headless

ACK window (all modes):
  🟢 GREEN   = currently decoding / processing
  🔵 BLUE    = frame verified — ACK sent (sender advances)
  🟡 YELLOW  = decode failed or checksum mismatch (sender retries)
"""

import argparse
import sys


def _apply_visual_config(args):
    import marichi.config as cfg
    cfg.BLOCK_SIZE = args.block
    cfg.CELLS_X    = cfg.SCREEN_W // args.block
    cfg.CELLS_Y    = cfg.SCREEN_H // args.block
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
    cfg.CAM_INDEX     = args.cam
    cfg.ACK_SIGNAL_MS = args.ack_ms


def _run_visual(args):
    _apply_visual_config(args)
    from marichi.receiver import Receiver
    r = Receiver(args.output,
                 cam_index=args.cam,
                 timeout_s=args.timeout,
                 show_ack=not args.no_ack)
    return r.run()


def _run_audio(args):
    from marichi.transport.audio_modem import AudioReceiver, BAUD_RATE
    baud = args.baud if args.baud > 0 else BAUD_RATE
    r = AudioReceiver(args.output,
                      baud=baud,
                      timeout_s=args.timeout)
    return r.run()


def _run_qr(args):
    from marichi.transport.qr_stream import QRReceiver
    r = QRReceiver(args.output,
                   cam_index=args.cam,
                   timeout_s=args.timeout,
                   show_ack=not args.no_ack)
    return r.run()


def main():
    parser = argparse.ArgumentParser(
        description="MARICHI (मरीचि) — Multi-Transport Receiver  v0.3",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    parser.add_argument("output", help="Output file path")

    # Mode selection
    parser.add_argument("--mode", "-m",
                        choices=["visual", "audio", "qr",
                                 "a", "c", "d"],   # single-letter aliases
                        default="visual",
                        help="Transport mode: visual(A) audio(C) qr(D). Default: visual")

    # Visual / QR shared camera options
    parser.add_argument("--cam",     "-c", type=int, default=0,
                        help="[visual/qr] Camera device index. Default: 0")
    parser.add_argument("--block",   "-b", type=int, default=2,
                        help="[visual] Block size — must match sender. Default: 2")
    parser.add_argument("--timeout", "-t", type=int, default=7200,
                        help="Max wait seconds. Default: 7200")
    parser.add_argument("--no-ack",        action="store_true", default=False,
                        dest="no_ack",
                        help="Disable ACK window (headless mode)")
    parser.add_argument("--ack-ms",        type=int, default=1500,
                        dest="ack_ms",
                        help="[visual/qr] ACK flash duration ms. Default: 1500")

    # Audio mode options
    parser.add_argument("--baud",    type=int, default=0,
                        help="[audio] Baud rate: 300, 600, 1200. Must match sender. Default: 300")

    args = parser.parse_args()

    # Normalise single-letter aliases
    alias = {"a": "visual", "c": "audio", "d": "qr"}
    args.mode = alias.get(args.mode, args.mode)

    dispatch = {
        "visual": _run_visual,
        "audio":  _run_audio,
        "qr":     _run_qr,
    }
    result = dispatch[args.mode](args)

    if result:
        print(f"\n✅  Received: {result}")
        print(f"    Validate: python validate.py <original> {result}")
        sys.exit(0)
    else:
        print("\n❌  Receive failed or incomplete.")
        sys.exit(1)


if __name__ == "__main__":
    main()
