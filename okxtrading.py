"""
OKX Trading Signal Analysis - Main Program Entry

Usage:
  python trading_signal.py                       # Analyze all instruments
  python trading_signal.py --instruments BTC ETH # Analyze specified instruments only
  python trading_signal.py --refresh             # Force full refresh from API (ignore local cache)
  python trading_signal.py --db-path ./data/my.db # Specify database path
"""

import sys
import os
import json
import logging
import argparse
import time
import pandas as pd
import http.server
import urllib.request
import threading
import webbrowser
import signal
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from concurrent.futures import ThreadPoolExecutor, as_completed

from okx_data import (
    fetch_candles,
    fetch_candles_history,
    fetch_open_interest,
    fetch_funding_rate,
    fetch_recent_trades,
    fetch_trades_batch,
    fetch_mark_price,
    fetch_liquidation_map,
    fetch_orderbook,
    detect_order_walls,
    _make_request,
)
from ema_analyzer import analyze_trend
from sr_analyzer import (
    find_structural_sr,
    find_psychological_levels,
    find_fvg,
    score_sr,
    get_sr_zones
)
from big_order_detector import (
    analyze_big_orders,
    analyze_oi_change,
    check_funding_rate,
    big_order_confirmation
)
from signal_generator import generate_signal
from chart_builder import build_html
from vp_analyzer import calc_volume_profile, check_sr_vp_resonance
from db_manager import DBManager
from liquidation_heatmap import compute_liquidation_heatmap

# Log configuration: console + rolling file (24h runtime diagnostics)
from logging.handlers import RotatingFileHandler

_log_dir = Path(__file__).parent / "logs"
_log_dir.mkdir(exist_ok=True)
_log_file = _log_dir / "trading_signal.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(),
        RotatingFileHandler(
            _log_file, maxBytes=10 * 1024 * 1024, backupCount=3, encoding="utf-8"
        ),
    ],
)
logger = logging.getLogger("trading_signal")

# ============ Built-in HTTP Server ============
class TradingHandler(http.server.SimpleHTTPRequestHandler):
    """Custom HTTP request handler providing static file serving and API proxy"""

    # --- TradFi state (class-level, shared across requests) ---
    _tradfi_progress = {"pct": 0, "msg": "", "done": False, "report_url": ""}
    _tradfi_lock = threading.Lock()
    _tradfi_session_id = 0
    _tradfi_cancel = threading.Event()

    def do_GET(self):
        parsed = urlparse(self.path)

        # Static file routing
        if parsed.path.startswith('/static/'):
            filename = parsed.path[len('/static/'):]
            # Security check: prevent directory traversal
            if '..' in filename or '/' in filename or '\\' in filename:
                self.send_response(403)
                self.end_headers()
                return
            static_dir = os.path.join(os.path.dirname(__file__), 'static')
            filepath = os.path.join(static_dir, filename)
            if not os.path.isfile(filepath):
                self.send_response(404)
                self.end_headers()
                return
            ext = os.path.splitext(filename)[1].lower()
            mime_map = {'.js': 'application/javascript', '.css': 'text/css',
                        '.html': 'text/html', '.json': 'application/json',
                        '.png': 'image/png', '.svg': 'image/svg+xml'}
            self.send_response(200)
            self.send_header('Content-Type', mime_map.get(ext, 'application/octet-stream'))
            self.send_header('Cache-Control', 'public, max-age=86400')
            self.end_headers()
            with open(filepath, 'rb') as f:
                self.wfile.write(f.read())
            return

        # API proxy: /api/tickers?instType=SWAP or /api/tickers?instType=SPOT
        if parsed.path == '/api/tickers':
            params = parse_qs(parsed.query)
            inst_type = params.get('instType', ['SWAP'])[0]

            try:
                # Use okx_data._make_request (raw socket + SSL, supports multi-link fallback)
                # Instead of urllib.request (SSL handshake fails after PySocks monkey-patch)
                data = _make_request("/api/v5/market/tickers", {"instType": inst_type}, timeout=10)
                if data is not None:
                    result = json.dumps({"code": "0", "data": data})
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Access-Control-Allow-Origin', '*')
                    self.end_headers()
                    self.wfile.write(result.encode())
                else:
                    self.send_response(502)
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({'code': '-1', 'msg': 'OKX API all links failed'}).encode())
            except Exception as e:
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'code': '-1', 'msg': str(e)}).encode())
            return

        # API: /api/progress returns current loading progress
        if parsed.path == '/api/progress':
            with _fetch_progress_lock:
                data = dict(_fetch_progress)
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps(data, default=str).encode())
            return

        # API proxy: /api/live_data?instId=BTC-USDT-SWAP returns real-time orderbook + liquidation data
        if parsed.path == '/api/live_data':
            params = parse_qs(parsed.query)
            inst_id = params.get('instId', [''])[0]
            with _live_data_lock:
                data = _live_data.get(inst_id, {})
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps(data, default=str).encode())
            return

        # Static file serving: if accessing /, auto-find the latest HTML file
        if parsed.path == '/' or parsed.path == '':
            html_files = [
                f for f in os.listdir('.')
                if f.startswith('trading_signal_') and f.endswith('.html')
            ]
            if html_files:
                html_files.sort(reverse=True)
                self.path = '/' + html_files[0]
            else:
                self.send_response(404)
                self.send_header('Content-Type', 'text/plain; charset=utf-8')
                self.end_headers()
                self.wfile.write('No trading signal HTML found'.encode())
                return

        # API: /api/tradfi_progress
        if parsed.path == '/api/tradfi_progress':
            with TradingHandler._tradfi_lock:
                data = dict(TradingHandler._tradfi_progress)
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps(data, default=str).encode())
            return

        # API: /api/tradfi_instruments
        if parsed.path == '/api/tradfi_instruments':
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
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps(result).encode())
            except Exception as e:
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode())
            return

        # TradFi selector page
        if parsed.path == '/tradfi':
            sel_path = Path(__file__).parent / "_tradfi_selector.html"
            if sel_path.exists():
                html = sel_path.read_text(encoding='utf-8')
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.end_headers()
                self.wfile.write(html.encode())
            else:
                self.send_response(404)
                self.send_header('Content-Type', 'text/plain; charset=utf-8')
                self.end_headers()
                self.wfile.write('_tradfi_selector.html not found'.encode())
            return

        # TradFi report file routing
        if parsed.path.startswith('/tradfi_report_') and parsed.path.endswith('.html'):
            report_path = Path(__file__).parent / parsed.path.lstrip('/')
            if report_path.exists():
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.end_headers()
                self.wfile.write(report_path.read_bytes())
            else:
                self.send_response(404)
                self.send_header('Content-Type', 'text/plain; charset=utf-8')
                self.end_headers()
                self.wfile.write('TradFi report not found'.encode())
            return

        # Other files handled normally (static file serving by SimpleHTTPRequestHandler)
        return super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == '/api/tradfi_start':
            try:
                length = int(self.headers.get('Content-Length', 0))
                body = json.loads(self.rfile.read(length))
                inst_ids = body.get('inst_ids', [])
                # Cancel old analysis thread and create new session
                TradingHandler._tradfi_cancel.set()
                TradingHandler._tradfi_session_id += 1
                TradingHandler._tradfi_cancel = threading.Event()
                session_id = TradingHandler._tradfi_session_id
                cancel_event = TradingHandler._tradfi_cancel
                threading.Thread(
                    target=_run_tradfi_analysis, args=(inst_ids, session_id, cancel_event),
                    daemon=True
                ).start()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"status": "started", "count": len(inst_ids), "session_id": session_id}).encode())
            except Exception as e:
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode())
            return
        self.send_error(404)

    def log_message(self, format, *args):
        """Silent logging, reduce console output"""
        pass


def _run_tradfi_analysis(inst_ids, session_id, cancel_event):
    """Background thread: construct temporary config for each inst_id, call analyze_instrument and build_html

    Args:
        inst_ids: Instrument instId list
        session_id: Current session ID, validated when writing shared state to prevent old thread contamination
        cancel_event: Cancellation signal, set when new request arrives, old thread exits early when detected
    """
    db_dir = Path(__file__).parent / "data"
    db_dir.mkdir(exist_ok=True)
    tradfi_db = db_dir / "tradfi.db"

    if tradfi_db.exists():
        tradfi_db.unlink()
    # Use independent DB
    tradfi_db_mgr = DBManager(tradfi_db)

    # Temporarily store DB reference to avoid contaminating main program
    orig_db = _db_manager
    try:
        # Temporarily replace global DB manager
        import okxtrading
        okxtrading._db_manager = tradfi_db_mgr

        with TradingHandler._tradfi_lock:
            if session_id != TradingHandler._tradfi_session_id:
                return  # Old session, exit directly
            TradingHandler._tradfi_progress = {
                "pct": 0, "msg": f"Starting analysis of {len(inst_ids)} instruments...",
                "done": False, "report_url": ""
            }

        results = []
        for i, inst_id in enumerate(inst_ids):
            # Check cancellation signal
            if cancel_event.is_set():
                logger.info(f"TradFi analysis cancelled (session {session_id}), analyzed {i}/{len(inst_ids)} instruments")
                return

            base = inst_id.split('-')[0]

            with TradingHandler._tradfi_lock:
                if session_id != TradingHandler._tradfi_session_id:
                    return
                TradingHandler._tradfi_progress["pct"] = int((i / len(inst_ids)) * 80)
                TradingHandler._tradfi_progress["msg"] = f"Analyzing: {inst_id} ({i+1}/{len(inst_ids)})"

            # Start progress monitoring thread (poll task completion status in _fetch_progress)
            mon_done = threading.Event()

            def _monitor_fetch_progress(inst_idx, total_insts, sid, mon_event):
                base_pct = int((inst_idx / total_insts) * 80)
                slice_pct = 80 / total_insts  # Each instrument's share in 80% progress bar
                while not mon_event.is_set():
                    with _fetch_progress_lock:
                        tasks = dict(_fetch_progress.get("tasks", {}))
                    done = sum(
                        1 for t in tasks.values()
                        if t.get("status") in ("done", "error")
                    )
                    total = len(tasks)
                    if total > 0:
                        sub_pct = int((done / total) * slice_pct)
                        with TradingHandler._tradfi_lock:
                            if sid == TradingHandler._tradfi_session_id:
                                TradingHandler._tradfi_progress["pct"] = base_pct + sub_pct
                    time.sleep(0.8)

            mon = threading.Thread(
                target=_monitor_fetch_progress,
                args=(i, len(inst_ids), session_id, mon_done),
                daemon=True
            )
            mon.start()

            try:
                if cancel_event.is_set():
                    mon_done.set()
                    return
                r = analyze_instrument(base, refresh=True)
                if r:
                    results.append(r)
            except Exception as e:
                logger.error(f"TradFi {inst_id} analysis failed: {e}")
            finally:
                mon_done.set()
                mon.join(timeout=1)

        # After loop, check if still current session
        with TradingHandler._tradfi_lock:
            if session_id != TradingHandler._tradfi_session_id:
                return
            TradingHandler._tradfi_progress["pct"] = 90
            TradingHandler._tradfi_progress["msg"] = "Generating report..."

        if results:
            html = build_html(results)
            out = Path(__file__).parent / f"tradfi_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
            out.write_text(html, encoding='utf-8')

            with TradingHandler._tradfi_lock:
                if session_id != TradingHandler._tradfi_session_id:
                    return
                TradingHandler._tradfi_progress["pct"] = 100
                TradingHandler._tradfi_progress["msg"] = f"Done! Total {len(results)} instruments"
                TradingHandler._tradfi_progress["done"] = True
                TradingHandler._tradfi_progress["report_url"] = f"/{out.name}"
            logger.info(f"TradFi report generated: {out}")
        else:
            with TradingHandler._tradfi_lock:
                if session_id == TradingHandler._tradfi_session_id:
                    TradingHandler._tradfi_progress["msg"] = "All instruments failed"
                    TradingHandler._tradfi_progress["done"] = True
    finally:
        # Only restore global state for current session (old threads should not touch)
        with TradingHandler._tradfi_lock:
            if session_id == TradingHandler._tradfi_session_id:
                TradingHandler._tradfi_progress["done"] = True
        # session_id is the only valid check: only restore DB manager for current active session
        # Removed cancel_event condition: old thread's cancel_event is already set, keeping it would cause old thread to skip restore
        if session_id == TradingHandler._tradfi_session_id:
            import okxtrading
            okxtrading._db_manager = orig_db


def _get_lan_ip():
    """Get local LAN IPv4 address"""
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.5)
        s.connect(('192.168.255.255', 1))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return None


def _ensure_firewall_rule(port):
    """Try to add Windows firewall inbound rule (requires admin privileges)"""
    import subprocess
    rule_name = f"OKX Trading Dashboard (TCP {port})"
    try:
        # First check if rule already exists
        check = subprocess.run(
            f'netsh advfirewall firewall show rule name="{rule_name}"',
            shell=True, capture_output=True, text=True
        )
        if 'No rules match' in check.stdout:
            result = subprocess.run(
                f'netsh advfirewall firewall add rule name="{rule_name}" '
                f'dir=in action=allow protocol=TCP localport={port}',
                shell=True, capture_output=True, text=True
            )
            if result.returncode == 0:
                print("Firewall rule added for this port")
            else:
                print(f"Failed to add firewall rule (may need admin privileges). Please run manually:")
                print(f'  netsh advfirewall firewall add rule name="{rule_name}" dir=in action=allow protocol=TCP localport={port}')
        else:
            print("Firewall rule already exists")
    except Exception:
        print(f"Firewall check failed, please manually allow port {port} for LAN access")


def start_server(port=8080, output_dir=None):
    """Start built-in HTTP server (runs in background thread), auto-handle port conflicts"""
    if output_dir:
        os.chdir(output_dir)

    # Try multiple ports to avoid Address already in use from residual processes
    for attempt_port in range(port, port + 10):
        try:
            server = http.server.HTTPServer(('0.0.0.0', attempt_port), TradingHandler)
            server.allow_reuse_address = True
            port = attempt_port
            break
        except OSError:
            if attempt_port == port + 9:
                raise
            continue

    # Get LAN IP for access from other devices
    lan_ip = _get_lan_ip()
    url = f'http://127.0.0.1:{port}'
    lan_url = f'http://{lan_ip}:{port}' if lan_ip else None

    def serve():
        try:
            server.serve_forever()
        except Exception:
            pass

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    return server, url, lan_url


# Instrument configuration
INSTRUMENTS_CONFIG = {
    "BTC": {
        "inst_id": "BTC-USDT-SWAP",
        "name": "Bitcoin",
        "type": "swap",
    },
    "ETH": {
        "inst_id": "ETH-USDT-SWAP",
        "name": "Ethereum",
        "type": "swap",
    },
    "XAU": {
        "inst_id": "XAU-USDT-SWAP",
        "name": "Gold",
        "type": "swap",
    },
    "XAG": {
        "inst_id": "XAG-USDT-SWAP",
        "name": "Silver",
        "type": "swap",
    },
    "BTC_SPOT": {
        "inst_id": "BTC-USDT",
        "name": "Bitcoin Spot",
        "type": "spot",
    },
    "ETH_SPOT": {
        "inst_id": "ETH-USDT",
        "name": "Ethereum Spot",
        "type": "spot",
    },
}

# Data count needed per period
CANDLE_NEED_COUNT = {
    "1D": 365,
    "4H": 2190,
    "1H": 8760,
}

# Candle interval per period (milliseconds), for checking data freshness
BAR_INTERVAL_MS = {
    "1D": 86400000,
    "4H": 14400000,
    "1H": 3600000,
}

# Global DBManager instance (initialized in main)
_db_manager = None

# Analysis concurrency lock (prevents candle refresh-triggered analysis from running concurrently with previous round; also used by on_closing to wait for analysis completion)
_analysis_lock = threading.Lock()

# ---- Progress tracking (loading page polling display) ----
_fetch_progress = {
    "stage": "idle",        # idle / fetching / analyzing / rendering / done
    "instrument": "",       # Current instrument name
    "tasks": {},            # {task_id: {"label": "xxx", "status": "running"/"done"/"error"}}
    "total": 0,             # Total instrument count
    "current": 0,           # Current instrument index (1-based)
    "message": "",          # Human-readable message
    "failed_instruments": [],  # Failed instrument list
    "error": "",            # Error details (exception info)
}
_fetch_progress_lock = threading.Lock()

def _update_progress(**kwargs):
    """Thread-safely update progress state"""
    with _fetch_progress_lock:
        _fetch_progress.update(kwargs)

def _set_task_status(task_id, status, label=None):
    """Update individual task status"""
    with _fetch_progress_lock:
        if task_id not in _fetch_progress["tasks"]:
            _fetch_progress["tasks"][task_id] = {"label": label or task_id, "status": status}
        else:
            _fetch_progress["tasks"][task_id]["status"] = status
            if label:
                _fetch_progress["tasks"][task_id]["label"] = label


def _cleanup_old_reports(output_dir, keep_count=2):
    """Clean up old HTML report files, keep only the latest N"""
    try:
        html_files = sorted(
            [f for f in os.listdir(output_dir) if f.startswith('trading_signal_') and f.endswith('.html')],
            reverse=True
        )
        for old_file in html_files[keep_count:]:
            try:
                os.remove(os.path.join(output_dir, old_file))
                logger.info(f"Cleaning old report: {old_file}")
            except OSError:
                pass
    except Exception:
        pass


# ---- Real-time data management (orderbook + liquidation data 5-minute refresh) ----
_live_data = {}           # inst_id -> {orderbook, liquidation, updated_at}
_live_data_lock = threading.Lock()
_live_data_running = False

def _start_live_refresh(instruments_config, interval=300):
    """Start periodic orderbook + liquidation data refresh (default 5 minutes)"""
    global _live_data_running
    _live_data_running = True

    def _refresh_loop():
        while _live_data_running:
            for cfg in instruments_config:
                inst_id = cfg["inst_id"]
                if cfg.get("type") == "spot":
                    continue
                try:
                    orderbook = fetch_orderbook(inst_id, depth=200)
                    liquidation = fetch_liquidation_map(inst_id)
                    with _live_data_lock:
                        _live_data[inst_id] = {
                            "orderbook": orderbook,
                            "liquidation": liquidation,
                            "updated_at": datetime.now(timezone.utc).isoformat()
                        }
                except Exception as e:
                    logger.error(f"Real-time data refresh failed {inst_id}: {e}")
            time.sleep(interval)

    thread = threading.Thread(target=_refresh_loop, daemon=True)
    thread.start()
    logger.info(f"Real-time data refresh started (interval {interval}s)")

def _stop_live_refresh():
    """Stop real-time data refresh"""
    global _live_data_running
    _live_data_running = False


# ---- Candle data periodic incremental refresh (1H:30min / 4H:2h / 1D:12h) ----
_candle_refresh_event = threading.Event()
_candle_refresh_thread = None

def _start_candle_refresh(instruments_config, on_data_updated=None):
    """
    Start periodic incremental candle data refresh (calculated by UTC+8 local time).
    Only does incremental pulls (after local latest ts), no historical candle pulling.
    
    on_data_updated: Optional callback, called when any instrument has new candles
    """
    global _candle_refresh_event, _candle_refresh_thread
    _candle_refresh_event.clear()

    # Alignment rules: align to specified moments by absolute time
    # 1H -> every 30 min (xx:00, xx:30); 4H -> every 2 hours (00/02/04...); 1D -> every 12 hours (00:00, 12:00)
    _last_aligned = {}  # f"{inst_id}_{bar}" -> last aligned moment (datetime)

    def _get_aligned(dt, bar):
        """Calculate the pull moment dt should align to"""
        if bar == "1H":
            m = (dt.minute // 30) * 30
            return dt.replace(minute=m, second=0, microsecond=0)
        elif bar == "4H":
            h = (dt.hour // 2) * 2
            return dt.replace(hour=h, minute=0, second=0, microsecond=0)
        elif bar == "1D":
            h = (dt.hour // 12) * 12
            return dt.replace(hour=h, minute=0, second=0, microsecond=0)
        return dt

    def _refresh_loop():
        global _analysis_lock
        _last_db_maintenance = 0  # Database maintenance timer
        
        while not _candle_refresh_event.is_set():
            try:
                # Try to acquire analysis lock, prevent concurrency with running analysis
                if not _analysis_lock.acquire(blocking=False):
                    _candle_refresh_event.wait(60)
                    continue

                try:
                    now = datetime.now(timezone.utc)  # UTC time
                    any_updated = False
                    pending_updates = {}  # {key: aligned} pending candle refresh

                    for cfg in instruments_config:
                        inst_id = cfg["inst_id"]
                        for bar in ("1H", "4H", "1D"):
                            key = f"{inst_id}_{bar}"
                            aligned = _get_aligned(now, bar)
                            if _last_aligned.get(key) != aligned:
                                try:
                                    need_count = CANDLE_NEED_COUNT.get(bar, 500)
                                    df = load_candles(inst_id, bar, need_count, 
                                                      force_incremental=True)
                                    if not df.empty:
                                        any_updated = True
                                        pending_updates[key] = aligned
                                        logger.info(f"Candle incremental refresh: {inst_id} {bar} -> {len(df)} bars")
                                    else:
                                        # No local data for this instrument, advance alignment point directly
                                        _last_aligned[key] = aligned
                                except Exception as e:
                                    logger.error(f"Candle incremental refresh failed {inst_id} {bar}: {e}")

                    if any_updated and on_data_updated:
                        try:
                            on_data_updated()
                            # Re-analysis successful -> confirm all pending alignment points
                            for key, aligned in pending_updates.items():
                                _last_aligned[key] = aligned
                        except Exception as e:
                            logger.error(f"on_data_updated callback failed: {e}")
                            # Failed -> pending_updates not confirmed, retry next loop

                    # Execute database WAL checkpoint every hour (prevent WAL file from growing indefinitely)
                    if now.timestamp() - _last_db_maintenance > 3600:
                        try:
                            db = _get_db_manager()
                            db.checkpoint()
                            _last_db_maintenance = now.timestamp()
                        except Exception as e:
                            logger.error(f"Database maintenance failed: {e}")
                finally:
                    _analysis_lock.release()

            except Exception as e:
                logger.error(f"Candle refresh loop error: {e}")

            _candle_refresh_event.wait(60)  # Check every minute, can be interrupted by set()

    _candle_refresh_thread = threading.Thread(target=_refresh_loop, daemon=True)
    _candle_refresh_thread.start()
    logger.info(f"Candle incremental refresh started (1H align xx:00/xx:30 | 4H align 00/02/04... | 1D align 00:00/12:00, UTC time)")


def _stop_candle_refresh():
    """Stop candle incremental refresh, wait for thread to exit"""
    global _candle_refresh_event, _candle_refresh_thread
    _candle_refresh_event.set()
    if _candle_refresh_thread and _candle_refresh_thread.is_alive():
        _candle_refresh_thread.join(timeout=5)


def _get_db_manager(db_path: Path = None) -> DBManager:
    """Get global DBManager instance"""
    global _db_manager
    if _db_manager is None:
        _db_manager = DBManager(db_path)
    return _db_manager


def _is_spot(instrument: str) -> bool:
    """Check if instrument is spot type"""
    config = INSTRUMENTS_CONFIG.get(instrument, {})
    return config.get("type") == "spot"


def load_candles(inst_id: str, bar: str, need_count: int = 1000, refresh: bool = False,
                 force_incremental: bool = False) -> pd.DataFrame:
    """
    Smart candle data loading: local cache + incremental API fill

    Flow:
    1. Check if local database has data
    2. If no data:
       - Pull need_count historical data from API (fetch_candles_history)
       - Write all to local database
       - Return data
    3. If has data:
       - Check local latest data timestamp vs current time
       - If local data is fresh enough (latest 1H within 1 hour), use local data directly
       - If local data is stale, incremental fill from API:
         a. Use fetch_candles to pull latest (after=local latest ts), fill gap
         b. Write incremental data to local database
       - If local data count < need_count:
         a. First fill latest data (step 3b)
         b. If need earlier history, use fetch_candles_history to backfill
         c. Write all to database
    4. Return data

    Args:
        inst_id: Instrument ID
        bar: Candle period
        need_count: Total needed
        refresh: Whether to force full refresh

    Returns:
        DataFrame, columns: ts, open, high, low, close, vol, volCcy
    """
    global _db_manager
    db = _get_db_manager()
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    bar_interval_ms = BAR_INTERVAL_MS.get(bar, 3600000)

    def _strip_incomplete(_df):
        """Filter incomplete candles: prefer OKX API's confirm field, fallback to time-based calculation"""
        if _df.empty:
            return _df
        if "confirm" in _df.columns:
            return _df[_df["confirm"] == "1"]
        return _df[
            _df["ts"].apply(lambda t: (t.value // 1_000_000) + bar_interval_ms <= now_ms)
        ]

    try:
        if refresh:
            # Force full refresh
            print(f"  {inst_id} {bar}: Force full refresh, fetching {need_count} bars from API...")
            df = fetch_candles_history(inst_id, bar, need_count)
            if not df.empty:
                db.save_candles(inst_id, bar, df)
            return _strip_incomplete(df)

        # Check local data
        local_count = db.get_candle_count(inst_id, bar)

        if local_count == 0:
            if force_incremental:
                # First incremental refresh for new instrument: do a full pull, then go incremental
                print(f"  {inst_id} {bar}: First incremental, full pull {need_count} bars...")
                df = fetch_candles_history(inst_id, bar, need_count)
                if not df.empty:
                    db.save_candles(inst_id, bar, df)
                return _strip_incomplete(df)
            # No local data, full pull from API
            print(f"  {inst_id} {bar}: New database, fetching {need_count} bars from API...")
            df = fetch_candles_history(inst_id, bar, need_count)
            if not df.empty:
                db.save_candles(inst_id, bar, df)
            return _strip_incomplete(df)

        # Local has data, check if incremental fill needed
        local_latest_ts = db.get_latest_ts(inst_id, bar)

        # Check if local data is fresh enough (latest bar within 1 period -> no incremental fill needed)
        # force_incremental: for scheduled refresh, skip freshness check and force incremental pull, no history backfill
        if force_incremental:
            is_data_fresh = False
        else:
            is_data_fresh = local_latest_ts is not None and (now_ms - local_latest_ts) < bar_interval_ms

        # Read local data
        df_local = db.get_candles(inst_id, bar)
        incremental_count = 0

        if not is_data_fresh and local_latest_ts is not None:
            # Local data is stale, incremental fill latest data
            # Use after parameter to page backward from now, ensure no data loss when gap > 300 bars
            # OKX API: after=X returns data with ts < X (page backward in history)
            local_latest_dt = pd.to_datetime(local_latest_ts, unit="ms")
            pivot_ms = now_ms + bar_interval_ms  # Start slightly beyond current time to cover just-completed candles
            all_new_batches = []
            max_pages = 10  # Safety limit: 10 pages x 300 = 3000 bars, prevent accidental infinite loop

            for _ in range(max_pages):
                batch = fetch_candles(inst_id, bar, 300, after=str(pivot_ms))
                if batch.empty:
                    break

                # Only keep candles later than local latest and completed
                # Incomplete candles (ts + bar_interval_ms > now_ms) have changing OHLC, skip to avoid chart gaps
                bar_interval_ms = BAR_INTERVAL_MS.get(bar, 3600000)
                completed_mask = batch["ts"].apply(
                    lambda ts: (ts.value // 1_000_000) + bar_interval_ms <= now_ms
                )
                new_in_batch = batch[(batch["ts"] >= local_latest_dt) & completed_mask].copy()
                if not new_in_batch.empty:
                    all_new_batches.append(new_in_batch)

                # If this batch reached local latest candle (includes ts <= local_latest), gap is filled
                if (batch["ts"] <= local_latest_dt).any():
                    break

                # If less than 300, reached end of data
                if len(batch) < 300:
                    break

                # Page: use this batch's earliest timestamp -1ms to continue pulling backward
                pivot_ms = int(batch["ts"].min().value // 1_000_000) - 1

            if all_new_batches:
                df_new = pd.concat(all_new_batches, ignore_index=True)
                df_new = df_new.sort_values("ts").drop_duplicates(subset=["ts"]).reset_index(drop=True)

                if not df_new.empty:
                    db.save_candles(inst_id, bar, df_new)
                    incremental_count += len(df_new)
                    # Re-read local data
                    df_local = db.get_candles(inst_id, bar)
            else:
                # all_new_batches is empty means no new data (may have just completed a round)
                pass

        # Check if data count is sufficient (no history backfill in force_incremental mode to avoid long pulls)
        if not force_incremental and len(df_local) < need_count:
            # Local data insufficient, need to backfill historical data
            earliest_ts = db.get_earliest_ts(inst_id, bar)
            need_more = need_count - len(df_local)

            if earliest_ts is not None:
                print(f"  {inst_id} {bar}: Local {len(df_local)} bars, need {need_more} more to backfill...")
                # before_ts=earliest_ts-1 to skip boundary candle (already local), avoid wasting a request
                df_older = fetch_candles_history(inst_id, bar, need_more, before_ts=earliest_ts - 1)
                if not df_older.empty:
                    db.save_candles(inst_id, bar, df_older)
                    incremental_count += len(df_older)

            # Re-read
            df_local = db.get_candles(inst_id, bar)

        # Output sync result
        if incremental_count > 0:
            print(f"  {inst_id} {bar}: Local {local_count} bars, filled {incremental_count} bars -> total {len(df_local)} bars")
        else:
            print(f"  {inst_id} {bar}: Local cache hit {len(df_local)} bars")

        # Return latest need_count bars, ensure sorted and deduped
        if len(df_local) > need_count:
            df_local = df_local.iloc[-need_count:].reset_index(drop=True)

        # Defensive sort and dedup (prevent any edge cases causing disorder or duplicates)
        df_local = df_local.sort_values("ts").drop_duplicates(subset=["ts"]).reset_index(drop=True)

        if len(df_local) < need_count:
            logger.warning(
                f"{inst_id} {bar}: Target {need_count} bars, actual only {len(df_local)} bars "
                f"(OKX regular endpoint 1440 limit + history endpoint 3-month lookback limit)"
            )

        return _strip_incomplete(df_local)

    except Exception as e:
        logger.warning(f"Local cache load failed, falling back to direct API pull: {e}")
        # Fallback to direct API pull when database operations fail
        df = fetch_candles_history(inst_id, bar, need_count)
        return _strip_incomplete(df)


def analyze_instrument(instrument: str, refresh: bool = False, inst_id: str = None) -> dict:
    """
    Analyze a single instrument

    Args:
        instrument: Instrument name, e.g. BTC, ETH, XAU, XAG, BTC_SPOT, ETH_SPOT
        refresh: Whether to force full refresh
        inst_id: Optional, directly specify instId (used by TradFi thread, bypasses INSTRUMENTS_CONFIG lookup)

    Returns:
        Complete analysis result
    """
    if inst_id:
        # TradFi thread directly specifies instId, doesn't depend on global INSTRUMENTS_CONFIG
        name = instrument
        is_spot = not inst_id.endswith('-SWAP')
    else:
        config = INSTRUMENTS_CONFIG.get(instrument)
        if not config:
            logger.error(f"Unknown instrument: {instrument}")
            return {}
        inst_id = config["inst_id"]
        name = config["name"]
        is_spot = config.get("type") == "spot"

    spot_label = " (Spot)" if is_spot else ""
    print(f"\n{'='*60}")
    print(f"Starting analysis {name} ({inst_id}){spot_label}")
    print(f"{'='*60}")

    # ============ 1. Parallel data fetching ============
    print(f"\nFetching market data in parallel...")

    # Build task pool: all independent API calls launched in parallel at once
    tasks = {
        "df_1d": lambda: load_candles(inst_id, "1D", CANDLE_NEED_COUNT["1D"], refresh=refresh),
        "df_4h": lambda: load_candles(inst_id, "4H", CANDLE_NEED_COUNT["4H"], refresh=refresh),
        "df_1h": lambda: load_candles(inst_id, "1H", CANDLE_NEED_COUNT["1H"], refresh=refresh),
        "orderbook": lambda: fetch_orderbook(inst_id, depth=200),
    }

    if not is_spot:
        tasks["oi_current"] = lambda: fetch_open_interest(inst_id)
        tasks["funding_data"] = lambda: fetch_funding_rate(inst_id)
        tasks["trades"] = lambda: fetch_trades_batch(inst_id, total=500)
        tasks["mark_price"] = lambda: fetch_mark_price(inst_id)
        tasks["liquidation_data"] = lambda: fetch_liquidation_map(inst_id)

    results = {}
    failed = []

    # Register all tasks for progress tracking
    _task_labels = {"df_1d": "Daily Candles", "df_4h": "4H Candles", "df_1h": "1H Candles",
                    "orderbook": "Orderbook", "oi_current": "Open Interest", "funding_data": "Funding Rate",
                    "trades": "Tick Trades", "mark_price": "Mark Price", "liquidation_data": "Liquidation Data"}
    for tid in tasks:
        _set_task_status(tid, "running", _task_labels.get(tid, tid))

    with ThreadPoolExecutor(max_workers=10) as executor:
        future_map = {executor.submit(fn): key for key, fn in tasks.items()}
        for future in as_completed(future_map):
            key = future_map[future]
            try:
                results[key] = future.result()
                _set_task_status(key, "done")
            except Exception as e:
                logger.error(f"Parallel load {key} failed: {e}")
                failed.append(key)
                results[key] = None
                _set_task_status(key, "error")

    _update_progress(stage="analyzing", message=f"{name} data loading complete, starting analysis...")

    # Extract results
    df_1d = results.get("df_1d", pd.DataFrame())
    df_4h = results.get("df_4h", pd.DataFrame())
    df_1h = results.get("df_1h", pd.DataFrame())

    print(f"  1D Candles: {len(df_1d)} bars")
    print(f"  4H Candles: {len(df_4h)} bars")
    print(f"  1H Candles: {len(df_1h)} bars")

    if is_spot:
        oi_current = None
        funding_data = None
        trades = []
        mark_price = None
        liquidation_data = []
        print(f"  Open Interest/Funding Rate/Tick Trades/Mark Price/Liquidation: Not supported for spot, skipped")
    else:
        oi_current = results.get("oi_current")
        funding_data = results.get("funding_data")
        trades = results.get("trades") or []
        mark_price = results.get("mark_price")
        liquidation_data = results.get("liquidation_data") or []

        print(f"  Open Interest: {oi_current.get('oi', 'N/A') if oi_current else 'N/A'}")
        print(f"  Funding Rate: {funding_data.get('fundingRate', 'N/A') if funding_data else 'N/A'}")
        print(f"  Tick Trades: {len(trades)} trades (batch fetch)")
        print(f"  Mark Price: {mark_price}")
        print(f"  Liquidation Zones: {len(liquidation_data)} price levels")
        for lq in liquidation_data:
            side_str = "Long" if lq["side"] == "long" else "Short"
            print(f"      {lq['price']} ({side_str} liquidation, vol={lq['volume']})")

    # Orderbook + Order walls
    orderbook = results.get("orderbook") or {}
    print(f"  Orderbook: {len(orderbook.get('bids', []))} bid levels / {len(orderbook.get('asks', []))} ask levels")

    order_walls = detect_order_walls(orderbook, inst_id)
    print(f"  Order Walls: bid walls={len(order_walls.get('bid_walls', 0))}, ask walls={len(order_walls.get('ask_walls', 0))}, bid/ask ratio={order_walls.get('imbalance', 1.0)}")
    for wall in order_walls.get("bid_walls", [])[:3]:
        print(f"      Bid wall {wall['price']} [strength={wall['strength']}x, size={wall['size']}]")
    for wall in order_walls.get("ask_walls", [])[:3]:
        print(f"      Ask wall {wall['price']} [strength={wall['strength']}x, size={wall['size']}]")

    # ============ 2. EMA Trend Analysis ============
    _update_progress(message=f"{name} EMA trend analysis...")
    print(f"\nEMA Trend Analysis...")
    trend = analyze_trend(df_1d)
    trend_text = {
        "GOLDEN_CROSS": "Bullish Trend",
        "DEATH_CROSS": "Bearish Trend",
        "ENTANGLED": "Entangled - Wait"
    }
    print(f"  Trend Status: {trend_text.get(trend['trend'], trend['trend'])}")
    print(f"  EMA144: {trend['ema144']:.2f}")
    print(f"  EMA169: {trend['ema169']:.2f}")
    print(f"  Separation: {trend['separation_pct']}%")
    print(f"  Strength: {trend['trend_strength']}")

    # ============ 3. Support/Resistance Identification ============
    _update_progress(message=f"{name} support/resistance identification...")
    print(f"\nSupport/Resistance Identification...")

    if df_1h.empty:
        raise RuntimeError(f"{inst_id} 1H candle data is empty, cannot continue analysis (all APIs may have failed)")
    current_price = float(df_1h["close"].iloc[-1])
    structural_sr = find_structural_sr(df_1d, lookback=60)
    print(f"  Structural S/R: {len(structural_sr)} levels")

    psych_levels = find_psychological_levels(current_price, instrument)
    print(f"  Psychological Levels: {len(psych_levels)} - {psych_levels}")

    fvgs = find_fvg(df_1d)
    print(f"  FVG Gaps: {len(fvgs)}")

    # Scoring (pass liquidation zone data + order wall data)
    sr_scored = []
    for sr in structural_sr:
        scored = score_sr(sr, df_1d, psych_levels, fvgs, liquidation_data, order_walls)
        sr_scored.append(scored)

    # Add psychological levels as S/R
    for pl in psych_levels:
        pl_type = "resistance" if pl > current_price else "support"
        psych_sr = score_sr(
            {"level": pl, "type": pl_type, "touch_count": 1},
            df_1d, psych_levels, fvgs, liquidation_data, order_walls
        )
        psych_sr["is_psychological"] = True
        sr_scored.append(psych_sr)

    # Add FVG as S/R
    for fvg in fvgs:
        fvg_sr = score_sr(
            {"level": fvg["level"], "type": fvg["type"], "touch_count": 1},
            df_1d, psych_levels, fvgs, liquidation_data, order_walls
        )
        fvg_sr["is_fvg"] = True
        sr_scored.append(fvg_sr)

    # Sort and output
    sr_scored.sort(key=lambda x: x["score"], reverse=True)
    print(f"\n  S/R Score Ranking:")
    for sr in sr_scored[:10]:
        type_str = "Support" if sr["type"] == "support" else "Resistance"
        strength_str = {"super": "Super", "strong": "Strong", "weak": "Weak"}
        badges = []
        if sr["is_psychological"]:
            badges.append("PsychLevel")
        if sr["is_fvg"]:
            badges.append("FVG")
        if sr.get("is_liquidation"):
            badges.append("LiquidationZone")
        if sr.get("is_order_wall"):
            badges.append("OrderWall")
        badge_str = f" [{','.join(badges)}]" if badges else ""
        print(f"    {sr['level']:.2f} | {type_str} | {sr['score']}pts - {strength_str.get(sr['strength'], sr['strength'])}{badge_str}")

    # Generate S/R zones
    significant_sr = [s for s in sr_scored if s["score"] >= 1]
    sr_zones = get_sr_zones(significant_sr, instrument)

    # ============ 4. Volume Profile Analysis ============
    print(f"\nVolume Profile Analysis...")
    vp_result = calc_volume_profile(df_1h, num_bins=100)
    print(f"  POC: {vp_result['poc']}")
    print(f"  Value Area: {vp_result['va_low']} ~ {vp_result['va_high']}")
    print(f"  High Volume Nodes: {len(vp_result['high_volume_nodes'])}")

    # Check S/R and Volume Profile resonance
    sr_zones = check_sr_vp_resonance(sr_zones, vp_result, instrument)
    resonance_count = sum(1 for z in sr_zones if z.get("resonance"))
    print(f"  S/R Resonance: {resonance_count} zones with VP resonance")
    for z in sr_zones:
        if z.get("resonance"):
            res_str = {"strong": "Strong Resonance", "normal": "Resonance", "weak": "Weak Resonance"}.get(z["resonance"], z["resonance"])
            print(f"      {z['level']:.2f} ({z['type']}) -> {res_str}: {z.get('resonance_reason', '')}")

    # ============ 5. Big Order Detection ============
    print(f"\nBig Order Detection...")

    big_order = analyze_big_orders(trades, inst_id)
    print(f"  Active Buy Volume: {big_order['buy_volume']}")
    print(f"  Active Sell Volume: {big_order['sell_volume']}")
    print(f"  Big Buy: {big_order['big_buy_volume']} ({big_order['big_buy_count']} trades)")
    print(f"  Big Sell: {big_order['big_sell_volume']} ({big_order['big_sell_count']} trades)")
    print(f"  Big Order Ratio: {big_order['big_ratio']}")
    print(f"  Big Order Signal: {big_order['signal']}")

    # OI change (spot instruments use simulated values)
    if is_spot:
        oi_change = {"direction": "UNKNOWN", "change_pct": 0.0, "signal": "NEUTRAL"}
        print(f"  OI Change: Not supported for spot, skipped")
    else:
        # Simulate previous OI, estimate using 98% of current value
        oi_previous = {"oi": oi_current.get("oi", 0) * 0.98} if oi_current else {"oi": 0}
        oi_change = analyze_oi_change(oi_current, oi_previous)
        print(f"  OI Change: {oi_change['change_pct']}% ({oi_change['direction']})")

    funding_check = check_funding_rate(funding_data)
    if is_spot:
        print(f"  Funding Rate: Not supported for spot, skipped")
    else:
        print(f"  Funding Rate: {funding_check['rate_pct']}% - {funding_check['status']}")
        if funding_check.get("warning"):
            print(f"  ⚠️ {funding_check['warning']}")

    # ============ 6. Liquidation Heatmap ============
    print(f"\nLiquidation Heatmap Calculation...")
    if is_spot:
        heatmap_data = []
        print(f"  Liquidation Heatmap: Not supported for spot, skipped")
    else:
        heatmap_data = compute_liquidation_heatmap(
            instrument=instrument,
            df_1h=df_1h,
            oi_current=oi_current,
            funding_data=funding_data,
        )
        print(f"  Liquidation Heatmap: {len(heatmap_data)} price grids")
        for hm in heatmap_data[:5]:
            rating_str = "*" * hm["rating"]
            print(f"      {hm['price']} | Total Liq={hm['total_liq']} | Rating={rating_str}")

    # ============ 7. Big Order Confirmation ============
    if trend["trend"] == "GOLDEN_CROSS":
        confirm_direction = "LONG"
    elif trend["trend"] == "DEATH_CROSS":
        confirm_direction = "SHORT"
    else:
        confirm_direction = "NEUTRAL"

    big_order_confirm = big_order_confirmation(
        confirm_direction, big_order, oi_change, funding_check, df_1h=df_1h
    )
    if is_spot:
        print(f"\n  Big Order Confirmation: Not supported for spot, skipped")
    else:
        print(f"\n  Big Order Confirmation: {'Confirmed' if big_order_confirm['confirmed'] else 'Not Confirmed'} (Score: {big_order_confirm['score']})")
        for reason in big_order_confirm["reasons"]:
            print(f"    · {reason}")

    # ============ 7. Signal Generation ============
    _update_progress(message=f"{name} generating signal...")
    print(f"\nSignal Generation...")

    signal = generate_signal(
        trend=trend,
        sr_zones=sr_zones,
        big_order_result=big_order_confirm,
        df_4h=df_4h,
        df_1h=df_1h,
        funding_check=funding_check,
        instrument=instrument,
        order_walls=order_walls
    )

    signal_emoji = {"LONG": "Long", "SHORT": "Short", "NEUTRAL": "Neutral"}
    print(f"  Signal Direction: {signal_emoji.get(signal['direction'], signal['direction'])}")
    if signal["direction"] != "NEUTRAL":
        print(f"  Entry Price: {signal['entry_price']}")
        print(f"  Stop Loss: {signal['stop_loss']}")
        print(f"  Take Profit: {signal['take_profit']}")
        print(f"  Risk/Reward: {signal['risk_reward_ratio']}:1")

    print(f"\n  Checklist:")
    checklist_labels = {
        "ema_trend": "EMA Trend Direction",
        "in_support_zone": "Price in Support Zone",
        "in_resistance_zone": "Price in Resistance Zone",
        "bullish_candle": "1H Bullish Reversal Candle",
        "bearish_candle": "1H Bearish Reversal Candle",
        "big_order_confirm": "Big Order Confirmation",
        "funding_rate_ok": "Funding Rate Normal"
    }
    for k, v in signal["checklist"].items():
        label = checklist_labels.get(k, k)
        icon = "✅" if v else "❌"
        print(f"    {icon} {label}")

    # ============ Prepare chart data ============
    # Multi-period candle data
    def df_to_candles_json(df):
        """Convert DataFrame to JSON for Lightweight Charts"""
        df_chart = df[["ts", "open", "high", "low", "close"]].copy()
        df_chart["time"] = df_chart["ts"].apply(
            lambda x: int(x.value // 1_000_000_000) if hasattr(x, 'value') else int(x.timestamp())
        )
        return json.dumps(
            df_chart[["time", "open", "high", "low", "close"]].to_dict("records")
        )

    candles_1h_json = df_to_candles_json(df_1h)
    candles_4h_json = df_to_candles_json(df_4h)
    candles_1d_json = df_to_candles_json(df_1d)

    # EMA series: calculated directly from each period's close (aligned to each period's timeline)
    from ema_analyzer import calc_ema

    def compute_ema_json(df, period=144):
        """Calculate EMA based on DataFrame's close, return JSON series"""
        close = df["close"]
        ema_series = calc_ema(close, period)
        points = []
        for i, row in df.iterrows():
            val = ema_series.iloc[i] if i < len(ema_series) else None
            if val is not None and pd.notna(val):
                ts = row["ts"]
                unix_ts = int(ts.value // 1_000_000_000) if hasattr(ts, 'value') else int(ts.timestamp())
                points.append({"time": unix_ts, "value": float(val)})
        return json.dumps(points)

    ema144_1h_json = compute_ema_json(df_1h, 144)
    ema169_1h_json = compute_ema_json(df_1h, 169)
    ema144_4h_json = compute_ema_json(df_4h, 144)
    ema169_4h_json = compute_ema_json(df_4h, 169)
    ema144_1d_json = compute_ema_json(df_1d, 144)
    ema169_1d_json = compute_ema_json(df_1d, 169)

    # Clean non-serializable data from trend
    trend_clean = {
        "trend": trend["trend"],
        "ema144": trend["ema144"],
        "ema169": trend["ema169"],
        "separation_pct": trend["separation_pct"],
        "trend_strength": trend["trend_strength"]
    }

    # Clean non-serializable data from signal
    signal_clean = {
        "direction": signal["direction"],
        "entry_price": signal["entry_price"],
        "stop_loss": signal["stop_loss"],
        "take_profit": signal["take_profit"],
        "reasons": signal["reasons"],
        "checklist": signal["checklist"],
        "risk_reward_ratio": signal["risk_reward_ratio"]
    }

    # Clean sr_zones (contains resonance field)
    sr_zones_clean = []
    for z in sr_zones:
        sr_zones_clean.append({
            "level": z["level"],
            "type": z["type"],
            "zone_low": z["zone_low"],
            "zone_high": z["zone_high"],
            "score": z["score"],
            "strength": z["strength"],
            "resonance": z.get("resonance"),
            "resonance_reason": z.get("resonance_reason", "")
        })

    # Clean sr_scored (contains is_liquidation/is_order_wall fields)
    sr_scored_clean = []
    for s in sr_scored:
        sr_scored_clean.append({
            "level": s["level"],
            "type": s["type"],
            "score": s["score"],
            "strength": s["strength"],
            "touch_count": s["touch_count"],
            "is_psychological": s["is_psychological"],
            "is_fvg": s["is_fvg"],
            "is_liquidation": s.get("is_liquidation", False),
            "is_order_wall": s.get("is_order_wall", False)
        })

    # Clean vp_result
    vp_result_clean = {
        "poc": vp_result.get("poc", 0),
        "va_high": vp_result.get("va_high", 0),
        "va_low": vp_result.get("va_low", 0),
        "high_volume_nodes": vp_result.get("high_volume_nodes", []),
        "bins": vp_result.get("bins", [])
    }

    # Clean liquidation_data
    liquidation_data_clean = []
    for lq in liquidation_data:
        liquidation_data_clean.append({
            "price": lq["price"],
            "volume": lq["volume"],
            "side": lq["side"]
        })

    # Clean order_walls
    order_walls_clean = {
        "bid_walls": order_walls.get("bid_walls", []),
        "ask_walls": order_walls.get("ask_walls", []),
        "bid_total": order_walls.get("bid_total", 0),
        "ask_total": order_walls.get("ask_total", 0),
        "imbalance": order_walls.get("imbalance", 1.0)
    }

    return {
        "instrument": instrument,
        "inst_id": inst_id,
        "trend": trend_clean,
        "sr_zones": sr_zones_clean,
        "sr_scored": sr_scored_clean,
        "big_order": big_order,
        "oi_change": oi_change,
        "funding_check": funding_check,
        "big_order_confirm": big_order_confirm,
        "signal": signal_clean,
        # Multi-period candle data
        "candles_1h_json": candles_1h_json,
        "candles_4h_json": candles_4h_json,
        "candles_1d_json": candles_1d_json,
        # Multi-period EMA data
        "ema144_1h_json": ema144_1h_json,
        "ema169_1h_json": ema169_1h_json,
        "ema144_4h_json": ema144_4h_json,
        "ema169_4h_json": ema169_4h_json,
        "ema144_1d_json": ema144_1d_json,
        "ema169_1d_json": ema169_1d_json,
        # Analysis data
        "vp_result": vp_result_clean,
        "liquidation_data": liquidation_data_clean,
        "order_walls": order_walls_clean,
        "heatmap_data": heatmap_data,
    }


def print_db_status(instruments: list):
    """Print database status"""
    global _db_manager
    db = _get_db_manager()

    db_path = db.db_path
    print(f"\nLocal Database: {db_path}")

    for inst in instruments:
        config = INSTRUMENTS_CONFIG.get(inst)
        if not config:
            continue
        inst_id = config["inst_id"]
        for bar in ["1D", "4H", "1H"]:
            count = db.get_candle_count(inst_id, bar)
            count_str = f"{count} bars" if count > 0 else "0 bars (New Database)"
            print(f"  {inst} {bar}: {count_str}")


def main():
    """Main function"""
    # License verification (show dialog and exit if invalid/expired/not activated)
    try:
        from license_manager import check_license_on_startup
        check_license_on_startup()
    except ImportError as e:
        import tkinter.messagebox as _mb
        _mb.showerror("Startup Error", f"Missing required dependencies, please run first:\npip install pycryptodome\n\nError: {e}")
        sys.exit(1)
    except Exception as e:
        import tkinter.messagebox as _mb
        _mb.showerror("Startup Error", f"License verification error:\n{e}")
        sys.exit(1)

    parser = argparse.ArgumentParser(description="OKX Trading Signal Analysis Tool")
    parser.add_argument(
        "--instruments",
        nargs="+",
        default=["BTC", "ETH", "XAU", "XAG", "BTC_SPOT", "ETH_SPOT"],
        choices=["BTC", "ETH", "XAU", "XAG", "BTC_SPOT", "ETH_SPOT"],
        help="Instruments to analyze, default all"
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Force full refresh from API (ignore local cache)"
    )
    parser.add_argument(
        "--db-path",
        type=str,
        default=None,
        help="Specify database path (default ./data/trading.db)"
    )
    args = parser.parse_args()

    # Initialize database
    db_path = Path(args.db_path) if args.db_path else None
    global _db_manager
    _db_manager = DBManager(db_path)

    print("=" * 60)
    print("OKX Trading Signal Analysis System V7")
    print(f"Analysis Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Instruments: {', '.join(args.instruments)}")
    if args.refresh:
        print(f"Mode: Force Full Refresh")
    print("=" * 60)

    # Print database status
    print_db_status(args.instruments)

    # ---- Start HTTP server first ----
    output_dir = str(Path(__file__).parent)
    server, base_url, lan_url = start_server(port=8080, output_dir=output_dir)
    print(f"HTTP server started: {base_url}")
    if lan_url:
        print(f"LAN access URL:   {lan_url}")
        _ensure_firewall_rule(port=8080)
    else:
        print("(Could not get LAN IP, local access only)")

    # ---- Generate loading page HTML ----
    loading_html = r'''<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#0d1117;color:#e6edf3;display:flex;align-items:center;justify-content:center;height:100vh;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
.loader{width:520px;max-width:92vw}
h2{font-size:18px;font-weight:600;margin-bottom:4px;color:#e6edf3;text-align:center}
.sub{font-size:12px;color:#6b7280;margin-bottom:20px;text-align:center}
#msg{font-size:13px;color:#e6edf3;margin-bottom:8px;text-align:center;min-height:18px}
.pbar{width:100%;height:6px;background:#1e2738;border-radius:3px;overflow:hidden;margin-bottom:16px}
.pbar-fill{height:100%;background:linear-gradient(90deg,#58a6ff,#3fb950);border-radius:3px;transition:width .4s ease;width:5%}
.tasks{display:flex;flex-wrap:wrap;gap:6px;justify-content:center;margin-bottom:12px}
.task{font-size:11px;padding:3px 10px;border-radius:10px;background:#161b22;color:#6b7280;border:1px solid #21262d;transition:all .3s}
.task.running{color:#58a6ff;border-color:#58a6ff33;background:#1a2332}
.task.done{color:#3fb950;border-color:#3fb95033;background:#122416}
.task.error{color:#f85149;border-color:#f8514933;background:#241216}
.inst-info{font-size:12px;color:#8b949e;text-align:center;margin-bottom:6px}
.fail-panel{display:none;margin-top:12px;padding:12px 16px;background:#1a1518;border:1px solid #f8514933;border-radius:8px}
.fail-panel h4{font-size:13px;color:#f85149;margin-bottom:8px}
.fail-panel .fail-list{font-size:12px;color:#c9d1d9;line-height:1.8}
.fail-panel .fail-reason{font-size:12px;color:#8b949e;margin-top:4px;border-top:1px solid #21262d;padding-top:6px}
.retry-hint{display:none;font-size:11px;color:#6b7280;text-align:center;margin-top:10px}
</style></head>
<body>
<div class="loader">
  <h2>OKX Trading Signal Analysis System</h2>
  <div class="sub" id="inst-info">Initializing...</div>
  <div class="pbar"><div class="pbar-fill" id="pbar"></div></div>
  <div id="msg">Preparing to connect to API...</div>
  <div class="tasks" id="tasks"></div>
  <div class="fail-panel" id="fail-panel">
    <h4>Analysis Failed</h4>
    <div class="fail-list" id="fail-list"></div>
    <div class="fail-reason" id="fail-reason"></div>
  </div>
  <div class="retry-hint" id="retry-hint">Please check your network connection and restart</div>
</div>
<script>
let pct = 5;
let settled = false;
function poll(){
  fetch("/api/progress").then(r=>r.json()).then(d=>{
    let m = document.getElementById("msg");
    let inst = document.getElementById("inst-info");
    let pbar = document.getElementById("pbar");
    let failPanel = document.getElementById("fail-panel");
    let failList = document.getElementById("fail-list");
    let failReason = document.getElementById("fail-reason");
    let retryHint = document.getElementById("retry-hint");

    if(d.message) m.textContent = d.message;
    if(d.instrument && d.total>0){
      inst.textContent = d.instrument + " (" + d.current + "/" + d.total + ")";
      pct = Math.min(92, Math.round((d.current-1)/d.total*55 + 5));
    }

    // Task labels
    let tasks = d.tasks || {};
    let container = document.getElementById("tasks");
    let html = "";
    for(let k in tasks){
      let t = tasks[k];
      let cls = t.status==="running"?"running":t.status==="done"?"done":t.status==="error"?"error":"";
      html += '<span class="task '+cls+'">'+t.label+'</span>';
    }
    container.innerHTML = html;

    let hasRunning = Object.values(tasks).some(t=>t.status==="running");
    if(!hasRunning && Object.keys(tasks).length>0) pct = Math.max(pct, 40);
    if(d.stage==="analyzing") pct = Math.max(pct, 68);
    if(d.stage==="rendering") pct = 88;

    if(d.stage==="done"){
      settled = true;
      pct = 100;
      let isFail = d.error || (d.message && d.message.indexOf("fail")>=0);
      if(isFail){
        // Failure state: red progress bar + show failure panel
        pbar.style.background = "#f85149";
        m.style.color = "#f85149";
        // Show failure panel
        if(d.failed_instruments && d.failed_instruments.length>0){
          failPanel.style.display = "block";
          failList.innerHTML = "Failed instruments: " + d.failed_instruments.map(function(x){
            let labels = {"BTC":"Bitcoin","ETH":"Ethereum","XAU":"Gold","XAG":"Silver","BTC_SPOT":"Bitcoin Spot","ETH_SPOT":"Ethereum Spot"};
            return labels[x] || x;
          }).join(", ");
        }
        if(d.error){
          failPanel.style.display = "block";
          failReason.textContent = d.error;
        }
        retryHint.style.display = "block";
      } else {
        // Success state: green progress bar
        pbar.style.background = "#3fb950";
      }
    }
    pbar.style.width = pct + "%";
  }).catch(function(){});
}
setInterval(poll, 500);
poll();
</script>
</body></html>'''

    loading_file = Path(output_dir) / "_loading.html"
    loading_file.write_text(loading_html, encoding="utf-8")

    # ---- Try to open desktop window with pywebview ----
    use_webview = False
    try:
        import webview
        use_webview = True
        print(f"pywebview detected, using desktop window mode")
    except ImportError:
        print(f"pywebview not installed, using browser mode")

    if use_webview:
        # Show loading page first
        window = webview.create_window(
            title="OKX Trading Signal Analysis System",
            url=f"{base_url}/_loading.html",
            width=1600,
            height=950,
            min_size=(1200, 700),
            resizable=True,
        )

        # Execute analysis in background thread
        analysis_result = {"results": None, "failed": [], "error": None}

        def run_analysis():
            """Background thread: execute data analysis"""
            try:
                results = []
                failed_instruments = []
                _update_progress(stage="fetching", total=len(args.instruments), current=0)
                for i, inst in enumerate(args.instruments):
                    _update_progress(current=i+1, instrument=inst, tasks={}, failed_instruments=failed_instruments)
                    try:
                        result = analyze_instrument(inst, refresh=args.refresh)
                        if result:
                            results.append(result)
                        else:
                            failed_instruments.append(inst)
                            print(f"  {inst}: Analysis returned empty result")
                    except Exception as e:
                        failed_instruments.append(inst)
                        logger.error(f"Error analyzing {inst}: {e}", exc_info=True)

                analysis_result["results"] = results
                analysis_result["failed"] = failed_instruments

                if not results:
                    analysis_result["error"] = "No instruments analyzed successfully"
                    _update_progress(
                        stage="done",
                        message="Analysis failed",
                        failed_instruments=failed_instruments,
                        error="All instruments failed, please check network connection or API availability"
                    )
                    return

                # Generate HTML report
                _update_progress(stage="rendering", message="Generating HTML report...")
                print(f"\nGenerating HTML report...")
                html_content = build_html(results)

                output_file = Path(output_dir) / f"trading_signal_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
                output_file.write_text(html_content, encoding="utf-8")
                _cleanup_old_reports(output_dir)
                _update_progress(
                    stage="done",
                    message=f"Report generated (success {len(results)}, failed {len(failed_instruments)})" if failed_instruments else "Report generated",
                    failed_instruments=failed_instruments
                )
                print(f"Report generated: {output_file}")

                # Summary to console
                print(f"\nSignal Summary")
                for r in results:
                    sig = r["signal"]
                    trend = r["trend"]
                    inst = r["instrument"]
                    direction = sig["direction"]
                    print(f"  {inst}: {direction} | Trend {trend['trend']} | Separation {trend['separation_pct']}%")
                    if direction != "NEUTRAL":
                        print(f"      Entry: {sig['entry_price']} | Stop Loss: {sig['stop_loss']} | Take Profit: {sig['take_profit']}")

                print(f"\nThe above analysis is for reference only and does not constitute investment advice!")

            except Exception as e:
                analysis_result["error"] = str(e)
                logger.error(f"Analysis error: {e}", exc_info=True)
                _update_progress(
                    stage="done",
                    message="Analysis failed",
                    failed_instruments=analysis_result["failed"],
                    error=f"Program error: {str(e)}"
                )

        def on_closing():
            """Cleanup resources on window close: stop refresh -> wait for analysis -> close DB -> close server"""
            print(f"Closing window, cleaning up...")
            _stop_live_refresh()
            _stop_candle_refresh()
            # Wait for ongoing analysis to complete (max 60s), prevent DB from being closed while in use
            if _analysis_lock.acquire(timeout=60):
                logger.info("Analysis complete, starting database cleanup")
                _analysis_lock.release()
            else:
                logger.warning("Analysis did not complete within 60s, force closing database")
            if _db_manager is not None:
                try:
                    _db_manager.checkpoint()
                except Exception:
                    pass
                _db_manager.close()
                print(f"Database closed")
            server.shutdown()
            print(f"Resource cleanup complete")

        window.events.closing += on_closing

        # Start webview event loop (in main thread), analysis in child thread
        import time as _time

        def start_analysis_after_delay():
            """Start analysis after 2 second delay (wait for window to load)"""
            _time.sleep(2)
            run_analysis()
            # Switch to report page after analysis completes
            if analysis_result["results"]:
                # Find the latest HTML file
                html_files = [
                    f for f in os.listdir(output_dir)
                    if f.startswith('trading_signal_') and f.endswith('.html')
                ]
                if html_files:
                    html_files.sort(reverse=True)
                    report_url = f"{base_url}/{html_files[0]}"
                    print(f"Switching to report page: {report_url}")
                    window.load_url(report_url)
                # Start real-time data 5-minute refresh (orderbook + liquidation)
                _start_live_refresh(list(INSTRUMENTS_CONFIG.values()), interval=300)
                # Start candle periodic incremental refresh

                def _on_candle_updated():
                    """Callback after candle data update: re-analyze -> generate new report -> update webview
                    Note: caller refresh_loop already holds _analysis_lock, don't acquire here"""
                    try:
                        logger.info("Candle data updated, triggering re-analysis...")
                        analysis_result["results"] = None
                        analysis_result["failed"] = []
                        analysis_result["error"] = None
                        run_analysis()
                        if analysis_result["results"]:
                            html_content = build_html(analysis_result["results"])
                            output_file = Path(output_dir) / f"trading_signal_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
                            output_file.write_text(html_content, encoding="utf-8")
                            _cleanup_old_reports(output_dir)
                            report_url = f"{base_url}/{output_file.name}"
                            print(f"Report refreshed: {output_file}")
                            window.load_url(report_url)
                    except Exception as e:
                        logger.error(f"Failed to refresh report: {e}")

                _start_candle_refresh(list(INSTRUMENTS_CONFIG.values()),
                                      on_data_updated=_on_candle_updated)

        analysis_thread = threading.Thread(target=start_analysis_after_delay, daemon=True)
        analysis_thread.start()

        webview.start(debug=False)
        print(f"Stopped")

    else:
        # ---- Browser mode: analyze first, then open ----
        results = []
        failed_instruments = []
        _update_progress(stage="fetching", total=len(args.instruments), current=0)
        for i, inst in enumerate(args.instruments):
            _update_progress(current=i+1, instrument=inst, tasks={}, failed_instruments=failed_instruments)
            try:
                result = analyze_instrument(inst, refresh=args.refresh)
                if result:
                    results.append(result)
                else:
                    failed_instruments.append(inst)
                    print(f"  {inst}: Analysis returned empty result")
            except Exception as e:
                failed_instruments.append(inst)
                        logger.error(f"Error analyzing {inst}: {e}", exc_info=True)

        if failed_instruments:
            print(f"\nFailed instruments: {', '.join(failed_instruments)}")
            print(f"Success: {len(results)}, Failed: {len(failed_instruments)}")

        if not results:
            print("\nNo instruments analyzed successfully")
            _update_progress(
                stage="done",
                message="Analysis failed",
                failed_instruments=failed_instruments,
                error="All instruments failed, please check network connection or API availability"
            )
            server.shutdown()
            if _db_manager is not None:
                _db_manager.close()
            return

        # Generate HTML report
        _update_progress(stage="rendering", message="Generating HTML report...")
        print(f"\nGenerating HTML report...")
        html_content = build_html(results)

        output_file = Path(output_dir) / f"trading_signal_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
        output_file.write_text(html_content, encoding="utf-8")

        _update_progress(
            stage="done",
            message=f"Report generated (success {len(results)}, failed {len(failed_instruments)})" if failed_instruments else "Report generated",
            failed_instruments=failed_instruments
        )
        print(f"Report generated: {output_file}")
        print(f"Open in browser: file:///{output_file}")

        # Summary
        print(f"\nSignal Summary")
        for r in results:
            sig = r["signal"]
            trend = r["trend"]
            inst = r["instrument"]
            direction = sig["direction"]
            emoji = {"LONG": "Long", "SHORT": "Short", "NEUTRAL": "Neutral"}.get(direction, "?")
            print(f"  {inst}: {emoji} | Trend {trend['trend']} | Separation {trend['separation_pct']}%")
            if direction != "NEUTRAL":
                print(f"      Entry: {sig['entry_price']} | Stop Loss: {sig['stop_loss']} | Take Profit: {sig['take_profit']} | R/R: {sig['risk_reward_ratio']}:1")

        print(f"\nThe above analysis is for reference only and does not constitute investment advice!")

        # Start real-time data 5-minute refresh
        _start_live_refresh(list(INSTRUMENTS_CONFIG.values()), interval=300)

        # Open browser
        import time as _time
        browser_opened = False
        try:
            webbrowser.open(base_url)
            browser_opened = True
            print(f"Browser opened: {base_url}")
        except Exception as e:
            print(f"webbrowser open failed: {e}")

        if not browser_opened and os.name == 'nt':
            try:
                os.startfile(base_url)
                print(f"Browser opened (os.startfile): {base_url}")
            except Exception as e:
                print(f"os.startfile open failed: {e}")

        print(f"If browser did not open automatically, please visit: {base_url}")
        print(f"Press Ctrl+C to stop")

        try:
            while True:
                _time.sleep(1)
        except KeyboardInterrupt:
            _stop_live_refresh()
            if _db_manager is not None:
                _db_manager.close()
                print(f"Database closed")
            server.shutdown()
            print(f"Stopped")


if __name__ == "__main__":
    main()
