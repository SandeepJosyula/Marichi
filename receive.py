#!/usr/bin/env python3
"""
MARICHI Receiver  v0.3  — multi-transport

Receive modes (fully independent of sender's --mode):
  --mode visual  (A) — pixel frame capture via camera         [default]
  --mode audio   (C) — MFSK acoustic modem via microphone
  --mode qr      (D) — QR-code stream via camera (phone-compatible)
  --mode auto        — visual + audio simultaneously; first complete channel wins

Sender / Receiver independence:
  The sender and receiver no longer need to use the same mode.
  Examples:
    Sender: python send.py file.zip --mode visual,audio    (transmits on both)
    Receiver: python receive.py out.zip --mode audio       (only listens on audio)

    Sender: python send.py file.zip --mode visual          (visual only)
    Receiver: python receive.py out.zip --mode auto        (tries visual + audio)
                → visual channel succeeds, audio times out, file is assembled

    Sender: python send.py file.zip --mode all             (all three channels)
    Receiver: python receive.py out.zip --mode auto        (takes whichever arrives first)

  --mode auto is recommended when you are unsure which channel the sender is using,
  or when you want maximum resilience. It uses camera (visual) + microphone (audio)
  in parallel daemon threads; the first to deliver a complete, verified file wins.
  Camera and microphone are separate hardware so they do not conflict.

Usage:
    python receive.py <output>                         # visual (cam 0, ACK on)
    python receive.py <output> --cam 1                 # different camera
    python receive.py <output> --mode audio            # acoustic modem
    python receive.py <output> --mode audio --baud 600 # must match sender baud
    python receive.py <output> --mode qr               # QR stream receiver
    python receive.py <output> --mode qr  --no-ack     # QR headless
    python receive.py <output> --mode auto             # visual + audio race
    python receive.py <output> --no-ack                # any mode, headless

ACK window (visual and QR modes):
  🟢 GREEN   = currently decoding / processing
  🔵 BLUE    = frame verified — ACK sent (sender advances)
  🟡 YELLOW  = decode failed or checksum mismatch (sender retries)
"""

import argparse
import sys
import os
import queue
import threading


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


def _run_auto(args):
    """
    Run visual (camera) and audio (microphone) receivers in parallel.

    Visual uses the camera; audio uses the microphone — they are separate hardware
    and do not conflict.  Each receiver writes to its own temporary file.
    The first channel to deliver a complete, verified file renames its temp file
    to the requested output path.  The losing channel's partial file is cleaned up.

    This mode is completely decoupled from the sender's choice of transport.
    The sender may be using --mode visual, --mode audio, --mode visual,audio,
    --mode all, or any other combination — the auto receiver handles all cases.
    """
    result_q: queue.Queue = queue.Queue()

    # Temporary output paths — each receiver writes to a separate file
    visual_tmp = args.output + ".marichi_visual"
    audio_tmp  = args.output + ".marichi_audio"

    print(f"\n[MARICHI — AUTO RECEIVE]  Listening on visual (camera) + audio (microphone)")
    print(f"  Whichever channel delivers a complete file first wins.")
    print(f"  Press Ctrl+C to abort.\n")

    def visual_worker():
        try:
            _apply_visual_config(args)
            from marichi.receiver import Receiver
            r = Receiver(visual_tmp,
                         cam_index=args.cam,
                         timeout_s=args.timeout,
                         show_ack=not args.no_ack)
            result = r.run()
            result_q.put(("visual", visual_tmp, result is not None))
        except Exception as e:
            print(f"\n[AUTO] Visual channel error: {e}")
            result_q.put(("visual", visual_tmp, False))

    def audio_worker():
        try:
            from marichi.transport.audio_modem import AudioReceiver, BAUD_RATE
            baud = args.baud if args.baud > 0 else BAUD_RATE
            r = AudioReceiver(audio_tmp,
                              baud=baud,
                              timeout_s=args.timeout)
            result = r.run()
            result_q.put(("audio", audio_tmp, result is not None))
        except Exception as e:
            print(f"\n[AUTO] Audio channel error: {e}")
            result_q.put(("audio", audio_tmp, False))

    threads = [
        threading.Thread(target=visual_worker, name="auto-visual", daemon=True),
        threading.Thread(target=audio_worker,  name="auto-audio",  daemon=True),
    ]
    for t in threads:
        t.start()

    winner_path = None
    completed_channels = 0
    total_channels = len(threads)

    try:
        while completed_channels < total_channels:
            try:
                channel, tmp_path, success = result_q.get(timeout=5)
            except queue.Empty:
                # No result yet — keep waiting (threads are still running)
                continue

            completed_channels += 1

            if success and winner_path is None:
                # First successful channel — claim the output file
                try:
                    os.replace(tmp_path, args.output)
                    winner_path = args.output
                    print(f"\n[AUTO] ✅  {channel.upper()} channel won!  "
                          f"File saved to: {args.output}")
                    print(f"[AUTO] Remaining channel(s) will be cleaned up on exit.")
                    # Don't break — let other threads finish or time out naturally
                    # (they are daemons so they'll be killed on process exit)
                    break
                except OSError as e:
                    print(f"\n[AUTO] Could not rename {tmp_path} → {args.output}: {e}")

    except KeyboardInterrupt:
        print("\n[AUTO] Aborted.")

    # Clean up any leftover temp files
    for tmp in (visual_tmp, audio_tmp):
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass

    return winner_path


def main():
    parser = argparse.ArgumentParser(
        description="MARICHI (मरीचि) — Multi-Transport Receiver  v0.3",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    parser.add_argument("output", help="Output file path")

    # Mode selection — now includes 'auto'
    parser.add_argument("--mode", "-m",
                        choices=["visual", "audio", "qr", "auto",
                                 "a", "c", "d"],   # single-letter aliases
                        default="visual",
                        help=(
                            "Transport mode: visual(a) | audio(c) | qr(d) | auto.  "
                            "auto = visual + audio in parallel, first wins.  "
                            "Does NOT need to match sender's --mode.  Default: visual"
                        ))

    # Visual / QR shared camera options
    parser.add_argument("--cam",     "-c", type=int, default=0,
                        help="[visual/qr/auto] Camera device index. Default: 0")
    parser.add_argument("--block",   "-b", type=int, default=2,
                        help="[visual/auto] Block size — must match sender. Default: 2")
    parser.add_argument("--timeout", "-t", type=int, default=7200,
                        help="Max wait seconds (per channel). Default: 7200")
    parser.add_argument("--no-ack",        action="store_true", default=False,
                        dest="no_ack",
                        help="Disable ACK window (headless mode)")
    parser.add_argument("--ack-ms",        type=int, default=1500,
                        dest="ack_ms",
                        help="[visual/qr] ACK flash duration ms. Default: 1500")

    # Audio mode options
    parser.add_argument("--baud",    type=int, default=0,
                        help="[audio/auto] Baud rate: 300, 600, 1200. "
                             "Must match sender baud if sender uses --mode audio. Default: 300")

    args = parser.parse_args()

    # Normalise single-letter aliases
    alias = {"a": "visual", "c": "audio", "d": "qr"}
    args.mode = alias.get(args.mode, args.mode)

    dispatch = {
        "visual": _run_visual,
        "audio":  _run_audio,
        "qr":     _run_qr,
        "auto":   _run_auto,
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
