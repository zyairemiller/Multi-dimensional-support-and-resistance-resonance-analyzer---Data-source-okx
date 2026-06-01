"""
OKX交易信号分析 - 主程序入口

用法:
  python trading_signal.py                       # 分析全部品种
  python trading_signal.py --instruments BTC ETH # 只分析指定品种
  python trading_signal.py --refresh             # 强制从API全量刷新（忽略本地缓存）
  python trading_signal.py --db-path ./data/my.db # 指定数据库路径
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

# 日志配置：控制台 + 滚动文件（24h运行诊断）
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

# ============ 内置HTTP服务器 ============
class TradingHandler(http.server.SimpleHTTPRequestHandler):
    """自定义HTTP请求处理器，提供静态文件服务和API代理"""

    # --- TradFi 状态（类级别，跨请求共享）---
    _tradfi_progress = {"pct": 0, "msg": "", "done": False, "report_url": ""}
    _tradfi_lock = threading.Lock()
    _tradfi_session_id = 0
    _tradfi_cancel = threading.Event()

    def do_GET(self):
        parsed = urlparse(self.path)

        # 静态文件路由
        if parsed.path.startswith('/static/'):
            filename = parsed.path[len('/static/'):]
            # 安全检查：禁止目录穿越
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

        # API代理：/api/tickers?instType=SWAP 或 /api/tickers?instType=SPOT
        if parsed.path == '/api/tickers':
            params = parse_qs(parsed.query)
            inst_type = params.get('instType', ['SWAP'])[0]

            try:
                # 使用 okx_data._make_request（raw socket + SSL，支持多链路回退）
                # 而非 urllib.request（PySocks monkey-patch 后 SSL 握手失败）
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

        # API：/api/progress 返回当前加载进度
        if parsed.path == '/api/progress':
            with _fetch_progress_lock:
                data = dict(_fetch_progress)
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps(data, default=str).encode())
            return

        # API代理：/api/live_data?instId=BTC-USDT-SWAP 返回实时订单簿+清算数据
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

        # 静态文件服务：如果访问 /，自动找到最新的HTML文件
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

        # API：/api/tradfi_progress
        if parsed.path == '/api/tradfi_progress':
            with TradingHandler._tradfi_lock:
                data = dict(TradingHandler._tradfi_progress)
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps(data, default=str).encode())
            return

        # API：/api/tradfi_instruments
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

        # TradFi 选择页
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

        # TradFi 报告文件路由
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

        # 其他文件正常处理（由SimpleHTTPRequestHandler提供静态文件服务）
        return super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == '/api/tradfi_start':
            try:
                length = int(self.headers.get('Content-Length', 0))
                body = json.loads(self.rfile.read(length))
                inst_ids = body.get('inst_ids', [])
                # 取消旧分析线程并创建新会话
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
        """静默日志，减少控制台输出"""
        pass


def _run_tradfi_analysis(inst_ids, session_id, cancel_event):
    """后台线程：为每个 inst_id 构造临时配置，调用 analyze_instrument 和 build_html

    Args:
        inst_ids: 品种 instId 列表
        session_id: 当前会话 ID，写入共享状态时校验防止旧线程污染
        cancel_event: 取消信号，新请求到来时 set，旧线程检测到后提前退出
    """
    db_dir = Path(__file__).parent / "data"
    db_dir.mkdir(exist_ok=True)
    tradfi_db = db_dir / "tradfi.db"

    if tradfi_db.exists():
        tradfi_db.unlink()
    # 使用独立 DB
    tradfi_db_mgr = DBManager(tradfi_db)

    # 暂存 DB 引用，避免污染主程序
    orig_db = _db_manager
    try:
        # 临时替换全局 DB 管理器
        import okxtrading
        okxtrading._db_manager = tradfi_db_mgr

        with TradingHandler._tradfi_lock:
            if session_id != TradingHandler._tradfi_session_id:
                return  # 旧会话，直接退出
            TradingHandler._tradfi_progress = {
                "pct": 0, "msg": f"开始分析 {len(inst_ids)} 个品种...",
                "done": False, "report_url": ""
            }

        results = []
        for i, inst_id in enumerate(inst_ids):
            # 检查取消信号
            if cancel_event.is_set():
                logger.info(f"TradFi 分析被取消 (session {session_id})，已分析 {i}/{len(inst_ids)} 品种")
                return

            base = inst_id.split('-')[0]

            with TradingHandler._tradfi_lock:
                if session_id != TradingHandler._tradfi_session_id:
                    return
                TradingHandler._tradfi_progress["pct"] = int((i / len(inst_ids)) * 80)
                TradingHandler._tradfi_progress["msg"] = f"分析中: {inst_id} ({i+1}/{len(inst_ids)})"

            # 启动进度监控线程（轮询 _fetch_progress 中任务完成状态）
            mon_done = threading.Event()

            def _monitor_fetch_progress(inst_idx, total_insts, sid, mon_event):
                base_pct = int((inst_idx / total_insts) * 80)
                slice_pct = 80 / total_insts  # 每个品种占 80% 进度条中的份额
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
                logger.error(f"TradFi {inst_id} 分析失败: {e}")
            finally:
                mon_done.set()
                mon.join(timeout=1)

        # 循环结束后检查是否仍为当前会话
        with TradingHandler._tradfi_lock:
            if session_id != TradingHandler._tradfi_session_id:
                return
            TradingHandler._tradfi_progress["pct"] = 90
            TradingHandler._tradfi_progress["msg"] = "生成报告中..."

        if results:
            html = build_html(results)
            out = Path(__file__).parent / f"tradfi_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
            out.write_text(html, encoding='utf-8')

            with TradingHandler._tradfi_lock:
                if session_id != TradingHandler._tradfi_session_id:
                    return
                TradingHandler._tradfi_progress["pct"] = 100
                TradingHandler._tradfi_progress["msg"] = f"完成! 共 {len(results)} 个品种"
                TradingHandler._tradfi_progress["done"] = True
                TradingHandler._tradfi_progress["report_url"] = f"/{out.name}"
            logger.info(f"TradFi 报告已生成: {out}")
        else:
            with TradingHandler._tradfi_lock:
                if session_id == TradingHandler._tradfi_session_id:
                    TradingHandler._tradfi_progress["msg"] = "所有品种分析失败"
                    TradingHandler._tradfi_progress["done"] = True
    finally:
        # 仅当前会话才恢复全局状态（旧线程不应触碰）
        with TradingHandler._tradfi_lock:
            if session_id == TradingHandler._tradfi_session_id:
                TradingHandler._tradfi_progress["done"] = True
        # session_id 是唯一有效判断依据：仅当前活跃会话才恢复 DB 管理器
        # 移除 cancel_event 条件：旧线程被取消后 cancel_event 已 set，若保留该条件会导致旧线程跳过恢复
        if session_id == TradingHandler._tradfi_session_id:
            import okxtrading
            okxtrading._db_manager = orig_db


def _get_lan_ip():
    """获取本机局域网IPv4地址"""
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
    """尝试添加 Windows 防火墙入站规则（需要管理员权限）"""
    import subprocess
    rule_name = f"OKX Trading Dashboard (TCP {port})"
    try:
        # 先检查规则是否已存在
        check = subprocess.run(
            f'netsh advfirewall firewall show rule name="{rule_name}"',
            shell=True, capture_output=True, text=True
        )
        if '没有与指定条件相匹配的规则' in check.stdout or 'No rules match' in check.stdout:
            result = subprocess.run(
                f'netsh advfirewall firewall add rule name="{rule_name}" '
                f'dir=in action=allow protocol=TCP localport={port}',
                shell=True, capture_output=True, text=True
            )
            if result.returncode == 0:
                print("防火墙已放行此端口")
            else:
                print(f"防火墙规则添加失败（可能需要管理员权限运行）。请手动执行:")
                print(f'  netsh advfirewall firewall add rule name="{rule_name}" dir=in action=allow protocol=TCP localport={port}')
        else:
            print("防火墙规则已存在")
    except Exception:
        print(f"防火墙检查失败，如需从局域网访问请手动放行端口 {port}")


def start_server(port=8080, output_dir=None):
    """启动内置HTTP服务器（在后台线程中运行），自动处理端口冲突"""
    if output_dir:
        os.chdir(output_dir)

    # 尝试多个端口，避免因残留进程导致 Address already in use
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

    # 获取局域网 IP，用于其他设备访问
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


# 品种配置
INSTRUMENTS_CONFIG = {
    "BTC": {
        "inst_id": "BTC-USDT-SWAP",
        "name": "比特币",
        "type": "swap",
    },
    "ETH": {
        "inst_id": "ETH-USDT-SWAP",
        "name": "以太坊",
        "type": "swap",
    },
    "XAU": {
        "inst_id": "XAU-USDT-SWAP",
        "name": "黄金",
        "type": "swap",
    },
    "XAG": {
        "inst_id": "XAG-USDT-SWAP",
        "name": "白银",
        "type": "swap",
    },
    "BTC_SPOT": {
        "inst_id": "BTC-USDT",
        "name": "比特币现货",
        "type": "spot",
    },
    "ETH_SPOT": {
        "inst_id": "ETH-USDT",
        "name": "以太坊现货",
        "type": "spot",
    },
}

# 各周期需要的数据量
CANDLE_NEED_COUNT = {
    "1D": 365,
    "4H": 2190,
    "1H": 8760,
}

# 各周期的K线间隔（毫秒），用于判断数据是否足够新
BAR_INTERVAL_MS = {
    "1D": 86400000,
    "4H": 14400000,
    "1H": 3600000,
}

# 全局DBManager实例（在main中初始化）
_db_manager = None

# 分析并发锁（防止K线刷新触发分析时与上一轮并发；同时供 on_closing 等待分析完成）
_analysis_lock = threading.Lock()

# ---- 进度追踪（加载页面轮询展示） ----
_fetch_progress = {
    "stage": "idle",        # idle / fetching / analyzing / rendering / done
    "instrument": "",       # 当前品种名
    "tasks": {},            # {task_id: {"label": "xxx", "status": "running"/"done"/"error"}}
    "total": 0,             # 总品种数
    "current": 0,           # 当前品种序号 (1-based)
    "message": "",          # 人可读的提示文字
    "failed_instruments": [],  # 分析失败的品种列表
    "error": "",            # 错误详情（异常信息）
}
_fetch_progress_lock = threading.Lock()

def _update_progress(**kwargs):
    """线程安全地更新进度状态"""
    with _fetch_progress_lock:
        _fetch_progress.update(kwargs)

def _set_task_status(task_id, status, label=None):
    """更新单个任务状态"""
    with _fetch_progress_lock:
        if task_id not in _fetch_progress["tasks"]:
            _fetch_progress["tasks"][task_id] = {"label": label or task_id, "status": status}
        else:
            _fetch_progress["tasks"][task_id]["status"] = status
            if label:
                _fetch_progress["tasks"][task_id]["label"] = label


def _cleanup_old_reports(output_dir, keep_count=2):
    """清理旧的HTML报告文件，仅保留最近N份"""
    try:
        html_files = sorted(
            [f for f in os.listdir(output_dir) if f.startswith('trading_signal_') and f.endswith('.html')],
            reverse=True
        )
        for old_file in html_files[keep_count:]:
            try:
                os.remove(os.path.join(output_dir, old_file))
                logger.info(f"清理旧报告: {old_file}")
            except OSError:
                pass
    except Exception:
        pass


# ---- 实时数据管理（订单簿+清算数据5分钟刷新） ----
_live_data = {}           # inst_id → {orderbook, liquidation, updated_at}
_live_data_lock = threading.Lock()
_live_data_running = False

def _start_live_refresh(instruments_config, interval=300):
    """启动订单簿+清算数据周期性刷新（默认5分钟）"""
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
                    logger.error(f"实时数据刷新失败 {inst_id}: {e}")
            time.sleep(interval)

    thread = threading.Thread(target=_refresh_loop, daemon=True)
    thread.start()
    logger.info(f"实时数据刷新已启动（间隔 {interval}s）")

def _stop_live_refresh():
    """停止实时数据刷新"""
    global _live_data_running
    _live_data_running = False


# ---- K线数据定时增量刷新（1H:30min / 4H:2h / 1D:12h） ----
_candle_refresh_event = threading.Event()
_candle_refresh_thread = None

def _start_candle_refresh(instruments_config, on_data_updated=None):
    """
    启动K线数据周期性增量刷新（按UTC+8本地时间计算间隔）。
    只做增量拉取（after本地最新ts），不拉历史K线。
    
    on_data_updated: 可选回调，在任一品种有新K线时调用
    """
    global _candle_refresh_event, _candle_refresh_thread
    _candle_refresh_event.clear()

    # 对齐规则：按绝对时间对齐到指定时刻
    # 1H → 每30分钟（xx:00, xx:30）；4H → 每2小时（00/02/04...）；1D → 每12小时（00:00, 12:00）
    _last_aligned = {}  # f"{inst_id}_{bar}" → 上次对齐到的时刻(datetime)

    def _get_aligned(dt, bar):
        """计算 dt 应对齐到的拉取时刻"""
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
        _last_db_maintenance = 0  # 数据库维护计时
        
        while not _candle_refresh_event.is_set():
            try:
                # 尝试获取分析锁，防止与正在运行的分析并发
                if not _analysis_lock.acquire(blocking=False):
                    _candle_refresh_event.wait(60)
                    continue

                try:
                    now = datetime.now(timezone.utc)  # UTC 时间
                    any_updated = False
                    pending_updates = {}  # {key: aligned} 待确认的K线刷新

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
                                        logger.info(f"K线增量刷新: {inst_id} {bar} → {len(df)} 根")
                                    else:
                                        # 本地无此品种数据，对齐点直接前进
                                        _last_aligned[key] = aligned
                                except Exception as e:
                                    logger.error(f"K线增量刷新失败 {inst_id} {bar}: {e}")

                    if any_updated and on_data_updated:
                        try:
                            on_data_updated()
                            # 重分析成功 → 确认所有待定对齐点
                            for key, aligned in pending_updates.items():
                                _last_aligned[key] = aligned
                        except Exception as e:
                            logger.error(f"on_data_updated 回调失败: {e}")
                            # 失败 → pending_updates 不确认，下次循环重试

                    # 每小时执行一次数据库WAL checkpoint（防止WAL文件无限膨胀）
                    if now.timestamp() - _last_db_maintenance > 3600:
                        try:
                            db = _get_db_manager()
                            db.checkpoint()
                            _last_db_maintenance = now.timestamp()
                        except Exception as e:
                            logger.error(f"数据库维护失败: {e}")
                finally:
                    _analysis_lock.release()

            except Exception as e:
                logger.error(f"K线刷新循环异常: {e}")

            _candle_refresh_event.wait(60)  # 每分钟检查一次，可被set()中断

    _candle_refresh_thread = threading.Thread(target=_refresh_loop, daemon=True)
    _candle_refresh_thread.start()
    logger.info(f"K线增量刷新已启动（1H对齐xx:00/xx:30 | 4H对齐00/02/04... | 1D对齐00:00/12:00，按UTC时间）")


def _stop_candle_refresh():
    """停止K线增量刷新，等待线程退出"""
    global _candle_refresh_event, _candle_refresh_thread
    _candle_refresh_event.set()
    if _candle_refresh_thread and _candle_refresh_thread.is_alive():
        _candle_refresh_thread.join(timeout=5)


def _get_db_manager(db_path: Path = None) -> DBManager:
    """获取全局DBManager实例"""
    global _db_manager
    if _db_manager is None:
        _db_manager = DBManager(db_path)
    return _db_manager


def _is_spot(instrument: str) -> bool:
    """判断品种是否为现货类型"""
    config = INSTRUMENTS_CONFIG.get(instrument, {})
    return config.get("type") == "spot"


def load_candles(inst_id: str, bar: str, need_count: int = 1000, refresh: bool = False,
                 force_incremental: bool = False) -> pd.DataFrame:
    """
    智能加载K线数据：本地缓存 + 增量API补全

    流程：
    1. 检查本地数据库有没有数据
    2. 如果没有数据：
       - 从API拉取need_count条历史数据（fetch_candles_history）
       - 全部写入本地数据库
       - 返回数据
    3. 如果有数据：
       - 检查本地最新数据的时间戳 vs 当前时间
       - 如果本地数据足够新（1H的最近一根在1小时内），直接用本地数据
       - 如果本地数据落后，从API增量补全：
         a. 用fetch_candles拉取最新的（after=本地最新ts），补上缺口
         b. 将增量数据写入本地数据库
       - 如果本地数据量不足need_count：
         a. 先补全最新数据（步骤3b）
         b. 如果需要更早的历史，用fetch_candles_history往前补
         c. 全部写入数据库
    4. 返回数据

    Args:
        inst_id: 产品ID
        bar: K线周期
        need_count: 需要的总条数
        refresh: 是否强制全量刷新

    Returns:
        DataFrame，列: ts, open, high, low, close, vol, volCcy
    """
    global _db_manager
    db = _get_db_manager()
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    bar_interval_ms = BAR_INTERVAL_MS.get(bar, 3600000)

    def _strip_incomplete(_df):
        """过滤未完成K线：优先使用OKX API返回的confirm字段，兜底用时间推算"""
        if _df.empty:
            return _df
        if "confirm" in _df.columns:
            return _df[_df["confirm"] == "1"]
        return _df[
            _df["ts"].apply(lambda t: (t.value // 1_000_000) + bar_interval_ms <= now_ms)
        ]

    try:
        if refresh:
            # 强制全量刷新
            print(f"  🔄 {inst_id} {bar}: 强制全量刷新，从API获取 {need_count} 条...")
            df = fetch_candles_history(inst_id, bar, need_count)
            if not df.empty:
                db.save_candles(inst_id, bar, df)
            return _strip_incomplete(df)

        # 检查本地数据
        local_count = db.get_candle_count(inst_id, bar)

        if local_count == 0:
            if force_incremental:
                # 增量刷新首次遇到新品种：做一次全量拉取，后续走增量
                print(f"  🆕 {inst_id} {bar}: 首次增量，全量拉取 {need_count} 条...")
                df = fetch_candles_history(inst_id, bar, need_count)
                if not df.empty:
                    db.save_candles(inst_id, bar, df)
                return _strip_incomplete(df)
            # 本地无数据，全量从API拉取
            print(f"  🆕 {inst_id} {bar}: 新数据库，从API获取 {need_count} 条...")
            df = fetch_candles_history(inst_id, bar, need_count)
            if not df.empty:
                db.save_candles(inst_id, bar, df)
            return _strip_incomplete(df)

        # 本地有数据，检查是否需要增量补全
        local_latest_ts = db.get_latest_ts(inst_id, bar)

        # 判断本地数据是否足够新（最新一根在1个周期以内 → 不需要增量补全）
        # force_incremental: 定时刷新专用，跳过freshness检查强制增量拉取，不补历史
        if force_incremental:
            is_data_fresh = False
        else:
            is_data_fresh = local_latest_ts is not None and (now_ms - local_latest_ts) < bar_interval_ms

        # 读取本地数据
        df_local = db.get_candles(inst_id, bar)
        incremental_count = 0

        if not is_data_fresh and local_latest_ts is not None:
            # 本地数据落后，增量补全最新数据
            # 使用 after 参数从 now 向历史方向翻页，确保缺口 > 300 根时不丢数据
            # OKX API: after=X 返回 ts < X 的数据（向历史方向翻页）
            local_latest_dt = pd.to_datetime(local_latest_ts, unit="ms")
            pivot_ms = now_ms + bar_interval_ms  # 起点略超当前时间，确保覆盖刚完成的K线
            all_new_batches = []
            max_pages = 10  # 安全上限：10页 × 300 = 3000根，防止意外死循环

            for _ in range(max_pages):
                batch = fetch_candles(inst_id, bar, 300, after=str(pivot_ms))
                if batch.empty:
                    break

                # 仅保留比本地最新更晚且已完成的K线
                # 未完成K线（ts + bar_interval_ms > now_ms）的OHLC在变化，跳过避免图表跳空
                bar_interval_ms = BAR_INTERVAL_MS.get(bar, 3600000)
                completed_mask = batch["ts"].apply(
                    lambda ts: (ts.value // 1_000_000) + bar_interval_ms <= now_ms
                )
                new_in_batch = batch[(batch["ts"] >= local_latest_dt) & completed_mask].copy()
                if not new_in_batch.empty:
                    all_new_batches.append(new_in_batch)

                # 如果这批已触及本地最新 K 线（含 ts <= local_latest），缺口填满
                if (batch["ts"] <= local_latest_dt).any():
                    break

                # 如果未满 300，说明已到数据尽头
                if len(batch) < 300:
                    break

                # 翻页：用本批最早时间戳 -1ms 继续向历史方向拉取
                pivot_ms = int(batch["ts"].min().value // 1_000_000) - 1

            if all_new_batches:
                df_new = pd.concat(all_new_batches, ignore_index=True)
                df_new = df_new.sort_values("ts").drop_duplicates(subset=["ts"]).reset_index(drop=True)

                if not df_new.empty:
                    db.save_candles(inst_id, bar, df_new)
                    incremental_count += len(df_new)
                    # 重新读取本地数据
                    df_local = db.get_candles(inst_id, bar)
            else:
                # all_new_batches 为空说明没有新数据（可能刚跑完一轮）
                pass

        # 检查数据量是否足够（force_incremental 模式下不补历史，避免长时间拉取）
        if not force_incremental and len(df_local) < need_count:
            # 本地数据不足，需要往前补历史数据
            earliest_ts = db.get_earliest_ts(inst_id, bar)
            need_more = need_count - len(df_local)

            if earliest_ts is not None:
                print(f"  📥 {inst_id} {bar}: 本地 {len(df_local)} 根，还需往前补 {need_more} 条...")
                # before_ts=earliest_ts-1 跳过边界K线（已在本地），避免浪费一次请求
                df_older = fetch_candles_history(inst_id, bar, need_more, before_ts=earliest_ts - 1)
                if not df_older.empty:
                    db.save_candles(inst_id, bar, df_older)
                    incremental_count += len(df_older)

            # 重新读取
            df_local = db.get_candles(inst_id, bar)

        # 输出同步结果
        if incremental_count > 0:
            print(f"  🔄 {inst_id} {bar}: 本地 {local_count} 根，补全 {incremental_count} 根 → 共 {len(df_local)} 根")
        else:
            print(f"  ✅ {inst_id} {bar}: 本地缓存命中 {len(df_local)} 根")

        # 返回最近need_count条，确保排序去重
        if len(df_local) > need_count:
            df_local = df_local.iloc[-need_count:].reset_index(drop=True)

        # 防御性排序去重（防止任何边界情况导致乱序或重复）
        df_local = df_local.sort_values("ts").drop_duplicates(subset=["ts"]).reset_index(drop=True)

        if len(df_local) < need_count:
            logger.warning(
                f"⚠️ {inst_id} {bar}: 目标 {need_count} 根，实际仅 {len(df_local)} 根 "
                f"（OKX常规端点1440上限 + 历史端点3个月回溯上限）"
            )

        return _strip_incomplete(df_local)

    except Exception as e:
        logger.warning(f"本地缓存加载失败，fallback到直接API拉取: {e}")
        # 数据库操作失败时fallback到直接API拉取
        df = fetch_candles_history(inst_id, bar, need_count)
        return _strip_incomplete(df)


def analyze_instrument(instrument: str, refresh: bool = False, inst_id: str = None) -> dict:
    """
    分析单个品种

    Args:
        instrument: 品种名称，如 BTC, ETH, XAU, XAG, BTC_SPOT, ETH_SPOT
        refresh: 是否强制全量刷新
        inst_id: 可选，直接指定 instId（TradFi 线程使用，绕过 INSTRUMENTS_CONFIG 查找）

    Returns:
        完整分析结果
    """
    if inst_id:
        # TradFi 线程直接指定 instId，不依赖全局 INSTRUMENTS_CONFIG
        name = instrument
        is_spot = not inst_id.endswith('-SWAP')
    else:
        config = INSTRUMENTS_CONFIG.get(instrument)
        if not config:
            logger.error(f"未知品种: {instrument}")
            return {}
        inst_id = config["inst_id"]
        name = config["name"]
        is_spot = config.get("type") == "spot"

    spot_label = " (现货)" if is_spot else ""
    print(f"\n{'='*60}")
    print(f"📊 开始分析 {name} ({inst_id}){spot_label}")
    print(f"{'='*60}")

    # ============ 1. 并行获取数据 ============
    print(f"\n📡 并行获取市场数据...")

    # 构建任务池：所有独立API调用一次性并行发起
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

    # 注册所有任务用于进度追踪
    _task_labels = {"df_1d": "日线K线", "df_4h": "4小时K线", "df_1h": "1小时K线",
                    "orderbook": "订单簿", "oi_current": "持仓量", "funding_data": "资金费率",
                    "trades": "逐笔成交", "mark_price": "标记价格", "liquidation_data": "清算数据"}
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
                logger.error(f"并行加载 {key} 失败: {e}")
                failed.append(key)
                results[key] = None
                _set_task_status(key, "error")

    _update_progress(stage="analyzing", message=f"{name} 数据加载完成，开始分析...")

    # 提取结果
    df_1d = results.get("df_1d", pd.DataFrame())
    df_4h = results.get("df_4h", pd.DataFrame())
    df_1h = results.get("df_1h", pd.DataFrame())

    print(f"  ✅ 1D K线: {len(df_1d)} 根")
    print(f"  ✅ 4H K线: {len(df_4h)} 根")
    print(f"  ✅ 1H K线: {len(df_1h)} 根")

    if is_spot:
        oi_current = None
        funding_data = None
        trades = []
        mark_price = None
        liquidation_data = []
        print(f"  ⏭️ 持仓量/资金费率/逐笔成交/标记价格/清算: 现货品种不支持，跳过")
    else:
        oi_current = results.get("oi_current")
        funding_data = results.get("funding_data")
        trades = results.get("trades") or []
        mark_price = results.get("mark_price")
        liquidation_data = results.get("liquidation_data") or []

        print(f"  ✅ 持仓量: {oi_current.get('oi', 'N/A') if oi_current else 'N/A'}")
        print(f"  ✅ 资金费率: {funding_data.get('fundingRate', 'N/A') if funding_data else 'N/A'}")
        print(f"  ✅ 逐笔成交: {len(trades)} 笔 (分批获取)")
        print(f"  ✅ 标记价格: {mark_price}")
        print(f"  ✅ 清算密集区: {len(liquidation_data)} 个价位")
        for lq in liquidation_data:
            side_str = "多头" if lq["side"] == "long" else "空头"
            print(f"      {lq['price']} ({side_str}清算, 量={lq['volume']})")

    # 订单簿 + 订单墙
    orderbook = results.get("orderbook") or {}
    print(f"  ✅ 订单簿: {len(orderbook.get('bids', []))} 买档 / {len(orderbook.get('asks', []))} 卖档")

    order_walls = detect_order_walls(orderbook, inst_id)
    print(f"  ✅ 订单墙: 买单墙 {len(order_walls.get('bid_walls', []))} 个, 卖单墙 {len(order_walls.get('ask_walls', []))} 个, 买卖比 {order_walls.get('imbalance', 1.0)}")
    for wall in order_walls.get("bid_walls", [])[:3]:
        print(f"      买墙 {wall['price']} [强度={wall['strength']}x, 量={wall['size']}]")
    for wall in order_walls.get("ask_walls", [])[:3]:
        print(f"      卖墙 {wall['price']} [强度={wall['strength']}x, 量={wall['size']}]")

    # ============ 2. EMA趋势分析 ============
    _update_progress(message=f"{name} EMA趋势分析中...")
    print(f"\n📈 EMA趋势分析...")
    trend = analyze_trend(df_1d)
    trend_text = {
        "GOLDEN_CROSS": "多头趋势 🟢",
        "DEATH_CROSS": "空头趋势 🔴",
        "ENTANGLED": "缠绕观望 🟡"
    }
    print(f"  趋势状态: {trend_text.get(trend['trend'], trend['trend'])}")
    print(f"  EMA144: {trend['ema144']:.2f}")
    print(f"  EMA169: {trend['ema169']:.2f}")
    print(f"  分离度: {trend['separation_pct']}%")
    print(f"  强度: {trend['trend_strength']}")

    # ============ 3. 支撑阻力识别 ============
    _update_progress(message=f"{name} 支撑阻力识别中...")
    print(f"\n🎯 支撑阻力识别...")

    if df_1h.empty:
        raise RuntimeError(f"{inst_id} 1H K线数据为空，无法继续分析（API可能全部故障）")
    current_price = float(df_1h["close"].iloc[-1])
    structural_sr = find_structural_sr(df_1d, lookback=60)
    print(f"  结构性S/R: {len(structural_sr)} 个")

    psych_levels = find_psychological_levels(current_price, instrument)
    print(f"  心理关口: {len(psych_levels)} 个 - {psych_levels}")

    fvgs = find_fvg(df_1d)
    print(f"  FVG缺口: {len(fvgs)} 个")

    # 评分（传入清算密集区数据 + 订单墙数据）
    sr_scored = []
    for sr in structural_sr:
        scored = score_sr(sr, df_1d, psych_levels, fvgs, liquidation_data, order_walls)
        sr_scored.append(scored)

    # 加入心理关口作为S/R
    for pl in psych_levels:
        pl_type = "resistance" if pl > current_price else "support"
        psych_sr = score_sr(
            {"level": pl, "type": pl_type, "touch_count": 1},
            df_1d, psych_levels, fvgs, liquidation_data, order_walls
        )
        psych_sr["is_psychological"] = True
        sr_scored.append(psych_sr)

    # 加入FVG作为S/R
    for fvg in fvgs:
        fvg_sr = score_sr(
            {"level": fvg["level"], "type": fvg["type"], "touch_count": 1},
            df_1d, psych_levels, fvgs, liquidation_data, order_walls
        )
        fvg_sr["is_fvg"] = True
        sr_scored.append(fvg_sr)

    # 排序输出
    sr_scored.sort(key=lambda x: x["score"], reverse=True)
    print(f"\n  S/R评分排名:")
    for sr in sr_scored[:10]:
        type_str = "支撑" if sr["type"] == "support" else "阻力"
        strength_str = {"super": "超级⭐", "strong": "强💪", "weak": "弱"}
        badges = []
        if sr["is_psychological"]:
            badges.append("整数关口")
        if sr["is_fvg"]:
            badges.append("FVG")
        if sr.get("is_liquidation"):
            badges.append("清算区")
        if sr.get("is_order_wall"):
            badges.append("订单墙")
        badge_str = f" [{','.join(badges)}]" if badges else ""
        print(f"    {sr['level']:.2f} | {type_str} | {sr['score']}分 · {strength_str.get(sr['strength'], sr['strength'])}{badge_str}")

    # 生成S/R区间
    significant_sr = [s for s in sr_scored if s["score"] >= 1]
    sr_zones = get_sr_zones(significant_sr, instrument)

    # ============ 4. Volume Profile分析 ============
    print(f"\n📊 Volume Profile分析...")
    vp_result = calc_volume_profile(df_1h, num_bins=100)
    print(f"  POC: {vp_result['poc']}")
    print(f"  Value Area: {vp_result['va_low']} ~ {vp_result['va_high']}")
    print(f"  高成交量节点: {len(vp_result['high_volume_nodes'])} 个")

    # 检查S/R与Volume Profile共振
    sr_zones = check_sr_vp_resonance(sr_zones, vp_result, instrument)
    resonance_count = sum(1 for z in sr_zones if z.get("resonance"))
    print(f"  S/R共振: {resonance_count} 个区间与VP共振")
    for z in sr_zones:
        if z.get("resonance"):
            res_str = {"strong": "强共振⚡", "normal": "共振", "weak": "弱共振"}.get(z["resonance"], z["resonance"])
            print(f"      {z['level']:.2f} ({z['type']}) → {res_str}: {z.get('resonance_reason', '')}")

    # ============ 5. 大单检测 ============
    print(f"\n🐋 大单检测...")

    big_order = analyze_big_orders(trades, inst_id)
    print(f"  主动买入量: {big_order['buy_volume']}")
    print(f"  主动卖出量: {big_order['sell_volume']}")
    print(f"  大单买入: {big_order['big_buy_volume']} ({big_order['big_buy_count']}笔)")
    print(f"  大单卖出: {big_order['big_sell_volume']} ({big_order['big_sell_count']}笔)")
    print(f"  大单比率: {big_order['big_ratio']}")
    print(f"  大单信号: {big_order['signal']}")

    # OI变化（现货品种使用模拟值）
    if is_spot:
        oi_change = {"direction": "UNKNOWN", "change_pct": 0.0, "signal": "NEUTRAL"}
        print(f"  OI变化: 现货品种不支持，跳过")
    else:
        # 模拟之前OI，用当前值的98%估算
        oi_previous = {"oi": oi_current.get("oi", 0) * 0.98} if oi_current else {"oi": 0}
        oi_change = analyze_oi_change(oi_current, oi_previous)
        print(f"  OI变化: {oi_change['change_pct']}% ({oi_change['direction']})")

    funding_check = check_funding_rate(funding_data)
    if is_spot:
        print(f"  资金费率: 现货品种不支持，跳过")
    else:
        print(f"  资金费率: {funding_check['rate_pct']}% - {funding_check['status']}")
        if funding_check.get("warning"):
            print(f"  ⚠️ {funding_check['warning']}")

    # ============ 6. 清算热力图 ============
    print(f"\n🔥 清算热力图计算...")
    if is_spot:
        heatmap_data = []
        print(f"  ⏭️ 清算热力图: 现货品种不支持，跳过")
    else:
        heatmap_data = compute_liquidation_heatmap(
            instrument=instrument,
            df_1h=df_1h,
            oi_current=oi_current,
            funding_data=funding_data,
        )
        print(f"  ✅ 清算热力图: {len(heatmap_data)} 个价格网格")
        for hm in heatmap_data[:5]:
            rating_str = "⭐" * hm["rating"]
            print(f"      {hm['price']} | 总清算={hm['total_liq']} | 评级={rating_str}")

    # ============ 7. 大单综合确认 ============
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
        print(f"\n  大单确认: 现货品种不支持，跳过")
    else:
        print(f"\n  大单确认: {'✅ 已确认' if big_order_confirm['confirmed'] else '❌ 未确认'} (得分: {big_order_confirm['score']})")
        for reason in big_order_confirm["reasons"]:
            print(f"    · {reason}")

    # ============ 7. 信号生成 ============
    _update_progress(message=f"{name} 信号生成中...")
    print(f"\n🔔 信号生成...")

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

    signal_emoji = {"LONG": "🟢 做多", "SHORT": "🔴 做空", "NEUTRAL": "🟡 观望"}
    print(f"  信号方向: {signal_emoji.get(signal['direction'], signal['direction'])}")
    if signal["direction"] != "NEUTRAL":
        print(f"  入场价: {signal['entry_price']}")
        print(f"  止损: {signal['stop_loss']}")
        print(f"  止盈: {signal['take_profit']}")
        print(f"  盈亏比: {signal['risk_reward_ratio']}:1")

    print(f"\n  Checklist:")
    checklist_labels = {
        "ema_trend": "EMA趋势方向",
        "in_support_zone": "价格在支撑区间",
        "in_resistance_zone": "价格在阻力区间",
        "bullish_candle": "1H多头反转K线",
        "bearish_candle": "1H空头反转K线",
        "big_order_confirm": "大单确认",
        "funding_rate_ok": "资金费率正常"
    }
    for k, v in signal["checklist"].items():
        label = checklist_labels.get(k, k)
        icon = "✅" if v else "❌"
        print(f"    {icon} {label}")

    # ============ 准备图表数据 ============
    # 多周期K线数据
    def df_to_candles_json(df):
        """将DataFrame转为Lightweight Charts所需的JSON"""
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

    # EMA序列：基于1H/4H/1D各自的close直接计算（对齐各周期时间轴）
    from ema_analyzer import calc_ema

    def compute_ema_json(df, period=144):
        """基于DataFrame的close计算EMA，返回JSON序列"""
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

    # 清理trend中不可序列化的数据
    trend_clean = {
        "trend": trend["trend"],
        "ema144": trend["ema144"],
        "ema169": trend["ema169"],
        "separation_pct": trend["separation_pct"],
        "trend_strength": trend["trend_strength"]
    }

    # 清理signal中不可序列化的数据
    signal_clean = {
        "direction": signal["direction"],
        "entry_price": signal["entry_price"],
        "stop_loss": signal["stop_loss"],
        "take_profit": signal["take_profit"],
        "reasons": signal["reasons"],
        "checklist": signal["checklist"],
        "risk_reward_ratio": signal["risk_reward_ratio"]
    }

    # 清理sr_zones（包含resonance字段）
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

    # 清理sr_scored（包含is_liquidation/is_order_wall字段）
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

    # 清理vp_result
    vp_result_clean = {
        "poc": vp_result.get("poc", 0),
        "va_high": vp_result.get("va_high", 0),
        "va_low": vp_result.get("va_low", 0),
        "high_volume_nodes": vp_result.get("high_volume_nodes", []),
        "bins": vp_result.get("bins", [])
    }

    # 清理liquidation_data
    liquidation_data_clean = []
    for lq in liquidation_data:
        liquidation_data_clean.append({
            "price": lq["price"],
            "volume": lq["volume"],
            "side": lq["side"]
        })

    # 清理order_walls
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
        # 多周期K线数据
        "candles_1h_json": candles_1h_json,
        "candles_4h_json": candles_4h_json,
        "candles_1d_json": candles_1d_json,
        # 多周期EMA数据
        "ema144_1h_json": ema144_1h_json,
        "ema169_1h_json": ema169_1h_json,
        "ema144_4h_json": ema144_4h_json,
        "ema169_4h_json": ema169_4h_json,
        "ema144_1d_json": ema144_1d_json,
        "ema169_1d_json": ema169_1d_json,
        # 分析数据
        "vp_result": vp_result_clean,
        "liquidation_data": liquidation_data_clean,
        "order_walls": order_walls_clean,
        "heatmap_data": heatmap_data,
    }


def print_db_status(instruments: list):
    """打印数据库状态"""
    global _db_manager
    db = _get_db_manager()

    db_path = db.db_path
    print(f"\n📦 本地数据库: {db_path}")

    for inst in instruments:
        config = INSTRUMENTS_CONFIG.get(inst)
        if not config:
            continue
        inst_id = config["inst_id"]
        for bar in ["1D", "4H", "1H"]:
            count = db.get_candle_count(inst_id, bar)
            count_str = f"{count} 根" if count > 0 else "0 根 (新数据库)"
            print(f"  {inst} {bar}: {count_str}")


def main():
    """主函数"""
    # 授权验证（无效/过期/未激活则弹窗并退出）
    try:
        from license_manager import check_license_on_startup
        check_license_on_startup()
    except ImportError as e:
        import tkinter.messagebox as _mb
        _mb.showerror("启动错误", f"缺少必要依赖，请先运行:\npip install pycryptodome\n\n错误: {e}")
        sys.exit(1)
    except Exception as e:
        import tkinter.messagebox as _mb
        _mb.showerror("启动错误", f"授权验证异常:\n{e}")
        sys.exit(1)

    parser = argparse.ArgumentParser(description="OKX交易信号分析工具")
    parser.add_argument(
        "--instruments",
        nargs="+",
        default=["BTC", "ETH", "XAU", "XAG", "BTC_SPOT", "ETH_SPOT"],
        choices=["BTC", "ETH", "XAU", "XAG", "BTC_SPOT", "ETH_SPOT"],
        help="要分析的品种，默认全部"
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="强制从API全量刷新（忽略本地缓存）"
    )
    parser.add_argument(
        "--db-path",
        type=str,
        default=None,
        help="指定数据库路径（默认 ./data/trading.db）"
    )
    args = parser.parse_args()

    # 初始化数据库
    db_path = Path(args.db_path) if args.db_path else None
    global _db_manager
    _db_manager = DBManager(db_path)

    print("=" * 60)
    print("OKX 交易信号分析系统 V7")
    print(f"分析时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"分析品种: {', '.join(args.instruments)}")
    if args.refresh:
        print(f"模式: 强制全量刷新")
    print("=" * 60)

    # 打印数据库状态
    print_db_status(args.instruments)

    # ---- 先启动HTTP服务器 ----
    output_dir = str(Path(__file__).parent)
    server, base_url, lan_url = start_server(port=8080, output_dir=output_dir)
    print(f"HTTP服务器已启动: {base_url}")
    if lan_url:
        print(f"局域网访问地址:   {lan_url}")
        _ensure_firewall_rule(port=8080)
    else:
        print("(未能获取局域网IP，仅可本机访问)")

    # ---- 生成加载页面HTML ----
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
  <h2>OKX 交易信号分析系统</h2>
  <div class="sub" id="inst-info">正在初始化...</div>
  <div class="pbar"><div class="pbar-fill" id="pbar"></div></div>
  <div id="msg">准备连接API...</div>
  <div class="tasks" id="tasks"></div>
  <div class="fail-panel" id="fail-panel">
    <h4>分析失败</h4>
    <div class="fail-list" id="fail-list"></div>
    <div class="fail-reason" id="fail-reason"></div>
  </div>
  <div class="retry-hint" id="retry-hint">请检查网络连接后重启程序重试</div>
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

    // 任务标签
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
      let isFail = d.error || (d.message && d.message.indexOf("失败")>=0);
      if(isFail){
        // 失败状态：红色进度条 + 显示失败面板
        pbar.style.background = "#f85149";
        m.style.color = "#f85149";
        // 显示失败面板
        if(d.failed_instruments && d.failed_instruments.length>0){
          failPanel.style.display = "block";
          failList.innerHTML = "失败品种：" + d.failed_instruments.map(function(x){
            let labels = {"BTC":"比特币","ETH":"以太坊","XAU":"黄金","XAG":"白银","BTC_SPOT":"比特币现货","ETH_SPOT":"以太坊现货"};
            return labels[x] || x;
          }).join("、");
        }
        if(d.error){
          failPanel.style.display = "block";
          failReason.textContent = d.error;
        }
        retryHint.style.display = "block";
      } else {
        // 成功状态：绿色进度条
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

    # ---- 尝试用pywebview打开桌面窗口 ----
    use_webview = False
    try:
        import webview
        use_webview = True
        print(f"检测到pywebview，使用桌面窗口模式")
    except ImportError:
        print(f"pywebview未安装，使用浏览器模式")

    if use_webview:
        # 先弹出加载页面
        window = webview.create_window(
            title="OKX 交易信号分析系统",
            url=f"{base_url}/_loading.html",
            width=1600,
            height=950,
            min_size=(1200, 700),
            resizable=True,
        )

        # 在后台线程中执行分析
        analysis_result = {"results": None, "failed": [], "error": None}

        def run_analysis():
            """后台线程：执行数据分析"""
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
                            print(f"  {inst}: 分析返回空结果")
                    except Exception as e:
                        failed_instruments.append(inst)
                        logger.error(f"分析 {inst} 时出错: {e}", exc_info=True)

                analysis_result["results"] = results
                analysis_result["failed"] = failed_instruments

                if not results:
                    analysis_result["error"] = "没有成功分析任何品种"
                    _update_progress(
                        stage="done",
                        message="分析失败",
                        failed_instruments=failed_instruments,
                        error="所有品种分析均失败，请检查网络连接或API是否可用"
                    )
                    return

                # 生成HTML报告
                _update_progress(stage="rendering", message="生成HTML报告中...")
                print(f"\n生成HTML报告...")
                html_content = build_html(results)

                output_file = Path(output_dir) / f"trading_signal_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
                output_file.write_text(html_content, encoding="utf-8")
                _cleanup_old_reports(output_dir)
                _update_progress(
                    stage="done",
                    message=f"报告生成完毕（成功 {len(results)} 个，失败 {len(failed_instruments)} 个）" if failed_instruments else "报告生成完毕",
                    failed_instruments=failed_instruments
                )
                print(f"报告已生成: {output_file}")

                # 汇总信号到控制台
                print(f"\n信号汇总")
                for r in results:
                    sig = r["signal"]
                    trend = r["trend"]
                    inst = r["instrument"]
                    direction = sig["direction"]
                    print(f"  {inst}: {direction} | 趋势 {trend['trend']} | 分离度 {trend['separation_pct']}%")
                    if direction != "NEUTRAL":
                        print(f"      入场: {sig['entry_price']} | 止损: {sig['stop_loss']} | 止盈: {sig['take_profit']}")

                print(f"\n以上分析仅供参考，不构成投资建议！")

            except Exception as e:
                analysis_result["error"] = str(e)
                logger.error(f"分析过程出错: {e}", exc_info=True)
                _update_progress(
                    stage="done",
                    message="分析失败",
                    failed_instruments=analysis_result["failed"],
                    error=f"程序运行异常: {str(e)}"
                )

        def on_closing():
            """关闭窗口时清理资源：停止刷新 → 等待分析完成 → 关闭DB → 关闭服务器"""
            print(f"关闭窗口，清理资源...")
            _stop_live_refresh()
            _stop_candle_refresh()
            # 等待正在进行的分析完成（最长60s），防止DB在使用中被关闭
            if _analysis_lock.acquire(timeout=60):
                logger.info("分析已完成，开始清理数据库")
                _analysis_lock.release()
            else:
                logger.warning("分析未在60s内完成，强制关闭数据库")
            if _db_manager is not None:
                try:
                    _db_manager.checkpoint()
                except Exception:
                    pass
                _db_manager.close()
                print(f"数据库已关闭")
            server.shutdown()
            print(f"资源清理完成")

        window.events.closing += on_closing

        # 启动webview事件循环（在主线程），分析在子线程
        import time as _time

        def start_analysis_after_delay():
            """延迟2秒后开始分析（等窗口加载完）"""
            _time.sleep(2)
            run_analysis()
            # 分析完成后切换到报告页面
            if analysis_result["results"]:
                # 找到最新的HTML文件
                html_files = [
                    f for f in os.listdir(output_dir)
                    if f.startswith('trading_signal_') and f.endswith('.html')
                ]
                if html_files:
                    html_files.sort(reverse=True)
                    report_url = f"{base_url}/{html_files[0]}"
                    print(f"切换到报告页面: {report_url}")
                    window.load_url(report_url)
                # 启动实时数据5分钟刷新（订单簿+清算）
                _start_live_refresh(list(INSTRUMENTS_CONFIG.values()), interval=300)
                # 启动K线定时增量刷新

                def _on_candle_updated():
                    """K线数据更新后的回调：重新分析→生成新报告→更新webview
                    注意：调用方 refresh_loop 已持有 _analysis_lock，此处不再获取"""
                    try:
                        logger.info("K线数据更新，触发重新分析...")
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
                            print(f"报告已刷新: {output_file}")
                            window.load_url(report_url)
                    except Exception as e:
                        logger.error(f"刷新报告失败: {e}")

                _start_candle_refresh(list(INSTRUMENTS_CONFIG.values()),
                                      on_data_updated=_on_candle_updated)

        analysis_thread = threading.Thread(target=start_analysis_after_delay, daemon=True)
        analysis_thread.start()

        webview.start(debug=False)
        print(f"已停止")

    else:
        # ---- 浏览器模式：先分析，再打开 ----
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
                    print(f"  {inst}: 分析返回空结果")
            except Exception as e:
                failed_instruments.append(inst)
                logger.error(f"分析 {inst} 时出错: {e}", exc_info=True)

        if failed_instruments:
            print(f"\n以下品种分析失败: {', '.join(failed_instruments)}")
            print(f"成功: {len(results)} 个, 失败: {len(failed_instruments)} 个")

        if not results:
            print("\n没有成功分析任何品种")
            _update_progress(
                stage="done",
                message="分析失败",
                failed_instruments=failed_instruments,
                error="所有品种分析均失败，请检查网络连接或API是否可用"
            )
            server.shutdown()
            if _db_manager is not None:
                _db_manager.close()
            return

        # 生成HTML报告
        _update_progress(stage="rendering", message="生成HTML报告中...")
        print(f"\n生成HTML报告...")
        html_content = build_html(results)

        output_file = Path(output_dir) / f"trading_signal_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
        output_file.write_text(html_content, encoding="utf-8")

        _update_progress(
            stage="done",
            message=f"报告生成完毕（成功 {len(results)} 个，失败 {len(failed_instruments)} 个）" if failed_instruments else "报告生成完毕",
            failed_instruments=failed_instruments
        )
        print(f"报告已生成: {output_file}")
        print(f"用浏览器打开查看: file:///{output_file}")

        # 汇总信号
        print(f"\n信号汇总")
        for r in results:
            sig = r["signal"]
            trend = r["trend"]
            inst = r["instrument"]
            direction = sig["direction"]
            emoji = {"LONG": "做多", "SHORT": "做空", "NEUTRAL": "中性"}.get(direction, "?")
            print(f"  {inst}: {emoji} | 趋势 {trend['trend']} | 分离度 {trend['separation_pct']}%")
            if direction != "NEUTRAL":
                print(f"      入场: {sig['entry_price']} | 止损: {sig['stop_loss']} | 止盈: {sig['take_profit']} | 盈亏比: {sig['risk_reward_ratio']}:1")

        print(f"\n以上分析仅供参考，不构成投资建议！")

        # 启动实时数据5分钟刷新
        _start_live_refresh(list(INSTRUMENTS_CONFIG.values()), interval=300)

        # 打开浏览器
        import time as _time
        browser_opened = False
        try:
            webbrowser.open(base_url)
            browser_opened = True
            print(f"浏览器已打开: {base_url}")
        except Exception as e:
            print(f"webbrowser打开失败: {e}")

        if not browser_opened and os.name == 'nt':
            try:
                os.startfile(base_url)
                print(f"浏览器已打开(os.startfile): {base_url}")
            except Exception as e:
                print(f"os.startfile打开失败: {e}")

        print(f"如果浏览器未自动打开，请手动访问: {base_url}")
        print(f"按 Ctrl+C 停止")

        try:
            while True:
                _time.sleep(1)
        except KeyboardInterrupt:
            _stop_live_refresh()
            if _db_manager is not None:
                _db_manager.close()
                print(f"数据库已关闭")
            server.shutdown()
            print(f"已停止")


if __name__ == "__main__":
    main()
