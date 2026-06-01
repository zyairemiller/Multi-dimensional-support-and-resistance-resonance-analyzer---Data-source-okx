"""
OKX 数据获取模块 - 通过OKX公开API获取市场数据
无需API Key，使用公开端点
支持 v2rayN/clash SOCKS5 代理（PySocks monkey-patch 方式）
"""

import os
import time
import logging
import pandas as pd
import numpy as np
from typing import Optional

logger = logging.getLogger(__name__)

# ========== SOCKS5 代理配置 ==========
# 检测并使用本地代理（v2rayN/clash 默认端口）
_USE_PROXY = False
_SOCKS5_HOST = "127.0.0.1"
_SOCKS5_PORT = 10808
_ORIG_SOCKET = None  # 保存原始 socket 类，用于直连回退

def _init_proxy():
    """初始化 SOCKS5 代理（全局 monkey-patch socket）"""
    global _USE_PROXY, _ORIG_SOCKET
    # 清除可能冲突的环境变量
    for key in ['HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy']:
        os.environ.pop(key, None)

    try:
        import socks
        # 检测代理是否可用
        import socket as _socket
        _ORIG_SOCKET = _socket.socket  # 在 monkey-patch 之前保存原始引用
        s = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
        s.settimeout(2)
        if s.connect_ex((_SOCKS5_HOST, _SOCKS5_PORT)) == 0:
            socks.set_default_proxy(socks.SOCKS5, _SOCKS5_HOST, _SOCKS5_PORT)
            _socket.socket = socks.socksocket
            _USE_PROXY = True
            logger.info(f"SOCKS5 代理已启用: {_SOCKS5_HOST}:{_SOCKS5_PORT}")
        s.close()
    except ImportError:
        logger.warning("PySocks 未安装，无法使用 SOCKS5 代理。安装: pip install PySocks")
    except Exception as e:
        logger.warning(f"代理初始化失败: {e}")

_init_proxy()

# ========== 配置 ==========

# OKX API 主域名 + 备用域名
BASE_URL = "https://www.okx.com"
BASE_URL_BACKUP = "https://www.okx.cab"  # 备用域名，CDN 不同，国内代理更友好

# 请求间隔（秒），避免限流
REQUEST_INTERVAL = 0.5

# 重试配置
MAX_RETRIES = 5
RETRY_BASE_DELAY = 0.5

# 现货品种的inst_id集合
SPOT_INST_IDS = {"BTC-USDT", "ETH-USDT"}

# 代理回退：如果代理失败，是否尝试直连
FALLBACK_DIRECT = True


def _is_spot(inst_id: str) -> bool:
    """判断产品ID是否为现货品种"""
    return inst_id in SPOT_INST_IDS


def _safe_float(val, default=0.0):
    """安全转换浮点数，空字符串返回默认值"""
    if val is None or val == "":
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def _http_get_json(host: str, url_path: str, params: dict, timeout: int = 15) -> Optional[dict]:
    """核心 HTTP GET → JSON data（单次链路尝试）"""
    import ssl
    import json
    from urllib.parse import urlencode as _urlencode

    path = url_path
    if params:
        path += "?" + _urlencode(params)

    for attempt in range(MAX_RETRIES):
        sock = None
        ss = None
        try:
            import socket
            sock = socket.create_connection((host, 443), timeout=timeout)

            ctx = ssl.create_default_context()
            ss = ctx.wrap_socket(sock, server_hostname=host)
            ss.settimeout(timeout)

            req = (
                f"GET {path} HTTP/1.1\r\n"
                f"Host: {host}\r\n"
                f"User-Agent: Mozilla/5.0\r\n"
                f"Accept: */*\r\n"
                f"Connection: close\r\n"
                f"\r\n"
            )
            ss.sendall(req.encode())

            raw = b""
            while True:
                try:
                    chunk = ss.recv(65536)
                    if not chunk:
                        break
                    raw += chunk
                except Exception:
                    break
            ss.close()
            ss = None
            sock = None

            header_end = raw.find(b"\r\n\r\n")
            if header_end == -1:
                logger.warning(f"{host}: 响应解析失败, attempt {attempt+1}/{MAX_RETRIES}")
                time.sleep(RETRY_BASE_DELAY * (2 ** attempt))
                continue

            headers_text = raw[:header_end].decode("utf-8", errors="replace")
            body = raw[header_end + 4:]

            is_chunked = any(
                line.lower().startswith("transfer-encoding:") and "chunked" in line.lower()
                for line in headers_text.split("\r\n")
            )
            if is_chunked:
                decoded = b""
                pos = 0
                while pos < len(body):
                    crlf = body.find(b"\r\n", pos)
                    if crlf == -1:
                        break
                    try:
                        chunk_size = int(body[pos:crlf], 16)
                    except ValueError:
                        break
                    if chunk_size == 0:
                        break
                    pos = crlf + 2
                    decoded += body[pos:pos + chunk_size]
                    pos += chunk_size + 2
                body = decoded

            if not is_chunked:
                for line in headers_text.split("\r\n"):
                    if line.lower().startswith("content-length:"):
                        try:
                            cl = int(line.split(":")[1].strip())
                            body = body[:cl]
                        except ValueError:
                            pass
                        break

            status_line = headers_text.split("\r\n")[0]
            try:
                code = int(status_line.split()[1])
            except (IndexError, ValueError):
                code = 0

            if code == 200:
                data = json.loads(body.decode("utf-8"))
                if data.get("code") == "0":
                    return data.get("data", [])
                else:
                    logger.warning(f"{host}: API错误 code={data.get('code')} msg={data.get('msg')}")
                    time.sleep(RETRY_BASE_DELAY)
                    continue
            elif code == 429:
                delay = RETRY_BASE_DELAY * (4 ** attempt)
                logger.warning(f"{host}: 限流 429, {delay:.0f}s后重试")
                time.sleep(delay)
                continue
            else:
                logger.warning(f"{host}: HTTP {code}, attempt {attempt+1}/{MAX_RETRIES}")
                time.sleep(RETRY_BASE_DELAY)

        except Exception as e:
            delay = RETRY_BASE_DELAY * (2 ** attempt) if not isinstance(e, (ssl.SSLEOFError, ssl.SSLError, OSError, BrokenPipeError, ConnectionResetError)) else RETRY_BASE_DELAY * (attempt + 1)
            logger.warning(f"{host}: {type(e).__name__}, {delay:.1f}s后重试 ({attempt+1}/{MAX_RETRIES})")
            time.sleep(delay)
        finally:
            for s in [ss, sock]:
                if s is not None:
                    try:
                        s.close()
                    except Exception:
                        pass

    return None


def _make_request(url_path: str, params: dict, timeout: int = 15) -> Optional[dict]:
    """
    发送 HTTPS GET 请求，带多重回退：
    1. 代理 + okx.com  2. 代理 + okx.cab  3. 直连 + okx.com
    """
    import socket as _socket

    hosts = [
        ("www.okx.com", "主域名(代理)"),
    ]
    if BASE_URL_BACKUP:
        hosts.append((BASE_URL_BACKUP.replace("https://", "").replace("http://", ""), "备域名(代理)"))

    for host, label in hosts:
        result = _http_get_json(host, url_path, params, timeout)
        if result is not None:
            return result
        logger.warning(f"{label} 失败，切换中...")
        time.sleep(0.5)

    # 直连兜底（临时关闭代理）
    if FALLBACK_DIRECT and _ORIG_SOCKET is not None:
        _socket.socket = _ORIG_SOCKET  # 恢复原始 socket（不走代理）

        logger.info("切换到直连尝试...")
        result = _http_get_json("www.okx.com", url_path, params, timeout)

        # 恢复代理 socket
        if _USE_PROXY:
            try:
                import socks
                _socket.socket = socks.socksocket
            except Exception:
                pass

        if result is not None:
            logger.info("直连成功，恢复代理")
            return result

    logger.error(f"所有链路均失败: {url_path}")
    return None


def fetch_candles(inst_id: str, bar: str = "1D", limit: int = 300, after: str = None, before: str = None) -> pd.DataFrame:
    """
    获取K线数据

    Args:
        inst_id: 产品ID，如 BTC-USDT-SWAP
        bar: K线周期，如 1D, 4H, 1H
        limit: 数据条数，最大300
        after: 分页参数，请求此时间戳之前的数据（往历史方向翻页）
        before: 分页参数，请求此时间戳之后的数据（获取增量更新）

    Returns:
        DataFrame，列: ts, open, high, low, close, vol, volCcy
    """
    params = {
        "instId": inst_id,
        "bar": bar,
        "limit": str(limit)
    }
    if after is not None:
        params["after"] = after
    if before is not None:
        params["before"] = before

    time.sleep(REQUEST_INTERVAL)
    data = _make_request("/api/v5/market/candles", params)

    if data is None or len(data) == 0:
        logger.warning(f"获取K线数据失败: {inst_id} {bar}")
        return pd.DataFrame()

    df = pd.DataFrame(data, columns=["ts", "open", "high", "low", "close", "vol", "volCcy", "volCcyQuote", "confirm"])
    # 保留 confirm 字段：OKX API 标记 K线是否已闭合 ("0"=未闭合, "1"=已闭合)
    df = df[["ts", "open", "high", "low", "close", "vol", "volCcy", "confirm"]]

    # 转换数据类型
    for col in ["open", "high", "low", "close", "vol", "volCcy"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df["ts"] = pd.to_datetime(df["ts"].astype(float), unit="ms")
    df = df.sort_values("ts").reset_index(drop=True)

    return df


def _extract_earliest_ts(data: list) -> int:
    """
    从OKX标准K线数组（每行 [ts, open, high, low, close, ...]）中提取最早的时间戳

    Args:
        data: OKX K线原始数据列表，每项第一列为ts（毫秒字符串）

    Returns:
        最早的时间戳（毫秒），提取失败返回 None
    """
    try:
        earliest = None
        for item in data:
            item_ts = item[0]
            if item_ts:
                ts_int = int(float(item_ts))
                if earliest is None or ts_int < earliest:
                    earliest = ts_int
        return earliest
    except (IndexError, ValueError, TypeError):
        return None


def _fetch_history_candles_batch(inst_id: str, bar: str, total: int, after_ts: int) -> list:
    """
    从 /api/v5/market/history-candles 端点分页获取历史K线

    该端点特点：
    - 数据有至少2天延迟
    - 最多回溯约3个月
    - 单次最多100根
    - after参数：返回 ts <= after 的数据（往历史方向）

    Args:
        inst_id: 产品ID
        bar: K线周期
        total: 需要的总条数
        after_ts: 分页起始时间戳（毫秒），获取早于此时刻的数据

    Returns:
        OKX原始K线数据列表，每项为 [ts, open, high, low, close, vol, volCcy, volCcyQuote, confirm]
    """
    all_data = []
    remaining = total
    batch_limit = 100

    for batch_idx in range((total + batch_limit - 1) // batch_limit):
        fetch_count = min(batch_limit, remaining)

        params = {
            "instId": inst_id,
            "bar": bar,
            "limit": str(fetch_count)
        }
        if after_ts is not None:
            params["after"] = str(after_ts)

        if batch_idx > 0:
            time.sleep(0.15)

        data = _make_request("/api/v5/market/history-candles", params)

        if data is None or len(data) == 0:
            logger.info(f"历史端点: 第{batch_idx + 1}批无数据（已达3个月回溯上限或品种不支持）")
            break

        all_data.extend(data)
        remaining -= len(data)

        earliest_ts = _extract_earliest_ts(data)
        if earliest_ts is None:
            break

        # -1ms 跳过边界，避免与上一批末尾重叠
        after_ts = earliest_ts - 1

        if len(data) < fetch_count:
            break

    return all_data


def fetch_candles_history(inst_id: str, bar: str = "1D", total: int = 1000, before_ts: int = None) -> pd.DataFrame:
    """
    分页获取大量历史K线数据（两阶段策略，突破常规端点1440根限制）

    阶段1：/api/v5/market/candles（常规端点）
           - 从最新（或before_ts）开始往回拉，最多1440根
           - 单次300根，分页用after参数翻页
    阶段2：/api/v5/market/history-candles（历史端点，阶段1数据不足时触发）
           - 以阶段1最早K线的 ts-1ms 作为起点，向前回溯
           - 数据有~2天延迟，最多回溯~3个月，单次100根

    对齐策略：阶段2的 after = 阶段1最早的ts - 1ms
    → 阶段2返回 ts < 阶段1最早ts 的数据 → 两条数据链在边界无缝衔接

    Args:
        inst_id: 产品ID
        bar: K线周期
        total: 需要的总条数
        before_ts: 截止时间戳（毫秒），只获取早于此时间的数据；None则从最新开始

    Returns:
        DataFrame，列: ts, open, high, low, close, vol, volCcy
    """
    all_data = []
    after_ts = before_ts
    remaining = total

    if before_ts is not None:
        logger.info(f"分页获取历史K线（补缺口模式）: {inst_id} {bar} 目标 {total} 条, before_ts={before_ts}")
    else:
        logger.info(f"分页获取历史K线: {inst_id} {bar} 目标 {total} 条")

    # ========== 阶段1：常规端点（最多1440根） ==========
    phase1_limit = min(remaining, 1440)
    phase1_count = 0

    for batch_idx in range((phase1_limit + 299) // 300):
        fetch_count = min(300, phase1_limit - phase1_count)
        if fetch_count <= 0:
            break

        params = {
            "instId": inst_id,
            "bar": bar,
            "limit": str(fetch_count)
        }
        if after_ts is not None:
            params["after"] = str(after_ts)

        if batch_idx > 0:
            time.sleep(REQUEST_INTERVAL)

        data = _make_request("/api/v5/market/candles", params)

        if data is None or len(data) == 0:
            logger.info(f"常规端点: 第{batch_idx + 1}批无数据")
            break

        all_data.extend(data)
        phase1_count += len(data)

        earliest_ts = _extract_earliest_ts(data)
        if earliest_ts is None:
            break
        after_ts = earliest_ts - 1

        if len(data) < fetch_count:
            break

    remaining = total - len(all_data)

    # ========== 阶段2：历史端点（补充1440根之外的更早数据） ==========
    if remaining > 0 and len(all_data) > 0:
        boundary_ts = _extract_earliest_ts(all_data)
        if boundary_ts:
            logger.info(f"常规端点获取 {len(all_data)} 根，补给线 ts={boundary_ts}，还需 {remaining} 根 → 切换历史端点")
            after_ts = boundary_ts - 1  # 对齐关键：从边界 -1ms 开始，无缝衔接
            history_data = _fetch_history_candles_batch(inst_id, bar, remaining, after_ts)
            all_data.extend(history_data)
            logger.info(f"历史端点补充 {len(history_data)} 根")

    # ========== 兜底 ==========
    if not all_data:
        logger.warning(f"分页获取历史K线数据为空: {inst_id} {bar}")
        return pd.DataFrame()

    # ========== 构建DataFrame ==========
    df = pd.DataFrame(all_data, columns=["ts", "open", "high", "low", "close", "vol", "volCcy", "volCcyQuote", "confirm"])
    df = df[["ts", "open", "high", "low", "close", "vol", "volCcy", "confirm"]]

    for col in ["open", "high", "low", "close", "vol", "volCcy"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df["ts"] = df["ts"].astype(float).astype(int)
    df = df.drop_duplicates(subset=["ts"], keep="first")
    df["ts"] = pd.to_datetime(df["ts"], unit="ms")
    df = df.sort_values("ts").reset_index(drop=True)

    if len(df) > total:
        df = df.iloc[-total:].reset_index(drop=True)

    logger.info(f"两阶段获取完成: {inst_id} {bar} 共 {len(df)} 根（目标 {total}）")
    return df


def fetch_open_interest(inst_id: str) -> Optional[dict]:
    """
    获取持仓量

    现货品种不支持持仓量，直接返回空值

    Args:
        inst_id: 产品ID

    Returns:
        dict: {oi, oiCcy, ts} 或 None
    """
    # 现货品种不支持OI，直接返回空值
    if _is_spot(inst_id):
        logger.info(f"现货品种 {inst_id} 不支持持仓量，跳过")
        return None

    params = {"instId": inst_id}

    time.sleep(REQUEST_INTERVAL)
    data = _make_request("/api/v5/public/open-interest", params)

    if data is None or len(data) == 0:
        logger.warning(f"获取持仓量失败: {inst_id}")
        return None

    item = data[0]
    return {
        "oi": _safe_float(item.get("oi"), 0),
        "oiCcy": _safe_float(item.get("oiCcy"), 0),
        "ts": item.get("ts", "")
    }


def fetch_oi_history(inst_id: str, period: str = "5M", limit: int = 24) -> list:
    """
    获取历史持仓量数据（用于计算OI变化率）

    使用 OKX /api/v5/rubik/stat/contracts/open-interest-history 端点

    Args:
        inst_id: 产品ID（如 BTC-USDT-SWAP）
        period: 数据粒度（5M/1H/1D），默认 5M
        limit: 返回条数，最大 100，默认 24（约2小时5M数据或24小时1H数据）

    Returns:
        list of dict: [{oi, oiCcy, ts}, ...] 或空列表
    """
    # 现货品种不支持OI
    if _is_spot(inst_id):
        logger.info(f"现货品种 {inst_id} 不支持历史持仓量，跳过")
        return []

    params = {
        "instId": inst_id,
        "period": period,
        "limit": str(min(limit, 100))
    }

    time.sleep(REQUEST_INTERVAL)
    data = _make_request("/api/v5/rubik/stat/contracts/open-interest-history", params)

    if data is None or len(data) == 0:
        logger.warning(f"获取历史持仓量失败: {inst_id}")
        return []

    result = []
    for item in data:
        result.append({
            "oi": _safe_float(item.get("oi"), 0),
            "oiCcy": _safe_float(item.get("oiCcy"), 0),
            "ts": item.get("ts", "")
        })
    return result


def fetch_funding_rate(inst_id: str) -> Optional[dict]:
    """
    获取资金费率

    现货品种不支持资金费率，直接返回空值

    Args:
        inst_id: 产品ID

    Returns:
        dict: {fundingRate, fundingTime, nextFundingRate, nextFundingTime} 或 None
    """
    # 现货品种不支持资金费率，直接返回空值
    if _is_spot(inst_id):
        logger.info(f"现货品种 {inst_id} 不支持资金费率，跳过")
        return None

    params = {"instId": inst_id}

    time.sleep(REQUEST_INTERVAL)
    data = _make_request("/api/v5/public/funding-rate", params)

    if data is None or len(data) == 0:
        logger.warning(f"获取资金费率失败: {inst_id}")
        return None

    item = data[0]

    return {
        "fundingRate": _safe_float(item.get("fundingRate"), 0),
        "fundingTime": item.get("fundingTime", ""),
        "nextFundingRate": _safe_float(item.get("nextFundingRate"), 0),
        "nextFundingTime": item.get("nextFundingTime", "")
    }


def fetch_recent_trades(inst_id: str, limit: int = 100) -> list:
    """
    获取最近逐笔成交

    Args:
        inst_id: 产品ID
        limit: 数量，最大100

    Returns:
        list of dict: [{tradeId, px, sz, side, ts}, ...]
    """
    params = {
        "instId": inst_id,
        "limit": str(limit)
    }

    time.sleep(REQUEST_INTERVAL)
    data = _make_request("/api/v5/market/trades", params)

    if data is None or len(data) == 0:
        logger.warning(f"获取逐笔成交失败: {inst_id}")
        return []

    trades = []
    for item in data:
        trades.append({
            "tradeId": item.get("tradeId", ""),
            "px": _safe_float(item.get("px"), 0),
            "sz": _safe_float(item.get("sz"), 0),
            "side": item.get("side", ""),
            "ts": item.get("ts", "")
        })

    return trades


def fetch_trades_batch(inst_id: str, total: int = 500) -> list:
    """
    分批获取最近逐笔成交（支持分页，突破100条限制）

    现货品种不支持逐笔成交批量获取，直接返回空列表

    利用OKX分页：请求时传after参数（传上一次结果中最早的tradeId对应的ts）
    每次请求间隔0.5秒防限流

    Args:
        inst_id: 产品ID
        total: 需要获取的总条数，默认500

    Returns:
        list of dict: [{tradeId, px, sz, side, ts}, ...]
    """
    # 现货品种不支持trades批量获取，直接返回空列表
    if _is_spot(inst_id):
        logger.info(f"现货品种 {inst_id} 不支持逐笔成交批量获取，跳过")
        return []

    all_trades = []
    batch_size = 100
    remaining = total
    after_ts = None

    for batch_idx in range((total + batch_size - 1) // batch_size):
        fetch_count = min(batch_size, remaining)

        params = {
            "instId": inst_id,
            "limit": str(fetch_count)
        }
        if after_ts is not None:
            params["after"] = str(after_ts)

        # 每批次请求间隔0.5秒（第一跳除外）
        if batch_idx > 0:
            time.sleep(REQUEST_INTERVAL)

        data = _make_request("/api/v5/market/trades", params)

        if data is None or len(data) == 0:
            logger.warning(f"分批获取成交数据: 第{batch_idx+1}批无数据，停止分页")
            break

        batch_trades = []
        for item in data:
            trade = {
                "tradeId": item.get("tradeId", ""),
                "px": _safe_float(item.get("px"), 0),
                "sz": _safe_float(item.get("sz"), 0),
                "side": item.get("side", ""),
                "ts": item.get("ts", "")
            }
            batch_trades.append(trade)

        all_trades.extend(batch_trades)
        remaining -= len(batch_trades)

        # 设置分页游标：取本批次最早的ts
        if batch_trades:
            earliest_ts = batch_trades[-1].get("ts", "")
            if earliest_ts:
                after_ts = earliest_ts
            else:
                break
        else:
            break

        # 如果本批数据不足batch_size，说明已无更多数据
        if len(batch_trades) < fetch_count:
            break

    logger.info(f"分批获取成交数据: 共获取 {len(all_trades)} 笔 (目标 {total})")
    return all_trades


def fetch_liquidation_map(inst_id: str) -> list:
    """
    获取清算密集区数据

    现货品种不支持清算数据，直接返回空列表

    方法：用OKX持仓量历史 + 价格变化推算
    GET https://www.okx.com/api/v5/public/open-interest-history?instId=BTC-USDT-SWAP&period=5m

    当OI短时间内大幅下降（>2%）且价格同时大幅波动，说明有大面积清算发生
    结合当时的K线价格，标记清算密集价位

    Args:
        inst_id: 产品ID，如 BTC-USDT-SWAP

    Returns:
        list: [{price, volume, side}, ...]
        - price: 清算密集的价位
        - volume: 清算量（估算）
        - side: "long"或"short"（被清算的方向）
    """
    # 现货品种不支持清算数据，直接返回空列表
    if _is_spot(inst_id):
        logger.info(f"现货品种 {inst_id} 不支持清算密集区数据，跳过")
        return []

    try:
        # 1. 获取OI历史（5m级别，最近24小时）
        time.sleep(REQUEST_INTERVAL)
        params = {
            "instId": inst_id,
            "period": "5m"
        }
        oi_data = _make_request("/api/v5/rubik/stat/contracts/open-interest-history", params)

        if oi_data is None or len(oi_data) < 2:
            logger.warning(f"获取OI历史数据不足，无法推算清算密集区: {inst_id}")
            return []

        # 解析OI历史数据
        oi_history = []
        for item in oi_data:
            if isinstance(item, list) and len(item) >= 2:
                oi_history.append({
                    "ts": str(item[0]),
                    "oi": _safe_float(item[1], 0)
                })
            elif isinstance(item, dict):
                oi_history.append({
                    "ts": item.get("ts", ""),
                    "oi": _safe_float(item.get("oi"), 0)
                })

        oi_history.sort(key=lambda x: x["ts"])

        # 2. 获取1H K线数据（最近24小时），用于关联价格
        df_1h = fetch_candles(inst_id, "1H", 48)

        # 3. 找OI大幅下降(>2%)的时间点
        liquidation_levels = []

        for i in range(1, len(oi_history)):
            prev_oi = oi_history[i - 1]["oi"]
            curr_oi = oi_history[i]["oi"]

            if prev_oi <= 0:
                continue

            oi_change_pct = (curr_oi - prev_oi) / prev_oi * 100

            if oi_change_pct < -2.0:
                oi_ts_ms = int(oi_history[i]["ts"]) if oi_history[i]["ts"] else 0
                if oi_ts_ms == 0:
                    continue

                best_price = None
                best_price_change = 0.0

                for _, row in df_1h.iterrows():
                    candle_ts_ms = int(row["ts"].value // 1_000_000) if hasattr(row["ts"], "value") else 0
                    if candle_ts_ms == 0:
                        continue
                    if abs(candle_ts_ms - oi_ts_ms) < 3600000:
                        best_price = float(row["close"])
                        best_price_change = float(row["close"]) - float(row["open"])
                        break

                if best_price is None:
                    continue

                if best_price_change < 0:
                    side = "long"
                else:
                    side = "short"

                volume = abs(prev_oi - curr_oi)

                liquidation_levels.append({
                    "price": round(best_price, 2),
                    "volume": round(volume, 2),
                    "side": side
                })

        merged = _merge_liquidation_levels(liquidation_levels, 0.005)

        logger.info(f"清算密集区识别: {inst_id} 发现 {len(merged)} 个清算价位")
        return merged

    except Exception as e:
        logger.warning(f"获取清算密集区失败，不影响主流程: {e}")
        return []


def _merge_liquidation_levels(levels: list, threshold_pct: float) -> list:
    """
    合并相近价位的清算点

    Args:
        levels: 清算价位列表
        threshold_pct: 合并阈值百分比

    Returns:
        合并后的清算价位列表
    """
    if not levels:
        return levels

    sorted_levels = sorted(levels, key=lambda x: x["price"])
    merged = [sorted_levels[0].copy()]

    for level in sorted_levels[1:]:
        prev = merged[-1]
        if abs(level["price"] - prev["price"]) / prev["price"] < threshold_pct:
            total_vol = prev["volume"] + level["volume"]
            avg_price = (prev["price"] * prev["volume"] + level["price"] * level["volume"]) / total_vol
            merged[-1] = {
                "price": round(avg_price, 2),
                "volume": round(total_vol, 2),
                "side": prev["side"]
            }
        else:
            merged.append(level.copy())

    return merged


def fetch_mark_price(inst_id: str) -> Optional[float]:
    """
    获取标记价格

    现货品种不支持标记价格，直接返回None

    Args:
        inst_id: 产品ID

    Returns:
        标记价格 或 None
    """
    # 现货品种不支持标记价格，直接返回None
    if _is_spot(inst_id):
        logger.info(f"现货品种 {inst_id} 不支持标记价格，跳过")
        return None

    params = {"instId": inst_id}

    time.sleep(REQUEST_INTERVAL)
    data = _make_request("/api/v5/public/mark-price", params)

    if data is None or len(data) == 0:
        logger.warning(f"获取标记价格失败: {inst_id}")
        return None

    return _safe_float(data[0].get("markPx"), 0)


def fetch_orderbook(inst_id: str, depth: int = 200) -> dict:
    """
    获取OKX订单簿深度数据

    GET https://www.okx.com/api/v5/market/books?instId=BTC-USDT-SWAP&sz=200

    OKX返回格式: {"code":"0","data":[{"asks":[[price,size,num_orders,...],...],"bids":[[price,size,num_orders,...],...],"ts":"..."}]}

    Args:
        inst_id: 产品ID，如 BTC-USDT-SWAP
        depth: 深度档位数，最大200

    Returns:
        {
            "bids": [{"price": float, "size": float, "num_orders": int}, ...],
            "asks": [{"price": float, "size": float, "num_orders": int}, ...],
            "ts": int
        }
    """
    params = {
        "instId": inst_id,
        "sz": str(depth)
    }

    time.sleep(REQUEST_INTERVAL)
    data = _make_request("/api/v5/market/books", params)

    if data is None or len(data) == 0:
        logger.warning(f"获取订单簿数据失败: {inst_id}")
        return {"bids": [], "asks": [], "ts": 0}

    item = data[0]
    bids = []
    asks = []

    # 解析bids: [[price, size, num_orders, ...], ...]
    for bid in item.get("bids", []):
        if len(bid) >= 3:
            bids.append({
                "price": _safe_float(bid[0], 0),
                "size": _safe_float(bid[1], 0),
                "num_orders": int(_safe_float(bid[2], 0))
            })

    # 解析asks: [[price, size, num_orders, ...], ...]
    for ask in item.get("asks", []):
        if len(ask) >= 3:
            asks.append({
                "price": _safe_float(ask[0], 0),
                "size": _safe_float(ask[1], 0),
                "num_orders": int(_safe_float(ask[2], 0))
            })

    ts = int(_safe_float(item.get("ts", 0), 0))

    return {
        "bids": bids,
        "asks": asks,
        "ts": ts
    }


def detect_order_walls(orderbook: dict, inst_id: str, multiplier: float = 5.0) -> dict:
    """
    检测订单簿中的大额挂单墙

    算法：
    1. 计算所有档位size的均值和标准差
    2. 大单墙 = size > mean + multiplier * std 的档位
    3. 按size从大到小排序

    Args:
        orderbook: fetch_orderbook 返回的订单簿数据
        inst_id: 产品ID，用于日志
        multiplier: 标准差倍数阈值，默认5.0

    Returns:
        {
            "bid_walls": [{"price": float, "size": float, "strength": float}, ...],
            "ask_walls": [{"price": float, "size": float, "strength": float}, ...],
            "bid_total": float,
            "ask_total": float,
            "imbalance": float,   # 买卖比 bid_total/ask_total
        }
        strength = size / mean_size
    """
    bids = orderbook.get("bids", [])
    asks = orderbook.get("asks", [])

    # 如果订单簿为空，返回默认值
    if not bids and not asks:
        return {
            "bid_walls": [],
            "ask_walls": [],
            "bid_total": 0.0,
            "ask_total": 0.0,
            "imbalance": 1.0
        }

    # 合并所有档位的size用于计算统计量
    all_sizes = [b["size"] for b in bids] + [a["size"] for a in asks]

    if not all_sizes:
        return {
            "bid_walls": [],
            "ask_walls": [],
            "bid_total": 0.0,
            "ask_total": 0.0,
            "imbalance": 1.0
        }

    mean_size = float(np.mean(all_sizes))
    std_size = float(np.std(all_sizes))

    # 防止标准差为0（所有档位size相同时）
    if std_size == 0:
        std_size = mean_size * 0.1

    threshold = mean_size + multiplier * std_size

    # 检测买单墙
    bid_walls = []
    for b in bids:
        if b["size"] > threshold:
            bid_walls.append({
                "price": b["price"],
                "size": b["size"],
                "strength": round(b["size"] / mean_size, 1) if mean_size > 0 else 0.0
            })
    bid_walls.sort(key=lambda x: x["size"], reverse=True)

    # 检测卖单墙
    ask_walls = []
    for a in asks:
        if a["size"] > threshold:
            ask_walls.append({
                "price": a["price"],
                "size": a["size"],
                "strength": round(a["size"] / mean_size, 1) if mean_size > 0 else 0.0
            })
    ask_walls.sort(key=lambda x: x["size"], reverse=True)

    # 计算买卖总量和买卖比
    bid_total = sum(b["size"] for b in bids)
    ask_total = sum(a["size"] for a in asks)

    if ask_total > 0:
        imbalance = round(bid_total / ask_total, 3)
    elif bid_total > 0:
        imbalance = 99.0
    else:
        imbalance = 1.0

    logger.info(f"订单墙检测: {inst_id} 买单墙={len(bid_walls)}个, 卖单墙={len(ask_walls)}个, 买卖比={imbalance}")

    return {
        "bid_walls": bid_walls,
        "ask_walls": ask_walls,
        "bid_total": round(bid_total, 4),
        "ask_total": round(ask_total, 4),
        "imbalance": imbalance
    }



