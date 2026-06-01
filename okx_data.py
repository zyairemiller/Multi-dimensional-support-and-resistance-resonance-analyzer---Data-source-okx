"""
OKX Data Fetching Module - Fetch market data via OKX public API
No API Key required, uses public endpoints
Supports v2rayN/clash SOCKS5 proxy (PySocks monkey-patch method)
"""

import os
import time
import logging
import pandas as pd
import numpy as np
from typing import Optional

logger = logging.getLogger(__name__)

# ========== SOCKS5 Proxy Configuration ==========
_USE_PROXY = False
_SOCKS5_HOST = "127.0.0.1"
_SOCKS5_PORT = 10808
_ORIG_SOCKET = None

def _init_proxy():
    """Initialize SOCKS5 proxy (global monkey-patch socket)"""
    global _USE_PROXY, _ORIG_SOCKET
    for key in ['HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy']:
        os.environ.pop(key, None)

    try:
        import socks
        import socket as _socket
        _ORIG_SOCKET = _socket.socket
        s = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
        s.settimeout(2)
        if s.connect_ex((_SOCKS5_HOST, _SOCKS5_PORT)) == 0:
            socks.set_default_proxy(socks.SOCKS5, _SOCKS5_HOST, _SOCKS5_PORT)
            _socket.socket = socks.socksocket
            _USE_PROXY = True
            logger.info(f"SOCKS5 proxy enabled: {_SOCKS5_HOST}:{_SOCKS5_PORT}")
        s.close()
    except ImportError:
        logger.warning("PySocks not installed, cannot use SOCKS5 proxy. Install: pip install PySocks")
    except Exception as e:
        logger.warning(f"Proxy initialization failed: {e}")

_init_proxy()

# ========== Configuration ==========

BASE_URL = "https://www.okx.com"
BASE_URL_BACKUP = "https://www.okx.cab"
REQUEST_INTERVAL = 0.5
MAX_RETRIES = 5
RETRY_BASE_DELAY = 0.5
SPOT_INST_IDS = {"BTC-USDT", "ETH-USDT"}
FALLBACK_DIRECT = True


def _is_spot(inst_id: str) -> bool:
    """Check if instrument ID is a spot instrument"""
    return inst_id in SPOT_INST_IDS


def _safe_float(val, default=0.0):
    """Safely convert to float, return default for empty string"""
    if val is None or val == "":
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def _http_get_json(host: str, url_path: str, params: dict, timeout: int = 15) -> Optional[dict]:
    """Core HTTP GET -> JSON data (single link attempt)"""
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
                logger.warning(f"{host}: Response parse failed, attempt {attempt+1}/{MAX_RETRIES}")
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
                    logger.warning(f"{host}: API error code={data.get('code')} msg={data.get('msg')}")
                    time.sleep(RETRY_BASE_DELAY)
                    continue
            elif code == 429:
                delay = RETRY_BASE_DELAY * (4 ** attempt)
                logger.warning(f"{host}: Rate limited 429, retry in {delay:.0f}s")
                time.sleep(delay)
                continue
            else:
                logger.warning(f"{host}: HTTP {code}, attempt {attempt+1}/{MAX_RETRIES}")
                time.sleep(RETRY_BASE_DELAY)

        except Exception as e:
            delay = RETRY_BASE_DELAY * (2 ** attempt) if not isinstance(e, (ssl.SSLEOFError, ssl.SSLError, OSError, BrokenPipeError, ConnectionResetError)) else RETRY_BASE_DELAY * (attempt + 1)
            logger.warning(f"{host}: {type(e).__name__}, retry in {delay:.1f}s ({attempt+1}/{MAX_RETRIES})")
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
    Send HTTPS GET request with multiple fallbacks:
    1. Proxy + okx.com  2. Proxy + okx.cab  3. Direct + okx.com
    """
    import socket as _socket

    hosts = [
        ("www.okx.com", "Main domain (proxy)"),
    ]
    if BASE_URL_BACKUP:
        hosts.append((BASE_URL_BACKUP.replace("https://", "").replace("http://", ""), "Backup domain (proxy)"))

    for host, label in hosts:
        result = _http_get_json(host, url_path, params, timeout)
        if result is not None:
            return result
        logger.warning(f"{label} failed, switching...")
        time.sleep(0.5)

    if FALLBACK_DIRECT and _ORIG_SOCKET is not None:
        _socket.socket = _ORIG_SOCKET
        logger.info("Switching to direct connection...")
        result = _http_get_json("www.okx.com", url_path, params, timeout)
        if _USE_PROXY:
            try:
                import socks
                _socket.socket = socks.socksocket
            except Exception:
                pass
        if result is not None:
            logger.info("Direct connection successful, proxy restored")
            return result

    logger.error(f"All links failed: {url_path}")
    return None


def fetch_candles(inst_id: str, bar: str = "1D", limit: int = 300, after: str = None, before: str = None) -> pd.DataFrame:
    """
    Fetch candlestick data

    Args:
        inst_id: Instrument ID, e.g. BTC-USDT-SWAP
        bar: Candle period, e.g. 1D, 4H, 1H
        limit: Number of data points, max 300
        after: Pagination parameter, request data before this timestamp (page backward)
        before: Pagination parameter, request data after this timestamp

    Returns:
        DataFrame, columns: ts, open, high, low, close, vol, volCcy
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
        logger.warning(f"Failed to fetch candle data: {inst_id} {bar}")
        return pd.DataFrame()

    df = pd.DataFrame(data, columns=["ts", "open", "high", "low", "close", "vol", "volCcy", "volCcyQuote", "confirm"])
    df = df[["ts", "open", "high", "low", "close", "vol", "volCcy", "confirm"]]

    for col in ["open", "high", "low", "close", "vol", "volCcy"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df["ts"] = pd.to_datetime(df["ts"].astype(float), unit="ms")
    df = df.sort_values("ts").reset_index(drop=True)

    return df


def _extract_earliest_ts(data: list) -> int:
    """
    Extract the earliest timestamp from OKX standard candle array

    Args:
        data: OKX candle raw data list

    Returns:
        Earliest timestamp (milliseconds), None if extraction fails
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
    Paginate historical candles from /api/v5/market/history-candles endpoint

    Endpoint characteristics:
    - Data has at least 2 days delay
    - Maximum lookback of about 3 months
    - Max 100 per request

    Args:
        inst_id: Instrument ID
        bar: Candle period
        total: Total number needed
        after_ts: Pagination start timestamp (milliseconds)

    Returns:
        OKX raw candle data list
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
            logger.info(f"History endpoint: batch {batch_idx + 1} has no data (3-month lookback limit reached or instrument not supported)")
            break

        all_data.extend(data)
        remaining -= len(data)

        earliest_ts = _extract_earliest_ts(data)
        if earliest_ts is None:
            break

        after_ts = earliest_ts - 1

        if len(data) < fetch_count:
            break

    return all_data


def fetch_candles_history(inst_id: str, bar: str = "1D", total: int = 1000, before_ts: int = None) -> pd.DataFrame:
    """
    Paginate large amounts of historical candle data (two-phase strategy, breaks 1440 limit)

    Phase 1: /api/v5/market/candles (regular endpoint, max 1440)
    Phase 2: /api/v5/market/history-candles (history endpoint, ~3 month lookback)

    Args:
        inst_id: Instrument ID
        bar: Candle period
        total: Total needed
        before_ts: Cutoff timestamp (milliseconds)

    Returns:
        DataFrame, columns: ts, open, high, low, close, vol, volCcy
    """
    all_data = []
    after_ts = before_ts
    remaining = total

    if before_ts is not None:
        logger.info(f"Paginating historical candles (gap-fill mode): {inst_id} {bar} target {total} points, before_ts={before_ts}")
    else:
        logger.info(f"Paginating historical candles: {inst_id} {bar} target {total} points")

    # Phase 1: Regular endpoint (max 1440)
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
            logger.info(f"Regular endpoint: batch {batch_idx + 1} has no data")
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

    # Phase 2: History endpoint
    if remaining > 0 and len(all_data) > 0:
        boundary_ts = _extract_earliest_ts(all_data)
        if boundary_ts:
            logger.info(f"Regular endpoint fetched {len(all_data)} candles, boundary ts={boundary_ts}, need {remaining} more -> switching to history endpoint")
            after_ts = boundary_ts - 1
            history_data = _fetch_history_candles_batch(inst_id, bar, remaining, after_ts)
            all_data.extend(history_data)
            logger.info(f"History endpoint supplemented {len(history_data)} candles")

    if not all_data:
        logger.warning(f"Paginated historical candle data is empty: {inst_id} {bar}")
        return pd.DataFrame()

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

    logger.info(f"Two-phase fetch complete: {inst_id} {bar} total {len(df)} candles (target {total})")
    return df


def fetch_open_interest(inst_id: str) -> Optional[dict]:
    """
    Fetch open interest. Spot instruments do not support OI, returns None directly.

    Args:
        inst_id: Instrument ID

    Returns:
        dict: {oi, oiCcy, ts} or None
    """
    if _is_spot(inst_id):
        logger.info(f"Spot instrument {inst_id} does not support open interest, skipped")
        return None

    params = {"instId": inst_id}
    time.sleep(REQUEST_INTERVAL)
    data = _make_request("/api/v5/public/open-interest", params)

    if data is None or len(data) == 0:
        logger.warning(f"Failed to fetch open interest: {inst_id}")
        return None

    item = data[0]
    return {
        "oi": _safe_float(item.get("oi"), 0),
        "oiCcy": _safe_float(item.get("oiCcy"), 0),
        "ts": item.get("ts", "")
    }


def fetch_oi_history(inst_id: str, period: str = "5M", limit: int = 24) -> list:
    """
    Fetch historical open interest data (for calculating OI change rate)

    Args:
        inst_id: Instrument ID (e.g. BTC-USDT-SWAP)
        period: Data granularity (5M/1H/1D), default 5M
        limit: Number of records, max 100, default 24

    Returns:
        list of dict: [{oi, oiCcy, ts}, ...] or empty list
    """
    if _is_spot(inst_id):
        logger.info(f"Spot instrument {inst_id} does not support historical open interest, skipped")
        return []

    params = {
        "instId": inst_id,
        "period": period,
        "limit": str(min(limit, 100))
    }

    time.sleep(REQUEST_INTERVAL)
    data = _make_request("/api/v5/rubik/stat/contracts/open-interest-history", params)

    if data is None or len(data) == 0:
        logger.warning(f"Failed to fetch historical open interest: {inst_id}")
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
    Fetch funding rate. Spot instruments do not support funding rate.

    Args:
        inst_id: Instrument ID

    Returns:
        dict: {fundingRate, fundingTime, nextFundingRate, nextFundingTime} or None
    """
    if _is_spot(inst_id):
        logger.info(f"Spot instrument {inst_id} does not support funding rate, skipped")
        return None

    params = {"instId": inst_id}
    time.sleep(REQUEST_INTERVAL)
    data = _make_request("/api/v5/public/funding-rate", params)

    if data is None or len(data) == 0:
        logger.warning(f"Failed to fetch funding rate: {inst_id}")
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
    Fetch recent tick trades

    Args:
        inst_id: Instrument ID
        limit: Quantity, max 100

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
        logger.warning(f"Failed to fetch tick trades: {inst_id}")
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
    Fetch recent tick trades in batches (supports pagination, breaks through 100 limit)

    Spot instruments do not support batch trades fetching.

    Args:
        inst_id: Instrument ID
        total: Total needed, default 500

    Returns:
        list of dict: [{tradeId, px, sz, side, ts}, ...]
    """
    if _is_spot(inst_id):
        logger.info(f"Spot instrument {inst_id} does not support batch tick trades fetching, skipped")
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

        if batch_idx > 0:
            time.sleep(REQUEST_INTERVAL)

        data = _make_request("/api/v5/market/trades", params)

        if data is None or len(data) == 0:
            logger.warning(f"Batch fetch trades: batch {batch_idx+1} has no data, stopping pagination")
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

        if batch_trades:
            earliest_ts = batch_trades[-1].get("ts", "")
            if earliest_ts:
                after_ts = earliest_ts
            else:
                break
        else:
            break

        if len(batch_trades) < fetch_count:
            break

    logger.info(f"Batch fetch trades: total {len(all_trades)} trades (target {total})")
    return all_trades


def fetch_liquidation_map(inst_id: str) -> list:
    """
    Fetch liquidation dense zone data

    Spot instruments do not support liquidation data.

    Args:
        inst_id: Instrument ID, e.g. BTC-USDT-SWAP

    Returns:
        list: [{price, volume, side}, ...]
    """
    if _is_spot(inst_id):
        logger.info(f"Spot instrument {inst_id} does not support liquidation dense zone data, skipped")
        return []

    try:
        time.sleep(REQUEST_INTERVAL)
        params = {
            "instId": inst_id,
            "period": "5m"
        }
        oi_data = _make_request("/api/v5/rubik/stat/contracts/open-interest-history", params)

        if oi_data is None or len(oi_data) < 2:
            logger.warning(f"Insufficient OI history data, cannot estimate liquidation dense zones: {inst_id}")
            return []

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

        df_1h = fetch_candles(inst_id, "1H", 48)

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

        logger.info(f"Liquidation zone identification: {inst_id} found {len(merged)} liquidation price levels")
        return merged

    except Exception as e:
        logger.warning(f"Failed to fetch liquidation dense zones, does not affect main flow: {e}")
        return []


def _merge_liquidation_levels(levels: list, threshold_pct: float) -> list:
    """
    Merge liquidation points at similar price levels

    Args:
        levels: Liquidation price level list
        threshold_pct: Merge threshold percentage

    Returns:
        Merged liquidation price level list
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
    Fetch mark price. Spot instruments do not support mark price.

    Args:
        inst_id: Instrument ID

    Returns:
        Mark price or None
    """
    if _is_spot(inst_id):
        logger.info(f"Spot instrument {inst_id} does not support mark price, skipped")
        return None

    params = {"instId": inst_id}
    time.sleep(REQUEST_INTERVAL)
    data = _make_request("/api/v5/public/mark-price", params)

    if data is None or len(data) == 0:
        logger.warning(f"Failed to fetch mark price: {inst_id}")
        return None

    return _safe_float(data[0].get("markPx"), 0)


def fetch_orderbook(inst_id: str, depth: int = 200) -> dict:
    """
    Fetch OKX orderbook depth data

    Args:
        inst_id: Instrument ID, e.g. BTC-USDT-SWAP
        depth: Depth levels, max 200

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
        logger.warning(f"Failed to fetch orderbook data: {inst_id}")
        return {"bids": [], "asks": [], "ts": 0}

    item = data[0]
    bids = []
    asks = []

    for bid in item.get("bids", []):
        if len(bid) >= 3:
            bids.append({
                "price": _safe_float(bid[0], 0),
                "size": _safe_float(bid[1], 0),
                "num_orders": int(_safe_float(bid[2], 0))
            })

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
    Detect large order walls in orderbook

    Args:
        orderbook: Orderbook data from fetch_orderbook
        inst_id: Instrument ID, for logging
        multiplier: Standard deviation multiplier threshold, default 5.0

    Returns:
        {
            "bid_walls": [{"price": float, "size": float, "strength": float}, ...],
            "ask_walls": [{"price": float, "size": float, "strength": float}, ...],
            "bid_total": float,
            "ask_total": float,
            "imbalance": float,
        }
    """
    bids = orderbook.get("bids", [])
    asks = orderbook.get("asks", [])

    if not bids and not asks:
        return {
            "bid_walls": [],
            "ask_walls": [],
            "bid_total": 0.0,
            "ask_total": 0.0,
            "imbalance": 1.0
        }

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

    if std_size == 0:
        std_size = mean_size * 0.1

    threshold = mean_size + multiplier * std_size

    bid_walls = []
    for b in bids:
        if b["size"] > threshold:
            bid_walls.append({
                "price": b["price"],
                "size": b["size"],
                "strength": round(b["size"] / mean_size, 1) if mean_size > 0 else 0.0
            })
    bid_walls.sort(key=lambda x: x["size"], reverse=True)

    ask_walls = []
    for a in asks:
        if a["size"] > threshold:
            ask_walls.append({
                "price": a["price"],
                "size": a["size"],
                "strength": round(a["size"] / mean_size, 1) if mean_size > 0 else 0.0
            })
    ask_walls.sort(key=lambda x: x["size"], reverse=True)

    bid_total = sum(b["size"] for b in bids)
    ask_total = sum(a["size"] for a in asks)

    if ask_total > 0:
        imbalance = round(bid_total / ask_total, 3)
    elif bid_total > 0:
        imbalance = 99.0
    else:
        imbalance = 1.0

    logger.info(f"Order wall detection: {inst_id} bid walls={len(bid_walls)}, ask walls={len(ask_walls)}, bid/ask ratio={imbalance}")

    return {
        "bid_walls": bid_walls,
        "ask_walls": ask_walls,
        "bid_total": round(bid_total, 4),
        "ask_total": round(ask_total, 4),
        "imbalance": imbalance
    }
