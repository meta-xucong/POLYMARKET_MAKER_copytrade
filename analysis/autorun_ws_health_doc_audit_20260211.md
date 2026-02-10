# WS 健康度复盘：官方文档对照 + 当前实现审计

## 1) 先给结论（TL;DR）

- 你的主判断是对的：这次并发衰减的“触发器”大概率是 **WS 健康度下降**，而不是纯调度参数问题。
- 代码整体方向（单连接聚合 + 增量订阅 + 自动重连）是合理的，但有几处与官方消息模型的“落差”，会放大健康问题：
  1. `book` 事件解析不充分（只读 `best_bid/bid`，没解析官方 `buys/sells` 盘口数组）。
  2. 对 `best_bid_ask` / `tick_size_change` 等消息利用不足，导致可用行情被丢弃。
  3. silence guard 固定 600s，在低活跃 token 场景下容易“误判静默 -> 主动重连”。
  4. 订阅成功判定过于乐观（send 成功即计入 subscribed），缺少服务端 ACK 或事件到达确认。
  5. open 握手发空 `assets_ids: []` 再增量订阅虽可用，但不是官方 quickstart 的推荐形态。

## 2) 官方文档关键点（本次复查）

### 2.1 连接与订阅

官方 WSS 概览：连接后要发送包含 `type` + `assets_ids`（market）或 `markets`（user）的订阅消息；随后可用 `operation=subscribe/unsubscribe` 做增量订阅。还支持 `custom_feature_enabled`。  
来源：
- https://docs.polymarket.com/developers/CLOB/websocket/wss-overview.md
- https://docs.polymarket.com/quickstart/websocket/WSS-Quickstart.md

### 2.2 Market Channel 事件

官方 market channel 至少包括：`book`、`price_change`、`last_trade_price`、`tick_size_change`、`best_bid_ask`（后两者含 feature/条件）。其中：
- `book` 结构文档写的是 `buys/sells`（示例里也出现 `bids/asks`）；
- `price_change` 新 schema（2025-09-15 生效）使用 `price_changes[]`，每条里有 `asset_id/best_bid/best_ask`。  
来源：
- https://docs.polymarket.com/developers/CLOB/websocket/market-channel.md
- https://docs.polymarket.com/developers/CLOB/websocket/market-channel-migration-guide.md

## 3) 你的实现对照审计（逐项）

## A. 连接管理：总体合理

你当前实现：
- 单连接 + 自动重连 + 指数退避；
- `run_forever` 里使用 websocket 底层 ping/pong（`ping_interval/ping_timeout`）；
- 文本 `PING` 默认关闭，避免双心跳抖动。  
代码位置：`WSAggregatorClient`。  

结论：这部分方向是正确的，不是明显“违规用法”。

## B. 握手消息：可用，但不是最优实践

当前做法：`on_open` 先发 `{"type":"market","assets_ids":[]}`，再靠 flush 线程发送 `operation=subscribe`。  
代码：`Volatility_arbitrage_main_ws.py`。  

风险：
- 在网络波动或连接抖动时，`open -> empty subscribe -> delayed flush` 之间存在窗口期；
- quickstart 示例更直接：on_open 就带首批 `assets_ids`。

结论：不是错误，但可优化。

## C. 事件解析：这里是“健康度变差”的核心嫌疑

当前在 `poly_maker_autorun._on_ws_event` 中：
- `price_change` 路径基本跟上新 schema（读取 `price_changes`）✅；
- `book`/`tick` 路径只尝试读 `best_bid/bid` 和 `best_ask/ask`，**未解析 `buys/sells` 或 `bids/asks` 队列首档**。  

这会造成：
- 明明收到 `book`，但算不出 bid/ask，就写不进缓存；
- 下游看到“bid None / 数据陈旧”，触发降级和超时，进一步造成退出与并发下滑。

结论：这是优先级最高的优化点。

## D. 事件覆盖面不足

目前对 `best_bid_ask`、`tick_size_change` 没有专门处理（会落入过滤统计）。

影响：
- 丢掉可直接更新 top-of-book 的消息；
- 在 `price_change` 稀疏时，更容易被判“WS 无有效行情”。

## E. silence guard 策略偏“激进”

`_silence_timeout = 600`，10 分钟无消息就主动 close 重连。  
若订阅组合进入低成交时段，这可能是“正常静默”而非断线。

影响：
- 不必要的重连（connect_count 抬高）；
- 恢复窗口期间 bid/ask 可用性波动。

## F. 订阅状态确认偏乐观

`subscribe` 发送成功后，立即将 token 计入 `_subscribed_ids`，缺少服务端 ACK 或首条事件确认。

影响：
- 若 send 成功但服务端未真正生效，统计层会“看起来已订阅”，但实盘无数据；
- 排障难度变高。

## 4) 和你的日志现象如何对上

你日志里出现了这些组合信号：
- `WebSocket bid 返回 None（连续 N 次）`，触发回退 REST；
- `连接次数`上升，且“已订阅 token 数”下降；
- 运行池随时间从双位数跌到 2。  

这与上面的 C/D/E 三点高度一致：
- 事件在来，但解析/利用率不足；
- 低活跃阶段触发过多重连；
- 下游将 WS 质量问题映射为“无信号超时退出”。

## 5) 这套逻辑“是否合理”？

- **架构层面：合理。**（聚合订阅、共享缓存、自动重连都对）
- **实现细节：存在几个关键不合理点**（主要是事件解析与健康判定）。
- 换句话说：你不是“方向错了”，而是“最后一公里没打磨到官方消息模型”。

## 6) 可执行优化方案（按优先级）

1. **P0：修正 `book` 解析**
   - 兼容 `buys/sells` 与 `bids/asks` 两种字段；
   - 从买一/卖一计算 `best_bid/best_ask`，无论是否有 `best_bid_ask` 消息都能更新缓存。

2. **P0：纳入 `best_bid_ask` 事件处理**
   - 直接写缓存 top-of-book；
   - 在初始化订阅消息里可尝试打开 `custom_feature_enabled`（按官方开关语义）。

3. **P1：silence guard 自适应**
   - 由固定 600s 改为“按订阅 token 的近期事件频率动态阈值”；
   - 或至少在低活跃时段提升阈值到 20~30 分钟。

4. **P1：订阅生效确认机制**
   - 将 send-success 与 subscribed 分离；
   - 以“收到该 token 任一市场事件”作为生效确认，再计入稳定订阅数。

5. **P2：健康度分层**
   - 区分“连接健康（TCP/WS 存活）”和“行情健康（可用 bid/ask 更新率）”；
   - 调度层用行情健康作 timeout 决策，避免把连接抖动放大成策略退出。

## 7) 关键代码位置（便于你快速改）

- WS 连接器：
  - `POLYMARKET_MAKER_AUTO/POLYMARKET_MAKER/Volatility_arbitrage_main_ws.py`
  - `on_open` 初始订阅与辅助线程；`_flush_pending_subscriptions` 订阅状态维护。
- 事件解析与缓存写入：
  - `POLYMARKET_MAKER_AUTO/poly_maker_autorun.py::_on_ws_event`

