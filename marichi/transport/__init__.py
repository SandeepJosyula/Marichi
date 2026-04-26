"""
MARICHI Transport Layer — multi-channel data transmission

Transport modes:
  A  (visual)  — Full-screen pixel frame encoding via screen + camera  [built v0.1]
  C  (audio)   — MFSK acoustic modem via speaker + microphone          [built v0.2]
  D  (qr)      — QR-code animation via screen + camera (mobile-safe)   [built v0.2]
  all          — All transports simultaneously (best reliability)

Choose mode based on your device combination:
  Two laptops with cameras facing each other → visual (fastest)
  Phone/tablet receiving from laptop screen  → qr    (mobile-compatible)
  No camera available / audio-only hardware  → audio (lowest throughput)
  Maximum reliability / redundancy needed    → all
"""

from .audio_modem import AudioSender, AudioReceiver          # Option C
from .qr_stream  import QRSender,    QRReceiver              # Option D

__all__ = [
    "AudioSender", "AudioReceiver",
    "QRSender",    "QRReceiver",
]
