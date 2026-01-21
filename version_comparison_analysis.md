# 新旧版本对比分析报告

## 问题症状
- **原版本（独立WS）：** 很容易达成下单条件
- **新版本（聚合WS）：** 运行很久也不下单

## 架构差异

### 原版本 (POLYMARKET_MAKER_copytrade_v2_原版参考)
```
每个token子进程 → 独立WS连接 → 实时接收事件 → 立即处理 → 调用策略
```

### 新版本 (POLYMARKET_MAKER_copytrade_v2)
```
聚合器线程 → 单个WS连接订阅所有token → 写入共享缓存文件
                                           ↓
子进程 → 轮询读取缓存文件 (0.2秒/次) → 去重处理 → 调用策略
```

---

## 🔴 关键问题1：独立WS模式下的60秒限流 (P0问题)

**文件：** `Volatility_arbitrage_run.py` (新版本)
**位置：** 第2487-2489行

### 问题代码
```python
def _on_event(ev: Dict[str, Any]):
    nonlocal market_closed_detected, last_event_processed_ts, last_signal_ts
    # ... 省略前面的代码 ...

    now = time.time()
    if now - last_event_processed_ts < 60.0:  # ❌ 问题在这里
        return
    last_event_processed_ts = now

    for pc in pcs:
        # 处理价格变化，调用 strategy.on_tick()
```

### 影响分析
1. **策略更新延迟：** 即使独立WS模式收到实时事件，也要60秒才处理一次
2. **错过下单机会：** 价格在60秒内的波动完全被忽略
3. **状态机卡住：** 策略状态机无法及时响应市场变化

### 原版本对比
```python
def _on_event(ev: Dict[str, Any]):
    # ... 省略前面的代码 ...

    # ✅ 没有限流逻辑，每次事件都立即处理
    for pc in pcs:
        if str(pc.get("asset_id")) != str(token_id):
            continue
        bid, ask, last = _parse_price_change(pc)
        latest[token_id] = {"price": last, "best_bid": bid, "best_ask": ask, "ts": ts}
        action = strategy.on_tick(best_ask=ask, best_bid=bid, ts=ts)  # 立即调用
        if action and action.action in (ActionType.BUY, ActionType.SELL):
            action_queue.put(action)
```

---

## 🟡 关键问题2：共享WS模式的去重和频率限制

**文件：** `Volatility_arbitrage_run.py` (新版本)
**位置：** `_apply_shared_ws_snapshot()` 函数，第2720-2732行

### 去重逻辑
```python
if seq == _apply_shared_ws_snapshot._last_seq:
    # seq相同，检查updated_at
    if updated_at > _apply_shared_ws_snapshot._last_updated_at:
        # 同一seq但时间戳变大，可能是数据修正
        should_feed_strategy = True
    else:
        # seq和updated_at都不变，检查是否需要周期性喂给策略
        time_since_last_tick = now - _apply_shared_ws_snapshot._last_tick_ts
        if time_since_last_tick >= 10.0:  # ⚠️ 10秒才喂一次
            should_feed_strategy = True
        else:
            # 距离上次on_tick不到10秒，跳过策略调用
            _apply_shared_ws_snapshot._skip_count += 1
            return  # ❌ 直接返回，不调用策略
```

### 主循环轮询频率
```python
while not stop_event.is_set():
    # ... 省略其他代码 ...
    if use_shared_ws:
        _apply_shared_ws_snapshot()  # 每0.2秒调用一次
    time.sleep(0.2)
```

### 影响分析
1. **数据读取延迟：** 0.2秒轮询间隔 + 去重逻辑
2. **横盘时响应慢：** 价格不变时，10秒才更新一次策略
3. **累计延迟：** 缓存写入延迟 + 读取延迟 + 去重延迟

---

## 🔍 下单条件判断逻辑对比

### 策略触发流程
两个版本都使用相同的 `VolArbStrategy` 策略类，触发条件相同：
- **买入条件：** 价格跌幅达到阈值 `drop_pct`
- **卖出条件：** 持仓盈利达到 `profit_pct`

### 关键差异：`on_tick` 调用频率
| 指标 | 原版本 | 新版本(独立WS) | 新版本(共享WS) |
|------|--------|---------------|---------------|
| 事件接收 | 实时 | 实时 | 延迟(聚合器→缓存) |
| 事件处理 | 立即 | **60秒一次** | 0.2秒轮询 + 去重 |
| on_tick频率 | 高频 | 极低 | 低频(10秒) |
| 响应延迟 | ~毫秒级 | ~60秒 | ~10秒+ |

---

## 📊 问题根因总结

### 根因1：错误的限流逻辑
新版本在独立WS模式的 `_on_event` 函数中添加了60秒限流，这是**完全不必要**的：
- 独立WS本身就是单个token专用连接，不会有高频问题
- 这个限流逻辑严重降低了策略响应速度
- **很可能是从共享WS模式复制代码时误加的**

### 根因2：共享WS的架构延迟
共享WS模式本身就存在固有延迟：
1. **数据流转路径长：** WS事件 → 聚合器 → 缓存文件 → 子进程
2. **文件I/O开销：** 频繁读写共享缓存文件
3. **去重逻辑保守：** 为避免重复处理而牺牲响应速度

### 根因3：横盘市场的策略更新缺失
在价格横盘（seq和updated_at不变）时：
- 原版本：每次WS事件仍会调用 `on_tick`，累积历史价格
- 新版本：10秒才调用一次，历史窗口数据不足

---

## 💡 建议修复方案

### 方案A：移除独立WS模式的限流 (推荐，简单快速)
**文件：** `Volatility_arbitrage_run.py`
**修改：** 删除第2487-2489行的限流代码

```diff
 def _on_event(ev: Dict[str, Any]):
     nonlocal market_closed_detected, last_event_processed_ts, last_signal_ts
     # ... 省略 ...

-    now = time.time()
-    if now - last_event_processed_ts < 60.0:
-        return
-    last_event_processed_ts = now

     for pc in pcs:
         # 处理价格变化
```

### 方案B：优化共享WS的去重逻辑
将10秒周期性更新改为更频繁（如1-2秒），或在价格虽然不变但时间窗口需要更新时强制调用：

```python
if time_since_last_tick >= 2.0:  # 从10秒改为2秒
    should_feed_strategy = True
```

### 方案C：在共享WS模式下提高轮询频率
将主循环的 `time.sleep(0.2)` 改为 `0.05` 或 `0.1`，加快数据读取速度。

---

## ⚖️ 是否纯粹是Token波动率问题？

### 结论：**不是**

1. **代码逻辑问题确实存在：** 60秒限流是明显的bug
2. **架构延迟真实存在：** 共享WS模式固有延迟
3. **策略响应频率显著降低：** 从高频变为低频甚至极低频

### 定量对比
假设token价格每10秒变化一次：
- **原版本：** 每次变化都触发策略检查，响应速度~毫秒级
- **新版本(独立WS)：** 60秒才检查一次，错过5次机会
- **新版本(共享WS)：** 最快10秒检查一次，错过部分机会

**即使token波动率完全相同，新版本的下单概率也会远低于原版本。**

---

## 🎯 验证建议

1. **对比日志：** 查看两个版本的 `[WS]` 和 `on_tick` 日志频率
2. **临时修复测试：** 移除60秒限流，观察下单情况是否改善
3. **监控指标：**
   - `strategy.on_tick` 调用频率
   - 从价格变化到策略响应的延迟
   - 实际下单次数对比

---

## 📝 总结

**问题根因：**
1. 独立WS模式误加60秒限流（主要问题）
2. 共享WS模式固有的轮询+去重延迟（次要问题）
3. 策略响应频率大幅降低导致错过下单机会

**不是单纯的token波动率问题，而是代码逻辑和架构导致的系统性响应延迟。**

修复建议优先级：
1. **P0：** 移除独立WS模式的60秒限流
2. **P1：** 优化共享WS的去重周期（10秒→2秒）
3. **P2：** 考虑提高轮询频率（0.2秒→0.05秒）
