# 共享WS模式下单频率低的深度分析

## 症状概览
根据运行日志：
- 10个运行实例全部使用共享WS模式
- WS聚合器显示：`4474 次/分钟` 的缓存更新，40个tokens
- 但每个子进程显示：`updates=1 (2.0/min)`，即每30秒才更新一次
- **巨大的差距：聚合器每分钟4474次更新 vs 子进程每分钟2次更新**

## 数据流分析

### 理论数据流
```
WS事件 → 聚合器接收 → 更新内存缓存(seq递增) → 每1秒刷新到文件
                                                      ↓
                              子进程每1秒读取文件 → 检查seq → 调用strategy.on_tick()
```

### 实际情况
```
WS聚合器: 4474次/分钟 = 约75次/秒 ÷ 40个tokens = 每个token约2次/秒
                                                ↓
                                            【断层】
                                                ↓
子进程: 2.0次/分钟 = 每30秒1次
```

---

## 🔴 核心问题1：seq机制的逻辑缺陷

### 当前实现
**文件：** `poly_maker_autorun.py` 第686行

```python
with self._ws_cache_lock:
    old_data = self._ws_cache.get(token_id, {})
    seq = old_data.get("seq", 0) + 1  # ❌ 问题在这里
```

### 问题分析
1. **seq只在有price_change事件时递增**
2. 聚合器统计的"4474次/分钟"可能包含了：
   - book事件（orderbook更新）
   - tick事件
   - last_trade_price事件（第588-604行的 `_on_ws_last_trade`）
   - 其他非price_change事件
3. **但只有price_change事件才会递增seq并更新完整的bid/ask/price数据**

### 验证：聚合器的事件过滤
**文件：** `poly_maker_autorun.py` 第606-718行

```python
def _on_ws_event(self, ev: Dict[str, Any]) -> None:
    # 统计所有事件
    self._ws_event_count += 1

    # 只处理price_change事件
    if ev.get("event_type") != "price_change":
        self._ws_filtered_count += 1  # 过滤掉的事件
        return

    # ... 处理price_change
    for pc in pcs:
        # 更新seq
        seq = old_data.get("seq", 0) + 1
```

**结论：**
- 聚合器统计的"4474次/分钟"是**所有WS事件**（包括book、tick等）
- 但实际更新seq的只有**price_change事件**
- 如果price_change事件占比很低（如5%），那么实际seq更新频率只有约224次/分钟 ÷ 40 = 每个token 5.6次/分钟
- 而这5.6次/分钟还要通过去重逻辑的过滤

---

## 🔴 核心问题2：去重逻辑过于激进

### 去重条件
**文件：** `Volatility_arbitrage_run.py` 第2700-2732行

```python
if seq > _apply_shared_ws_snapshot._last_seq:
    # seq递增 → 调用策略
    should_feed_strategy = True
elif seq == _apply_shared_ws_snapshot._last_seq:
    # seq相同，检查updated_at
    if updated_at > _apply_shared_ws_snapshot._last_updated_at:
        should_feed_strategy = True
    else:
        # seq和updated_at都不变
        time_since_last_tick = now - _apply_shared_ws_snapshot._last_tick_ts
        if time_since_last_tick >= 10.0:  # ⚠️ 10秒才喂一次
            should_feed_strategy = True
        else:
            return  # ❌ 直接跳过
```

### 问题分析
1. **主循环每1秒调用一次 `_apply_shared_ws_snapshot()`**
2. **缓存文件每1秒刷新一次**（第721行）
3. 但如果某个token在这1秒内没有price_change事件，seq就不会递增
4. seq不递增时，需要等10秒才会再次调用策略

### 真实场景举例
假设某个token每5秒才有一次price_change事件：
```
时间  | WS事件类型      | 聚合器seq | 子进程读取 | 去重结果
------|----------------|-----------|-----------|----------
0s    | price_change   | 1         | seq=1     | ✅ 调用策略
1s    | book           | 1         | seq=1     | ❌ 跳过（seq不变，距上次0.1s）
2s    | book           | 1         | seq=1     | ❌ 跳过（seq不变，距上次1s）
3s    | tick           | 1         | seq=1     | ❌ 跳过（seq不变，距上次2s）
4s    | book           | 1         | seq=1     | ❌ 跳过（seq不变，距上次3s）
5s    | price_change   | 2         | seq=2     | ✅ 调用策略
10s   | price_change   | 3         | seq=3     | ✅ 调用策略
15s   | (横盘，无事件) | 3         | seq=3     | ❌ 跳过（seq不变，距上次5s）
20s   | (横盘，无事件) | 3         | seq=3     | ❌ 跳过（seq不变，距上次5s）
25s   | (横盘，无事件) | 3         | seq=3     | ✅ 调用策略（10秒周期）
```

**结果：** 即使有WS事件流，但如果price_change频率低，策略更新频率也会很低。

---

## 🔴 核心问题3：文件I/O的延迟和竞态

### 缓存刷新：原子写入
**文件：** `poly_maker_autorun.py` 第736-741行

```python
# 先写临时文件
tmp_path = self._ws_cache_path.with_suffix('.tmp')
with tmp_path.open("w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

# 原子操作：重命名
tmp_path.replace(self._ws_cache_path)
```

### 缓存读取：直接读取
**文件：** `Volatility_arbitrage_run.py` 第2574-2575行

```python
with open(shared_ws_cache_path, "r", encoding="utf-8") as f:
    payload = json.load(f)
```

### 潜在问题
1. **JSON解析开销：** 40个tokens的数据，每次都要完整解析
2. **文件读写冲突：** 虽然用了原子替换，但在高频读写下可能有延迟
3. **indent=2：** 格式化输出会增加文件大小和写入时间

---

## 🟡 核心问题4：不同token的事件频率差异

### 日志证据
```
[RUN 1] seq=20, updates=1 (2.0/min), bid=0.14, ask=0.17
[RUN 2] seq=80, updates=1 (2.0/min), bid=0.09, ask=0.098
[RUN 3] 窗口价格(mid): 高 0.6850 / 低 0.6850  ← 横盘
[RUN 4] seq=473, updates=1 (2.0/min), bid=0.752, ask=0.773
[RUN 5] 窗口价格(mid): 高 0.4050 / 低 0.4050  ← 横盘
[RUN 6] 窗口价格(mid): 高 0.6300 / 低 0.6300  ← 横盘
[RUN 7] 窗口跌幅: 当前 0.00%                  ← 横盘
[RUN 8] 窗口价格(mid): 高 0.8400 / 低 0.8400  ← 横盘
[RUN 9] seq=945, updates=1 (2.0/min), bid=0.202, ask=0.29
[RUN 10] seq=646, updates=1 (2.0/min), bid=0.49, ask=0.5
```

### 分析
1. **seq差异巨大：** 从20到945，说明不同token的WS事件频率差异很大
2. **但更新频率相同：** 都是2.0/min，说明瓶颈不在WS事件，而在去重逻辑
3. **横盘token占比高：** 10个token中有5个显示横盘（50%）

---

## 🔴 核心问题5：策略状态机依赖高频更新

### 策略窗口依赖
**文件：** `Volatility_arbitrage_strategy.py`

策略需要计算滑动窗口内的价格波动：
- `drop_window_minutes`：跌幅计算窗口（如5分钟）
- 需要在窗口内累积足够的价格点，才能准确计算波动率

### 当前问题
1. **原版本：** 每个WS事件都更新策略，窗口内数据点充足
2. **新版本（共享WS）：** 每30秒才更新一次，5分钟窗口内只有10个数据点
3. **数据点不足：** 无法准确捕捉短期波动，错过下单机会

### 举例说明
假设策略配置：`drop_pct=0.1%`，`drop_window_minutes=5`

**原版本：**
```
0:00 - 价格0.50 ✓
0:01 - 价格0.50 ✓
0:02 - 价格0.49 ✓  ← 跌幅2%，触发买入
```

**新版本：**
```
0:00 - 价格0.50 ✓
0:30 - 价格0.50 ✓
1:00 - 价格0.49 ✓  ← 只看到0:00到1:00的跌幅，错过了0:02的买入机会
```

---

## 📊 性能对比总结

| 指标 | 原版本（独立WS） | 新版本（共享WS实际） | 理论最优（共享WS） |
|------|----------------|---------------------|-------------------|
| WS事件接收 | 实时，每个token独立 | 聚合，4474次/分钟（所有token） | 同实际 |
| seq更新频率 | 每次price_change | 仅聚合器处理price_change | 同实际 |
| 策略on_tick调用 | 每次WS事件立即 | 2次/分钟（30秒1次） | 应该是每次seq变化时 |
| 响应延迟 | 毫秒级 | 平均15秒 | 应该是1-2秒 |
| 数据点密度 | 高（连续） | 极低（稀疏） | 中（离散但频繁） |

---

## 💡 根因总结

### 主要根因（按影响程度排序）

#### 1. seq机制设计缺陷 (P0 - 致命)
- 只有price_change事件才更新seq
- 其他WS事件（book、tick等）被忽略
- 导致seq更新频率远低于WS事件接收频率

#### 2. 去重逻辑过于保守 (P0 - 致命)
- seq不变时，需等待10秒才周期性更新
- 主循环虽然每1秒调用，但大部分被去重逻辑跳过
- 30秒内只有1次有效更新

#### 3. price_change事件本身频率低 (P1 - 市场因素)
- Polymarket的WS可能只在订单簿价格变化时发送price_change
- 横盘市场下，price_change事件确实很少
- 但原版本即使横盘也能收到其他事件（book、tick）

#### 4. 策略窗口数据点不足 (P1 - 连锁反应)
- 低频更新导致滑动窗口内数据点稀疏
- 无法准确计算短期波动率
- 即使有下单机会也可能检测不到

#### 5. 文件I/O开销 (P2 - 次要)
- JSON解析有延迟，但不是主要瓶颈
- indent=2增加文件大小

---

## 🎯 修复建议（按优先级）

### 方案A：改进seq更新机制 (推荐)
**目标：** 让所有有价值的WS事件都能触发seq递增

```python
# 在 poly_maker_autorun.py 的 _on_ws_event 中：
def _on_ws_event(self, ev: Dict[str, Any]) -> None:
    event_type = ev.get("event_type")

    # 扩展触发seq更新的事件类型
    if event_type in ("price_change", "book", "tick", "last_trade_price"):
        # ... 处理并递增seq
```

**优点：** 从根本上解决问题，增加seq更新频率
**缺点：** 需要修改聚合器逻辑

---

### 方案B：降低去重周期 (简单)
**目标：** 即使seq不变，也更频繁地喂给策略

```python
# 在 Volatility_arbitrage_run.py 第2722行：
if time_since_last_tick >= 2.0:  # 从10秒改为2秒
```

**优点：** 修改简单，立即生效
**缺点：** 治标不治本，仍然错过很多WS事件

---

### 方案C：引入updated_at敏感度 (平衡)
**目标：** 不仅依赖seq，也检查updated_at的变化

```python
# 修改去重逻辑：
elif seq == _apply_shared_ws_snapshot._last_seq:
    # 即使seq不变，如果updated_at在最近2秒内更新过，也喂给策略
    if updated_at > _apply_shared_ws_snapshot._last_updated_at - 2.0:
        should_feed_strategy = True
```

**优点：** 能捕捉到last_trade_price等事件的更新
**缺点：** 可能会有重复数据

---

### 方案D：混合模式（最优但复杂）
**目标：** 保留共享WS的资源优势，但增加独立更新通道

```python
# 子进程：
# 1. 从共享缓存读取基础数据（bid/ask/price）
# 2. 同时订阅自己token的lightweight WS（只接收事件通知，不解析详细数据）
# 3. 收到lightweight事件时，立即从共享缓存读取最新数据并调用策略
```

**优点：** 兼顾资源节省和响应速度
**缺点：** 实现复杂度高

---

## 📋 验证步骤

### 1. 确认问题根因
```bash
# 查看聚合器的事件过滤统计（如果有日志）
grep "filtered" logs/autorun/autorun_*.log

# 查看某个token的seq变化频率
grep "seq=" logs/autorun/autorun_<token_id>.log | tail -50
```

### 2. 临时测试（方案B）
修改第2722行，将10秒改为2秒，观察：
- 更新频率是否提升到 30次/分钟（每2秒1次）
- 下单机会是否增加

### 3. 对比原版本
使用相同token同时运行原版本和新版本，对比：
- `strategy.on_tick()` 调用次数
- 检测到的波动次数
- 实际下单次数

---

## 🔬 深入调试建议

### 添加详细日志
在 `_apply_shared_ws_snapshot()` 开头添加：
```python
if not hasattr(_apply_shared_ws_snapshot, "_debug_log"):
    _apply_shared_ws_snapshot._debug_log = True
    print(f"[DEBUG] 开启详细去重日志")

# 每次调用都打印（或每10次打印一次）
if _apply_shared_ws_snapshot._read_count % 10 == 0:
    print(f"[DEBUG] seq={seq}, last_seq={_apply_shared_ws_snapshot._last_seq}, "
          f"updated_at={updated_at:.2f}, last_updated_at={_apply_shared_ws_snapshot._last_updated_at:.2f}, "
          f"should_feed={should_feed_strategy}")
```

### 监控聚合器事件分布
在 `poly_maker_autorun.py` 添加事件类型统计：
```python
# 在 _on_ws_event 中：
event_type = ev.get("event_type")
if not hasattr(self, "_event_type_stats"):
    self._event_type_stats = {}
self._event_type_stats[event_type] = self._event_type_stats.get(event_type, 0) + 1

# 定期打印
if now - self._last_stats_log >= 60:
    print(f"[WS][STATS] 事件类型分布: {self._event_type_stats}")
    self._event_type_stats = {}
```

---

## 结论

**共享WS模式下单频率低的根本原因不是token波动率问题，而是架构设计缺陷：**

1. **seq机制过于局限**：只有price_change事件才更新seq，忽略了其他有价值的WS事件
2. **去重逻辑过于保守**：10秒周期导致策略更新频率低，窗口数据点不足
3. **缺乏对低频事件的应对**：横盘市场下price_change事件少，但应该通过更频繁的周期性更新来弥补

**即使token本身波动率低，原版本通过高频的WS事件更新（book、tick等）也能保持策略状态机的活跃度，而新版本在这方面有明显的退化。**

修复优先级：
1. **P0：** 降低去重周期到2秒（快速见效）
2. **P1：** 改进seq更新机制，支持更多事件类型（根本性解决）
3. **P2：** 优化文件I/O（性能优化）
