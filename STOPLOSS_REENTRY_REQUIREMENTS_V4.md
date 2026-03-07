# Stoploss-Reentry 改造需求说明（V4）

## 1. 背景与目标

当前策略在极端下跌和震荡行情中存在两类问题：

1. `死扛到底` 带来的尾部风险过大（接近归零风险）。
2. 止损与回补若处理不当，可能出现反复平进平出/磨损。

本改造目标：

1. 引入“止损清仓 + 条件回补 + 恢复maker波段”的闭环。
2. 最大化规避平进平出和反复磨损。
3. 在程序重启、数据偶发缺失、缓存抖动场景下保持稳定运行。

---

## 2. 适用范围与总原则

1. 本机制作为独立风险层，覆盖 classic/aggressive 下的止损执行路径。
2. classic/aggressive 仅负责常规 maker 波段信号，不直接执行止损/回补判定。
3. 所有价格判定优先使用可成交 taker 价格，不使用中间价。

---

## 3. 核心策略规则（最终口径）

### 3.1 止损规则

1. 初始止损阈值：`5%`（后续每轮递增，见 3.5）。
2. 若轮询时已穿透阈值（如已到 -10%），按实际可成交价格止损清仓。
3. 记录实际止损成交价：`stop_exit_price`（后续回补唯一锚点）。

### 3.2 回补价格区间（硬限制）

1. 回补线：`reentry_line = stop_exit_price - 2*tick`（区间上界）。
2. 回补区间下界：`stop_exit_price - 2%`。
3. 仅当可成交 taker 买入价落入以下区间时，允许回补：
   - `stop_exit_price - 2% <= taker_buy_price <= stop_exit_price - 2*tick`
4. 区间外一律不回补（宁可错过，不追高）。

### 3.3 回补前置结构（防半山腰接刀）

必须满足“先下探，再回弹”：

1. 先向下有效跌破回补线：
   - `probe_line = reentry_line * (1 - 5%)`
   - 即先出现价格到达/低于 `probe_line`。
2. 在已下探成立后，再回弹进入 3.2 定义的回补价格区间，才允许回补。
3. 下探与回弹均需连续命中确认（默认 `2` 次轮询）。

### 3.4 时间间隔

1. 清仓后到回补最短间隔：`2h`。
2. 回补后到下一次可清仓最短间隔：`30min`。
3. 第 5 次清仓后：每次回补额外 `+24h` 等待。
   - 仅影响“清仓->回补”等待。
   - 不影响“回补->下一次清仓”最短间隔（仍为 30min）。

### 3.5 轮次递进（无上限、无衰减）

1. 每完成一轮“回补后再次清仓”，下一轮止损阈值在上轮基础上 `+1%`。
2. 不设上限，不做时间衰减。
3. 预期效果：后期更不易再触发清仓，减少噪声磨损。

### 3.6 回补后行为

1. 回补完成后进入 `REENTRY_HOLD`，先不恢复 normal maker 卖单节奏。
2. 当价格满足“旧 maker 地板价 + 盈利阈值”后，才恢复 `NORMAL_MAKER`。

---

## 4. 熔断机制（按 token，不是全局）

1. 熔断粒度：**token 级别**。
2. 条件：某 token 当天累计已实现亏损 `>=10%`。
3. 动作：仅该 token 当天暂停回补（`reentry_paused_for_day=true`）。
4. 其他 token 不受影响。
5. 日期切换后重置该 token 当日累计字段。

---

## 5. 状态机定义

1. `NORMAL_MAKER`
2. `STOPLOSS_EXITED_WAITING_WINDOW`（清仓后等待最小回补间隔）
3. `STOPLOSS_EXITED_WAITING_PROBE`（等待先下探）
4. `STOPLOSS_EXITED_WAITING_REBOUND`（已下探，等待回弹回补线）
5. `REENTRY_HOLD`（已回补，等待恢复maker条件）
6. `CLOSED_FINAL`（市场关闭/已结算，终止）

状态迁移必须可跨进程恢复，不依赖内存临时变量。

---

## 6. 持久化与容灾

### 6.1 主存储位置（防 data 文件夹误删）

放在 copytrade 侧：

1. 主文件：`copytrade/stoploss_reentry_state.json`
2. 备份：`copytrade/stoploss_reentry_state.bak.json`

可选镜像（运行态缓存）：

1. `POLYMARKET_MAKER_AUTO/data/stoploss_reentry_state.cache.json`（非真源）

### 6.2 读写策略

1. 写入使用原子写（tmp + replace）。
2. 启动时优先读主文件，损坏则回退到 `.bak`。
3. 每轮更新后同步刷新 `.bak`。

---

## 7. 记录字段（每个 token）

至少包含以下字段：

1. `token_id`
2. `state`
3. `stoploss_cycle_count`
4. `next_stoploss_threshold_pct`
5. `stop_exit_price`
6. `stop_exit_ts`
7. `reentry_line_price`
8. `probe_line_price`
9. `probe_seen`
10. `probe_seen_ts`
11. `rebound_confirm_hits`
12. `reentry_earliest_ts`
13. `next_stoploss_earliest_ts`
14. `old_maker_floor_price`
15. `old_entry_price`
16. `old_last_buy_price`
17. `today_realized_loss_pct`
18. `reentry_paused_for_day`
19. `source_detached`
20. `market_status_last`
21. `last_price_check_ts`
22. `last_error`
23. `version`

---

## 8. 清理与一致性规则

### 8.1 跟随账户清仓

1. 若检测到目标账户该 token 已清仓，且本地无持仓：立即清理该 token 状态记录。
2. 若目标账户清仓但本地仍有持仓：保留记录并标记 `source_detached=true`，仅做本地风险处置，不再继续跟随回补。
3. 本地风险处置优先复用现有 sell/cleanup 链路（`_apply_sell_signals -> pending_exit_topics -> _schedule_pending_exit_cleanup -> _start_exit_cleanup`），避免新增并行清仓分支。

### 8.2 市场关闭

1. 市场 `CLOSED/RESOLVED`：立即清理该 token stoploss/reentry 记录。

### 8.3 防误删

1. token 不再跟随且本地无持仓时，延迟 1 个轮询周期再清理，避免数据抖动误判。

---

## 9. 防抖与异常保护

1. 下探判定与回弹判定均采用连续命中确认（默认 2 次）。
2. 数据缺失（bid/ask 无效、时间戳过旧、API异常）时：
   - 本轮只观测，不推进交易动作。
3. 持仓读取冲突（cache/stale-cache 抖动）时：
   - 不执行关键状态迁移，等待下一轮确认。
4. 所有交易动作执行前再次校验 market status，避免关市误操作。

---

## 10. 日志与可观测性

统一日志标签：

1. `[STOPLOSS]`
2. `[REENTRY_PROBE]`
3. `[REENTRY_REBOUND]`
4. `[REENTRY_EXEC]`
5. `[REENTRY_HOLD]`
6. `[RISK_GUARD]`
7. `[STATE_SYNC]`

要求：每次状态迁移、阈值更新、清理动作必须打结构化日志。

---

## 11. 代码改造范围

1. `POLYMARKET_MAKER_AUTO/POLYMARKET_MAKER/Volatility_arbitrage_run.py`
2. `POLYMARKET_MAKER_AUTO/poly_maker_autorun.py`
3. 必要的状态读写工具（建议新建模块统一管理 stoploss/reentry 状态）

---

## 12. 测试要求（新增）

至少覆盖：

1. 穿透止损执行（5% 目标但 10% 成交）。
2. 未先下探不回补。
3. 先下探后回弹才回补。
4. 回补线高于允许价格时不回补。
5. 清仓->回补 2h 生效。
6. 回补->下一次清仓 30min 生效。
7. 第 5 次后回补额外 +24h 生效。
8. token 级 10% 熔断仅影响该 token。
9. 市场关闭/目标账户清仓时正确清理。
10. 重启后状态恢复连续。

---

## 13. 验收标准

1. 不再出现“未先下探即半山腰回补”。
2. 回补不允许追高到 `stop_exit_price` 之上。
3. 震荡场景中平进平出显著下降。
4. 程序重启后 stoploss/reentry 状态不丢失。
5. token 级熔断与清理逻辑稳定可复现。
