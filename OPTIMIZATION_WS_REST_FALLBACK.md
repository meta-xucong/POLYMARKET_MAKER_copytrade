# WebSocket快照过期与REST API Fallback优化

## 问题描述

在运行中发现以下问题：

### 主要问题
- WebSocket快照持续过期（200+秒未更新）
- 快照过期导致WS返回None
- 系统无法获取市场价格，影响交易决策

### 次要问题
- REST API fallback失败
- 缺少详细的错误日志
- 难以诊断问题根源

## 优化方案

### 1. 分级快照判断（ws_orderbook.py）

**优化内容：**
- 增加 `max_stale_sec` 参数（默认300秒=5分钟）
- 实现分级判断逻辑：
  - **新鲜快照**：age ≤ `stale_sec`（默认30s）→ 直接使用
  - **陈旧快照**：`stale_sec` < age ≤ `max_stale_sec`（30s-300s）→ 标记为stale，可选使用
  - **过期快照**：age > `max_stale_sec`（>300s）→ 拒绝使用

**配置方式：**
```json
{
  "orderbook": {
    "ws_stale_sec": 30.0,        // 新鲜快照阈值（秒）
    "ws_max_stale_sec": 300.0    // 最大陈旧快照阈值（秒）
  }
}
```

**详细日志输出：**
- `[WS][GET_BEST] ... 快照新鲜: age=5.3s, bid=0.34, ask=0.35`
- `[WS][GET_BEST] ... 使用陈旧快照: age=120.5s, bid=0.34, ask=0.35`
- `[WS][GET_BEST] ... 快照过期: age=350.2s > 阈值=30.0s (max=300.0s)`

### 2. 增强REST Fallback优先级（maker_engine.py）

**优化流程：**
1. **第1步**：尝试WS缓存（新鲜快照）
   - 成功 → 直接返回

2. **第2步**：尝试WS缓存（陈旧快照）
   - 成功 → 标记为stale，继续尝试REST更新

3. **第3步**：检查本地缓存
   - 有效 → 返回缓存数据

4. **第4步**：调用REST API
   - 成功 → 更新缓存并返回
   - 失败且有陈旧快照 → 回退使用陈旧快照
   - 失败且无快照 → 返回None

**日志输出示例：**
```
[PRICE][WS] token=162062674403... 使用新鲜快照: bid=0.34, ask=0.35
[PRICE][WS] token=162062674403... 使用陈旧快照（将尝试REST更新）: bid=0.34, ask=0.35
[PRICE][REST] token=162062674403... 开始REST API查询（WS不可用或陈旧）
[PRICE][REST] token=162062674403... REST成功: bid=0.34, ask=0.35
[PRICE][REST] token=162062674403... REST失败，回退使用陈旧WS快照: bid=0.34, ask=0.35
```

### 3. 详细错误日志（maker_execution.py）

**优化内容：**
- 记录尝试的每个API方法
- 记录每个方法的失败原因（TypeError、异常类型、错误消息）
- 区分"方法不存在"、"调用失败"、"响应无法解析"三种情况

**日志输出示例：**
```
[FETCH][REST] token=162062674403... side=bid 失败: 尝试了13个方法: get_market_orderbook, get_order_book, get_orderbook, ...
[FETCH][REST] token=162062674403... 错误详情: get_market_orderbook: HTTPError(404); get_order_book: 响应无法提取bid价格; ...
[FETCH][REST] token=162062674403... side=bid 成功: method=get_order_book, price=0.34
```

## 优化效果

### 对低流动性Token的改进
- **之前**：快照过期 → 立即返回None → 无法交易
- **之后**：快照过期 → 使用陈旧快照 + 尝试REST更新 → 保持交易能力

### 诊断能力提升
- **之前**：只知道"REST API失败"，不知道原因
- **之后**：详细记录每个方法的失败原因，便于定位问题

### 系统鲁棒性提升
- 多层级fallback机制：WS（新鲜）→ WS（陈旧）→ 本地缓存 → REST → WS（陈旧回退）
- 对于流动性极低的市场（价格长期不变），仍能正常运行

## 使用建议

### 1. 调整stale阈值
对于流动性极低的token，可以适当放宽阈值：
```json
{
  "orderbook": {
    "ws_stale_sec": 60.0,        // 1分钟
    "ws_max_stale_sec": 600.0    // 10分钟
  }
}
```

### 2. 监控日志
关注以下日志关键词：
- `[WS][GET_BEST] ... 快照过期` - 表示token长期无更新
- `[PRICE][REST] ... REST失败` - 表示REST API问题
- `[FETCH][REST] ... 错误详情` - 详细的失败原因

### 3. 低流动性Token策略
对于长期无价格更新的token：
- 考虑是否值得继续跟单
- 评估使用陈旧快照的风险
- 适当增加 `ws_max_stale_sec` 阈值

## 技术细节

### 修改的文件
1. `POLYMARKET_MAKER_copytrade/modules/ws_orderbook.py`
   - 增加 `max_stale_sec` 参数
   - 修改 `get_best()` 方法，增加 `allow_stale` 参数
   - 增加详细的日志输出

2. `POLYMARKET_MAKER_copytrade/modules/maker_engine.py`
   - 重构 `_get_best_prices()` 方法
   - 实现4步fallback流程
   - 增加详细的日志输出

3. `POLYMARKET_MAKER_copytrade/maker_execution.py`
   - 修改 `_fetch_best_price()` 函数
   - 增加 `logger` 参数
   - 记录每个API方法的尝试和失败详情

4. `POLYMARKET_MAKER_copytrade/copytrade_maker.py`
   - 初始化时读取 `ws_max_stale_sec` 配置

### 向后兼容性
- 所有新增参数都有默认值
- `allow_stale` 参数默认为 `False`，保持原有行为
- `logger` 参数为可选，不传入也不会报错

## 测试建议

1. **正常场景**：观察日志是否正常输出新鲜快照信息
2. **陈旧快照场景**：故意停止WebSocket连接，观察是否回退到REST
3. **REST失败场景**：观察是否使用陈旧快照作为fallback
4. **低流动性场景**：运行低流动性token，观察长时间无更新时的行为

## 总结

此次优化主要解决了低流动性token因WebSocket快照过期导致无法获取价格的问题，通过分级判断和多层级fallback机制，大幅提升了系统的鲁棒性和诊断能力。同时保持了向后兼容性，不影响现有配置和行为。
