#!/usr/bin/env python3
"""
MARICHI Web UI  v1.0
A browser-based control panel + QR web transfer for MARICHI.

Run (HTTP – laptop only):
    python app.py

Run (HTTPS – needed for phone camera):
    python app.py --https        # self-signed cert (accept warning on phone once)

Then open:
    Laptop  →  http://localhost:5000
    Android →  http://<laptop-ip>:5000
    Scanner →  http://<laptop-ip>:5000/scanner
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import math
import os
import secrets
import socket
import struct
import subprocess
import sys
import threading
import time
import uuid
import zlib
from pathlib import Path
from queue import Empty, Queue

from flask import Flask, Response, jsonify, render_template, request, send_file, stream_with_context

# ── Paths ──────────────────────────────────────────────────────────────────────
PROJECT_DIR = Path(__file__).parent
UPLOAD_DIR  = Path('/tmp/marichi_uploads')
OUTPUT_DIR  = Path('/tmp/marichi_output')
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(PROJECT_DIR))

# ── Flask ──────────────────────────────────────────────────────────────────────
app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 2 * 1024 * 1024 * 1024   # 2 GB

_PORT = 5000


# ══════════════════════════════════════════════════════════════════════════════
#  JOB MANAGER  (subprocess-based for visual / audio / qr CLI modes)
# ══════════════════════════════════════════════════════════════════════════════

_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()


def _new_job() -> tuple[str, dict]:
    jid = uuid.uuid4().hex[:12]
    job = {
        'process':     None,
        'log_q':       Queue(),
        'status':      'starting',
        'output_file': None,
        't0':          time.time(),
    }
    with _jobs_lock:
        _jobs[jid] = job
    return jid, job


def _run_subprocess(job: dict, cmd: list[str], cwd: str) -> None:
    """Run cmd, push each stdout line into job['log_q'], then push __DONE__."""
    try:
        env  = {**os.environ, 'PYTHONUNBUFFERED': '1'}
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, cwd=cwd, env=env,
        )
        job['process'] = proc
        job['status']  = 'running'
        for line in proc.stdout:
            job['log_q'].put(line.rstrip('\n'))
        proc.wait()
        job['status'] = 'done' if proc.returncode == 0 else 'error'
        job['log_q'].put(f'__DONE__ {proc.returncode}')
    except Exception as exc:
        job['status'] = 'error'
        job['log_q'].put(f'[FATAL] {exc}')
        job['log_q'].put('__DONE__ 1')


# ══════════════════════════════════════════════════════════════════════════════
#  QR WEB SESSION  (browser-native QR transfer — no OpenCV needed)
# ══════════════════════════════════════════════════════════════════════════════

QR_WEB_CHUNK = 2048   # payload bytes per web-QR frame (base64-safe)


class QRWebSession:
    """
    Manages a browser-native QR transfer.

    Flow:
      1.  Laptop uploads file → POST /api/qr/start → session created
      2.  Laptop browser fetches /api/qr/frame/<sid>/<n>  (PNG image)
      3.  Laptop browser shows frames in sequence (timer or ACK-driven)
      4.  Phone browser (BarcodeDetector) scans each QR code
      5.  Phone assembles file client-side and offers download

    QR text format (all ASCII — BarcodeDetector compatible):
        MRCH1|<session_hex>|<frame_no>|<total>|<crc32_HEX>|<base64_payload>
    """

    def __init__(self, filepath: str):
        self.filepath   = filepath
        self.name       = os.path.basename(filepath)
        self.session_id = secrets.token_bytes(8)
        self.sid        = self.session_id.hex()
        self.data       = Path(filepath).read_bytes()
        self.size       = len(self.data)
        self.sha256     = hashlib.sha256(self.data).hexdigest()
        self.total      = max(1, math.ceil(self.size / QR_WEB_CHUNK))
        self.acked: set[int] = set()
        self.done       = False
        self._cache: dict[int, bytes] = {}   # PNG cache (last 10 frames)
        self._lock  = threading.Lock()

    # ── PNG generation (on-demand, cached) ────────────────────────────────────
    def get_png(self, n: int) -> bytes:
        with self._lock:
            if n in self._cache:
                return self._cache[n]

        png = self._generate_png(n)

        with self._lock:
            if len(self._cache) >= 10:
                oldest = min(self._cache)
                del self._cache[oldest]
            self._cache[n] = png
        return png

    def _generate_png(self, n: int) -> bytes:
        import qrcode                          # type: ignore
        from qrcode.constants import ERROR_CORRECT_L  # type: ignore

        s   = n * QR_WEB_CHUNK
        e   = min(s + QR_WEB_CHUNK, self.size)
        pay = self.data[s:e]
        crc = f'{zlib.crc32(pay) & 0xFFFFFFFF:08X}'
        b64 = base64.b64encode(pay).decode()
        txt = f'MRCH1|{self.sid}|{n}|{self.total}|{crc}|{b64}'

        qr = qrcode.QRCode(
            version=None, error_correction=ERROR_CORRECT_L,
            box_size=5, border=2,
        )
        qr.add_data(txt)
        qr.make(fit=True)
        img = qr.make_image(fill_color='black', back_color='white')

        buf = io.BytesIO()
        img.save(buf, format='PNG')
        return buf.getvalue()

    # ── ACK (phone reports a frame received) ──────────────────────────────────
    def ack(self, n: int) -> dict:
        self.acked.add(n)
        if len(self.acked) >= self.total:
            self.done = True
        # Compute next frame laptop should display (first gap)
        nxt = n + 1
        while nxt in self.acked and nxt < self.total:
            nxt += 1
        return {
            'ok':        True,
            'acked':     len(self.acked),
            'total':     self.total,
            'done':      self.done,
            'next_frame': min(nxt, self.total - 1),
        }

    @property
    def progress(self) -> dict:
        return {
            'session_id': self.sid,
            'name':       self.name,
            'size':       self.size,
            'total':      self.total,
            'acked':      len(self.acked),
            'done':       self.done,
            'sha256':     self.sha256,
        }


_qr_sessions: dict[str, QRWebSession] = {}


# ══════════════════════════════════════════════════════════════════════════════
#  UTILITIES
# ══════════════════════════════════════════════════════════════════════════════

def _my_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return '127.0.0.1'


# ══════════════════════════════════════════════════════════════════════════════
#  ROUTES
# ══════════════════════════════════════════════════════════════════════════════

@app.route('/')
def index():
    return render_template('index.html', scanner=False)


@app.route('/scanner')
def scanner_page():
    """Phone-optimised QR scanner view."""
    return render_template('index.html', scanner=True)


# ── Upload ─────────────────────────────────────────────────────────────────────
@app.route('/api/upload', methods=['POST'])
def api_upload():
    f = request.files.get('file')
    if not f or not f.filename:
        return jsonify(error='no file'), 400
    fid  = uuid.uuid4().hex
    ext  = Path(f.filename).suffix
    path = UPLOAD_DIR / f'{fid}{ext}'
    f.save(str(path))
    return jsonify(file_id=fid, filename=f.filename,
                   path=str(path), size=path.stat().st_size)


# ── Send ───────────────────────────────────────────────────────────────────────
@app.route('/api/send', methods=['POST'])
def api_send():
    b    = request.json or {}
    path = b.get('path', '')
    if not os.path.exists(path):
        return jsonify(error='file not found'), 400

    mode    = b.get('mode', 'visual')
    jid, job = _new_job()

    cmd = [sys.executable, 'send.py', path, '--mode', mode]
    if mode in ('visual', 'all'):
        cmd += ['--block', str(b.get('block', 2)),
                '--hold',  str(b.get('hold', 80))]
        if int(b.get('ack_cam', -1)) >= 0:
            cmd += ['--ack-cam', str(b.get('ack_cam'))]
    if mode in ('audio', 'all'):
        cmd += ['--baud', str(b.get('baud', 300))]
    if mode in ('qr', 'all'):
        cmd += ['--fps', str(b.get('fps', 3))]

    threading.Thread(
        target=_run_subprocess, args=(job, cmd, str(PROJECT_DIR)), daemon=True
    ).start()
    return jsonify(job_id=jid)


# ── Receive ────────────────────────────────────────────────────────────────────
@app.route('/api/receive', methods=['POST'])
def api_receive():
    b       = request.json or {}
    mode    = b.get('mode', 'visual')
    outname = b.get('output', f'recv_{int(time.time())}')
    out     = str(OUTPUT_DIR / outname)

    jid, job = _new_job()
    job['output_file'] = out

    cmd = [sys.executable, 'receive.py', out,
           '--mode', mode, '--timeout', str(b.get('timeout', 7200))]
    if mode in ('visual', 'qr'):
        cmd += ['--cam',   str(b.get('cam', 0)),
                '--block', str(b.get('block', 2))]
    if mode == 'audio':
        cmd += ['--baud', str(b.get('baud', 300))]
    if b.get('no_ack'):
        cmd += ['--no-ack']

    threading.Thread(
        target=_run_subprocess, args=(job, cmd, str(PROJECT_DIR)), daemon=True
    ).start()
    return jsonify(job_id=jid, output=out)


# ── SSE log stream ─────────────────────────────────────────────────────────────
@app.route('/api/stream/<jid>')
def api_stream(jid: str):
    with _jobs_lock:
        job = _jobs.get(jid)
    if not job:
        return jsonify(error='job not found'), 404

    def generate():
        q = job['log_q']
        while True:
            try:
                line = q.get(timeout=20)
                if line.startswith('__DONE__'):
                    code = int(line.split()[1])
                    yield f"data: {json.dumps({'t': 'exit', 'code': code})}\n\n"
                    return
                yield f"data: {json.dumps({'t': 'log', 'msg': line})}\n\n"
            except Empty:
                yield f"data: {json.dumps({'t': 'ping'})}\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
    )


# ── Stop job ───────────────────────────────────────────────────────────────────
@app.route('/api/stop/<jid>', methods=['POST'])
def api_stop(jid: str):
    with _jobs_lock:
        job = _jobs.get(jid)
    if job:
        proc = job.get('process')
        if proc and proc.poll() is None:
            proc.terminate()
            job['status'] = 'stopped'
    return jsonify(ok=True)


# ── Job status ─────────────────────────────────────────────────────────────────
@app.route('/api/status/<jid>')
def api_job_status(jid: str):
    with _jobs_lock:
        job = _jobs.get(jid)
    if not job:
        return jsonify(error='not found'), 404
    return jsonify(status=job['status'],
                   output=job.get('output_file'),
                   elapsed=round(time.time() - job['t0'], 1))


# ── Download received file ─────────────────────────────────────────────────────
@app.route('/api/download/<jid>')
def api_download(jid: str):
    with _jobs_lock:
        job = _jobs.get(jid)
    if not job:
        return jsonify(error='not found'), 404
    out = job.get('output_file')
    if not out or not os.path.exists(out):
        return jsonify(error='file not ready'), 404
    return send_file(out, as_attachment=True,
                     download_name=os.path.basename(out))


# ── Validate ───────────────────────────────────────────────────────────────────
@app.route('/api/validate', methods=['POST'])
def api_validate():
    orig = request.files.get('original')
    recv = request.files.get('received')
    if not orig or not recv:
        return jsonify(error='need both files'), 400

    op = UPLOAD_DIR / f'vo_{uuid.uuid4().hex}{Path(orig.filename or "f").suffix}'
    rp = UPLOAD_DIR / f'vr_{uuid.uuid4().hex}{Path(recv.filename or "f").suffix}'
    orig.save(str(op))
    recv.save(str(rp))

    try:
        from marichi.validator import Validator
        rep = Validator(str(op), str(rp)).run()
        return jsonify(
            verdict      = rep.verdict,
            orig_size    = rep.original_size,
            recv_size    = rep.received_size,
            byte_diffs   = rep.byte_diff_count,
            bit_diffs    = rep.bit_diff_count,
            first_diff   = rep.first_diff_offset,
            sha_match    = rep.original_sha256 == rep.received_sha256,
            sha_orig     = rep.original_sha256,
            sha_recv     = rep.received_sha256,
        )
    except Exception as exc:
        return jsonify(error=str(exc)), 500
    finally:
        op.unlink(missing_ok=True)
        rp.unlink(missing_ok=True)


# ── QR Web Transfer ────────────────────────────────────────────────────────────

@app.route('/api/qr/start', methods=['POST'])
def qr_start():
    b    = request.json or {}
    path = b.get('path', '')
    if not os.path.exists(path):
        return jsonify(error='file not found'), 400
    try:
        sess = QRWebSession(path)
        _qr_sessions[sess.sid] = sess
        return jsonify(session_id=sess.sid, total=sess.total,
                       size=sess.size, name=sess.name, sha256=sess.sha256)
    except Exception as exc:
        return jsonify(error=str(exc)), 500


@app.route('/api/qr/frame/<sid>/<int:n>')
def qr_frame(sid: str, n: int):
    sess = _qr_sessions.get(sid)
    if not sess:
        return jsonify(error='session not found'), 404
    if not (0 <= n < sess.total):
        return jsonify(error='frame out of range'), 400
    try:
        return Response(sess.get_png(n), mimetype='image/png',
                        headers={'Cache-Control': 'public, max-age=3600'})
    except Exception as exc:
        return jsonify(error=str(exc)), 500


@app.route('/api/qr/status/<sid>')
def qr_status(sid: str):
    sess = _qr_sessions.get(sid)
    if not sess:
        return jsonify(error='not found'), 404
    return jsonify(sess.progress)


@app.route('/api/qr/ack', methods=['POST'])
def qr_ack():
    """Phone calls this after successfully scanning a frame."""
    b   = request.json or {}
    sid = b.get('session_id', '')
    n   = b.get('frame_no', -1)
    sess = _qr_sessions.get(sid)
    if not sess:
        return jsonify(error='session not found'), 404
    return jsonify(sess.ack(int(n)))


# ── Network info ───────────────────────────────────────────────────────────────
@app.route('/api/info')
def api_info():
    ip     = _my_ip()
    scheme = 'https' if _USE_HTTPS else 'http'
    return jsonify(
        ip          = ip,
        port        = _PORT,
        url         = f'{scheme}://{ip}:{_PORT}',
        scanner_url = f'{scheme}://{ip}:{_PORT}/scanner',
    )


# ══════════════════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

_USE_HTTPS = False

if __name__ == '__main__':
    ap = argparse.ArgumentParser(description='MARICHI Web UI')
    ap.add_argument('--port',  type=int, default=5000)
    ap.add_argument('--https', action='store_true',
                    help='Enable HTTPS with adhoc cert (needed for phone camera)')
    args = ap.parse_args()

    _PORT      = args.port
    _USE_HTTPS = args.https

    ip     = _my_ip()
    scheme = 'https' if args.https else 'http'

    print('\n🌀 MARICHI Web UI  v1.0')
    print('─' * 40)
    print(f'  Laptop  : {scheme}://localhost:{args.port}')
    print(f'  Android : {scheme}://{ip}:{args.port}')
    print(f'  Scanner : {scheme}://{ip}:{args.port}/scanner')
    if not args.https:
        print()
        print('  ⚠️  Phone camera requires HTTPS.')
        print(f'     Run:  python app.py --https')
        print(f'     Then accept the cert warning on your phone.')
    print('─' * 40 + '\n')

    ssl_ctx = 'adhoc' if args.https else None
    app.run(host='0.0.0.0', port=args.port,
            debug=False, threaded=True, ssl_context=ssl_ctx)
