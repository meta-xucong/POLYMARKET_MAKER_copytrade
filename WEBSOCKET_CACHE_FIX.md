# WebSocket缓存持续更新修复

## 问题描述

### 症状
- WebSocket快照年龄持续增长（例如：2256秒 ≈ 37分钟）
- 每次查询都显示"快照过期"
- 持续fallback到REST API查询
- 虽然聚合器显示有缓存更新（1047次/分钟），但特定token的快照不更新

### 根本原因
在 `ws_orderbook.py` 的 `_on_event()` 函数中，事件过滤逻辑过于严格，只处理两种格式：
1. `event_type == "price_change"`
2. 包含 `"price_changes"` 字段的事件

**其他格式的WebSocket事件（如 `book`、`snapshot`、`delta` 等）被直接忽略**，导致：
- 第一次快照：设置时间戳 `ts`
- 后续增量更新：如果事件格式不匹配，被过滤掉
- 时间流逝：缓存 `age` 越来越大
- 30秒后：变成"陈旧快照"，触发REST fallback
- 300秒后：完全过期，返回 `None`

## 修复方案

### 1. 增强事件处理逻辑（3层策略）

修改 `ws_orderbook.py` 中的 `_on_event()` 函数，采用多层处理策略：

#### 策略1: 处理 price_change 事件（原有逻辑）
```python
if ev.get("event_type") == "price_change" or "price_changes" in ev:
    pcs = ev.get("price_changes", [])
    for pc in pcs:
        # 更新缓存
```

#### 策略2: 处理 book/snapshot/delta 事件（新增）
支持多种事件格式：
```python
# 事件格式示例：
{
  "event_type": "book|snapshot|delta|market",
  "asset_id": "...",
  "bids": [[price, size], ...],  # 或 best_bid
  "asks": [[price, size], ...],  # 或 best_ask
  "market": {
    "best_bid": 0.5,
    "best_ask": 0.6
  }
}
```

提取逻辑：
1. 优先从直接字段读取 `best_bid`/`best_ask`
2. 如果没有，从 `bids`/`asks` 数组中提取第一个价格
3. 支持数组格式：`[[price, size], ...]`
4. 支持对象格式：`[{"price": 0.5, "size": 100}, ...]`
5. 支持 `market` 子对象

#### 策略3: 诊断日志（新增）
记录未识别的事件类型（排除心跳等噪音）：
```python
if event_type and event_type not in ("pong", "subscribed", "unsubscribed", "heartbeat"):
    self._log("debug", f"[WS][UNHANDLED_EVENT] type={event_type}, keys={list(ev.keys())}")
```

### 2. 添加缓存更新统计

新增统计功能，每30秒报告一次：
- 总更新次数
- 更新的token数量
- 更新频率（次/分钟）
- 最活跃和最不活跃的token

示例输出：
```
[WS][STATS] 过去30秒: 523次更新, 10个token, 1046次/分钟 | 最活跃: 67076910...(85次), 33545963...(62次) | 最不活跃: 29505104...(12次)
```

### 3. 代码改进点

#### 新增辅助函数
```python
def _to_float_or_none(val: Any) -> Optional[float]:
    """Helper function to safely convert value to float"""
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None
```

#### 智能合并更新
如果事件中只提供了部分数据（只有bid或只有ask），保留已有缓存中的其他值：
```python
existing = self._latest.get(str(asset_id), {})
self._latest[str(asset_id)] = {
    "best_bid": best_bid if best_bid is not None else existing.get("best_bid"),
    "best_ask": best_ask if best_ask is not None else existing.get("best_ask"),
    "ts": time.time(),  # ✅ 关键：更新时间戳
}
```

## 修复效果

### 修复前
```
[DIAG][BID] 快照过期: 年龄=2256.1s > 阈值=30.0s
[DIAG][WS] WebSocket bid 返回 None（可能原因：快照过期或数据缺失）
[FETCH][WARN] 无法通过 REST API 获取 bid 价格
```
- ❌ 快照年龄持续增长，永不更新
- ❌ 每次都fallback到REST API
- ❌ REST API也可能失败（冷门市场）

### 修复后（预期）
```
[WS][STATS] 过去30秒: 523次更新, 10个token, 1046次/分钟
[WS][GET_BEST] token=67076910... 快照新鲜: age=2.3s, bid=0.006, ask=0.007
[PRICE][WS] token=67076910... 使用新鲜快照
```
- ✅ 快照持续更新，年龄保持在秒级
- ✅ 大部分查询使用WebSocket数据
- ✅ REST fallback仅在真正需要时触发

## 部署建议

1. **立即部署**：这是P0级别的修复，解决了核心数据流问题
2. **监控日志**：关注以下日志输出
   - `[WS][STATS]`：确认更新频率正常
   - `[WS][UNHANDLED_EVENT]`：发现新的未处理事件格式
   - `[DIAG][BID]` 和 `[DIAG][ASK]`：快照年龄应保持在30秒以内
3. **观察指标**：
   - 快照过期次数应显著减少
   - REST fallback次数应显著减少
   - 下单成功率应提升

## 相关文件

- **修改文件**：`POLYMARKET_MAKER_copytrade/modules/ws_orderbook.py`
- **影响范围**：所有使用WebSocket缓存的maker session
- **向后兼容**：完全兼容，增强了事件处理能力

## 测试建议

1. **启动应用**，观察日志中的 `[WS][STATS]` 输出
2. **检查快照年龄**：应保持在30秒以内
3. **监控REST fallback**：次数应大幅减少
4. **如果看到 `[WS][UNHANDLED_EVENT]`**：说明还有其他事件格式需要支持，请报告日志内容

## 技术细节

### WebSocket事件流程

```
WebSocket连接
    ↓
on_message() 接收JSON
    ↓
_on_event() 解析事件
    ↓
┌─────────────────────────────────────┐
│ 策略1: price_change / price_changes │ ✅ 原有格式
└────────┬────────────────────────────┘
         │ 未匹配
         ↓
┌─────────────────────────────────────┐
│ 策略2: book/snapshot/delta/market   │ ✅ 新增支持
│ - 直接字段: best_bid/best_ask       │
│ - 数组字段: bids[0][0], asks[0][0]  │
│ - 子对象: market.best_bid           │
└────────┬────────────────────────────┘
         │ 未匹配
         ↓
┌─────────────────────────────────────┐
│ 策略3: 记录诊断日志                  │ ⚠️ 需要人工分析
└─────────────────────────────────────┘
```

### 时间戳更新机制

每次成功处理事件后，都会更新时间戳：
```python
self._latest[str(asset_id)] = {
    "best_bid": ...,
    "best_ask": ...,
    "ts": time.time(),  # ✅ 重置年龄
}
```

这确保了：
- 只要WebSocket有更新，快照就不会过期
- REST fallback仅在WebSocket真正失效时触发
- 系统优先使用低延迟的WebSocket数据

## 总结

这次修复从根本上解决了WebSocket快照过期的问题，通过：
1. ✅ 支持更多WebSocket事件格式
2. ✅ 持续更新缓存时间戳
3. ✅ 添加统计监控，可观测性增强
4. ✅ 智能合并部分更新，避免数据丢失

**预期效果**：快照年龄从"分钟级"降低到"秒级"，REST fallback次数大幅减少，系统响应更及时。
