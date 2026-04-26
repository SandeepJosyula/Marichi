#!/usr/bin/env python3
"""
MARICHI Receiver — CLI entry point

Usage:
    python receive.py output.bin              # receive to output.bin
    python receive.py output.bin --cam 1      # use camera device 1
    python receive.py output.bin --block 2    # must match sender's --block value
    python receive.py output.bin --timeout 3600  # 1-hour timeout

After receiving, compare with original using:
    python validate.py <original_file> output.bin
"""

import argparse
import sys


def main():
    parser = argparse.ArgumentParser(
        description="MARICHI (मरीचि) — Visual Modem Receiver",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    parser.add_argument("output",           help="Path to write received file")
    parser.add_argument("--cam",  "-c", type=int, default=0,
                        help="Camera device index (default: 0)")
    parser.add_argument("--block","-b", type=int, default=2,
                        help="Block size — MUST match sender (default: 2)")
    parser.add_argument("--timeout","-t", type=int, default=7200,
                        help="Max seconds to wait (default: 7200 = 2 hrs)")
    args = parser.parse_args()

    import marichi.config as cfg
    cfg.BLOCK_SIZE = args.block
    cfg.CELLS_X    = cfg.SCREEN_W // args.block
    cfg.CELLS_Y    = cfg.SCREEN_H // args.block
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
    cfg.CAM_INDEX  = args.cam

    from marichi.receiver import Receiver
    r = Receiver(args.output, cam_index=args.cam, timeout_s=args.timeout)
    result = r.run()
    if result:
        print(f"\n✅ File saved: {result}")
        print(f"   Run:  python validate.py <original> {result}")
    else:
        print("\n❌ Receive failed or incomplete.")
        sys.exit(1)


if __name__ == "__main__":
    main()
