#!/usr/bin/env python3
"""
MARICHI Web UI  v1.0  —  pure Python stdlib (no Flask required)

Run:
    python app.py                  # HTTP  – laptop only
    python app.py --https          # HTTPS – needed for phone camera (uses openssl CLI)
    python app.py --port 8080      # custom port

Open:
    Laptop  →  http://localhost:5000
    Android →  http://<laptop-ip>:5000
    Scanner →  http://<laptop-ip>:5000/scanner
"""
from __future__ import annotations
import argparse, base64, hashlib, io, json, math, mimetypes
import os, re, secrets, socket, ssl, struct, subprocess, sys, threading
import time, uuid, zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from queue import Empty, Queue

# ── Paths ──────────────────────────────────────────────────────────────────────
PROJECT_DIR = Path(__file__).parent
UPLOAD_DIR  = Path('/tmp/marichi_uploads')
OUTPUT_DIR  = Path('/tmp/marichi_output')
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(PROJECT_DIR))

_PORT      = 5000
_USE_HTTPS = False

# ══════════════════════════════════════════════════════════════════════════════
#  JOB MANAGER
# ══════════════════════════════════════════════════════════════════════════════
_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()

def _new_job() -> tuple[str, dict]:
    jid = uuid.uuid4().hex[:12]
    job = {'process': None, 'log_q': Queue(), 'status': 'starting',
           'output_file': None, 't0': time.time()}
    with _jobs_lock:
        _jobs[jid] = job
    return jid, job

def _run_subprocess(job: dict, cmd: list, cwd: str) -> None:
    try:
        env  = {**os.environ, 'PYTHONUNBUFFERED': '1'}
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT,
                                text=True, bufsize=1, cwd=cwd, env=env)
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
#  QR WEB SESSION
# ══════════════════════════════════════════════════════════════════════════════
QR_WEB_CHUNK = 2048

class QRWebSession:
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
        self._cache: dict[int, bytes] = {}
        self._lock  = threading.Lock()

    def get_png(self, n: int) -> bytes:
        with self._lock:
            if n in self._cache:
                return self._cache[n]
        png = self._gen(n)
        with self._lock:
            if len(self._cache) >= 10:
                del self._cache[min(self._cache)]
            self._cache[n] = png
        return png

    def _gen(self, n: int) -> bytes:
        import qrcode
        from qrcode.constants import ERROR_CORRECT_L
        s   = n * QR_WEB_CHUNK
        e   = min(s + QR_WEB_CHUNK, self.size)
        pay = self.data[s:e]
        crc = f'{zlib.crc32(pay) & 0xFFFFFFFF:08X}'
        txt = f'MRCH1|{self.sid}|{n}|{self.total}|{crc}|{base64.b64encode(pay).decode()}'
        qr  = qrcode.QRCode(version=None, error_correction=ERROR_CORRECT_L,
                            box_size=5, border=2)
        qr.add_data(txt); qr.make(fit=True)
        img = qr.make_image(fill_color='black', back_color='white')
        buf = io.BytesIO(); img.save(buf, format='PNG')
        return buf.getvalue()

    def ack(self, n: int) -> dict:
        self.acked.add(n)
        if len(self.acked) >= self.total:
            self.done = True
        nxt = n + 1
        while nxt in self.acked and nxt < self.total:
            nxt += 1
        return {'ok': True, 'acked': len(self.acked),
                'total': self.total, 'done': self.done,
                'next_frame': min(nxt, self.total - 1)}

    @property
    def progress(self) -> dict:
        return {'session_id': self.sid, 'name': self.name, 'size': self.size,
                'total': self.total, 'acked': len(self.acked),
                'done': self.done, 'sha256': self.sha256}

_qr: dict[str, QRWebSession] = {}

# ══════════════════════════════════════════════════════════════════════════════
#  UTILITIES
# ══════════════════════════════════════════════════════════════════════════════
def _my_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]; s.close(); return ip
    except Exception:
        return '127.0.0.1'

def _json_resp(handler, data: dict, code: int = 200) -> None:
    body = json.dumps(data).encode()
    handler.send_response(code)
    handler.send_header('Content-Type', 'application/json')
    handler.send_header('Content-Length', str(len(body)))
    handler.send_header('Access-Control-Allow-Origin', '*')
    handler.end_headers()
    handler.wfile.write(body)

def _read_json(handler) -> dict:
    length = int(handler.headers.get('Content-Length', 0))
    return json.loads(handler.rfile.read(length)) if length else {}

def _read_multipart(handler) -> dict:
    """Return dict of {field_name: (filename|None, bytes)}."""
    ct = handler.headers.get('Content-Type', '')
    length = int(handler.headers.get('Content-Length', 0))
    data   = handler.rfile.read(length)
    # Extract boundary
    m = re.search(r'boundary=([^\s;]+)', ct)
    if not m:
        return {}
    boundary = ('--' + m.group(1)).encode()
    parts    = data.split(boundary)
    result   = {}
    for part in parts[1:]:
        if part in (b'--\r\n', b'--'):
            continue
        part = part.lstrip(b'\r\n')
        if b'\r\n\r\n' not in part:
            continue
        headers_raw, body = part.split(b'\r\n\r\n', 1)
        body = body.rstrip(b'\r\n--')
        hdr_text = headers_raw.decode('utf-8', errors='replace')
        cd = re.search(r'Content-Disposition:.*?name="([^"]+)"', hdr_text)
        fn = re.search(r'filename="([^"]*)"', hdr_text)
        if cd:
            name = cd.group(1)
            result[name] = (fn.group(1) if fn else None, body)
    return result


# ══════════════════════════════════════════════════════════════════════════════
#  REQUEST HANDLER
# ══════════════════════════════════════════════════════════════════════════════
class MarichiHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass   # silence default access log

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def do_GET(self):
        p = self.path.split('?')[0].rstrip('/')

        if p in ('', '/'):               return self._page(False)
        if p == '/scanner':              return self._page(True)
        if p.startswith('/static/'):     return self._static(p[8:])
        if p == '/api/info':             return self._api_info()
        if p.startswith('/api/stream/'): return self._api_stream(p[13:])
        if p.startswith('/api/status/'): return self._api_job_status(p[13:])
        if p.startswith('/api/download/'): return self._api_download(p[15:])

        m = re.match(r'/api/qr/frame/([0-9a-f]+)/(\d+)$', p)
        if m: return self._qr_frame(m.group(1), int(m.group(2)))

        m = re.match(r'/api/qr/status/([0-9a-f]+)$', p)
        if m: return self._qr_status(m.group(1))

        _json_resp(self, {'error': 'not found'}, 404)

    def do_POST(self):
        p = self.path.split('?')[0].rstrip('/')

        if p == '/api/upload':       return self._api_upload()
        if p == '/api/send':         return self._api_send()
        if p == '/api/receive':      return self._api_receive()
        if p == '/api/validate':     return self._api_validate()
        if p == '/api/qr/start':     return self._qr_start()
        if p == '/api/qr/ack':       return self._qr_ack()

        m = re.match(r'/api/stop/([0-9a-f]+)$', p)
        if m: return self._api_stop(m.group(1))

        _json_resp(self, {'error': 'not found'}, 404)

    # ── Pages ──────────────────────────────────────────────────────────────
    def _page(self, scanner: bool):
        html_path = PROJECT_DIR / 'templates' / 'index.html'
        try:
            html = html_path.read_text(encoding='utf-8')
            html = html.replace('%%SCANNER%%', 'true' if scanner else 'false')
            body = html.encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception as exc:
            _json_resp(self, {'error': str(exc)}, 500)

    # ── Static files ───────────────────────────────────────────────────────
    def _static(self, filename: str):
        path = PROJECT_DIR / 'static' / filename
        if not path.exists():
            _json_resp(self, {'error': 'not found'}, 404); return
        mime = mimetypes.guess_type(str(path))[0] or 'application/octet-stream'
        body = path.read_bytes()
        self.send_response(200)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'public, max-age=300')
        self.end_headers()
        self.wfile.write(body)

    # ── Network info ───────────────────────────────────────────────────────
    def _api_info(self):
        ip     = _my_ip()
        scheme = 'https' if _USE_HTTPS else 'http'
        _json_resp(self, {
            'ip': ip, 'port': _PORT,
            'url':         f'{scheme}://{ip}:{_PORT}',
            'scanner_url': f'{scheme}://{ip}:{_PORT}/scanner',
        })

    # ── File upload ────────────────────────────────────────────────────────
    def _api_upload(self):
        parts = _read_multipart(self)
        if 'file' not in parts:
            _json_resp(self, {'error': 'no file'}, 400); return
        filename, data = parts['file']
        fid  = uuid.uuid4().hex
        ext  = Path(filename or 'file').suffix
        path = UPLOAD_DIR / f'{fid}{ext}'
        path.write_bytes(data)
        _json_resp(self, {'file_id': fid, 'filename': filename,
                          'path': str(path), 'size': len(data)})

    # ── Send job ───────────────────────────────────────────────────────────
    def _api_send(self):
        b    = _read_json(self)
        path = b.get('path', '')
        if not os.path.exists(path):
            _json_resp(self, {'error': 'file not found'}, 400); return
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
        threading.Thread(target=_run_subprocess,
                         args=(job, cmd, str(PROJECT_DIR)), daemon=True).start()
        _json_resp(self, {'job_id': jid})

    # ── Receive job ────────────────────────────────────────────────────────
    def _api_receive(self):
        b       = _read_json(self)
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
        threading.Thread(target=_run_subprocess,
                         args=(job, cmd, str(PROJECT_DIR)), daemon=True).start()
        _json_resp(self, {'job_id': jid, 'output': out})

    # ── SSE log stream ─────────────────────────────────────────────────────
    def _api_stream(self, jid: str):
        with _jobs_lock:
            job = _jobs.get(jid)
        if not job:
            _json_resp(self, {'error': 'not found'}, 404); return

        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('X-Accel-Buffering', 'no')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        q = job['log_q']
        try:
            while True:
                try:
                    line = q.get(timeout=20)
                    if line.startswith('__DONE__'):
                        code = int(line.split()[1])
                        msg  = json.dumps({'t': 'exit', 'code': code}) + '\n\n'
                    else:
                        msg  = json.dumps({'t': 'log', 'msg': line}) + '\n\n'
                    self.wfile.write(f'data: {msg}'.encode())
                    self.wfile.flush()
                    if line.startswith('__DONE__'):
                        break
                except Empty:
                    self.wfile.write(b'data: {"t":"ping"}\n\n')
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass

    # ── Stop job ───────────────────────────────────────────────────────────
    def _api_stop(self, jid: str):
        with _jobs_lock:
            job = _jobs.get(jid)
        if job:
            proc = job.get('process')
            if proc and proc.poll() is None:
                proc.terminate(); job['status'] = 'stopped'
        _json_resp(self, {'ok': True})

    # ── Job status ─────────────────────────────────────────────────────────
    def _api_job_status(self, jid: str):
        with _jobs_lock:
            job = _jobs.get(jid)
        if not job:
            _json_resp(self, {'error': 'not found'}, 404); return
        _json_resp(self, {'status': job['status'],
                          'output': job.get('output_file'),
                          'elapsed': round(time.time() - job['t0'], 1)})

    # ── Download ───────────────────────────────────────────────────────────
    def _api_download(self, jid: str):
        with _jobs_lock:
            job = _jobs.get(jid)
        if not job:
            _json_resp(self, {'error': 'not found'}, 404); return
        out = job.get('output_file')
        if not out or not os.path.exists(out):
            _json_resp(self, {'error': 'file not ready'}, 404); return
        body = Path(out).read_bytes()
        name = os.path.basename(out)
        self.send_response(200)
        self.send_header('Content-Type', 'application/octet-stream')
        self.send_header('Content-Disposition', f'attachment; filename="{name}"')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # ── Validate ───────────────────────────────────────────────────────────
    def _api_validate(self):
        parts = _read_multipart(self)
        if 'original' not in parts or 'received' not in parts:
            _json_resp(self, {'error': 'need both files'}, 400); return
        orig_name, orig_data = parts['original']
        recv_name, recv_data = parts['received']
        op = UPLOAD_DIR / f'vo_{uuid.uuid4().hex}{Path(orig_name or "f").suffix}'
        rp = UPLOAD_DIR / f'vr_{uuid.uuid4().hex}{Path(recv_name or "f").suffix}'
        op.write_bytes(orig_data); rp.write_bytes(recv_data)
        try:
            from marichi.validator import Validator
            rep = Validator(str(op), str(rp)).run()
            _json_resp(self, {
                'verdict':   rep.verdict,
                'orig_size': rep.original_size,
                'recv_size': rep.received_size,
                'byte_diffs': rep.byte_diff_count,
                'bit_diffs':  rep.bit_diff_count,
                'first_diff': rep.first_diff_offset,
                'sha_match':  rep.original_sha256 == rep.received_sha256,
                'sha_orig':   rep.original_sha256,
                'sha_recv':   rep.received_sha256,
            })
        except Exception as exc:
            _json_resp(self, {'error': str(exc)}, 500)
        finally:
            op.unlink(missing_ok=True); rp.unlink(missing_ok=True)

    # ── QR Web ─────────────────────────────────────────────────────────────
    def _qr_start(self):
        b    = _read_json(self)
        path = b.get('path', '')
        if not os.path.exists(path):
            _json_resp(self, {'error': 'file not found'}, 400); return
        try:
            sess = QRWebSession(path)
            _qr[sess.sid] = sess
            _json_resp(self, {'session_id': sess.sid, 'total': sess.total,
                              'size': sess.size, 'name': sess.name,
                              'sha256': sess.sha256})
        except Exception as exc:
            _json_resp(self, {'error': str(exc)}, 500)

    def _qr_frame(self, sid: str, n: int):
        sess = _qr.get(sid)
        if not sess:
            _json_resp(self, {'error': 'session not found'}, 404); return
        if not (0 <= n < sess.total):
            _json_resp(self, {'error': 'out of range'}, 400); return
        try:
            body = sess.get_png(n)
            self.send_response(200)
            self.send_header('Content-Type', 'image/png')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'public, max-age=3600')
            self.end_headers()
            self.wfile.write(body)
        except Exception as exc:
            _json_resp(self, {'error': str(exc)}, 500)

    def _qr_status(self, sid: str):
        sess = _qr.get(sid)
        if not sess:
            _json_resp(self, {'error': 'not found'}, 404); return
        _json_resp(self, sess.progress)

    def _qr_ack(self):
        b   = _read_json(self)
        sid = b.get('session_id', '')
        n   = b.get('frame_no', -1)
        sess = _qr.get(sid)
        if not sess:
            _json_resp(self, {'error': 'session not found'}, 404); return
        _json_resp(self, sess.ack(int(n)))


# ══════════════════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════
def _make_ssl_context() -> ssl.SSLContext:
    """Generate self-signed cert with openssl CLI and wrap server socket."""
    d    = Path('/tmp/marichi_ssl'); d.mkdir(exist_ok=True)
    cert = d / 'cert.pem'; key = d / 'key.pem'
    if not cert.exists():
        subprocess.run([
            'openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
            '-out', str(cert), '-keyout', str(key),
            '-days', '365', '-subj', '/CN=marichi-local'
        ], capture_output=True, check=True)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(str(cert), str(key))
    return ctx


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description='MARICHI Web UI')
    ap.add_argument('--port',  type=int, default=5000)
    ap.add_argument('--https', action='store_true',
                    help='Enable HTTPS (self-signed cert via openssl)')
    args = ap.parse_args()

    _PORT      = args.port
    _USE_HTTPS = args.https

    ip     = _my_ip()
    scheme = 'https' if args.https else 'http'

    server = ThreadingHTTPServer(('0.0.0.0', args.port), MarichiHandler)

    if args.https:
        try:
            server.socket = _make_ssl_context().wrap_socket(
                server.socket, server_side=True)
            print(f'🔒 HTTPS enabled (self-signed cert)')
        except Exception as e:
            print(f'⚠️  HTTPS failed ({e}) — falling back to HTTP')

    print(f'\n🌀 MARICHI Web UI  v1.0')
    print('─' * 44)
    print(f'  Laptop  : {scheme}://localhost:{args.port}')
    print(f'  Android : {scheme}://{ip}:{args.port}')
    print(f'  Scanner : {scheme}://{ip}:{args.port}/scanner')
    if not args.https:
        print()
        print(f'  ⚠️  Phone camera needs HTTPS.')
        print(f'     Run:  python app.py --https')
        print(f'     Then tap "Advanced → Proceed" on cert warning.')
    print('─' * 44)
    print(f'  Ctrl+C to stop\n')

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\n[MARICHI] Server stopped.')
        server.shutdown()
