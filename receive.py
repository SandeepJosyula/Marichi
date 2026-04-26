#!/usr/bin/env python3
"""
MARICHI Receiver  v0.2  — with ACK signal display

Usage:
    python receive.py output.bin                     # default (cam 0, shows ACK window)
    python receive.py output.bin --cam 1             # use camera 1
    python receive.py output.bin --block 2           # must match sender's --block
    python receive.py output.bin --no-ack            # headless, no ACK window
    python receive.py output.bin --timeout 14400     # 4-hour timeout

ACK window behaviour:
  🟢 GREEN   = currently processing / decoding
  🔵 BLUE    = frame OK — three-way checksum passed (sender advances)
  🟡 YELLOW  = decode failed or checksum mismatch  (sender retries same frame)

IMPORTANT: The ACK window must be visible to the SENDER's webcam.
Position the receiver screen so the sender camera sees the ACK colors.
"""

import argparse
import sys


def _apply_config(args):
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
    cfg.CAM_INDEX  = args.cam
    cfg.ACK_SIGNAL_MS = args.ack_ms


def main():
    parser = argparse.ArgumentParser(
        description="MARICHI (मरीचि) — Visual Modem Receiver  v0.2",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    parser.add_argument("output",                 help="Output file path")
    parser.add_argument("--cam",    "-c", type=int, default=0,
                        help="Camera device index. Default: 0")
    parser.add_argument("--block",  "-b", type=int, default=2,
                        help="Block size — must match sender. Default: 2")
    parser.add_argument("--timeout","-t", type=int, default=7200,
                        help="Max wait seconds. Default: 7200")
    parser.add_argument("--no-ack",       action="store_true", default=False,
                        dest="no_ack",
                        help="Disable ACK window (headless mode)")
    parser.add_argument("--ack-ms",       type=int, default=1500,
                        dest="ack_ms",
                        help="How long to hold ACK flash in ms. Default: 1500")
    args = parser.parse_args()

    _apply_config(args)

    from marichi.receiver import Receiver
    r = Receiver(args.output,
                 cam_index=args.cam,
                 timeout_s=args.timeout,
                 show_ack=not args.no_ack)
    result = r.run()

    if result:
        print(f"\n✅  Received: {result}")
        print(f"    Validate: python validate.py <original> {result}")
        sys.exit(0)
    else:
        print("\n❌  Receive failed or incomplete.")
        sys.exit(1)


if __name__ == "__main__":
    main()
