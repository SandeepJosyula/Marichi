#!/usr/bin/env python3
"""
MARICHI Sender — CLI entry point

Usage:
    python send.py <file>                         # send file with defaults
    python send.py <file> --block 2 --hold 80     # BLOCK_SIZE=2, 80ms per frame
    python send.py <file> --block 1 --hold 50     # high-speed mode (1px cells)
    python send.py <file> --block 4 --hold 120    # high-robustness mode

Point the receiver's camera at this screen and run receive.py on target machine.
"""

import argparse
import sys
import os

def main():
    parser = argparse.ArgumentParser(
        description="MARICHI (मरीचि) — Visual Modem Sender",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    parser.add_argument("file",         help="Path to file to transmit")
    parser.add_argument("--block", "-b", type=int, default=2,
                        help="Block size in pixels per cell (1=fast, 4=robust). Default: 2")
    parser.add_argument("--hold", "-t", type=int, default=80,
                        help="Frame hold time in ms (~1000/hold = fps). Default: 80")
    args = parser.parse_args()

    if not os.path.exists(args.file):
        print(f"ERROR: File not found: {args.file}")
        sys.exit(1)

    # Apply config overrides before importing sender
    import marichi.config as cfg
    cfg.BLOCK_SIZE    = args.block
    cfg.CELLS_X       = cfg.SCREEN_W // args.block
    cfg.CELLS_Y       = cfg.SCREEN_H // args.block
    cfg.FRAME_HOLD_MS = args.hold
    # Recompute layout
    cfg.DATA_X0 = cfg.BORDER + cfg.MARKER_SIZE
    cfg.DATA_X1 = cfg.CELLS_X - cfg.BORDER - cfg.MARKER_SIZE
    cfg.DATA_Y0 = cfg.BORDER + cfg.MARKER_SIZE + cfg.HEADER_ROWS
    cfg.DATA_Y1 = cfg.CELLS_Y - cfg.BORDER
    cfg.DATA_COLS  = cfg.DATA_X1 - cfg.DATA_X0
    cfg.DATA_ROWS  = cfg.DATA_Y1 - cfg.DATA_Y0
    cfg.DATA_CELLS = cfg.DATA_COLS * cfg.DATA_ROWS
    cfg.BYTES_RAW_PER_FRAME = (cfg.DATA_CELLS * cfg.BITS_PER_CELL) // 8
    cfg.N_ECC_CHUNKS      = cfg.BYTES_RAW_PER_FRAME // cfg.CHUNK_ENC
    cfg.PAYLOAD_PER_FRAME = cfg.N_ECC_CHUNKS * cfg.CHUNK_RAW

    from marichi.sender import Sender
    s = Sender(args.file, block_size=args.block, hold_ms=args.hold)
    s.run()


if __name__ == "__main__":
    main()
