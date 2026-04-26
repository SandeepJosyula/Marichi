#!/usr/bin/env python3
"""
MARICHI Sender  v0.2  — with ACK auto-advance

Usage:
    python send.py <file>                            # timer mode (no ACK camera)
    python send.py <file> --ack-cam 1                # ACK mode: camera 1 watches receiver
    python send.py <file> --block 1 --ack-cam 1      # fast + ACK mode
    python send.py <file> --block 4 --hold 120       # robust timer mode

ACK mode (recommended):
    Set up sender's webcam to point at receiver's ACK window.
    Sender auto-advances only when receiver signals BLUE (frame verified).
    Re-shows same frame on YELLOW (checksum mismatch).
"""

import argparse
import sys
import os


def _apply_config(args):
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


def main():
    parser = argparse.ArgumentParser(
        description="MARICHI (मरीचि) — Visual Modem Sender  v0.2",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    parser.add_argument("file",               help="Path to file to transmit")
    parser.add_argument("--block",   "-b", type=int, default=2,
                        help="Pixels per cell (1=fast, 4=robust). Default: 2")
    parser.add_argument("--hold",    "-t", type=int, default=80,
                        help="Frame hold ms in timer mode. Default: 80")
    parser.add_argument("--ack-cam", "-a", type=int, default=-1,
                        dest="ack_cam",
                        help="Camera index for ACK detection (-1=disabled). Default: -1")
    args = parser.parse_args()

    if not os.path.exists(args.file):
        print(f"ERROR: File not found: {args.file}")
        sys.exit(1)

    _apply_config(args)

    from marichi.sender import Sender
    s = Sender(args.file,
               block_size=args.block,
               hold_ms=args.hold,
               ack_cam=args.ack_cam)
    s.run()


if __name__ == "__main__":
    main()
