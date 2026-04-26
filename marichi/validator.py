"""
MARICHI — Zero-Loss Validator

Compares original file against received file at every level:
  - SHA-256 hash (whole file)
  - SHA-256 hash (per 1 MB block)
  - Byte-by-byte diff with exact offset reporting
  - Bit-level diff count
  - Summary verdict: PERFECT / DEGRADED / CORRUPT
"""

from __future__ import annotations
import os
import hashlib
import struct
from dataclasses import dataclass, field


BLOCK_SIZE_BYTES = 1 * 1024 * 1024   # 1 MB blocks for progressive hashing


@dataclass
class ValidationReport:
    original_path: str
    received_path: str

    # File sizes
    original_size: int = 0
    received_size:  int = 0

    # Hashes
    original_sha256: str = ""
    received_sha256:  str = ""

    # Differences
    byte_diff_count:  int = 0
    bit_diff_count:   int = 0
    first_diff_offset: int = -1   # -1 = identical
    block_mismatches: list[int] = field(default_factory=list)   # block indices

    # Verdict
    verdict: str = ""   # "PERFECT" | "SIZE_MISMATCH" | "DEGRADED" | "CORRUPT"

    def print(self) -> None:
        pad = 26
        print("\n" + "═"*60)
        print("  MARICHI VALIDATION REPORT")
        print("═"*60)
        print(f"  {'Original':<{pad}}: {self.original_path}")
        print(f"  {'Received':<{pad}}: {self.received_path}")
        print()
        print(f"  {'Original size':<{pad}}: {self.original_size:,} bytes")
        print(f"  {'Received size':<{pad}}: {self.received_size:,} bytes")
        size_match = "✅ MATCH" if self.original_size == self.received_size else "❌ MISMATCH"
        print(f"  {'Size check':<{pad}}: {size_match}")
        print()
        print(f"  {'Original SHA-256':<{pad}}: {self.original_sha256}")
        print(f"  {'Received SHA-256':<{pad}}: {self.received_sha256}")
        hash_match = "✅ MATCH" if self.original_sha256 == self.received_sha256 else "❌ MISMATCH"
        print(f"  {'Hash check':<{pad}}: {hash_match}")
        print()
        if self.first_diff_offset >= 0:
            print(f"  {'First diff offset':<{pad}}: byte {self.first_diff_offset:,}"
                  f"  (0x{self.first_diff_offset:08X})")
            print(f"  {'Byte differences':<{pad}}: {self.byte_diff_count:,}")
            print(f"  {'Bit  differences':<{pad}}: {self.bit_diff_count:,}")
            pct = 100 * self.byte_diff_count / max(self.original_size, 1)
            print(f"  {'Corruption %':<{pad}}: {pct:.6f}%")
            if self.block_mismatches:
                blocks_str = str(self.block_mismatches[:10])
                if len(self.block_mismatches) > 10:
                    blocks_str += f" (+{len(self.block_mismatches)-10} more)"
                print(f"  {'Corrupt 1MB blocks':<{pad}}: {blocks_str}")
        else:
            print(f"  {'Differences':<{pad}}: NONE ✅")
        print()
        icon = {"PERFECT": "🟢", "DEGRADED": "🟡",
                "SIZE_MISMATCH": "🔴", "CORRUPT": "🔴"}.get(self.verdict, "⚪")
        print(f"  {'VERDICT':<{pad}}: {icon}  {self.verdict}")
        print("═"*60 + "\n")


class Validator:

    def __init__(self, original_path: str, received_path: str):
        self.original_path = original_path
        self.received_path = received_path

    def run(self) -> ValidationReport:
        rep = ValidationReport(
            original_path=self.original_path,
            received_path=self.received_path,
        )

        # ── Load both files ────────────────────────────────────────────────────
        if not os.path.exists(self.original_path):
            raise FileNotFoundError(f"Original not found: {self.original_path}")
        if not os.path.exists(self.received_path):
            raise FileNotFoundError(f"Received not found: {self.received_path}")

        with open(self.original_path, 'rb') as f:
            orig = f.read()
        with open(self.received_path, 'rb') as f:
            recv = f.read()

        rep.original_size = len(orig)
        rep.received_size  = len(recv)

        # ── Full-file SHA-256 ─────────────────────────────────────────────────
        rep.original_sha256 = hashlib.sha256(orig).hexdigest()
        rep.received_sha256  = hashlib.sha256(recv).hexdigest()

        # ── Size mismatch ─────────────────────────────────────────────────────
        if rep.original_size != rep.received_size:
            rep.verdict = "SIZE_MISMATCH"
            rep.print()
            return rep

        # ── Byte-level diff ───────────────────────────────────────────────────
        print("[VALIDATOR] Comparing bytes ...")
        orig_arr = bytearray(orig)
        recv_arr = bytearray(recv)
        size     = len(orig_arr)

        byte_diffs  = 0
        bit_diffs   = 0
        first_diff  = -1
        block_bad:  set[int] = set()

        # Process in 64KB chunks for memory efficiency
        CHUNK = 65536
        for offset in range(0, size, CHUNK):
            o_chunk = orig_arr[offset:offset+CHUNK]
            r_chunk = recv_arr[offset:offset+CHUNK]
            for j, (ob, rb) in enumerate(zip(o_chunk, r_chunk)):
                if ob != rb:
                    abs_off = offset + j
                    byte_diffs += 1
                    bit_diffs  += bin(ob ^ rb).count('1')
                    if first_diff == -1:
                        first_diff = abs_off
                    block_bad.add(abs_off // BLOCK_SIZE_BYTES)

        rep.byte_diff_count   = byte_diffs
        rep.bit_diff_count    = bit_diffs
        rep.first_diff_offset = first_diff
        rep.block_mismatches  = sorted(block_bad)

        # ── Verdict ───────────────────────────────────────────────────────────
        if byte_diffs == 0:
            rep.verdict = "PERFECT"
        else:
            pct = 100 * byte_diffs / size
            rep.verdict = "DEGRADED" if pct < 5.0 else "CORRUPT"

        rep.print()
        return rep


def per_block_hashes(filepath: str, block_bytes: int = BLOCK_SIZE_BYTES) -> list[str]:
    """Return SHA-256 hash for each block of a file. Useful for diffing large files."""
    hashes = []
    with open(filepath, 'rb') as f:
        while True:
            chunk = f.read(block_bytes)
            if not chunk:
                break
            hashes.append(hashlib.sha256(chunk).hexdigest())
    return hashes
