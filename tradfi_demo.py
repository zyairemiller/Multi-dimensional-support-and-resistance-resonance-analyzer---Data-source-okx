"""
TradFi Instrument Selector - Demo
Usage: python tradfi_demo.py
Visit: http://127.0.0.1:8090
"""

import sys
import os
import json
import threading
import time
import http.server
from pathlib import Path
from datetime import datetime
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, str(Path(__file__).parent))
from okx_data import _make_request
from db_manager import DBManager

PORT = 8090

# ============ Load external selector page ============
_SELECTOR_PATH = Path(__file__).parent / "_tradfi_selector.html"
SELECTOR_HTML = _SELECTOR_PATH.read_text(encoding="utf-8") if _SELECTOR_PATH.exists() else "<html><body><h1>Cannot find _tradfi_selector.html</h1></body></html>"

class DemoServable(http.server.SimpleHTTPRequestHandler):
    _progress = {"pct": 0, "msg": "", "done": False, "report_url": ""}

    def do_GET(self):
        try:
            parsed = urlparse(self.path)
            if parsed.path == '/':
                self._serve_html(SELECTOR_HTML)
                return
            if parsed.path == '/api/tradfi_instruments':
                self._serve_api_tradfi_instruments()
                return
            if parsed.path == '/api/tradfi_progress':
                self._serve_json(DemoServable._progress)
                return
            return super().do_GET()
        except Exception as e:
            self.send_error(500, str(e))
            import traceback
            traceback.print_exc()

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == '/api/tradfi_start':
            length = int(self.headers.get('Content-Length', 0))
            body = json.loads(self.rfile.read(length))
            inst_ids = body.get('inst_ids', [])
            threading.Thread(
                target=_run_analysis, args=(inst_ids,), daemon=True
            ).start()
            self._serve_json({"status": "started", "count": len(inst_ids)})
            return
        self.send_error(404)

    def _serve_html(self, html):
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.end_headers()
        self.wfile.write(html.encode())

    def _serve_json(self, data):
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(data, default=str).encode())

    def _serve_api_tradfi_instruments(self):
        try:
            swap = _make_request('/api/v5/public/instruments', {'instType': 'SWAP'}, timeout=10) or []
            spot = _make_request('/api/v5/public/instruments', {'instType': 'SPOT'}, timeout=10) or []
            all_inst = swap + spot
            result = []
            seen = set()
            for d in all_inst:
                inst_id = d.get('instId', '')
                if not (inst_id.endswith('-USDT') or inst_id.endswith('-USDT-SWAP')):
                    continue
                if inst_id in seen:
                    continue
                seen.add(inst_id)
                raw_base = d.get('baseCcy', '')
                raw_quote = d.get('quoteCcy', '')
                parts = inst_id.split('-')
                base_ccy = raw_base or parts[0]
                quote_ccy = raw_quote or (parts[1] if len(parts) > 1 else 'USDT')
                result.append({
                    "instId": inst_id,
                    "baseCcy": base_ccy,
                    "quoteCcy": quote_ccy,
                    "instType": d.get('instType', ''),
                    "lotSz": d.get('lotSz', ''),
                    "minSz": d.get('minSz', ''),
                })
            self._serve_json(result)
        except Exception as e:
            self.send_error(500, str(e))

    def log_message(self, format, *args):
        pass


def _run_analysis(inst_ids):
    """Background analysis thread: construct temporary config for each inst_id, call analysis"""
    import okxtrading
    from okxtrading import analyze_instrument, build_html, INSTRUMENTS_CONFIG

    db_dir = Path(__file__).parent / "data"
    db_dir.mkdir(exist_ok=True)
    db_path = db_dir / "tradfi.db"

    # Clear old DB and replace with independent DB
    if db_path.exists():
        db_path.unlink()
    orig_db = okxtrading._db_manager
    okxtrading._db_manager = DBManager(db_path)

    # Preserve original config to avoid contamination
    orig_config = dict(INSTRUMENTS_CONFIG)
    try:
        DemoServable._progress = {
            "pct": 0, "msg": f"Starting analysis of {len(inst_ids)} instruments...",
            "done": False, "report_url": ""
        }
        results = []
        for i, inst_id in enumerate(inst_ids):
            base = inst_id.split('-')[0]
            is_spot = not inst_id.endswith('-SWAP')
            # Temporarily inject config
            INSTRUMENTS_CONFIG[base] = {
                "inst_id": inst_id,
                "name": base,
                "type": "spot" if is_spot else "swap",
            }

            DemoServable._progress["pct"] = int((i / len(inst_ids)) * 80)
            DemoServable._progress["msg"] = f"Analyzing: {inst_id} ({i+1}/{len(inst_ids)})"
            try:
                r = analyze_instrument(base, refresh=True)
                if r:
                    results.append(r)
            except Exception as e:
                print(f"  {inst_id} analysis failed: {e}")

        DemoServable._progress["pct"] = 90
        DemoServable._progress["msg"] = "Generating report..."
        if results:
            html = build_html(results)
            out = Path(__file__).parent / f"tradfi_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
            out.write_text(html, encoding='utf-8')
            DemoServable._progress["pct"] = 100
            DemoServable._progress["msg"] = f"Done! Total {len(results)} instruments"
            DemoServable._progress["done"] = True
            DemoServable._progress["report_url"] = f"/{out.name}"
        else:
            DemoServable._progress["msg"] = "All instruments failed"
            DemoServable._progress["done"] = True
    finally:
        # Restore original config and DB
        INSTRUMENTS_CONFIG.clear()
        INSTRUMENTS_CONFIG.update(orig_config)
        okxtrading._db_manager = orig_db

    def log_message(self, format, *args):
        pass


if __name__ == "__main__":
    # Get LAN IP
    import socket
    lan_ip = ""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.5)
        s.connect(('192.168.255.255', 1))
        lan_ip = s.getsockname()[0]
        s.close()
    except Exception:
        pass

    os.chdir(Path(__file__).parent)
    server = http.server.HTTPServer(('0.0.0.0', PORT), DemoServable)
    print(f"TradFi Instrument Selector Demo")
    print(f"Local access: http://127.0.0.1:{PORT}")
    if lan_ip:
        print(f"LAN access:   http://{lan_ip}:{PORT}")
    print(f"Press Ctrl+C to exit")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()
        print("Exited")
