# OKX Trading Signal Analysis System — Algorithm & Implementation Documentation

## 1. System Overview

### 1.1 Architecture

The OKX Trading Signal Analysis System is a modular, multi-timeframe technical analysis platform designed for cryptocurrency and precious metals perpetual swap trading. The system follows a pipeline architecture with the following core modules:

| Module | Responsibility |
|--------|---------------|
| **Data Fetcher** | Retrieve candle, orderbook, liquidation, and OI data from OKX public API |
| **Trend Analyzer** | EMA-based trend classification (EMA144/EMA169) |
| **S/R Analyzer** | Multi-method support and resistance level identification |
| **Volume Profile** | Price-volume distribution analysis and POC/VA calculation |
| **Big Order Detector** | Orderbook imbalance and large order flow detection |
| **Liquidation Heatmap** | Liquidation cluster visualization |
| **Signal Generator** | Multi-factor signal synthesis and scoring |
| **Visualization** | Chart rendering and dashboard generation |
| **Web Server** | HTTP API and real-time data serving |

### 1.2 Supported Instruments

| Symbol | Type | Description |
|--------|------|-------------|
| `BTC-USDT-SWAP` | Perpetual Swap | Bitcoin perpetual futures |
| `ETH-USDT-SWAP` | Perpetual Swap | Ethereum perpetual futures |
| `XAU-USDT-SWAP` | Perpetual Swap | Gold perpetual futures |
| `XAG-USDT-SWAP` | Perpetual Swap | Silver perpetual futures |
| `BTC-USDT` | Spot | Bitcoin spot market |
| `ETH-USDT` | Spot | Ethereum spot market |

### 1.3 Data Source

All market data is sourced from the **OKX public API** (`https://www.okx.com/api/v5/`). No API key or authentication is required for public endpoints used by this system:

- `/market/candles` — Historical OHLCV candle data
- `/market/tickers` — Real-time ticker prices
- `/market/books` — Orderbook depth
- `/public/funding-rate` — Funding rate data
- `/public/open-interest` — Open interest data
- `/public/liquidation-orders` — Liquidation order history

---

## 2. Timing & Refresh Implementation

The system employs multiple independent timing mechanisms to ensure data freshness while minimizing API load.

### 2.1 Candle Data Incremental Refresh

Candle data is refreshed on a schedule aligned to UTC time boundaries:

| Timeframe | Refresh Interval | Alignment | Description |
|-----------|-----------------|-----------|-------------|
| **1H** | 30 minutes | xx:00 and xx:30 | Half-hourly refresh at minute 0 and 30 |
| **4H** | 2 hours | 00, 02, 04, ..., 22 UTC | Every even hour |
| **1D** | 12 hours | 00:00 and 12:00 UTC | Twice daily at midnight and noon |

**Implementation Details:**

```python
# Check interval: every 60 seconds
_candle_refresh_event.wait(60)

# Alignment check example for 1H candles
if current_minute in (0, 30) and current_second < 60:
    trigger_refresh("1H")
```

**Concurrency Control:**

- Uses `_analysis_lock` (threading.Lock) to prevent candle refresh during ongoing analysis
- If analysis is in progress, the refresh is deferred until the lock is released
- This prevents data inconsistency where mid-analysis data could change

**Refresh Flow:**

1. Timer thread wakes every 60 seconds
2. Check if current time aligns with any timeframe's refresh schedule
3. Acquire `_analysis_lock` (blocks if analysis is running)
4. Fetch latest candles via `/market/candles` endpoint
5. Merge new candles into local SQLite database
6. Release lock

### 2.2 Live Data Refresh

Real-time orderbook and liquidation data is refreshed independently:

| Data Type | Refresh Interval | Thread Type |
|-----------|-----------------|-------------|
| Orderbook (top 20 levels) | 5 minutes (300s) | Background daemon |
| Liquidation orders | 5 minutes (300s) | Background daemon |

**Implementation Details:**

- Runs in a dedicated daemon thread (`daemon=True`)
- Independent cancellation signal (`_live_stop_event`)
- Graceful shutdown: sets stop event, thread checks event before each sleep cycle
- Error handling: continues on individual fetch failures, logs errors

```python
def _live_data_loop():
    while not _live_stop_event.is_set():
        fetch_orderbook()
        fetch_liquidations()
        _live_stop_event.wait(300)  # 5 minutes
```

### 2.3 Frontend Real-time Price Refresh

The dashboard frontend polls for price updates:

| Parameter | Value | Description |
|-----------|-------|-------------|
| Poll interval | 10 seconds (`REFRESH_INTERVAL = 10000ms`) | JavaScript setInterval |
| Endpoint | `/api/tickers` | Returns latest prices for all symbols |
| Failure threshold | 2 consecutive failures | Before showing connection error |
| Price update | 1H candle close only | Updates latest candle's close price |

**Implementation Details:**

```javascript
// Frontend polling
setInterval(async () => {
    const response = await fetch('/api/tickers');
    if (response.ok) {
        consecutiveFailures = 0;
        updatePrices(await response.json());
    } else {
        consecutiveFailures++;
        if (consecutiveFailures >= 2) showError();
    }
}, REFRESH_INTERVAL);
```

**Backend Price Update Logic:**

- Only the 1H timeframe's latest candle close price is updated in real-time
- Higher timeframes (4H, 1D) use their respective refresh schedules
- This prevents unnecessary recalculation of higher-timeframe indicators

### 2.4 Progress Polling

During analysis execution, the frontend monitors progress:

| Polling Target | Interval | Purpose |
|---------------|----------|---------|
| `/api/progress` | 500ms | Main analysis progress |
| `/api/tradfi_progress` | 500ms | TradFi (traditional finance) analysis progress |

**Progress States:**

```python
{
    "status": "running",      # "idle" | "running" | "completed" | "error"
    "current_step": "EMA Analysis",
    "progress_percent": 45,
    "total_steps": 8,
    "current_step_index": 3
}
```

### 2.5 Database Maintenance

SQLite WAL (Write-Ahead Logging) checkpoint is performed hourly:

| Operation | Interval | Purpose |
|-----------|----------|---------|
| WAL checkpoint | Every 1 hour | Prevent WAL file growth, ensure data durability |

```python
# Periodic WAL checkpoint
def _db_maintenance_loop():
    while True:
        time.sleep(3600)  # 1 hour
        db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
```

### 2.6 HTTP Server

The system includes a built-in HTTP server for serving the dashboard and API:

| Feature | Implementation |
|---------|---------------|
| Server type | Python `http.server` (threaded) |
| Port detection | Auto-detection starting from 8080 |
| Port range | Tries ports 8080–8089 (10 ports) |
| Thread model | Main server in background thread |
| CORS | Enabled for local development |

**Port Detection Algorithm:**

```python
def find_available_port(start=8080, attempts=10):
    for port in range(start, start + attempts):
        try:
            server = HTTPServer(('0.0.0.0', port), RequestHandler)
            return server, port
        except OSError:
            continue
    raise RuntimeError("No available port found")
```

---

## 3. Core Algorithms

### 3.1 EMA Trend Analysis (`ema_analyzer.py`)

The trend analysis module uses two Exponential Moving Averages (EMA144 and EMA169) to classify market trends.

#### 3.1.1 EMA Calculation

The Exponential Moving Average is calculated using the standard recursive formula:

```
EMA(t) = Price(t) × k + EMA(t-1) × (1 - k)
```

Where:
- `k = 2 / (period + 1)` is the smoothing factor
- `period` is the EMA period (144 or 169)
- For EMA144: `k = 2 / 145 ≈ 0.01379`
- For EMA169: `k = 2 / 170 ≈ 0.01176`

**Initial Value:**

The first EMA value is seeded with the Simple Moving Average (SMA) of the first `period` data points:

```
EMA(0) = SMA(period) = (Price(1) + Price(2) + ... + Price(period)) / period
```

#### 3.1.2 Trend Classification

The trend is determined by the relationship between EMA144 and EMA169:

| Condition | Trend | Description |
|-----------|-------|-------------|
| `EMA144 > EMA169` | **GOLDEN_CROSS** | Bullish trend (fast EMA above slow EMA) |
| `EMA144 < EMA169` | **DEATH_CROSS** | Bearish trend (fast EMA below slow EMA) |
| `\|EMA144 - EMA169\| / price < 0.3%` | **ENTANGLED** | Neutral/ranging market |

#### 3.1.3 Separation Calculation

The separation between the two EMAs is expressed as a percentage of the current price:

```
Separation(%) = (EMA144 - EMA169) / CurrentPrice × 100
```

#### 3.1.4 Trend Strength Classification

| Separation Range | Strength | Interpretation |
|-----------------|----------|----------------|
| `\|Sep\| < 1%` | **Early** | Trend is forming, weak momentum |
| `1% ≤ \|Sep\| ≤ 4%` | **Mid** | Established trend, normal momentum |
| `\|Sep\| > 4%` | **Overheated** | Extended trend, potential reversal risk |

#### 3.1.5 Multi-Timeframe Trend Matrix

The system computes trends across all supported timeframes:

```
         1H    4H    1D
BTC    GOLD  DEATH  GOLD   → Mixed signals
ETH    DEATH DEATH  DEATH  → Strong bearish alignment
```

**Alignment Score:**
- All timeframes same direction: +3 (strong alignment)
- Two timeframes same: +1 (moderate alignment)
- All different: 0 (no alignment)

---

### 3.2 Support/Resistance Identification (`sr_analyzer.py`)

The S/R module combines four distinct methods to identify significant price levels.

#### 3.2.1 Structural S/R (Local Extrema Method)

**Algorithm:**

1. Scan candle data with a sliding window of size `window=3`
2. Identify local maxima: candle high > all neighboring highs within window
3. Identify local minima: candle low < all neighboring lows within window
4. Track touch count for each level (price within ±0.1% of level)
5. Merge levels within 0.5% of each other

```python
def find_local_extrema(candles, window=3):
    highs = [c.high for c in candles]
    lows = [c.low for c in candles]
    
    resistance_levels = []
    for i in range(window, len(highs) - window):
        if highs[i] == max(highs[i-window:i+window+1]):
            resistance_levels.append(highs[i])
    
    support_levels = []
    for i in range(window, len(lows) - window):
        if lows[i] == min(lows[i-window:i+window+1]):
            support_levels.append(lows[i])
    
    return support_levels, resistance_levels
```

#### 3.2.2 Psychological Levels

Round numbers that act as psychological support/resistance:

| Instrument | Step Size | Examples |
|------------|-----------|----------|
| BTC | $1,000 | 60000, 61000, 62000, ... |
| ETH | $100 | 3000, 3100, 3200, ... |
| XAU | $50 | 2300, 2350, 2400, ... |
| XAG | $1 | 28, 29, 30, ... |

**Filtering:** Only levels within ±15% of current price are included.

```python
def get_psychological_levels(current_price, step, range_pct=0.15):
    lower = current_price * (1 - range_pct)
    upper = current_price * (1 + range_pct)
    
    start = int(lower / step) * step
    end = int(upper / step) * step + step
    
    return [p for p in range(start, end, step) if lower <= p <= upper]
```

#### 3.2.3 Fair Value Gap (FVG)

Fair Value Gaps represent price inefficiencies that often act as support/resistance:

**Bullish FVG** (gap up):
```
Condition: Previous candle high < Next candle low
Gap range: [prev_high, next_low]
```

**Bearish FVG** (gap down):
```
Condition: Previous candle low > Next candle high
Gap range: [next_high, prev_low]
```

**Gap Filling:**
- A gap is considered "filled" if price subsequently trades through the gap range
- Only unfilled gaps are retained as active S/R levels

#### 3.2.4 Scoring System

Each S/R level receives a composite score based on multiple factors:

| Factor | Points | Description |
|--------|--------|-------------|
| Touch count | +1 per touch (max 5) | More touches = stronger level |
| Reversal magnitude | +2 | Price reversed > 2× ATR from level |
| Psychological level | +1 | Level coincides with round number |
| Fair Value Gap | +1 | Level is within an unfilled FVG |
| Liquidation zone | +1 | Level has significant liquidation clusters |
| Order wall (same direction) | +1 | Large order wall at level |

**Strength Classification:**

| Score | Strength | Color |
|-------|----------|-------|
| ≥ 5 | **Super** | Red/Green (thick line) |
| 3–4 | **Strong** | Orange/Yellow (medium line) |
| 1–2 | **Weak** | Gray (thin line) |

#### 3.2.5 S/R Zone Width

Support and resistance levels are displayed as zones rather than single lines:

| Instrument | Zone Width | Description |
|------------|------------|-------------|
| BTC | ±0.3% | e.g., 60000 → [59820, 60180] |
| ETH | ±0.4% | e.g., 3000 → [2988, 3012] |
| XAU | ±0.3% | e.g., 2300 → [2293.1, 2306.9] |
| XAG | ±0.5% | e.g., 28 → [27.86, 28.14] |

---

### 3.3 Volume Profile (`vp_analyzer.py`)

Volume Profile analyzes the distribution of volume across price levels to identify significant trading zones.

#### 3.3.1 Volume Distribution Algorithm

1. Define price range from lowest low to highest high in the lookback period
2. Divide range into `num_bins=50` equal-sized bins
3. For each candle, distribute its volume proportionally across overlapping bins

```python
def distribute_volume(candle, bins):
    candle_low, candle_high, volume = candle.low, candle.high, candle.volume
    
    for bin in bins:
        # Calculate overlap between candle range and bin range
        overlap_low = max(candle_low, bin.low)
        overlap_high = min(candle_high, bin.high)
        
        if overlap_low < overlap_high:
            # Proportional distribution
            candle_range = candle_high - candle_low
            overlap_range = overlap_high - overlap_low
            proportion = overlap_range / candle_range
            
            bin.volume += volume * proportion
```

#### 3.3.2 Key Levels

**Point of Control (POC):**
```
POC = price level with highest accumulated volume
```

**Value Area (VA):**
- Contains 70% of total volume
- Expands symmetrically outward from POC
- Upper bound: Value Area High (VAH)
- Lower bound: Value Area Low (VAL)

```python
def calculate_value_area(bins, poc_index, total_volume, va_pct=0.70):
    target_volume = total_volume * va_pct
    accumulated = bins[poc_index].volume
    
    upper = poc_index
    lower = poc_index
    
    while accumulated < target_volume:
        upper_vol = bins[upper + 1].volume if upper + 1 < len(bins) else 0
        lower_vol = bins[lower - 1].volume if lower - 1 >= 0 else 0
        
        if upper_vol >= lower_vol:
            upper += 1
            accumulated += upper_vol
        else:
            lower -= 1
            accumulated += lower_vol
    
    return bins[lower].low, bins[upper].high
```

**High Volume Nodes (HVN):**
```
HVN = price levels where volume > 30% of POC volume
```

#### 3.3.3 S/R-VP Resonance Detection

When Volume Profile levels coincide with S/R levels, the signal strength increases:

| Resonance Type | Condition | Strength Multiplier |
|---------------|-----------|-------------------|
| **POC-SR** | POC within S/R zone | ×2.0 (Strong) |
| **HVN-SR** | HVN within S/R zone | ×1.5 (Normal) |
| **VA-SR** | VA boundary (VAH/VAL) within S/R zone | ×1.2 (Weak) |

```python
def detect_resonance(sr_levels, vp_levels):
    resonances = []
    for sr in sr_levels:
        for vp in vp_levels:
            if abs(sr.price - vp.price) / sr.price < sr.zone_width:
                resonances.append({
                    'sr_level': sr,
                    'vp_level': vp,
                    'type': vp.level_type,  # 'POC', 'HVN', 'VA'
                    'strength': RESONANCE_MULTIPLIERS[vp.level_type]
                })
    return resonances
```

---

### 3.4 Big Order Detection (`big_order_detector.py`)

#### 3.4.1 Big Order Analysis

The system identifies large orders in the orderbook and classifies their market impact.

**Size Thresholds:**

| Instrument | Threshold | Description |
|------------|-----------|-------------|
| BTC | ≥ 10 BTC | ~$600,000+ at current prices |
| ETH | ≥ 500 ETH | ~$1,500,000+ at current prices |
| XAU | ≥ 500 oz | ~$1,150,000+ at current prices |
| XAG | ≥ 10,000 oz | ~$280,000+ at current prices |

**Buy/Sell Ratio Classification:**

```
Big Order Ratio = Total Buy Volume / Total Sell Volume

Ratio > 2.0  → BULLISH  (buying pressure dominates)
Ratio < 0.5  → BEARISH  (selling pressure dominates)
0.5 ≤ Ratio ≤ 2.0 → NEUTRAL (balanced)
```

#### 3.4.2 Open Interest (OI) Change Analysis

Open Interest changes indicate capital flow:

| OI Change | Interpretation |
|-----------|----------------|
| Increase > 1% | New capital entering market (trend continuation) |
| Decrease > 1% | Capital leaving market (trend exhaustion) |
| Change ≤ 1% | Stable, no significant flow |

#### 3.4.3 OI-Price Divergence

Divergences between price and OI provide strong reversal signals:

| Price Action | OI Change | Signal | Score |
|-------------|-----------|--------|-------|
| New High | OI Falling | **Bearish Divergence** | -3 |
| New Low | OI Rising | **Bearish Confirmation** | -2 |
| New Low | OI Falling | **Bullish Divergence** | -3 |
| New High | OI Rising | **Bullish Confirmation** | +3 |

**Divergence Detection Algorithm:**

```python
def detect_oi_divergence(prices, oi_values, lookback=20):
    recent_high = max(prices[-lookback:])
    recent_low = min(prices[-lookback:])
    current_price = prices[-1]
    current_oi = oi_values[-1]
    prev_oi = oi_values[-lookback]
    
    oi_change = (current_oi - prev_oi) / prev_oi
    
    if current_price >= recent_high * 0.99:  # Near high
        if oi_change < -0.01:
            return "BEARISH_DIVERGENCE", -3
        elif oi_change > 0.01:
            return "BULLISH_CONFIRMATION", +3
    
    elif current_price <= recent_low * 1.01:  # Near low
        if oi_change > 0.01:
            return "BEARISH_CONFIRMATION", -2
        elif oi_change < -0.01:
            return "BULLISH_DIVERGENCE", -3
    
    return "NEUTRAL", 0
```

---

### 3.5 Signal Generation (`signal_generator.py`)

The signal generator synthesizes all analysis modules into actionable trading signals.

#### 3.5.1 Multi-Factor Scoring Model

| Factor | Weight | Score Range | Description |
|--------|--------|-------------|-------------|
| EMA Trend | 30% | -3 to +3 | Golden/Death cross alignment |
| S/R Proximity | 25% | -5 to +5 | Distance to nearest S/R level |
| Volume Profile | 15% | -3 to +3 | POC/VA position relative to price |
| Big Orders | 15% | -3 to +3 | Orderbook imbalance |
| OI Analysis | 10% | -3 to +3 | Open interest flow and divergence |
| Funding Rate | 5% | -2 to +2 | Funding rate extreme |

#### 3.5.2 Signal Strength Classification

```
Total Score = Σ(Factor_Score × Weight)

Score > +6   → STRONG BUY
+3 to +6     → BUY
-3 to +3     → NEUTRAL
-6 to -3     → SELL
Score < -6   → STRONG SELL
```

#### 3.5.3 Confidence Calculation

```
Confidence = Base_Confidence × Timeframe_Alignment × Volume_Confirmation

Where:
- Base_Confidence = min(100, |Total_Score| × 10)
- Timeframe_Alignment = 1.0 + 0.1 × (aligned_timeframes - 1)
- Volume_Confirmation = 1.0 if volume > 20-day average, else 0.8
```

---

## 4. Database Schema

### 4.1 SQLite with WAL Mode

The system uses SQLite with Write-Ahead Logging (WAL) mode for concurrent read/write access:

```sql
PRAGMA journal_mode=WAL;
PRAGMA busy_timeout=5000;
```

### 4.2 Tables

**`candles`** — Historical OHLCV data

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER PRIMARY KEY | Auto-increment ID |
| symbol | TEXT | Instrument symbol |
| timeframe | TEXT | '1H', '4H', '1D' |
| timestamp | INTEGER | Unix timestamp (seconds) |
| open | REAL | Open price |
| high | REAL | High price |
| low | REAL | Low price |
| close | REAL | Close price |
| volume | REAL | Trading volume |
| UNIQUE(symbol, timeframe, timestamp) | | |

**`sr_levels`** — Support/Resistance levels

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER PRIMARY KEY | Auto-increment ID |
| symbol | TEXT | Instrument symbol |
| price | REAL | Level price |
| type | TEXT | 'support' or 'resistance' |
| strength | INTEGER | Score (1-10) |
| method | TEXT | 'structural', 'psychological', 'fvg' |
| touch_count | INTEGER | Number of touches |

---

## 5. API Endpoints

### 5.1 REST API

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/analysis` | GET | Full analysis results for a symbol |
| `/api/tickers` | GET | Real-time prices for all symbols |
| `/api/progress` | GET | Analysis progress status |
| `/api/tradfi_progress` | GET | TradFi analysis progress |
| `/api/candles` | GET | Candle data with parameters |
| `/api/sr_levels` | GET | S/R levels for a symbol |
| `/api/volume_profile` | GET | Volume profile data |
| `/api/signals` | GET | Trading signals |

### 5.2 Response Format

```json
{
    "success": true,
    "data": {
        "symbol": "BTC-USDT-SWAP",
        "timeframe": "1H",
        "timestamp": 1717200000,
        "analysis": {
            "trend": {
                "direction": "GOLDEN_CROSS",
                "strength": "Mid",
                "ema144": 67250.5,
                "ema169": 66980.2,
                "separation_pct": 0.40
            },
            "support_resistance": {
                "levels": [...],
                "nearest_support": 66500,
                "nearest_resistance": 68000
            },
            "signal": {
                "direction": "BUY",
                "strength": 4.2,
                "confidence": 72
            }
        }
    }
}
```

---

## 6. Error Handling & Resilience

### 6.1 API Rate Limiting

- Exponential backoff on 429 responses
- Minimum 100ms between API calls
- Maximum 3 retries per request

### 6.2 Data Validation

- Candle data validation: O, H, L, C must be positive; H ≥ L; H ≥ max(O,C); L ≤ min(O,C)
- Volume must be non-negative
- Timestamps must be monotonically increasing

### 6.3 Graceful Degradation

- If one timeframe fails, other timeframes continue
- If orderbook fetch fails, analysis proceeds without orderbook data
- If database is locked, retries with exponential backoff

---

## 7. Performance Considerations

### 7.1 Computational Complexity

| Operation | Time Complexity | Notes |
|-----------|----------------|-------|
| EMA calculation | O(n) | Single pass through candle data |
| S/R extrema detection | O(n × w) | n = candles, w = window size |
| Volume Profile | O(n × b) | b = number of bins (50) |
| Signal generation | O(1) | Aggregates pre-computed results |

### 7.2 Memory Usage

- 1H candles: ~1000 candles × 56 bytes ≈ 56 KB per symbol
- 4H candles: ~500 candles × 56 bytes ≈ 28 KB per symbol
- 1D candles: ~365 candles × 56 bytes ≈ 20 KB per symbol
- Total per symbol: ~104 KB
- 6 symbols: ~624 KB total

---

## 8. Configuration

### 8.1 Key Parameters

```python
# EMA Periods
EMA_FAST_PERIOD = 144
EMA_SLOW_PERIOD = 169

# S/R Detection
SR_WINDOW_SIZE = 3
SR_MERGE_THRESHOLD = 0.005  # 0.5%
SR_ZONE_WIDTHS = {
    'BTC': 0.003,  # ±0.3%
    'ETH': 0.004,  # ±0.4%
    'XAU': 0.003,  # ±0.3%
    'XAG': 0.005,  # ±0.5%
}

# Volume Profile
VP_NUM_BINS = 50
VP_VALUE_AREA_PCT = 0.70
VP_HVN_THRESHOLD = 0.30  # 30% of POC volume

# Big Order Thresholds
BIG_ORDER_THRESHOLDS = {
    'BTC': 10,
    'ETH': 500,
    'XAU': 500,
    'XAG': 10000,
}

# Refresh Intervals (seconds)
CANDLE_REFRESH_CHECK = 60
LIVE_DATA_INTERVAL = 300
FRONTEND_REFRESH_MS = 10000
PROGRESS_POLL_MS = 500
DB_MAINTENANCE_INTERVAL = 3600
```

---

## 9. Limitations & Known Issues

1. **API Rate Limits**: OKX public API has rate limits (20 requests/2 seconds per IP). The system may hit limits during concurrent multi-symbol analysis.

2. **Historical Data Depth**: OKX public API returns maximum 1000 candles per request. For longer history, multiple paginated requests are needed.

3. **Liquidation Data Delay**: Liquidation orders may have up to 1-minute delay from actual execution.

4. **Spot vs Swap Divergence**: Spot and perpetual swap prices may diverge, especially during high volatility. The system analyzes them independently.

5. **Single-threaded Analysis**: Analysis for each symbol runs sequentially. Multi-symbol parallel analysis would require additional locking complexity.

---

## 10. Future Enhancements

- [ ] WebSocket support for real-time streaming data
- [ ] Machine learning-based pattern recognition
- [ ] Backtesting framework for signal validation
- [ ] Multi-exchange support (Binance, Bybit)
- [ ] Alert system with webhook/email notifications
- [ ] Portfolio-level risk management

---

## License

This project is provided as-is for educational and research purposes. Trading involves significant risk. This system does not provide financial advice.

---

*Document Version: 1.0*
*Last Updated: 2026-06-01*
