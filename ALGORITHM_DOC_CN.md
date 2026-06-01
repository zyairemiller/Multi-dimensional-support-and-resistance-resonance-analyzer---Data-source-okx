# OKX 交易信号分析系统 — 算法与实现文档

## 1. 系统概述

### 1.1 架构设计

OKX 交易信号分析系统是一套模块化、多时间周期的技术分析平台，面向加密货币及贵金属永续合约交易。系统采用流水线（pipeline）架构，由以下核心模块组成：

| 模块 | 职责 |
|------|------|
| **数据获取器** | 从 OKX 公开 API 获取 K 线、订单簿、清算、持仓量等数据 |
| **趋势分析器** | 基于 EMA 的趋势分类（EMA144/EMA169） |
| **支撑阻力分析器** | 多方法支撑阻力位识别 |
| **成交量分布分析器** | 价格-成交量分布分析与 POC/VA 计算 |
| **大单检测器** | 订单簿失衡与大额挂单流检测 |
| **清算热力图** | 清算集群可视化 |
| **信号生成器** | 多因子信号综合与评分 |
| **可视化引擎** | 图表渲染与仪表盘生成 |
| **Web 服务器** | HTTP API 与实时数据服务 |

### 1.2 支持品种

| 代码 | 类型 | 说明 |
|------|------|------|
| `BTC-USDT-SWAP` | 永续合约 | 比特币永续期货 |
| `ETH-USDT-SWAP` | 永续合约 | 以太坊永续期货 |
| `XAU-USDT-SWAP` | 永续合约 | 黄金永续期货 |
| `XAG-USDT-SWAP` | 永续合约 | 白银永续期货 |
| `BTC-USDT` | 现货 | 比特币现货 |
| `ETH-USDT` | 现货 | 以太坊现货 |

### 1.3 数据源

所有行情数据均来自 **OKX 公开 API**（`https://www.okx.com/api/v5/`）。本系统使用的公开端点无需 API Key 或身份认证：

- `/market/candles` — 历史 OHLCV K 线数据
- `/market/tickers` — 实时行情报价
- `/market/books` — 订单簿深度
- `/public/funding-rate` — 资金费率
- `/public/open-interest` — 持仓量
- `/public/liquidation-orders` — 清算订单历史

---

## 2. 计时与刷新机制

系统采用多个独立的定时机制，确保数据新鲜度的同时最小化 API 负载。

### 2.1 K 线数据定时增量刷新

K 线数据按照与 UTC 时间对齐的计划进行刷新：

| 时间周期 | 刷新间隔 | 对齐方式 | 说明 |
|---------|---------|---------|------|
| **1H** | 30 分钟 | xx:00 和 xx:30 | 每半小时在 0 分和 30 分刷新 |
| **4H** | 2 小时 | 00, 02, 04, ..., 22 UTC | 每偶数小时 |
| **1D** | 12 小时 | 00:00 和 12:00 UTC | 每日午夜和正午两次 |

**实现细节：**

```python
# 检查间隔：每 60 秒
_candle_refresh_event.wait(60)

# 1H K 线对齐检查示例
if current_minute in (0, 30) and current_second < 60:
    trigger_refresh("1H")
```

**并发控制：**

- 使用 `_analysis_lock`（`threading.Lock`）防止分析过程中刷新 K 线数据
- 若分析正在进行，刷新将延迟至锁释放后执行
- 避免分析中途数据变化导致的不一致

**刷新流程：**

1. 定时线程每 60 秒唤醒一次
2. 检查当前时间是否匹配任一周期的刷新计划
3. 获取 `_analysis_lock`（若分析正在运行则阻塞）
4. 通过 `/market/candles` 端点拉取最新 K 线
5. 将新数据合并至本地 SQLite 数据库
6. 释放锁

### 2.2 实时数据刷新

订单簿和清算数据独立刷新：

| 数据类型 | 刷新间隔 | 线程类型 |
|---------|---------|---------|
| 订单簿（前 20 档） | 5 分钟（300s） | 后台守护线程 |
| 清算订单 | 5 分钟（300s） | 后台守护线程 |

**实现细节：**

- 在独立的守护线程中运行（`daemon=True`）
- 独立的取消信号（`_live_stop_event`）
- 优雅关停：设置停止事件，线程在每个休眠周期前检查事件
- 错误处理：单次拉取失败不影响整体流程，记录日志后继续

```python
def _live_data_loop():
    while not _live_stop_event.is_set():
        fetch_orderbook()
        fetch_liquidations()
        _live_stop_event.wait(300)  # 5 分钟
```

### 2.3 前端价格刷新

仪表盘前端轮询价格更新：

| 参数 | 值 | 说明 |
|------|---|------|
| 轮询间隔 | 10 秒（`REFRESH_INTERVAL = 10000ms`） | JavaScript setInterval |
| 端点 | `/api/tickers` | 返回所有品种最新价格 |
| 失败阈值 | 连续 2 次失败 | 才显示连接错误 |
| 价格更新 | 仅 1H K 线收盘价 | 更新最新 K 线的收盘价 |

**实现细节：**

```javascript
// 前端轮询
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

**后端价格更新逻辑：**

- 仅实时更新 1H 时间周期的最新 K 线收盘价
- 更高周期（4H、1D）使用各自的刷新计划
- 避免不必要地重新计算高周期指标

### 2.4 进度轮询

分析执行期间，前端监控进度：

| 轮询目标 | 间隔 | 用途 |
|---------|------|------|
| `/api/progress` | 500ms | 主分析进度 |
| `/api/tradfi_progress` | 500ms | TradFi（传统金融）分析进度 |

**进度状态：**

```python
{
    "status": "running",      # "idle" | "running" | "completed" | "error"
    "current_step": "EMA 分析",
    "progress_percent": 45,
    "total_steps": 8,
    "current_step_index": 3
}
```

### 2.5 数据库维护

SQLite WAL（Write-Ahead Logging）检查点每小时执行一次：

| 操作 | 间隔 | 用途 |
|------|------|------|
| WAL 检查点 | 每 1 小时 | 防止 WAL 文件无限增长，确保数据持久性 |

```python
# 定期 WAL 检查点
def _db_maintenance_loop():
    while True:
        time.sleep(3600)  # 1 小时
        db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
```

### 2.6 HTTP 服务器

系统内置 HTTP 服务器，用于提供仪表盘和 API 服务：

| 特性 | 实现 |
|------|------|
| 服务器类型 | Python `http.server`（多线程） |
| 端口检测 | 从 8080 起自动检测 |
| 端口范围 | 尝试 8080–8089（共 10 个端口） |
| 线程模型 | 主服务器在后台线程运行 |
| CORS | 已启用，支持本地开发 |

**端口检测算法：**

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

## 3. 核心算法

### 3.1 EMA 趋势分析（`ema_analyzer.py`）

趋势分析模块使用两条指数移动平均线（EMA144 和 EMA169）对市场趋势进行分类。

#### 3.1.1 EMA 计算

指数移动平均线采用标准递推公式计算：

```
EMA(t) = Price(t) × k + EMA(t-1) × (1 - k)
```

其中：
- `k = 2 / (period + 1)` 为平滑因子
- `period` 为 EMA 周期（144 或 169）
- EMA144：`k = 2 / 145 ≈ 0.01379`
- EMA169：`k = 2 / 170 ≈ 0.01176`

**初始值：**

首个 EMA 值使用前 `period` 个数据点的简单移动平均（SMA）作为种子：

```
EMA(0) = SMA(period) = (Price(1) + Price(2) + ... + Price(period)) / period
```

**代码实现（`ema_analyzer.py`）：**

```python
def calc_ema(series: pd.Series, period: int) -> pd.Series:
    k = 2.0 / (period + 1)
    ema = series.copy().astype(float)
    first_valid = series.first_valid_index()
    if first_valid is None:
        return ema
    if len(series) >= period:
        ema.iloc[:period] = np.nan
        ema.iloc[period - 1] = series.iloc[:period].mean()
        for i in range(period, len(series)):
            ema.iloc[i] = series.iloc[i] * k + ema.iloc[i - 1] * (1 - k)
    else:
        ema.iloc[-1] = series.mean()
    return ema
```

#### 3.1.2 趋势分类

趋势由 EMA144 与 EMA169 的相对关系决定：

| 条件 | 趋势 | 说明 |
|------|------|------|
| `EMA144 > EMA169` | **金叉（GOLDEN_CROSS）** | 多头趋势（快线在慢线之上） |
| `EMA144 < EMA169` | **死叉（DEATH_CROSS）** | 空头趋势（快线在慢线之下） |
| `\|EMA144 - EMA169\| / price < 0.3%` | **缠绕（ENTANGLED）** | 中性/震荡市 |

#### 3.1.3 分离度计算

两条 EMA 之间的分离度以当前价格的百分比表示：

```
分离度(%) = (EMA144 - EMA169) / 当前价格 × 100
```

#### 3.1.4 趋势强度分类

| 分离度范围 | 强度 | 含义 |
|-----------|------|------|
| `\|Sep\| < 1%` | **早期（Early）** | 趋势正在形成，动能较弱 |
| `1% ≤ \|Sep\| ≤ 4%` | **中期（Mid）** | 趋势确立，动能正常 |
| `\|Sep\| > 4%` | **过热（Overheated）** | 趋势过度延伸，存在反转风险 |

#### 3.1.5 多周期趋势矩阵

系统在所有支持的时间周期上计算趋势：

```
         1H    4H    1D
BTC    金叉  死叉  金叉   → 信号矛盾
ETH    死叉  死叉  死叉   → 强烈空头共振
```

**一致性评分：**
- 所有周期方向相同：+3（强一致性）
- 两个周期方向相同：+1（中等一致性）
- 所有周期方向不同：0（无一致性）

---

### 3.2 支撑阻力识别（`sr_analyzer.py`）

支撑阻力模块综合运用四种方法识别关键价格水平。

#### 3.2.1 结构性支撑阻力（局部极值法）

**算法：**

1. 以滑动窗口（`window=3`）扫描 K 线数据
2. 识别局部高点：K 线高点高于窗口内所有相邻高点
3. 识别局部低点：K 线低点低于窗口内所有相邻低点
4. 统计每个价位的触及次数（价格在 ±0.1% 范围内视为触及）
5. 合并距离 < 0.5% 的相邻价位

**代码实现（`sr_analyzer.py`）：**

```python
def find_structural_sr(df_1d: pd.DataFrame, lookback: int = 60) -> list:
    df = df_1d.tail(lookback).reset_index(drop=True)
    highs = df["high"].values
    lows = df["low"].values
    sr_levels = []
    window = 3  # 左右各看 3 根 K 线

    # 找阻力位（局部高点）
    for i in range(window, len(highs) - window):
        is_swing_high = all(highs[j] < highs[i]
                           for j in range(i - window, i + window + 1) if j != i)
        if is_swing_high:
            sr_levels.append({"level": float(highs[i]), "type": "resistance", "touch_count": 1})

    # 找支撑位（局部低点）
    for i in range(window, len(lows) - window):
        is_swing_low = all(lows[j] > lows[i]
                          for j in range(i - window, i + window + 1) if j != i)
        if is_swing_low:
            sr_levels.append({"level": float(lows[i]), "type": "support", "touch_count": 1})

    # 合并距离 < 0.5% 的相邻 S/R
    sr_levels = _merge_nearby_levels(sr_levels, 0.005)
    return sr_levels
```

#### 3.2.2 心理关口

整数关口作为心理支撑/阻力位：

| 品种 | 步长 | 示例 |
|------|------|------|
| BTC | $1,000 | 60000, 61000, 62000, ... |
| ETH | $100 | 3000, 3100, 3200, ... |
| XAU | $50 | 2300, 2350, 2400, ... |
| XAG | $1 | 28, 29, 30, ... |

**筛选范围：** 仅包含当前价格 ±15% 以内的价位。

```python
def find_psychological_levels(current_price: float, instrument: str) -> list:
    base_instrument = instrument.replace("_SPOT", "")
    step_map = {"BTC": 1000, "ETH": 100, "XAU": 50, "XAG": 1}
    step = step_map.get(base_instrument, int(current_price * 0.05))
    lower = int(current_price * 0.85 / step) * step
    upper = int(current_price * 1.15 / step) * step
    p = lower
    levels = []
    while p <= upper:
        if p > 0 and abs(p - current_price) / current_price < 0.15:
            levels.append(float(p))
        p += step
    return levels
```

#### 3.2.3 公允价值缺口（FVG）

公允价值缺口（Fair Value Gap）代表价格效率缺失区域，常充当支撑/阻力：

**看涨 FVG（向上跳空）：**
```
条件：前一根 K 线高点 < 后一根 K 线低点
缺口范围：[前高, 后低]
```

**看跌 FVG（向下跳空）：**
```
条件：前一根 K 线低点 > 后一根 K 线高点
缺口范围：[后高, 前低]
```

**缺口回补判定：**
- 若价格后续穿越缺口范围，则视为已"回补"
- 仅保留未回补的缺口作为有效 S/R 价位

#### 3.2.4 评分系统

每个 S/R 价位根据多个因子获得综合评分：

| 因子 | 分值 | 说明 |
|------|------|------|
| 触及次数 | +1/次（上限 5） | 触及越多，价位越强 |
| 反转幅度 | +2 | 价格从该位反转超过 2 倍 ATR |
| 整数关口 | +1 | 与心理关口重合 |
| 公允价值缺口 | +1 | 处于未回补 FVG 内 |
| 清算密集区 | +1 | 有显著清算集群 |
| 订单墙（同方向） | +1 | 存在同方向大额挂单墙 |

**强度分类：**

| 评分 | 强度 | 显示 |
|------|------|------|
| ≥ 5 | **超级（Super）** | 红/绿色粗线 |
| 3–4 | **强（Strong）** | 橙/黄色中线 |
| 1–2 | **弱（Weak）** | 灰色细线 |

#### 3.2.5 支撑阻力区间宽度

支撑阻力位以区间而非单线展示：

| 品种 | 区间宽度 | 示例 |
|------|---------|------|
| BTC | ±0.3% | 60000 → [59820, 60180] |
| ETH | ±0.4% | 3000 → [2988, 3012] |
| XAU | ±0.3% | 2300 → [2293.1, 2306.9] |
| XAG | ±0.5% | 28 → [27.86, 28.14] |

---

### 3.3 成交量分布（`vp_analyzer.py`）

成交量分布（Volume Profile）分析成交量在各价格水平上的分布，以识别重要交易区间。

#### 3.3.1 成交量分配算法

1. 确定回看期内最低低点到最高高点的价格范围
2. 将范围均分为 `num_bins=50` 个等宽区间
3. 对每根 K 线，按重叠比例将成交量分配至对应区间

```python
def distribute_volume(candle, bins):
    candle_low, candle_high, volume = candle.low, candle.high, candle.volume
    for bin in bins:
        # 计算 K 线范围与区间范围的重叠
        overlap_low = max(candle_low, bin.low)
        overlap_high = min(candle_high, bin.high)
        if overlap_low < overlap_high:
            candle_range = candle_high - candle_low
            overlap_range = overlap_high - overlap_low
            proportion = overlap_range / candle_range
            bin.volume += volume * proportion
```

#### 3.3.2 关键价位

**控制点（POC，Point of Control）：**
```
POC = 累计成交量最高的价格水平
```

**价值区间（VA，Value Area）：**
- 包含总成交量的 70%
- 从 POC 向两侧对称扩展
- 上沿：价值区间高点（VAH）
- 下沿：价值区间低点（VAL）

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

**高成交量节点（HVN，High Volume Nodes）：**
```
HVN = 成交量 > POC 成交量 30% 的价格水平
```

#### 3.3.3 S/R-VP 共振检测

当成交量分布的关键价位与 S/R 价位重合时，信号强度增强：

| 共振类型 | 条件 | 强度倍数 |
|---------|------|---------|
| **POC-SR** | POC 在 S/R 区间内 | ×2.0（强共振） |
| **HVN-SR** | HVN 在 S/R 区间内 | ×1.5（普通共振） |
| **VA-SR** | VA 边界（VAH/VAL）在 S/R 区间内 | ×1.2（弱共振） |

```python
def check_sr_vp_resonance(sr_zones, vp_result, instrument):
    for zone in sr_zones:
        zone_low = zone.get("zone_low", 0)
        zone_high = zone.get("zone_high", 0)

        # 1. POC 在 S/R 区间内 → 强共振
        if poc > 0 and zone_low <= poc <= zone_high:
            zone["resonance"] = "strong"
        # 2. 高成交量节点在 S/R 区间内 → 普通共振
        elif any(zone_low <= hvn <= zone_high for hvn in hvns):
            zone["resonance"] = "normal"
        # 3. VA 边界在 S/R 区间内 → 弱共振
        elif zone_low <= va_high <= zone_high or zone_low <= va_low <= zone_high:
            zone["resonance"] = "weak"
```

---

### 3.4 大单检测（`big_order_detector.py`）

#### 3.4.1 大单分析

系统识别订单簿中的大额挂单并分类其市场影响。

**数量门槛：**

| 品种 | 门槛 | 说明 |
|------|------|------|
| BTC | ≥ 10 BTC | 按当前价格约 $600,000+ |
| ETH | ≥ 500 ETH | 按当前价格约 $1,500,000+ |
| XAU | ≥ 500 盎司 | 按当前价格约 $1,150,000+ |
| XAG | ≥ 10,000 盎司 | 按当前价格约 $280,000+ |

**买卖比分类：**

```
大单比率 = 主动买入量 / 主动卖出量

比率 > 2.0  → 看多（BULLISH）   买方力量主导
比率 < 0.5  → 看空（BEARISH）   卖方力量主导
0.5 ≤ 比率 ≤ 2.0 → 中性（NEUTRAL） 买卖均衡
```

#### 3.4.2 持仓量（OI）变化分析

持仓量变化反映资金流向：

| OI 变化 | 含义 |
|---------|------|
| 增加 > 1% | 新资金入场（趋势延续） |
| 减少 > 1% | 资金离场（趋势衰竭） |
| 变化 ≤ 1% | 稳定，无显著资金流动 |

#### 3.4.3 OI-价格背离

价格与持仓量之间的背离提供强烈的反转信号：

| 价格走势 | OI 变化 | 信号 | 分值 |
|---------|---------|------|------|
| 创新高 | OI 下降 | **看跌背离** | -3 |
| 创新低 | OI 上升 | **看跌确认** | -2 |
| 创新低 | OI 下降 | **看涨背离** | +3 |
| 创新高 | OI 上升 | **看涨确认** | +2 |

**背离检测算法：**

```python
def detect_oi_divergence(prices, oi_values, lookback=20):
    recent_high = max(prices[-lookback:])
    recent_low = min(prices[-lookback:])
    current_price = prices[-1]
    current_oi = oi_values[-1]
    prev_oi = oi_values[-lookback]
    oi_change = (current_oi - prev_oi) / prev_oi

    if current_price >= recent_high * 0.99:  # 接近高点
        if oi_change < -0.01:
            return "BEARISH_DIVERGENCE", -3
        elif oi_change > 0.01:
            return "BULLISH_CONFIRMATION", +3
    elif current_price <= recent_low * 1.01:  # 接近低点
        if oi_change > 0.01:
            return "BEARISH_CONFIRMATION", -2
        elif oi_change < -0.01:
            return "BULLISH_DIVERGENCE", +3
    return "NEUTRAL", 0
```

#### 3.4.4 资金费率状态

| 费率范围 | 状态 | 说明 |
|---------|------|------|
| ≤ 0.01% | 正常（NORMAL） | 市场情绪平衡 |
| 0.01% – 0.05% | 偏高（ELEVATED） | 多头情绪过热，注意回调风险 |
| 0.05% – 0.1% | 高（HIGH） | 市场极度偏多，高风险 |
| > 0.1% | 极高（EXTREME） | 市场极度过热，强烈回调风险 |
| < 0% | 负值（NEGATIVE） | 空头付费给多头，市场偏空但可能反转 |

#### 3.4.5 综合确认

大单综合确认评分规则：

| 检查项 | 分值 | 说明 |
|--------|------|------|
| 大单方向一致 | +3 | 大单方向与信号方向相同 |
| 大单方向中性 | +1 | 无明显偏向 |
| 大单方向矛盾 | -2 | 大单方向与信号方向相反 |
| OI 方向确认 | +2 | OI 变化与信号方向一致 |
| OI 方向矛盾 | -1 | OI 变化与信号方向相反 |
| OI-价格看涨背离 | +3 | 价格创新低但 OI 下降 |
| OI-价格看跌背离 | -3 | 价格创新高但 OI 下降 |
| 资金费率有利 | +2 | 做多时费率正常/做空时费率高 |
| 资金费率不利 | -2 | 做多时费率过高 |

**确认阈值：综合得分 ≥ 4 分视为已确认。**

---

### 3.5 清算热力图（`liquidation_heatmap.py`）

#### 3.5.1 核心算法

清算热力图基于以下步骤计算：

1. 使用最近 168 根（7 天）1H K 线，以 VWAP 作为入场均价估计
2. 按杠杆分布权重将持仓量分配至各杠杆区间
3. 计算每个杠杆区间的做多/做空强平价
4. 按价格网格累加清算名义价值
5. 应用时间衰减
6. 输出热力图数据

**入场均价估计（VWAP）：**
```
VWAP = (High + Low + Close) / 3.0
```

#### 3.5.2 杠杆分布权重

权重基于 OKX 公开市场数据与主流交易所清算分布实证估计：

**加密货币（BTC/ETH）：**

| 杠杆倍数 | 权重 | 说明 |
|---------|------|------|
| 20x | 40% | 主力区间 |
| 50x | 30% | 高杠杆 |
| 10x | 20% | 保守仓 |
| 100x | 10% | 极端杠杆 |

**贵金属（XAU/XAG）：**

| 杠杆倍数 | 权重 | 说明 |
|---------|------|------|
| 10x | 40% | 主力区间 |
| 20x | 30% | 中等杠杆 |
| 5x | 20% | 保守仓 |
| 50x | 10% | 激进仓 |

#### 3.5.3 强平价计算

**做多强平价：**
```
P_liq = P_entry × (1 - 1/Leverage + MMR)
```

**做空强平价：**
```
P_liq = P_entry × (1 + 1/Leverage - MMR)
```

**维持保证金率（MMR，简化固定值）：**

| 品种 | MMR |
|------|-----|
| BTC | 0.004 |
| ETH | 0.005 |
| XAU | 0.006 |
| XAG | 0.006 |

#### 3.5.4 时间衰减

采用指数衰减模型：

```
衰减系数 = e^(-λt)
```

其中：
- `λ = 0.02`（衰减常数）
- `t` = 距当前的小时数
- 半衰期 ≈ 35 小时

#### 3.5.5 资金费率调整

高费率环境下高杠杆仓位权重增大：

```
杠杆乘数 = 1.0 + |资金费率| × 50
```

对杠杆 ≥ 50x 的仓位应用此乘数后重新归一化。

#### 3.5.6 清算评级

| 评级 | 条件 | 说明 |
|------|------|------|
| 5 星 | 总清算量 ≥ 5 倍均值 | 磁石级——强烈价格吸引 |
| 4 星 | ≥ 3 倍均值 | 强清算区 |
| 3 星 | ≥ 2 倍均值 | 中等清算区 |
| 2 星 | ≥ 1.5 倍均值 | 弱清算区 |
| 1 星 | < 1.5 倍均值 | 极弱 |

#### 3.5.7 价格网格精度

| 品种 | 网格步长 |
|------|---------|
| BTC | $50 |
| ETH | $2 |
| XAU | $5 |
| XAG | $0.05 |

---

### 3.6 信号生成（`signal_generator.py`）

信号生成器将所有分析模块综合为可执行的交易信号。

#### 3.6.1 做多条件（全部满足）

| 条件 | 说明 |
|------|------|
| 1. EMA 多头趋势 | 1D 级别 EMA144 > EMA169 |
| 2. 价格在支撑区间 | 4H 价格在支撑区间内或接近支撑位（<1%） |
| 3. 多头反转 K 线 | 1H 出现锤子线/看涨吞没/长下影阳线 |
| 4. 大单确认 | 综合得分 ≥ 4 |
| 5. 资金费率正常 | 资金费率 < 0.05% |

**做空条件为镜像：**
- EMA144 < EMA169
- 4H 价格在阻力区间
- 1H 出现射击之星/看跌吞没/长上影阴线
- 大单确认（空头方向）
- 资金费率条件放宽（高费率对做空有利）

#### 3.6.2 K 线形态识别

**多头反转形态：**

| 形态 | 条件 |
|------|------|
| 锤子线 | 下影线 > 实体×2，上影线 < 实体×0.5，收阳 |
| 看涨吞没 | 前阴后阳，当前实体完全包裹前一实体，且实体更大（>110%） |
| 长下影阳线 | 下影线占 K 线总长 > 60%，收阳 |

**空头反转形态：**

| 形态 | 条件 |
|------|------|
| 射击之星 | 上影线 > 实体×2，下影线 < 实体×0.5，收阴 |
| 看跌吞没 | 前阳后阴，当前实体完全包裹前一实体，且实体更大（>110%） |
| 长上影阴线 | 上影线占 K 线总长 > 60%，收阴 |

#### 3.6.3 止损止盈计算

**止损：**
- 做多：支撑区间下沿 − 1 ATR
- 做空：阻力区间上沿 + 1 ATR
- 无 S/R 区间时：当前价 ± 2 ATR

**止盈：**
- 做多：下一阻力位
- 做空：下一支撑位
- 无下一 S/R 时：当前价 ± 3 ATR

**最低盈亏比：** 2:1。若止盈不满足最低盈亏比，自动调整至风险的 2 倍。

#### 3.6.4 订单墙 Bonus

订单墙作为额外确认，不改变 checklist 结构：
- 做多时：支撑位附近有买单墙 → 额外确认
- 做空时：阻力位附近有卖单墙 → 额外确认

---

### 3.7 数据获取与存储

#### 3.7.1 两阶段数据获取策略（`okx_data.py`）

系统采用两阶段策略突破常规端点 1440 根限制：

**阶段 1：常规端点（`/market/candles`）**
- 从最新数据开始往回拉取
- 单次最多 300 根，分页使用 `after` 参数翻页
- 上限 1440 根

**阶段 2：历史端点（`/market/history-candles`）**
- 阶段 1 数据不足时触发
- 以阶段 1 最早 K 线的 `ts - 1ms` 作为起点向前回溯
- 数据有约 2 天延迟，最多回溯约 3 个月
- 单次最多 100 根

**对齐策略：** 阶段 2 的 `after` = 阶段 1 最早的 `ts - 1ms`，确保两条数据链在边界无缝衔接。

#### 3.7.2 网络容错

| 特性 | 实现 |
|------|------|
| 代理支持 | SOCKS5 代理（v2rayN/clash），通过 PySocks monkey-patch |
| 多链路回退 | 代理 + okx.com → 代理 + okx.cab → 直连 + okx.com |
| 请求间隔 | 0.5 秒，避免限流 |
| 重试策略 | 最多 5 次，指数退避（`0.5s × 2^attempt`） |
| 429 处理 | 指数退避（`0.5s × 4^attempt`） |

#### 3.7.3 数据库管理（`db_manager.py`）

| 特性 | 实现 |
|------|------|
| 引擎 | SQLite + WAL 模式 |
| 并发 | `check_same_thread=False`，支持跨线程访问 |
| 写入方式 | `INSERT OR REPLACE`（基于 inst_id + bar + ts 唯一键） |
| 批量写入 | 每 1000 条一批（`executemany`） |
| 索引 | `(inst_id, bar)` 和 `(inst_id, bar, ts)` |
| WAL 检查点 | `PRAGMA wal_checkpoint(PASSIVE)`，每小时执行 |

**表结构：**

```sql
CREATE TABLE IF NOT EXISTS candles (
    inst_id  TEXT NOT NULL,     -- 产品 ID
    bar      TEXT NOT NULL,     -- K 线周期 (1H/4H/1D)
    ts       INTEGER NOT NULL,  -- 毫秒时间戳
    open     REAL NOT NULL,
    high     REAL NOT NULL,
    low      REAL NOT NULL,
    close    REAL NOT NULL,
    vol      REAL,              -- 成交量
    volCcy REAL,                -- 成交额
    PRIMARY KEY (inst_id, bar, ts)
)
```

### 3.8 订单墙检测（`okx_data.py`）

#### 3.8.1 检测算法

订单簿中的大额挂单墙通过统计方法识别：

```
阈值 = 均值 + 5 × 标准差
```

其中均值和标准差基于所有档位的 `size` 计算。

**代码实现：**

```python
def detect_order_walls(orderbook, inst_id, multiplier=5.0):
    all_sizes = [b["size"] for b in bids] + [a["size"] for a in asks]
    mean_size = np.mean(all_sizes)
    std_size = np.std(all_sizes)
    threshold = mean_size + multiplier * std_size

    bid_walls = [b for b in bids if b["size"] > threshold]
    ask_walls = [a for a in asks if a["size"] > threshold]
```

#### 3.8.2 买卖比

```
买卖比 = 买单总量 / 卖单总量

买卖比 > 1.5  → 偏多
买卖比 < 0.67 → 偏空
```

**强度计算：** `strength = size / mean_size`（相对于均值的倍数）

---

## 4. 可视化

### 4.1 图表引擎

系统使用 **Lightweight Charts** 库进行图表渲染，提供专业级金融图表体验。

### 4.2 主题

采用深色主题，适合长时间盯盘：
- 背景色：深灰/黑色
- K 线：绿涨红跌（国际标准）
- 文字：浅灰色
- 网格线：深灰色

### 4.3 多周期切换

支持三种时间周期的独立分析和展示：
- **1H**：短线交易参考
- **4H**：日内波段参考
- **1D**：中长线趋势参考

### 4.4 叠加指标

- EMA144/EMA169 双线叠加于主图
- 支撑阻力区间以半透明矩形展示
- 清算热力图以颜色梯度展示

### 4.5 右侧面板

提供交易信号概览：
- 当前趋势方向和强度
- 最近支撑/阻力位
- 信号方向和信心度
- 大单方向和资金费率

---

## 5. 许可证与机器绑定

### 5.1 加密方案

系统使用 **AES-256-ECB** 模式对许可证载荷进行加密。

| 参数 | 值 |
|------|---|
| 算法 | AES-256 |
| 模式 | ECB |
| 载荷大小 | 12 字节 |
| 密钥生成 | SHA256(MAC 地址 + 磁盘序列号) |

### 5.2 机器绑定

许可证密钥与特定机器绑定，通过以下硬件标识生成：

1. 获取网络接口 MAC 地址
2. 获取系统磁盘序列号
3. 拼接后计算 SHA256 哈希
4. 哈希值作为 AES-256 密钥

此机制确保许可证仅在授权机器上有效。

---

## 6. 错误处理与容错

### 6.1 API 限流

- 429 响应时指数退避重试
- API 调用间最少 100ms 间隔
- 每次请求最多重试 3 次

### 6.2 数据验证

- K 线数据校验：O、H、L、C 必须为正数；H ≥ L；H ≥ max(O,C)；L ≤ min(O,C)
- 成交量必须非负
- 时间戳必须单调递增

### 6.3 优雅降级

- 某一时间周期失败时，其他周期继续运行
- 订单簿拉取失败时，分析仍可继续（不含订单簿数据）
- 数据库锁定时，指数退避重试

---

## 7. 性能考量

### 7.1 计算复杂度

| 操作 | 时间复杂度 | 说明 |
|------|-----------|------|
| EMA 计算 | O(n) | 单次遍历 K 线数据 |
| S/R 极值检测 | O(n × w) | n = K 线数，w = 窗口大小 |
| 成交量分布 | O(n × b) | b = 区间数（50） |
| 信号生成 | O(1) | 汇总预计算结果 |

### 7.2 内存占用

| 数据 | 每品种 |
|------|--------|
| 1H K 线（~1000 根 × 56 字节） | ~56 KB |
| 4H K 线（~500 根 × 56 字节） | ~28 KB |
| 1D K 线（~365 根 × 56 字节） | ~20 KB |
| **单品种合计** | **~104 KB** |
| **6 个品种合计** | **~624 KB** |

---

## 8. 配置参数总览

```python
# EMA 周期
EMA_FAST_PERIOD = 144
EMA_SLOW_PERIOD = 169

# S/R 检测
SR_WINDOW_SIZE = 3
SR_MERGE_THRESHOLD = 0.005  # 0.5%
SR_ZONE_WIDTHS = {
    'BTC': 0.003,  # ±0.3%
    'ETH': 0.004,  # ±0.4%
    'XAU': 0.003,  # ±0.3%
    'XAG': 0.005,  # ±0.5%
}

# 成交量分布
VP_NUM_BINS = 50
VP_VALUE_AREA_PCT = 0.70
VP_HVN_THRESHOLD = 0.30  # POC 成交量的 30%

# 大单门槛
BIG_ORDER_THRESHOLDS = {
    'BTC': 10,
    'ETH': 500,
    'XAU': 500,
    'XAG': 10000,
}

# 刷新间隔（秒）
CANDLE_REFRESH_CHECK = 60
LIVE_DATA_INTERVAL = 300
FRONTEND_REFRESH_MS = 10000
PROGRESS_POLL_MS = 500
DB_MAINTENANCE_INTERVAL = 3600
```

---

## 9. 已知限制

1. **API 限流**：OKX 公开 API 有频率限制（每 IP 每 2 秒 20 次请求）。多品种并发分析时可能触发限流。

2. **历史数据深度**：OKX 公开 API 单次最多返回 1000 根 K 线。更长历史需要多次分页请求。

3. **清算数据延迟**：清算订单可能有最多 1 分钟的延迟。

4. **现货与合约价格偏离**：现货和永续合约价格可能出现偏离，尤其在高波动期间。系统独立分析两者。

5. **单线程分析**：每个品种的分析顺序执行。多品种并行分析需要额外的锁机制复杂度。

---

## 10. 未来增强

- [ ] WebSocket 支持实时流式数据
- [ ] 基于机器学习的形态识别
- [ ] 信号回测验证框架
- [ ] 多交易所支持（Binance、Bybit）
- [ ] Webhook/邮件告警系统
- [ ] 组合级风险管理

---

## 许可声明

本项目按"原样"提供，仅供教育和研究目的。交易涉及重大风险。本系统不提供任何财务建议。

---

*文档版本：1.0*
*最后更新：2026-06-01*