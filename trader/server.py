"""Local-only application server; standard-library Python, no CDN dependencies."""
from __future__ import annotations

import json
import os
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from .engine import PaperEngine, run_backtest, validated_settings
from .mt5_bridge import MT5Bridge

STATIC = Path(__file__).parent / "static"


def data_directory():
    if os.getenv("TESTMEV_DATA_DIR"):
        return Path(os.environ["TESTMEV_DATA_DIR"])
    return Path(os.getenv("LOCALAPPDATA") or Path.home() / ".local" / "share") / "TESTMEV"


class Application:
    def __init__(self, data_dir=None):
        self.directory = Path(data_dir) if data_dir else data_directory()
        self.directory.mkdir(parents=True, exist_ok=True)
        settings = {}
        settings_file = self.directory / "settings.json"
        warning = None
        if settings_file.exists():
            try:
                settings = validated_settings(json.loads(settings_file.read_text("utf-8")))
            except (ValueError, TypeError, OSError):
                warning = "อ่านการตั้งค่าเดิมไม่ได้ ใช้ค่าเริ่มต้น · ไฟล์เดิมยังอยู่"
        self.engine = PaperEngine(settings)
        if warning:
            self.engine.log(warning)
        self.bridge = MT5Bridge()
        self.mt5_state = {"connected": False, "read_only": True}
        self.lock = threading.RLock()
        self.mt5_lock = threading.Lock()
        self.token = secrets.token_urlsafe(32)
        self.stop_event = threading.Event()
        self.server = None
        self.last_error = None

    def persist_settings(self, settings):
        temporary = self.directory / "settings.tmp"
        temporary.write_text(json.dumps(settings, ensure_ascii=False, indent=2), "utf-8")
        temporary.replace(self.directory / "settings.json")

    def archive_session(self):
        if self.engine.history:
            filename = "trades-" + time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2) + ".csv"
            (self.directory / filename).write_text(self.engine.csv(), encoding="utf-8-sig")

    def state(self):
        with self.lock:
            return {**self.engine.snapshot(), "mt5": self.mt5_state, "error": self.last_error}

    def action(self, action, data):
        # MT5 IPC may wait; it must not freeze the paper exchange or HTTP reads.
        if action in ("connect", "disconnect"):
            with self.mt5_lock:
                if action == "connect":
                    path, symbol = data.get("path", ""), data.get("symbol", "XAUUSD")
                    if not isinstance(path, str) or not isinstance(symbol, str) or not symbol or len(symbol) > 64:
                        raise ValueError("ชื่อสัญลักษณ์ MT5 ไม่ถูกต้อง")
                    result = self.bridge.connect(path.strip(), symbol.strip())
                else:
                    self.bridge.disconnect()
                    result = self.bridge.snapshot()
            with self.lock:
                self.mt5_state = result
                self.engine.log("เชื่อม MT5 สำหรับดูข้อมูลเท่านั้น" if action == "connect" else "ตัดการเชื่อมต่อ MT5 แล้ว")
            return self.state()
        if action == "backtest":
            with self.lock:
                settings = dict(self.engine.settings)
            return run_backtest(settings)
        with self.lock:
            if action == "start":
                self.engine.running = True
                self.engine.log("เริ่มบอท EMA ตัวอย่าง · ส่งคำสั่งในบัญชีจำลองเท่านั้น")
            elif action == "stop":
                self.engine.running = False
                self.engine.log("หยุดเปิดออเดอร์อัตโนมัติ · SL/TP ของออเดอร์เดิมยังทำงาน")
            elif action == "order":
                self.engine.order(data.get("side"))
            elif action == "close":
                self.engine.close(data.get("id"))
            elif action == "close_all":
                self.engine.running = False
                self.engine.close_all()
                self.engine.log("หยุดบอทและปิดออเดอร์จำลองทั้งหมดแล้ว")
            elif action == "settings":
                # Validate all business constraints before touching disk or memory.
                candidate = validated_settings(data, self.engine.settings)
                if self.engine.running:
                    raise ValueError("หยุดบอทก่อนเปลี่ยนการตั้งค่า")
                if self.engine.positions and any(candidate[k] != self.engine.settings[k] for k in ("symbol", "timeframe")):
                    raise ValueError("ปิดออเดอร์จำลองก่อนเปลี่ยนสินทรัพย์หรือช่วงเวลา")
                self.persist_settings(candidate)
                self.engine.configure(candidate)
            elif action == "reset":
                if self.engine.running or self.engine.positions:
                    raise ValueError("หยุดบอทและปิดออเดอร์ก่อนรีเซ็ต")
                self.archive_session()
                self.engine = PaperEngine(dict(self.engine.settings))
            elif action == "shutdown":
                self.engine.running = False
                self.stop_event.set()
                threading.Thread(target=self.server.shutdown, daemon=True).start()
            else:
                raise ValueError("ไม่พบคำสั่งนี้")
        return self.state()

    def run_ticks(self):
        while not self.stop_event.wait(2):
            try:
                with self.lock:
                    self.engine.tick()
            except Exception:
                with self.lock:
                    self.engine.running = False
                    self.last_error = "เครื่องจำลองหยุดเนื่องจากข้อผิดพลาด กรุณาเปิดแอปใหม่"
                break

    def poll_mt5(self):
        while not self.stop_event.wait(2):
            try:
                with self.mt5_lock:
                    state = self.bridge.snapshot()
                with self.lock:
                    self.mt5_state = state
            except Exception:
                with self.lock:
                    self.mt5_state = {"connected": False, "read_only": True, "error": "อ่านข้อมูล MT5 ไม่สำเร็จ กรุณาเชื่อมต่อใหม่"}


def create_server(app, port=0):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def send(self, status, body, content_type="application/json; charset=utf-8", disposition=None):
            if not isinstance(body, bytes):
                body = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            if disposition:
                self.send_header("Content-Disposition", disposition)
            self.end_headers()
            self.wfile.write(body)

        def json(self, status, payload):
            self.send(status, json.dumps(payload, ensure_ascii=False, allow_nan=False))

        def trusted(self):
            expected = f"127.0.0.1:{self.server.server_port}"
            if self.headers.get("Host") != expected:
                self.json(403, {"error": "Invalid host"})
                return False
            origin = self.headers.get("Origin")
            if origin and origin != f"http://{expected}":
                self.json(403, {"error": "Invalid origin"})
                return False
            if self.headers.get("Sec-Fetch-Site") == "cross-site":
                self.json(403, {"error": "Cross-site request denied"})
                return False
            return True

        def do_GET(self):
            if not self.trusted():
                return
            route = urlsplit(self.path).path
            if route == "/api/bootstrap":
                self.json(200, {"token": app.token, "version": "1.0.0"})
            elif route == "/api/state":
                self.json(200, app.state())
            elif route == "/api/export":
                with app.lock:
                    csv_text = app.engine.csv()
                self.send(200, ("\ufeff" + csv_text).encode("utf-8"), "text/csv; charset=utf-8", 'attachment; filename="testmev-paper-trades.csv"')
            elif route in ("/", "/app.js", "/style.css", "/icon.svg"):
                filename = "index.html" if route == "/" else route[1:]
                mime = {"html": "text/html; charset=utf-8", "js": "text/javascript; charset=utf-8", "css": "text/css; charset=utf-8", "svg": "image/svg+xml"}[filename.split(".")[-1]]
                self.send(200, (STATIC / filename).read_bytes(), mime)
            else:
                self.json(404, {"error": "Not found"})

        def do_POST(self):
            if not self.trusted():
                return
            if not secrets.compare_digest(self.headers.get("X-App-Token", ""), app.token):
                self.json(403, {"error": "Invalid session"})
                return
            route = urlsplit(self.path).path
            if not route.startswith("/api/"):
                self.json(404, {"error": "Not found"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 <= length <= 16384:
                    raise ValueError("คำขอมีขนาดใหญ่เกินไป")
                data = json.loads(self.rfile.read(length) or b"{}")
                if not isinstance(data, dict):
                    raise ValueError("รูปแบบคำขอไม่ถูกต้อง")
                result = app.action(route[5:], data)
                self.json(200, result)
            except (ValueError, TypeError) as exc:
                self.json(400, {"error": str(exc)})
            except OSError:
                self.json(500, {"error": "บันทึกไฟล์ไม่ได้ กรุณาตรวจสอบพื้นที่และสิทธิ์ในโฟลเดอร์ข้อมูล"})
            except Exception:
                self.json(500, {"error": "ทำรายการไม่สำเร็จ กรุณาลองใหม่"})

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    app.server = server
    return server
