# Multi-dimensional Support/Resistance Resonance Analyzer

> **Entry point: `python okxtrading.py`** | Dependencies: `pip install -r requirements.txt`
>
> ⚠️ **First run will be slow** — the system fetches ~11,000 historical candles (1H + 4H + 1D) from OKX public API for each instrument. This can take 5-10 minutes depending on network conditions. Data is cached locally in SQLite (`data/trading.db`), so **subsequent runs load in seconds** with only incremental updates.

---

## What Is This

A multi-factor trading signal system for crypto perpetual swaps (BTC, ETH, XAU, XAG) and spot markets. Instead of relying on a single indicator, it identifies **high-probability trade zones** where multiple independent analysis layers converge — structural support/resistance, volume profile, big order flow, liquidation clusters, and funding rate dynamics — then generates actionable LONG/SHORT/NEUTRAL signals with entry, stop-loss, and take-profit levels.

The core idea: **a price level supported by 3+ independent factors is exponentially more reliable than one supported by just 1.**

---

## The Resonance System

### What "Resonance" Means

In physics, resonance occurs when multiple oscillations amplify each other at the same frequency. Applied to markets: when structurally significant price levels are confirmed by volume distribution, order flow, and liquidation pressure simultaneously, the probability of a meaningful price reaction increases dramatically.

This system scores every S/R level across **6 independent dimensions**:

| Dimension | What It Measures | Score |
|-----------|-----------------|-------|
| **Structural S/R** | Local extrema on daily chart (swing highs/lows) | +1 per touch (max 5) |
| **Reversal Strength** | Price bounced >2× ATR from the level | +2 |
| **Psychological Levels** | Round numbers (BTC ±1000, ETH ±100, XAU ±50) | +1 |
| **Fair Value Gap (FVG)** | Unfilled price gaps on daily timeframe | +1 |
| **Liquidation Cluster** | Estimated liquidation density at that price | +1 |
| **Order Wall** | Large resting orders in the orderbook at/near the level | +1 |

**Score ≥ 5 → Super** | **3-4 → Strong** | **1-2 → Weak**

A "Super" S/R level has been tested multiple times, produced significant reversals, sits at a round number, has a FVG nearby, shows liquidation accumulation, AND has visible order wall support. These are the levels where the market has repeatedly said "this price matters."

### Volume Profile Resonance

Beyond static S/R, the system runs **Volume Profile analysis** on 1H candles to find where the market actually traded the most:

- **POC (Point of Control)** — the single price level with the highest traded volume. This is where the market "agreed" the most. When POC falls inside an S/R zone → **Strong Resonance**
- **High Volume Nodes (HVN)** — price levels with >30% of POC volume. These act as magnets. When HVN overlaps S/R → **Normal Resonance**
- **Value Area** — the price range containing 70% of all volume. VA boundaries often act as support/resistance. When VA boundary falls inside S/R → **Weak Resonance**

The resonance badge (⚡) appears directly on the dashboard next to each S/R level, giving you instant visual confirmation of multi-factor confluence.

### The Signal Checklist

A trade signal is only generated when **all 5 conditions pass**:

```
For LONG:
  ✅ EMA144 > EMA169 (bullish daily trend)
  ✅ 4H price is at/near a support zone
  ✅ 1H shows a bullish reversal candle (Hammer / Bullish Engulfing / Long Lower Shadow)
  ✅ Big order confirmation (buy volume > 2× sell volume)
  ✅ Funding rate < 0.05% (market not overheated)

For SHORT: mirror conditions
```

If even one condition fails → NEUTRAL (no signal). No partial signals, no "almost" trades.

Additionally, **order walls** near S/R zones provide bonus confirmation — if a bid wall sits near support, or an ask wall sits near resistance, the signal reason log notes it as extra confluence.

---

## System Architecture

```
okx_data.py          → OKX API data fetching (SOCKS5 proxy, multi-link fallback, 2-phase history)
db_manager.py         → SQLite WAL cache (incremental, avoids redundant API calls)
ema_analyzer.py       → EMA144/169 trend analysis (golden cross / death cross / entangled)
sr_analyzer.py        → S/R identification + scoring (structural + psychological + FVG)
vp_analyzer.py        → Volume Profile (POC, Value Area, HVN, S/R-VP resonance)
big_order_detector.py → Big order detection + OI-price divergence + funding rate
liquidation_heatmap.py → Liquidation heatmap (leverage distribution model, time decay)
signal_generator.py   → Multi-factor signal generation with checklist
chart_builder.py      → Interactive HTML dashboard (Lightweight Charts, dark theme)
okxtrading.py         → Main orchestrator (HTTP server, pywebview, timed refresh)
```

### Timing & Refresh

| Data | Refresh Interval | Method |
|------|-----------------|--------|
| 1H candles | Every 30 min (aligned to :00/:30) | Incremental from API |
| 4H candles | Every 2 hours (aligned to even hours) | Incremental from API |
| 1D candles | Every 12 hours (00:00 / 12:00 UTC) | Incremental from API |
| Orderbook + Liquidation | Every 5 minutes | Background thread |
| Real-time prices | Every 10 seconds | Frontend fetch `/api/tickers` |
| DB WAL checkpoint | Every hour | Prevents WAL file bloat |

---

## Why These Indicators

### EMA144/169 (not the usual 12/26/50/200)

Most traders use short-period EMAs that generate excessive noise. The 144/169 pair is based on Fibonacci numbers and operates on the **daily timeframe**, filtering out intraday noise to capture the true macro trend. The separation between them also indicates trend maturity — early (<1%), mid (1-4%), overheated (>4%). When they're entangled (<0.3%), the market has no direction and trading is a coin flip.

### Structural S/R + Scoring (not just "look left")

Drawing horizontal lines at old highs/lows is table stakes. This system goes further: it counts how many times a level was tested, measures the reversal strength against ATR, checks if it's a psychological round number, verifies FVG presence, cross-references liquidation density, AND confirms with orderbook walls. A level that scores 5+ across these dimensions has earned its status through market evidence, not chartist opinion.

### Volume Profile (the missing dimension)

Price-based S/R tells you *where* the market reacted. Volume Profile tells you *where the market committed capital*. A price level that looks like support on a chart but has zero volume traded there is a mirage. Conversely, a high-volume node that coincides with structural support is a fortress. The POC is particularly powerful — it's the price the market spent the most time and money at, making it a natural magnet and pivot.

### Big Order Detection + OI Divergence

Retail traders look at price. Smart money leaves footprints in order flow. When buy-side big orders (>10 BTC, >500 ETH) outnumber sell-side by 2:1+ at a support level, that's institutional accumulation. The OI-price divergence adds another layer: price making new highs while OI drops = bearish divergence (smart money exiting). Price making new lows while OI drops = bullish divergence (shorts covering). These are signals the chart alone can't show.

### Liquidation Heatmap (simulated)

Liquidation cascades are the market's nuclear events — forced selling/buying that creates violent moves. This system simulates liquidation density by distributing open interest across leverage tiers (10x/20x/50x/100x), computing where forced close prices cluster, and applying time decay (half-life ~35 hours). The result: you know where the "landmines" are before price reaches them. A "Magnet" rated liquidation zone near a Super S/R level is where the next cascade will likely trigger.

### Funding Rate as Regime Filter

Funding rate is the market's sentiment thermometer. At 0.01% it's neutral. Above 0.05% the longs are paying too much — the market is overcrowded and vulnerable to a squeeze. Below 0% (negative) the shorts are paying — bearish sentiment but potential reversal. The system uses this as a **gate**: it won't go long when funding is elevated (overheated longs), and it treats negative funding as a green light for longs (contrarian). For shorts, high funding is actually favorable (shorts collect the fee).

---

## Quick Start

```bash
# Install dependencies
pip install pandas numpy requests pywebview matplotlib plotly pycryptodome

# Run (opens desktop window automatically)
python okxtrading.py

# Or specify instruments
python okxtrading.py --instruments BTC ETH

# Force refresh (ignore local cache)
python okxtrading.py --refresh
```

## Output

The system generates an interactive HTML dashboard with:
- Candlestick chart (1H/4H/1D switching) with EMA144/169 overlay
- S/R price lines with resonance badges
- Order wall visualization
- Right panel: trend, S/R list, liquidation heatmap, order walls, Volume Profile, big orders, signal card, checklist

## Disclaimer

This system is for educational and research purposes only. It does not constitute investment advice. Trading cryptocurrency perpetual contracts carries substantial risk of loss. Use at your own risk.

---

# 多维支撑阻力共振分析器

> **程序入口：`python okxtrading.py`** | 安装依赖：`pip install -r requirements.txt`
>
> ⚠️ **首次运行会很慢** — 系统会从 OKX 公开 API 抓取每个品种约 11,000 根历史 K 线（1H + 4H + 1D），根据网络状况可能需要 5-10 分钟。数据会缓存到本地 SQLite 数据库（`data/trading.db`），**之后每次打开只需增量更新，几秒钟即可完成。**

---

## 这是什么

一个面向加密货币永续合约（BTC、ETH、XAU、XAG）和现货市场的多因子交易信号系统。它不依赖单一指标，而是让多个独立分析维度在**同一价位共振** — 结构性支撑阻力、成交量分布、大单流向、清算集群、资金率动态 — 然后生成可执行的 LONG/SHORT/NEUTRAL 信号，附带入场价、止损位和止盈位。

核心理念：**一个被 3 个以上独立维度确认的价位，其可靠性呈指数级增长。**

---

## 共振系统

### 什么是"共振"

物理学中，共振是多个振荡在同频率上相互放大的现象。映射到市场：当结构性关键价位同时被成交量分布、订单流和清算压力确认时，产生有意义价格反应的概率大幅提升。

系统对每个 S/R 价位进行 **6 个独立维度评分**：

| 维度 | 衡量内容 | 得分 |
|------|---------|------|
| **结构性 S/R** | 日线局部极值（波段高低点） | 每次触及 +1（最多5分） |
| **反转强度** | 价格从该位反弹 >2 倍 ATR | +2 |
| **心理关口** | 整数关口（BTC ±1000，ETH ±100，XAU ±50） | +1 |
| **FVG 缺口** | 日线级别未回补的跳空缺口 | +1 |
| **清算集群** | 该价位的估算清算密度 | +1 |
| **订单墙** | 订单簿中该价位附近的巨额挂单 | +1 |

**≥5 分 → 超级** | **3-4 → 强** | **1-2 → 弱**

一个"超级"支撑阻力位，是被市场反复测试过、产生过显著反转、恰好是整数关口、附近有 FVG、有清算堆积、还有订单墙撑腰的价位。这些是市场反复表态"这个价格很重要"的地方。

### Volume Profile 共振

在静态 S/R 之上，系统对 1H K 线运行 **Volume Profile 分析**，找到市场实际成交最密集的区域：

- **POC（控制点）** — 成交量最大的单一价位。这是市场"共识"最强的价格。当 POC 落在 S/R 区间内 → **强共振**
- **高成交量节点（HVN）** — 成交量超过 POC 30% 的价位。它们像磁铁一样吸引价格。HVN 与 S/R 重叠 → **普通共振**
- **价值区间（Value Area）** — 包含 70% 总成交量的价格范围。VA 边界本身常充当支撑/阻力。VA 边界落在 S/R 区间内 → **弱共振**

共振徽章（⚡）直接显示在仪表盘每个 S/R 价位旁边，让你一眼看出多因子汇合。

### 信号检查清单

交易信号**必须 5 个条件全部通过**才会生成：

```
做多条件：
  ✅ EMA144 > EMA169（日线多头趋势）
  ✅ 4H 价格在/接近支撑区间
  ✅ 1H 出现多头反转 K 线（锤子线 / 看涨吞没 / 长下影线）
  ✅ 大单确认（主动买入量 > 2 倍卖出量）
  ✅ 资金费率 < 0.05%（市场未过热）

做空：镜像条件
```

任何一个条件不满足 → NEUTRAL（不给信号）。没有"部分信号"，没有"差不多可以"。

此外，S/R 附近的**订单墙**提供 bonus 确认 — 如果买单墙在支撑位附近，或卖单墙在阻力位附近，信号日志会标注为额外共振。

---

## 系统架构

```
okx_data.py          → OKX API 数据获取（SOCKS5 代理、多链路回退、两阶段历史拉取）
db_manager.py         → SQLite WAL 缓存（增量更新，避免重复请求）
ema_analyzer.py       → EMA144/169 趋势分析（金叉/死叉/缠绕）
sr_analyzer.py        → 支撑阻力识别 + 评分（结构性 + 心理关口 + FVG）
vp_analyzer.py        → Volume Profile（POC、价值区间、高成交量节点、S/R-VP 共振）
big_order_detector.py → 大单检测 + OI-价格背离 + 资金费率
liquidation_heatmap.py → 清算热力图（杠杆分布模型、时间衰减）
signal_generator.py   → 多因子信号生成（检查清单机制）
chart_builder.py      → 交互式 HTML 仪表盘（Lightweight Charts、深色主题）
okxtrading.py         → 主程序编排（HTTP 服务器、pywebview、定时刷新）
```

### 定时刷新机制

| 数据 | 刷新间隔 | 方式 |
|------|---------|------|
| 1H K 线 | 每 30 分钟（对齐 :00/:30） | API 增量获取 |
| 4H K 线 | 每 2 小时（对齐偶数小时） | API 增量获取 |
| 1D K 线 | 每 12 小时（00:00 / 12:00 UTC） | API 增量获取 |
| 订单簿 + 清算数据 | 每 5 分钟 | 后台线程 |
| 实时价格 | 每 10 秒 | 前端 fetch `/api/tickers` |
| 数据库 WAL 检查点 | 每小时 | 防止 WAL 文件膨胀 |

---

## 为什么选择这些指标

### EMA144/169（不是常见的 12/26/50/200）

大多数交易者使用的短周期 EMA 会产生过多噪音。144/169 这对基于斐波那契数列的参数在**日线级别**运作，过滤掉日内噪音，捕捉真正的宏观趋势。两者之间的分离度还能指示趋势成熟度 — 初期（<1%）、中期（1-4%）、过热（>4%）。当分离度极小（<0.3%）时，市场没有方向，交易等于掷硬币。

### 结构性 S/R + 评分（不只是"看左边"）

在历史高低点画水平线是基本功。这个系统更进一步：统计价位被测试的次数、用 ATR 衡量反转强度、检查是否是心理整数关口、验证 FVG 是否存在、交叉引用清算密度、用订单簿大单墙确认。一个评分 5+ 的价位，是通过市场证据赢得其地位的，不是画线师的主观判断。

### Volume Profile（被忽视的维度）

基于价格的 S/R 告诉你市场在哪里*反应过*。Volume Profile 告诉你市场在哪里*投入过真金白银*。一个在图表上看起来像支撑但成交量为零的价位是海市蜃楼。反之，一个高成交量节点与结构性支撑重合的价位就是堡垒。POC 尤其强大 — 它是市场花最多时间和资金的价位，天然就是磁铁和枢轴点。

### 大单检测 + OI 背离

散户看价格。聪明钱在订单流里留下脚印。当买单大单（>10 BTC、>500 ETH）在支撑位以 2:1+ 的比例压倒卖单，那就是机构在吸筹。OI-价格背离增加另一层：价格创新高但 OI 下降 = 看跌背离（聪明钱在撤退）。价格创新低但 OI 下降 = 看涨背离（空头在平仓）。这些是图表本身看不到的信号。

### 清算热力图（模拟计算）

清算瀑布是市场的核爆事件 — 强制买卖引发剧烈波动。这个系统通过将持仓量分配到杠杆层级（10x/20x/50x/100x）、计算强平价格集群位置、应用时间衰减（半衰期约 35 小时）来模拟清算密度。结果：你在价格到达之前就知道"地雷"在哪里。一个评级为"磁石"的清算区紧挨着一个超级 S/R 位，就是下一个清算瀑布最可能触发的地方。

### 资金费率作为市场过滤器

资金费率是市场的情绪温度计。0.01% 是中性。超过 0.05% 意味着多头付费过多 — 市场过度拥挤，容易被轧空。低于 0%（负值）意味着空头在付费 — 偏空情绪但可能反转。系统用它作为**闸门**：资金费率偏高时不做多（多头过热），负费率反而给做多开绿灯（逆向思维）。做空时，高费率反而有利（空头收取费用）。

---

## 快速开始

```bash
# 安装依赖
pip install pandas numpy requests pywebview matplotlib plotly pycryptodome

# 运行（自动打开桌面窗口）
python okxtrading.py

# 指定品种
python okxtrading.py --instruments BTC ETH

# 强制刷新（忽略本地缓存）
python okxtrading.py --refresh
```

## 输出

系统生成交互式 HTML 仪表盘，包含：
- K 线图（1H/4H/1D 切换）+ EMA144/169 叠加
- S/R 价格线 + 共振徽章
- 订单墙可视化
- 右侧面板：趋势、S/R 列表、清算热力图、订单墙、Volume Profile、大单、信号卡、检查清单

## 免责声明

本系统仅供学习和研究使用，不构成任何投资建议。加密货币永续合约交易存在重大亏损风险。使用风险自担。
