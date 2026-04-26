"""
MARICHI — Option C: Acoustic FSK Modem

Transmits binary data as audio tones through speaker → microphone.
Uses 4-FSK (MFSK-4) modulation for 2× throughput over BFSK.

Physical setup:
  Sender laptop  → plays tones through its speaker
  Receiver laptop/phone → microphone captures tones

Modulation:
  MFSK-4: 4 tones, each symbol encodes 2 bits
  Tones : 1200 Hz (00) | 1800 Hz (01) | 2400 Hz (10) | 3000 Hz (11)
  Baud  : 300 symbols/sec (conservative — works over any speaker/mic)
  Rate  : 600 bps raw → ~56 bytes/s effective (after ECC + framing)

ACK channel (300–900 Hz, separate from data band):
  500 Hz  → receiver sending ACK  (advance to next frame)
  750 Hz  → receiver sending NACK (retry same frame)

Throughput guide:
  default (300 baud):  ~56  bytes/s  →  1 MB ≈  18 min
  600 baud:           ~112  bytes/s  →  1 MB ≈   9 min
  1200 baud:          ~225  bytes/s  →  1 MB ≈   5 min  (needs quiet room)

Best for: SSH keys, config files, credentials, data < 10 MB.

Dependencies:
  pip install sounddevice reedsolo
"""

from __future__ import annotations
import os
import sys
import time
import math
import zlib
import hashlib
import struct
import secrets
import threading
import queue
import numpy as np
from typing import Optional

try:
    import sounddevice as sd
except ImportError:
    sd = None  # Handled at runtime with a clear error message

try:
    from reedsolo import RSCodec
except ImportError:
    RSCodec = None

# ── Modulation constants ──────────────────────────────────────────────────────

SAMPLE_RATE      = 44100          # Hz
BAUD_RATE        = 300            # symbols per second (conservative default)
SAMPLES_PER_SYM  = SAMPLE_RATE // BAUD_RATE   # 147 samples per symbol

# MFSK-4 tones (data band: 1200–3000 Hz)
DATA_TONES = [1200, 1800, 2400, 3000]   # Hz; index = 2-bit symbol value

# ACK channel tones (low band: 400–900 Hz, clear of data)
ACK_TONE  = 500   # Hz  → "frame OK, advance"
NACK_TONE = 750   # Hz  → "frame bad, retry"

BITS_PER_SYM = 2     # log2(len(DATA_TONES))

# ── Frame constants ───────────────────────────────────────────────────────────

AUDIO_MAGIC    = b'\xCA\xFE\xAA\x55'  # 4 bytes
PREAMBLE_SYMS  = 64                   # sync symbols before every frame
TAIL_SYMS      = 16                   # silence after frame

ECC_NSYM       = 32    # RS parity bytes per chunk
CHUNK_RAW      = 128   # raw bytes per RS chunk
CHUNK_ENC      = CHUNK_RAW + ECC_NSYM   # 160 bytes encoded

# Max payload bytes per audio frame (keeps audio frames ≤ 5 seconds)
# 300 baud × 2 bits/sym = 600 bps → 75 bytes/s raw
# We target ~3s payload → 225 bytes raw → ~1.4 RS chunks → round down to 1 chunk
AUDIO_PAYLOAD_PER_FRAME = CHUNK_RAW   # 128 bytes decoded payload

# Encoded size per frame:
# preamble(64 syms) + header(26B encoded as syms) + ECC payload(160B) + tail
HEADER_BYTES = 26   # magic(4) + frame_no(4) + total(4) + session(8) + crc32(4) + pay_len(2)

# ACK signal duration
ACK_TONE_SECS = 0.25   # how long receiver holds ACK/NACK tone (seconds)
ACK_WAIT_SECS = 3.0    # sender waits up to this many seconds for ACK per frame
ACK_TIMEOUT_FRAMES = 3 # advance anyway after this many timeouts in a row

# ── Reed-Solomon ECC ──────────────────────────────────────────────────────────

def _get_rs() -> RSCodec:
    if RSCodec is None:
        raise RuntimeError("reedsolo not installed — run: pip install reedsolo")
    return RSCodec(ECC_NSYM)

def rs_encode(data: bytes) -> bytes:
    rs = _get_rs()
    out = bytearray()
    for i in range(0, len(data), CHUNK_RAW):
        chunk = data[i:i + CHUNK_RAW]
        out.extend(bytes(rs.encode(chunk)))
    return bytes(out)

def rs_decode(data: bytes) -> Optional[bytes]:
    rs = _get_rs()
    out = bytearray()
    for i in range(0, len(data), CHUNK_ENC):
        chunk = data[i:i + CHUNK_ENC]
        if len(chunk) < CHUNK_ENC:
            return None
        try:
            decoded, _, _ = rs.decode(chunk)
            out.extend(bytes(decoded))
        except Exception:
            return None
    return bytes(out)

# ── Signal synthesis ──────────────────────────────────────────────────────────

def _tone(freq: float, n_samples: int, volume: float = 0.8) -> np.ndarray:
    """Generate a single sinusoidal tone."""
    t = np.arange(n_samples) / SAMPLE_RATE
    return (np.sin(2 * np.pi * freq * t) * volume).astype(np.float32)

def _silence(n_samples: int) -> np.ndarray:
    return np.zeros(n_samples, dtype=np.float32)

def _encode_bits_to_audio(bits: list[int], baud: int = BAUD_RATE) -> np.ndarray:
    """Encode a list of bits (0/1) as MFSK-4 audio."""
    sps = SAMPLE_RATE // baud
    # Pad to even number of bits
    if len(bits) % BITS_PER_SYM:
        bits = bits + [0] * (BITS_PER_SYM - len(bits) % BITS_PER_SYM)

    chunks = []
    for i in range(0, len(bits), BITS_PER_SYM):
        sym = (bits[i] << 1) | bits[i + 1]   # 2-bit symbol
        chunks.append(_tone(DATA_TONES[sym], sps))
    return np.concatenate(chunks)

def _bytes_to_bits(data: bytes) -> list[int]:
    bits = []
    for b in data:
        for bit_pos in range(7, -1, -1):
            bits.append((b >> bit_pos) & 1)
    return bits

def _encode_preamble(baud: int = BAUD_RATE) -> np.ndarray:
    """
    Preamble: alternating 10 / 01 symbols (diagnostic pattern)
    followed by sync word 0xFE 0xFE at the end.
    """
    sps = SAMPLE_RATE // baud
    chunks = []
    for i in range(PREAMBLE_SYMS):
        tone = DATA_TONES[2 if i % 2 == 0 else 1]   # alternating 2400/1800 Hz
        chunks.append(_tone(tone, sps))
    # Sync word 0xFE 0xFE 0x7E 0x7E
    sync_bits = _bytes_to_bits(b'\xFE\xFE\x7E\x7E')
    chunks.append(_encode_bits_to_audio(sync_bits, baud))
    return np.concatenate(chunks)

def encode_audio_frame(payload: bytes,
                       frame_no: int,
                       total_frames: int,
                       session_id: bytes,
                       baud: int = BAUD_RATE) -> np.ndarray:
    """
    Encode one data frame as audio.

    Output: preamble + header_audio + ecc_payload_audio + tail_silence
    """
    crc32 = zlib.crc32(payload) & 0xFFFFFFFF
    header = struct.pack('>4sIIIHH',
                         AUDIO_MAGIC,
                         frame_no,
                         total_frames,
                         crc32,
                         len(payload),
                         0)                          # 2 bytes padding
    header = header[:4] + session_id[:8] + header[4:4+4+4+4+2+2]
    # Repack properly
    header = (AUDIO_MAGIC
              + session_id[:8]
              + struct.pack('>III', frame_no, total_frames, crc32)
              + struct.pack('>H', len(payload)))      # 4+8+4+4+4+2 = 26 bytes

    ecc_payload = rs_encode(payload)

    frame_bytes = header + ecc_payload
    bits = _bytes_to_bits(frame_bytes)

    preamble   = _encode_preamble(baud)
    data_audio = _encode_bits_to_audio(bits, baud)
    tail       = _silence(SAMPLE_RATE // 10)   # 100ms silence

    return np.concatenate([preamble, data_audio, tail])

# ── Signal detection ──────────────────────────────────────────────────────────

def _detect_symbol(window: np.ndarray, baud: int = BAUD_RATE) -> int:
    """FFT peak detection — return symbol index (0–3)."""
    sps = SAMPLE_RATE // baud
    n = len(window)
    spectrum = np.abs(np.fft.rfft(window * np.hanning(n), n=SAMPLE_RATE))
    best_sym  = 0
    best_mag  = -1.0
    for sym, freq in enumerate(DATA_TONES):
        mag = spectrum[freq]
        if mag > best_mag:
            best_mag = mag
            best_sym = sym
    return best_sym

def _detect_ack_tone(window: np.ndarray) -> Optional[str]:
    """Detect ACK (500 Hz) or NACK (750 Hz) in a short audio window."""
    n = len(window)
    spectrum = np.abs(np.fft.rfft(window * np.hanning(n), n=SAMPLE_RATE))
    ack_mag  = spectrum[ACK_TONE]
    nack_mag = spectrum[NACK_TONE]
    noise_floor = np.mean(spectrum[200:5000]) * 3   # 3× noise floor threshold
    if ack_mag > noise_floor and ack_mag > nack_mag:
        return "ACK"
    if nack_mag > noise_floor and nack_mag > ack_mag:
        return "NACK"
    return None

# ── Sender ────────────────────────────────────────────────────────────────────

class AudioSender:
    """
    Option C sender — plays data as MFSK-4 audio tones.

    Usage:
        AudioSender("/path/to/file.zip").run()
        AudioSender("/path/to/file.zip", baud=600).run()
    """

    def __init__(self, filepath: str,
                 baud: int = BAUD_RATE,
                 ack_mode: bool = True,
                 device=None):
        if sd is None:
            raise RuntimeError("sounddevice not installed — run: pip install sounddevice")
        self.filepath   = filepath
        self.baud       = baud
        self.ack_mode   = ack_mode
        self.device     = device
        self.session_id = secrets.token_bytes(8)

        with open(filepath, 'rb') as f:
            self.data = f.read()

        self.sha256      = hashlib.sha256(self.data).hexdigest()
        self.total_bytes = len(self.data)
        self.total_frames = max(1, math.ceil(self.total_bytes / AUDIO_PAYLOAD_PER_FRAME))

        eff_bytes_sec = (baud * BITS_PER_SYM) / 10 * 0.75  # ~56 B/s at 300 baud
        eta_s = self.total_bytes / eff_bytes_sec

        print(f"\n[MARICHI AUDIO SENDER — Option C]")
        print(f"  file        : {os.path.basename(filepath)}")
        print(f"  size        : {self.total_bytes:,} B  ({self.total_bytes/1024/1024:.2f} MB)")
        print(f"  SHA-256     : {self.sha256}")
        print(f"  session     : {self.session_id.hex()}")
        print(f"  frames      : {self.total_frames}")
        print(f"  baud        : {baud} sym/s  (MFSK-4 = {baud*BITS_PER_SYM} bps)")
        print(f"  ACK mode    : {'enabled (mic listens for ACK tones)' if ack_mode else 'disabled (timer)'}")
        print(f"  ETA         : {eta_s/60:.1f} min  ({eta_s/3600:.1f} hrs)")
        print(f"\n  ⚠️  Best for files ≤ 10 MB.  For larger files use --mode visual or --mode qr")

    def _build_frames(self) -> list[np.ndarray]:
        print(f"\n[BUILDING {self.total_frames} AUDIO FRAMES]")
        frames = []
        for i in range(self.total_frames):
            s = i * AUDIO_PAYLOAD_PER_FRAME
            e = min(s + AUDIO_PAYLOAD_PER_FRAME, self.total_bytes)
            frames.append(encode_audio_frame(self.data[s:e], i,
                                             self.total_frames,
                                             self.session_id,
                                             self.baud))
            if (i + 1) % 10 == 0 or (i + 1) == self.total_frames:
                print(f"\r  built {i+1}/{self.total_frames}", end='', flush=True)
        print(f"\n[BUILD OK]")
        return frames

    def _play(self, audio: np.ndarray) -> None:
        sd.play(audio, samplerate=SAMPLE_RATE, device=self.device, blocking=True)

    def _listen_for_ack(self, timeout_s: float = ACK_WAIT_SECS) -> Optional[str]:
        """Record briefly and listen for ACK or NACK tone from receiver."""
        n_samples = int(SAMPLE_RATE * timeout_s)
        chunk     = int(SAMPLE_RATE * 0.1)   # check in 100ms windows
        deadline  = time.time() + timeout_s
        while time.time() < deadline:
            rec = sd.rec(chunk, samplerate=SAMPLE_RATE,
                         channels=1, dtype='float32',
                         device=self.device, blocking=True)
            sig = _detect_ack_tone(rec[:, 0])
            if sig is not None:
                return sig
        return None

    def _send_frame_ack_mode(self, frames: list[np.ndarray]) -> None:
        """ACK-driven send: wait for receiver to signal ACK/NACK per frame."""
        n = len(frames)
        retries: dict[int, int] = {}
        consecutive_timeouts = 0
        idx = 0

        while idx < n:
            print(f"\r  ▶ Frame {idx+1}/{n}  retries={retries.get(idx,0)}", end='', flush=True)
            self._play(frames[idx])

            sig = self._listen_for_ack()
            if sig == "ACK":
                print(f"  ✅ Frame {idx+1}/{n} ACK'd  (retries={retries.get(idx,0)})")
                idx += 1
                consecutive_timeouts = 0
            elif sig == "NACK":
                retries[idx] = retries.get(idx, 0) + 1
                print(f"\r  ❌ Frame {idx+1}/{n} NACK  (retry #{retries[idx]})")
            else:
                consecutive_timeouts += 1
                print(f"\r  ⚠️  Frame {idx+1}/{n} no ACK — timeout #{consecutive_timeouts}")
                if consecutive_timeouts >= ACK_TIMEOUT_FRAMES:
                    print(f"  ⚠️  {ACK_TIMEOUT_FRAMES} consecutive timeouts — advancing (is receiver running?)")
                    idx += 1
                    consecutive_timeouts = 0

    def _send_frame_timer_mode(self, frames: list[np.ndarray]) -> None:
        """Timer mode: play each frame once in sequence (no ACK)."""
        for i, frame_audio in enumerate(frames):
            print(f"\r  ▶ Playing frame {i+1}/{len(frames)}", end='', flush=True)
            self._play(frame_audio)
        print()

    def run(self) -> None:
        frames = self._build_frames()

        print(f"\n[SENDER] Starting audio transmission.")
        print(f"         Ensure receiver is running and mic is aimed at this speaker.")
        print(f"         Press Ctrl+C to abort.\n")

        try:
            if self.ack_mode:
                self._send_frame_ack_mode(frames)
            else:
                self._send_frame_timer_mode(frames)
        except KeyboardInterrupt:
            print("\n[SENDER] Aborted.")
            return

        # Completion tone: 3× ACK tone burst
        completion = np.concatenate([_tone(ACK_TONE, int(SAMPLE_RATE * 0.3))] * 3
                                    + [_silence(int(SAMPLE_RATE * 0.1))] * 3)
        # Interleave silence
        arr = []
        for i in range(3):
            arr.append(_tone(ACK_TONE, int(SAMPLE_RATE * 0.3)))
            arr.append(_silence(int(SAMPLE_RATE * 0.1)))
        self._play(np.concatenate(arr))

        print(f"\n[SENDER] ✅ All {len(frames)} frames transmitted.")
        print(f"  Validate: python validate.py <original> <received_output>")


# ── Receiver ──────────────────────────────────────────────────────────────────

class AudioReceiver:
    """
    Option C receiver — captures audio from mic and decodes MFSK-4 frames.

    Usage:
        r = AudioReceiver("output.bin")
        result = r.run()

    The receiver:
      1. Listens for the MFSK-4 preamble pattern (1800/2400 Hz alternating)
      2. Once sync locked, decodes header + payload bytes
      3. Verifies CRC32 + Reed-Solomon ECC
      4. Sends ACK (500 Hz) or NACK (750 Hz) tone back to sender
      5. Assembles all frames into output file
    """

    def __init__(self, output_path: str,
                 baud: int = BAUD_RATE,
                 timeout_s: int = 7200,
                 show_progress: bool = True,
                 device=None):
        if sd is None:
            raise RuntimeError("sounddevice not installed — run: pip install sounddevice")
        self.output_path   = output_path
        self.baud          = baud
        self.timeout_s     = timeout_s
        self.show_progress = show_progress
        self.device        = device

        self.session_id:   Optional[bytes] = None
        self.total_frames: int             = 0
        self.received:     dict[int, bytes] = {}
        self.cksum_fails:  int             = 0

        sps = SAMPLE_RATE // baud
        eff = (baud * BITS_PER_SYM) / 10 * 0.75

        print(f"\n[MARICHI AUDIO RECEIVER — Option C]")
        print(f"  output      : {output_path}")
        print(f"  baud        : {baud} sym/s  (MFSK-4 = {baud*BITS_PER_SYM} bps)")
        print(f"  samples/sym : {sps}")
        print(f"  eff. rate   : ~{eff:.0f} bytes/s")
        print(f"  timeout     : {timeout_s}s")

    def _send_ack(self, ack: bool) -> None:
        """Play ACK or NACK tone back to sender."""
        freq  = ACK_TONE if ack else NACK_TONE
        audio = _tone(freq, int(SAMPLE_RATE * ACK_TONE_SECS), volume=0.6)
        sd.play(audio, samplerate=SAMPLE_RATE, device=self.device, blocking=True)

    def _sync_and_read_frame(self, buffer: np.ndarray, sps: int) -> Optional[dict]:
        """
        Scan buffer for preamble pattern → sync word → decode frame.
        Returns dict with keys: payload, frame_no, total_frames, session_id, crc_ok
        """
        sync_bytes = b'\xFE\xFE\x7E\x7E'
        # Build expected sync audio fingerprint (magnitude check)
        # We look for alternating high-mag symbols at TONE[2]/TONE[1]

        # Simple approach: walk through buffer in symbol-sized windows,
        # collect symbol stream, search for sync word
        n = len(buffer)
        syms = []
        for start in range(0, n - sps, sps):
            win = buffer[start:start + sps]
            syms.append(_detect_symbol(win, self.baud))

        if len(syms) < PREAMBLE_SYMS + 32:
            return None  # not enough data

        # Convert symbol stream to bits
        bits = []
        for s in syms:
            bits.append((s >> 1) & 1)
            bits.append(s & 1)

        # Search for sync word pattern in bit stream
        sync_bits = _bytes_to_bits(sync_bytes)
        for offset in range(len(bits) - len(sync_bits) - (HEADER_BYTES + CHUNK_ENC) * 8):
            if bits[offset:offset + len(sync_bits)] == sync_bits:
                # Sync found — decode from here
                pos = offset + len(sync_bits)
                needed_bits = (HEADER_BYTES + CHUNK_ENC) * 8
                if pos + needed_bits > len(bits):
                    return None

                # Reconstruct bytes
                frame_bits = bits[pos:pos + needed_bits]
                frame_bytes = bytearray()
                for i in range(0, len(frame_bits), 8):
                    byte = 0
                    for j in range(8):
                        byte = (byte << 1) | frame_bits[i + j]
                    frame_bytes.append(byte)

                # Parse header
                try:
                    magic   = bytes(frame_bytes[0:4])
                    if magic != AUDIO_MAGIC:
                        continue
                    session = bytes(frame_bytes[4:12])
                    frame_no, total_frames, crc32 = struct.unpack('>III', frame_bytes[12:24])
                    pay_len = struct.unpack('>H', frame_bytes[24:26])[0]
                except Exception:
                    continue

                if pay_len > AUDIO_PAYLOAD_PER_FRAME:
                    continue

                # Decode ECC payload
                ecc_data = bytes(frame_bytes[HEADER_BYTES:HEADER_BYTES + CHUNK_ENC])
                decoded  = rs_decode(ecc_data)
                if decoded is None:
                    return {"crc_ok": False}

                payload = decoded[:pay_len]
                actual_crc = zlib.crc32(payload) & 0xFFFFFFFF
                crc_ok = (actual_crc == crc32)

                return {
                    "payload":      payload,
                    "frame_no":     frame_no,
                    "total_frames": total_frames,
                    "session_id":   session,
                    "crc_ok":       crc_ok,
                }
        return None

    def run(self) -> Optional[str]:
        """Main receive loop."""
        sps          = SAMPLE_RATE // self.baud
        # Buffer size: enough to hold ~10 seconds of audio
        buffer_secs  = 12
        buffer_size  = SAMPLE_RATE * buffer_secs

        print(f"\n[RECEIVER] Listening on microphone.")
        print(f"           Aim microphone at sender's speaker.")
        print(f"           Press Ctrl+C to abort.\n")

        ring_buffer: list[float] = []
        start_time   = time.time()
        frames_since_progress = 0

        try:
            while True:
                if time.time() - start_time > self.timeout_s:
                    print("\n[RECEIVER] Timeout.")
                    break

                # Record a chunk (2× symbol duration for overlap processing)
                chunk_secs = 2.0
                rec = sd.rec(int(SAMPLE_RATE * chunk_secs),
                             samplerate=SAMPLE_RATE,
                             channels=1, dtype='float32',
                             device=self.device, blocking=True)
                audio_chunk = rec[:, 0]

                # Append to ring buffer
                ring_buffer.extend(audio_chunk.tolist())
                if len(ring_buffer) > buffer_size:
                    ring_buffer = ring_buffer[-buffer_size:]

                buf = np.array(ring_buffer, dtype=np.float32)
                result = self._sync_and_read_frame(buf, sps)

                if result is None:
                    if self.show_progress:
                        elapsed = time.time() - start_time
                        n = len(self.received)
                        tot = self.total_frames or "?"
                        print(f"\r  Scanning... {n}/{tot} frames  {elapsed:.0f}s",
                              end='', flush=True)
                    continue

                if not result.get("crc_ok", False):
                    self.cksum_fails += 1
                    print(f"\n  ⚠️  CRC/ECC error — sending NACK")
                    self._send_ack(False)
                    # Clear buffer to avoid re-processing same bad data
                    ring_buffer = ring_buffer[-SAMPLE_RATE:]
                    continue

                frame_no     = result["frame_no"]
                total_frames = result["total_frames"]
                session_id   = result["session_id"]
                payload      = result["payload"]

                # Session init
                if self.session_id is None:
                    self.session_id   = session_id
                    self.total_frames = total_frames
                    print(f"\n[SESSION]  id={session_id.hex()}  frames={total_frames}")

                if session_id != self.session_id:
                    continue   # wrong session

                already_have = frame_no in self.received
                if not already_have:
                    self.received[frame_no] = payload
                    n_recv = len(self.received)
                    pct    = 100 * n_recv // max(total_frames, 1)
                    print(f"\n  ✅ Frame {frame_no+1}/{total_frames}  ({pct}%)  "
                          f"CRC32={zlib.crc32(payload)&0xFFFFFFFF:08X}")
                else:
                    print(f"\n  (duplicate frame {frame_no} — ACK resent)")

                self._send_ack(True)
                ring_buffer = ring_buffer[-SAMPLE_RATE:]  # keep only 1s after decode

                if self.total_frames > 0 and len(self.received) >= self.total_frames:
                    print(f"\n[RECEIVER] All {self.total_frames} frames received!")
                    break

        except KeyboardInterrupt:
            print("\n[RECEIVER] Aborted.")

        if not self.received:
            print("[RECEIVER] No frames decoded.")
            return None

        return self._assemble()

    def _assemble(self) -> str:
        print(f"\n[ASSEMBLING]  {len(self.received)}/{self.total_frames} frames ...")
        missing = set(range(self.total_frames)) - set(self.received.keys())
        if missing:
            print(f"  ⚠️  MISSING: {sorted(missing)[:10]}"
                  f"{'...' if len(missing) > 10 else ''} ({len(missing)} frames)")

        out = bytearray()
        for i in range(self.total_frames):
            out.extend(self.received.get(i, b''))

        os.makedirs(os.path.dirname(os.path.abspath(self.output_path)), exist_ok=True)
        with open(self.output_path, 'wb') as f:
            f.write(out)

        sha    = hashlib.sha256(out).hexdigest()
        missing_n = len(missing) if missing else 0
        verdict = "✅ COMPLETE" if not missing_n else f"⚠️  {missing_n} MISSING"
        print(f"[ASSEMBLED]  {len(out):,} B → {self.output_path}")
        print(f"             SHA-256     : {sha}")
        print(f"             Frames recv : {len(self.received)}/{self.total_frames}")
        print(f"             CRC fails   : {self.cksum_fails}")
        print(f"             Status      : {verdict}")
        return self.output_path
