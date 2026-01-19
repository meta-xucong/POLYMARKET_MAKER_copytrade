# WS 共享架构 BUG 分析报告

## 执行时间
2026-01-19

## 问题背景

用户将系统从"每个token独立WS连接"改为"单一WS聚合器+数据分发"架构后，系统运行24小时不下单。

**症状**：
- `copytrade_run.py` 正常工作，获取到新token并写入json
- `poly_maker_autorun.py` 成功启动，maker子进程也运行了
- 但在Polymarket网页端没有看到任何下单操作

## 核心改动（Commit e31f8f8）

### 架构变更
**旧版本**：每个子进程独立建立WS连接
```python
# Volatility_arbitrage_run.py（旧版）
ws_thread = threading.Thread(
    target=ws_watch_by_ids,
    kwargs={"asset_ids": [token_id], ...},
)
ws_thread.start()  # 每个子进程一个WS连接
```

**新版本**：单一WS聚合器 + 文件共享
```python
# poly_maker_autorun.py（新版）
def _start_ws_aggregator(self):
    # 启动单一WS聚合器，订阅所有token
    self._ws_thread = threading.Thread(
        target=ws_watch_by_ids,
        kwargs={"asset_ids": token_ids, "on_event": self._on_ws_event},
    )
    # 将数据写入 ws_cache.json

# Volatility_arbitrage_run.py（新版）
if not use_shared_ws:
    ws_thread.start()  # 使用共享WS时，不启动独立连接
else:
    # 从 ws_cache.json 读取数据
    _apply_shared_ws_snapshot()
```

---

## 🔴 严重问题 1: WS聚合器静默启动失败

### 位置
`poly_maker_autorun.py:421-426`

### 问题代码
```python
def _start_ws_subscription(self, token_ids: List[str]) -> None:
    try:
        from Volatility_arbitrage_main_ws import ws_watch_by_ids
    except Exception as exc:
        print(f"[WARN] 无法启动 WS 聚合器: {exc}")
        return  # ⚠️ 静默返回，程序继续运行
```

### 问题分析

**导入可能失败的原因**：
1. **模块路径问题**：虽然第31-32行添加了 `MAKER_ROOT` 到 `sys.path`，但如果有其他路径问题仍可能失败
2. **依赖缺失**：`Volatility_arbitrage_main_ws.py` 依赖 `websocket-client`，如果未安装会导入失败
3. **运行时环境差异**：开发环境和生产环境的Python路径可能不同

**致命后果**：
- 如果导入失败，只打印一个 `[WARN]`，然后 `return`
- **不会抛出异常，不会停止程序**
- 主程序认为WS聚合器已启动，继续运行
- `ws_cache.json` **永远不会被创建**
- 所有子进程等待的数据源**不存在**

### 验证方法
检查是否存在 ws_cache.json：
```bash
find /path/to/project -name "ws_cache.json"
```
如果不存在，说明WS聚合器启动失败。

---

## 🔴 严重问题 2: 子进程没有 fallback 机制

### 位置
`Volatility_arbitrage_run.py:2395-2457`

### 问题代码
```python
shared_ws_cache_path = os.getenv("POLY_WS_SHARED_CACHE")
use_shared_ws = bool(shared_ws_cache_path)  # ⚠️ 只要环境变量存在就为True

# ...

if not use_shared_ws:
    ws_thread = threading.Thread(...)
    ws_thread.start()
# ⚠️ 如果 use_shared_ws=True，子进程不启动独立WS！
```

### 问题分析

**逻辑缺陷**：
1. `poly_maker_autorun.py:736` 无条件设置环境变量：
   ```python
   env["POLY_WS_SHARED_CACHE"] = str(self._ws_cache_path)
   ```
2. 子进程读取环境变量，`use_shared_ws = True`
3. 子进程**不启动自己的WS连接**（第2457行的 if 分支不执行）
4. 子进程完全依赖 `ws_cache.json`

**致命组合**：
- 如果WS聚合器启动失败（问题1）
- 子进程设置了 `use_shared_ws=True`
- 子进程不启动独立WS
- `ws_cache.json` 不存在
- **子进程永远收不到行情数据**

### 数据流断裂
```
WS聚合器启动失败
    ↓
ws_cache.json 不存在
    ↓
子进程 use_shared_ws=True
    ↓
子进程不启动独立WS
    ↓
没有任何数据源
    ↓
latest[token_id] 始终为空
    ↓
strategy.on_tick() 永远不被调用
    ↓
永远不下单
```

---

## 🔴 严重问题 3: 子进程依赖时间戳去重导致数据丢失

### 位置
`Volatility_arbitrage_run.py:2412-2436`

### 问题代码
```python
def _apply_shared_ws_snapshot() -> None:
    nonlocal last_shared_ts
    snapshot = _load_shared_ws_snapshot()
    if not snapshot:
        return  # ⚠️ 如果文件不存在或读取失败，静默返回

    ts = _extract_ts(snapshot.get("ts"))
    if ts is None:
        ts = time.time()

    if ts <= last_shared_ts:
        return  # ⚠️ 如果时间戳没变，认为数据没更新，跳过

    last_shared_ts = ts
    # ... 更新价格，触发策略
```

### 问题分析

**时间戳去重逻辑的缺陷**：
1. `ts` 来自 WS 事件的 `timestamp/ts/time` 字段（`poly_maker_autorun.py:460`）
2. **如果WS事件没有时间戳**，`ts = None`，然后被设为 `time.time()`
3. **如果多个事件来自同一个 batch**，它们的 `ts` 可能相同
4. 子进程认为 `ts <= last_shared_ts`，数据没更新，**直接跳过**

**实际影响**：
- 即使 WS 聚合器正常工作，写入了新的价格数据
- 如果 `ts` 字段没变化，子进程会认为是"旧数据"
- **真正的价格变化被忽略**
- 策略不会被触发
- 错过交易机会

### 更严重的问题：首次读取
```python
# 子进程启动时
last_shared_ts = 0.0

# 如果 ws_cache.json 中的 ts 也是 0.0 或 None
ts = _extract_ts(snapshot.get("ts"))  # None
if ts is None:
    ts = time.time()  # 比如 1737292800

# 但如果聚合器写入的 ts 也是 None
# 然后子进程也计算 time.time()，可能是同一秒
# 导致 ts <= last_shared_ts，跳过首次数据！
```

---

## 🟡 潜在问题 4: 聚合器 WS 事件的时间戳可能为 None

### 位置
`poly_maker_autorun.py:460`

### 问题代码
```python
def _on_ws_event(self, ev: Dict[str, Any]) -> None:
    # ...
    ts = ev.get("timestamp") or ev.get("ts") or ev.get("time")
    # ⚠️ 如果所有字段都不存在，ts = None

    for pc in pcs:
        # ...
        payload = {
            "price": last,
            "best_bid": bid,
            "best_ask": ask,
            "ts": ts,  # ⚠️ 可能是 None
            "updated_at": time.time(),
        }
        self._ws_cache[token_id] = payload
```

### 问题分析

**WS事件结构的不确定性**：
- Polymarket WS API 返回的事件结构可能不固定
- `timestamp`、`ts`、`time` 字段可能都不存在
- 导致 `ts = None` 被写入 `ws_cache.json`

**连锁反应**：
- 子进程读取 `snapshot.get("ts")` → `None`
- 子进程设置 `ts = time.time()` → 当前时间戳
- 下次读取时，如果 `ts` 还是 `None`，又设为 `time.time()`
- 可能导致 `ts <= last_shared_ts`，数据被跳过

---

## 🟡 潜在问题 5: 节流逻辑导致 WS 事件被丢弃

### 位置
`Volatility_arbitrage_run.py:2323-2327`（独立WS模式）

### 问题代码
```python
last_event_processed_ts = 0.0

def _on_event(ev: Dict[str, Any]):
    # ...
    now = time.time()
    if now - last_event_processed_ts < 60.0:
        return  # ⚠️ 60秒内的事件直接丢弃！
    last_event_processed_ts = now
```

### 问题分析

**这是针对独立WS的节流逻辑**：
- 限制每个子进程最多每60秒处理一次WS事件
- 目的是降低CPU使用率

**与共享WS架构的冲突**：
- 在共享WS架构中，这个 `_on_event` **不会被调用**（因为子进程没启动WS）
- 但节流逻辑保留在代码中，可能引起混淆
- **实际上对共享WS架构无影响**，但代码残留不清晰

---

## 🟡 潜在问题 6: ws_cache.json 写入失败静默处理

### 位置
`poly_maker_autorun.py:503-528`

### 问题代码
```python
def _flush_ws_cache_if_needed(self) -> None:
    # ...
    try:
        self._ws_cache_path.parent.mkdir(parents=True, exist_ok=True)
        with self._ws_cache_path.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except OSError as exc:
        print(f"[WARN] 写入 WS 聚合缓存失败: {exc}")
        # ⚠️ 不抛出异常，程序继续运行
```

### 问题分析

**文件写入可能失败的原因**：
1. 磁盘空间不足
2. 权限问题
3. 目录不存在（虽然有 `mkdir`，但可能失败）
4. 文件被锁定

**后果**：
- 写入失败只打印 `[WARN]`，不影响程序运行
- 子进程读取不到数据，认为是"文件不存在"
- 没有明确区分"文件写入失败"和"WS没收到数据"

---

## 🟡 潜在问题 7: 子进程读取文件时的竞态条件

### 位置
`Volatility_arbitrage_run.py:2399-2410`

### 问题代码
```python
def _load_shared_ws_snapshot() -> Optional[Dict[str, Any]]:
    if not shared_ws_cache_path:
        return None
    try:
        with open(shared_ws_cache_path, "r", encoding="utf-8") as f:
            payload = json.load(f)
        # ⚠️ 读取时，聚合器可能正在写入
    except (OSError, json.JSONDecodeError):
        return None  # ⚠️ 所有错误都返回None，无法区分原因
```

### 问题分析

**文件竞态条件**：
1. 聚合器每1秒写入一次 `ws_cache.json`（如果 dirty）
2. 子进程每0.2秒读取一次（主循环）
3. **可能在写入过程中读取，导致JSON格式错误**
4. `json.JSONDecodeError` 被捕获，返回 `None`
5. 子进程认为"没有数据"

**应该使用原子写入**：
```python
# 正确做法：先写临时文件，再原子重命名
tmp_path = self._ws_cache_path.with_suffix('.tmp')
with tmp_path.open("w") as f:
    json.dump(data, f)
tmp_path.replace(self._ws_cache_path)  # 原子操作
```

---

## 🟢 较小问题 8: 缺少健康检查和监控

### 问题分析

**缺少关键监控指标**：
1. WS聚合器是否成功启动？
2. WS聚合器是否接收到数据？
3. `ws_cache.json` 是否被定期更新？
4. 各个子进程是否读取到数据？
5. 子进程的 `last_shared_ts` 是什么值？

**建议添加**：
- 启动时验证WS聚合器状态
- 定期检查 `ws_cache.json` 的 `updated_at` 时间戳
- 如果长时间没更新，打印警告或重启聚合器
- 子进程定期报告"最后接收数据时间"

---

## 📊 问题总结

| 问题等级 | 问题描述 | 是否会导致不下单 | 优先级 |
|---------|---------|----------------|--------|
| 🔴 严重 | WS聚合器静默启动失败 | **是** | **最高** |
| 🔴 严重 | 子进程没有fallback机制 | **是** | **最高** |
| 🔴 严重 | 时间戳去重导致数据丢失 | **是** | **最高** |
| 🟡 中等 | WS事件时间戳可能为None | 可能 | 高 |
| 🟡 中等 | 节流逻辑代码残留 | 否（但混淆） | 低 |
| 🟡 中等 | 文件写入失败静默处理 | 可能 | 高 |
| 🟡 中等 | 文件读取竞态条件 | 可能 | 中 |
| 🟢 较小 | 缺少健康检查 | 间接 | 中 |

---

## 🎯 根本原因推断

基于症状"运行24小时不下单"，最可能的原因是：

### 场景A：WS聚合器启动失败（最有可能）
```
1. poly_maker_autorun.py 启动
2. 尝试导入 Volatility_arbitrage_main_ws
3. 导入失败（路径问题/依赖缺失）
4. 打印 "[WARN] 无法启动 WS 聚合器"
5. 继续运行，启动子进程
6. 子进程环境变量 POLY_WS_SHARED_CACHE 已设置
7. 子进程 use_shared_ws=True，不启动独立WS
8. 子进程尝试读取 ws_cache.json → 不存在
9. _apply_shared_ws_snapshot() 返回 None
10. latest[token_id] 始终为空
11. 策略永远不被触发
12. 永远不下单
```

### 场景B：WS聚合器成功启动但时间戳问题
```
1. WS聚合器成功启动，接收到事件
2. 事件中 ts=None 或固定值
3. 写入 ws_cache.json，ts=None
4. 子进程首次读取，ts=None，设为 time.time()
5. last_shared_ts = 当前时间
6. 下次读取，ts 还是 None，又设为 time.time()
7. 如果两次 time.time() 相同（或第二次更小）
8. ts <= last_shared_ts，跳过数据
9. 策略不被触发
10. 偶尔下单（当时间戳更新时）或永远不下单
```

### 场景C：文件读写竞态或权限问题
```
1. WS聚合器成功启动并写入数据
2. 但子进程读取时遇到 JSON 解析错误
3. 或文件权限问题导致读取失败
4. _load_shared_ws_snapshot() 返回 None
5. 子进程认为没有数据
6. 永远不下单
```

---

## 🔧 诊断步骤（按优先级）

### 步骤1：检查 WS 聚合器是否启动成功（最优先）

**检查日志**：
```bash
# 查看主进程日志
grep -r "WS 聚合器" /path/to/logs/
grep -r "无法启动" /path/to/logs/

# 应该看到
[WS] 聚合订阅启动，tokens=N  # 成功
# 或
[WARN] 无法启动 WS 聚合器: ...  # 失败
```

**检查 ws_cache.json**：
```bash
# 查找文件
find /path/to/project -name "ws_cache.json"

# 如果存在，查看内容
cat /path/to/data/ws_cache.json

# 检查是否有数据
jq '.tokens | length' /path/to/data/ws_cache.json

# 检查更新时间
jq '.updated_at' /path/to/data/ws_cache.json
```

**手动测试导入**：
```bash
cd /path/to/POLYMARKET_MAKER_AUTO
python3 -c "
import sys
from pathlib import Path
MAKER_ROOT = Path('POLYMARKET_MAKER')
sys.path.insert(0, str(MAKER_ROOT))
from Volatility_arbitrage_main_ws import ws_watch_by_ids
print('导入成功')
"
```

### 步骤2：检查子进程是否读取到数据

**查看子进程日志**：
```bash
# 查看所有子进程日志
ls -lh /path/to/logs/autorun_*.log

# 查看最新的几行
tail -50 /path/to/logs/autorun_*.log

# 查找关键信息
grep -E "尚未收到行情|等待行情|[PX]" /path/to/logs/autorun_*.log
```

**预期行为**：
- **正常**：应该看到 `[PX]` 日志，显示当前价格和持仓
- **异常**：一直显示 `[WAIT] 尚未收到行情，继续等待…`

### 步骤3：检查环境变量

```bash
# 检查子进程的环境变量
ps aux | grep Volatility_arbitrage_run.py
# 获取进程ID，比如 12345

cat /proc/12345/environ | tr '\0' '\n' | grep POLY_WS_SHARED_CACHE
# 应该输出：POLY_WS_SHARED_CACHE=/path/to/data/ws_cache.json
```

### 步骤4：手动测试 WS 连接

```python
# test_ws.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent / "POLYMARKET_MAKER"))

from Volatility_arbitrage_main_ws import ws_watch_by_ids
import threading
import time

events_received = []

def on_event(ev):
    print(f"[EVENT] {ev}")
    events_received.append(ev)

stop_event = threading.Event()

# 使用一个已知活跃的 token_id
token_id = "你的token_id"

t = threading.Thread(
    target=ws_watch_by_ids,
    kwargs={
        "asset_ids": [token_id],
        "label": "test",
        "on_event": on_event,
        "verbose": True,
        "stop_event": stop_event,
    },
    daemon=True,
)
t.start()

print("等待WS事件...")
time.sleep(60)

stop_event.set()
t.join(timeout=5)

print(f"\n接收到 {len(events_received)} 个事件")
if events_received:
    print(f"最新事件: {events_received[-1]}")
```

---

## 🔨 修复方案（按优先级）

### 修复1：添加 WS 聚合器启动验证（最高优先级）

**修改 `poly_maker_autorun.py:421-441`**：

```python
def _start_ws_subscription(self, token_ids: List[str]) -> None:
    try:
        from Volatility_arbitrage_main_ws import ws_watch_by_ids
    except Exception as exc:
        error_msg = f"[ERROR] 无法导入 WS 模块: {exc}"
        print(error_msg)
        # ⚠️ 关键修改：抛出异常或启动fallback
        raise RuntimeError(error_msg)  # 选项1：停止程序
        # return  # 选项2：静默失败（当前行为）

    # 验证 websocket-client 依赖
    try:
        import websocket
    except ImportError:
        error_msg = "[ERROR] 缺少依赖 websocket-client，请运行: pip install websocket-client"
        print(error_msg)
        raise RuntimeError(error_msg)

    stop_event = threading.Event()
    self._ws_thread_stop = stop_event
    self._ws_thread = threading.Thread(
        target=ws_watch_by_ids,
        kwargs={
            "asset_ids": token_ids,
            "label": "autorun-aggregator",
            "on_event": self._on_ws_event,
            "verbose": False,
            "stop_event": stop_event,
        },
        daemon=True,
    )
    self._ws_thread.start()
    print(f"[WS] 聚合订阅启动，tokens={len(token_ids)}")

    # ⚠️ 新增：验证线程是否运行
    time.sleep(2)
    if not self._ws_thread.is_alive():
        error_msg = "[ERROR] WS 聚合器线程启动后立即退出"
        print(error_msg)
        raise RuntimeError(error_msg)
```

**优点**：
- 明确失败原因
- 停止程序而不是静默运行（避免浪费资源）
- 便于调试

### 修复2：为子进程添加 fallback 机制（最高优先级）

**修改 `poly_maker_autorun.py:735-736`**：

```python
# 选项A：完全移除环境变量，让子进程使用独立WS
# env = os.environ.copy()
# # 不设置 POLY_WS_SHARED_CACHE

# 选项B：添加 fallback 开关
env = os.environ.copy()
if self._ws_thread and self._ws_thread.is_alive():
    # 只有WS聚合器成功运行时才使用共享模式
    env["POLY_WS_SHARED_CACHE"] = str(self._ws_cache_path)
else:
    # 否则让子进程使用独立WS
    print(f"[WARN] WS聚合器未运行，子进程将使用独立WS: {topic_id}")
```

**修改 `Volatility_arbitrage_run.py:2456-2472`**：

```python
# 添加健康检查逻辑
if use_shared_ws:
    print("[WS] 使用共享 WS 模式，路径:", shared_ws_cache_path)
    # 检查文件是否存在
    import os
    if not os.path.exists(shared_ws_cache_path):
        print(f"[WARN] 共享 WS 缓存文件不存在: {shared_ws_cache_path}")
        print("[WARN] 切换到独立 WS 模式")
        use_shared_ws = False

if not use_shared_ws:
    ws_thread = threading.Thread(
        target=ws_watch_by_ids,
        kwargs={
            "asset_ids": [token_id],
            "label": f"{title} ({token_id})",
            "on_event": _on_event,
            "on_state": _on_ws_state,
            "verbose": False,
            "stop_event": stop_event,
        },
        daemon=True,
    )
    ws_thread.start()
    print("[WS] 独立 WS 连接已启动")
```

**优点**：
- 如果共享WS失败，自动回退到独立WS
- 提高系统鲁棒性
- 不会因为聚合器问题导致所有子进程失效

### 修复3：修复时间戳去重逻辑（高优先级）

**修改 `poly_maker_autorun.py:460`**：

```python
def _on_ws_event(self, ev: Dict[str, Any]) -> None:
    # ...
    ts = ev.get("timestamp") or ev.get("ts") or ev.get("time")

    # ⚠️ 修改：如果事件没有时间戳，使用当前时间（毫秒精度）
    if ts is None:
        ts = time.time()

    for pc in pcs:
        # ...
        payload = {
            "price": last,
            "best_bid": bid,
            "best_ask": ask,
            "ts": ts,  # 确保总是有有效的时间戳
            "updated_at": time.time(),
            # ⚠️ 新增：单调递增的序列号
            "seq": self._ws_cache.get(token_id, {}).get("seq", 0) + 1,
        }
        # ...
```

**修改 `Volatility_arbitrage_run.py:2412-2427`**：

```python
def _apply_shared_ws_snapshot() -> None:
    nonlocal last_shared_ts
    snapshot = _load_shared_ws_snapshot()
    if not snapshot:
        return

    ts = _extract_ts(snapshot.get("ts"))
    if ts is None:
        ts = time.time()

    # ⚠️ 修改：使用序列号而不是时间戳去重
    seq = snapshot.get("seq", 0)
    last_seq = getattr(_apply_shared_ws_snapshot, "_last_seq", 0)

    if seq <= last_seq:
        return  # 序列号没变，确实是旧数据

    _apply_shared_ws_snapshot._last_seq = seq
    last_shared_ts = ts  # 保留用于日志

    # ... 继续处理
```

**优点**：
- 即使时间戳相同，只要数据更新就能检测到
- 更可靠的去重机制

### 修复4：使用原子文件写入（中优先级）

**修改 `poly_maker_autorun.py:503-528`**：

```python
def _flush_ws_cache_if_needed(self) -> None:
    now = time.time()
    if not self._ws_cache_dirty and now - self._ws_cache_last_flush < 1.0:
        return
    with self._ws_cache_lock:
        if not self._ws_cache_dirty and now - self._ws_cache_last_flush < 1.0:
            return
        data = {
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "tokens": self._ws_cache,
        }
        self._ws_cache_dirty = False
        self._ws_cache_last_flush = now

    try:
        self._ws_cache_path.parent.mkdir(parents=True, exist_ok=True)

        # ⚠️ 修改：原子写入
        tmp_path = self._ws_cache_path.with_suffix('.tmp')
        with tmp_path.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        # 原子操作：重命名
        tmp_path.replace(self._ws_cache_path)

    except OSError as exc:
        print(f"[ERROR] 写入 WS 聚合缓存失败: {exc}")
        # 可以考虑设置一个错误计数，连续失败N次后停止程序
```

**优点**：
- 避免读取不完整的JSON
- 减少竞态条件

### 修复5：添加详细日志和健康检查（中优先级）

**修改 `poly_maker_autorun.py:406-412`**：

```python
def _ws_aggregator_loop(self) -> None:
    last_health_check = 0.0
    while not self.stop_event.is_set():
        desired = self._desired_ws_token_ids()
        if desired != self._ws_token_ids:
            self._restart_ws_subscription(desired)
        self._flush_ws_cache_if_needed()

        # ⚠️ 新增：健康检查
        now = time.time()
        if now - last_health_check >= 60.0:
            self._health_check()
            last_health_check = now

        time.sleep(1.0)

def _health_check(self) -> None:
    """检查WS聚合器健康状态"""
    # 检查WS线程是否运行
    if self._ws_thread and not self._ws_thread.is_alive():
        print("[WARN] WS 聚合器线程已停止，尝试重启...")
        desired = self._desired_ws_token_ids()
        self._restart_ws_subscription(desired)

    # 检查是否有数据
    with self._ws_cache_lock:
        token_count = len(self._ws_cache)
        if token_count == 0 and len(self._ws_token_ids) > 0:
            print(f"[WARN] WS 聚合器订阅了 {len(self._ws_token_ids)} 个token，但缓存为空")

        # 检查数据新鲜度
        for token_id, data in self._ws_cache.items():
            updated_at = data.get("updated_at", 0)
            age = time.time() - updated_at
            if age > 300:  # 5分钟没更新
                print(f"[WARN] token {token_id} 数据过期，最后更新: {age:.0f}秒前")

    # 检查文件
    if self._ws_cache_path.exists():
        stat = self._ws_cache_path.stat()
        age = time.time() - stat.st_mtime
        if age > 120:  # 2分钟没更新
            print(f"[WARN] ws_cache.json 文件过期，最后修改: {age:.0f}秒前")
    else:
        print(f"[WARN] ws_cache.json 文件不存在: {self._ws_cache_path}")
```

**修改 `Volatility_arbitrage_run.py:2412-2437`**：

```python
def _apply_shared_ws_snapshot() -> None:
    nonlocal last_shared_ts
    snapshot = _load_shared_ws_snapshot()

    if not snapshot:
        # ⚠️ 新增：区分失败原因
        if not os.path.exists(shared_ws_cache_path):
            # 首次可能需要等待，不打印过多日志
            if not hasattr(_apply_shared_ws_snapshot, "_warned_missing"):
                print(f"[WARN] 共享WS缓存文件不存在: {shared_ws_cache_path}")
                _apply_shared_ws_snapshot._warned_missing = True
        return

    # ⚠️ 新增：重置警告标志
    if hasattr(_apply_shared_ws_snapshot, "_warned_missing"):
        print(f"[INFO] 共享WS缓存文件已就绪")
        delattr(_apply_shared_ws_snapshot, "_warned_missing")

    # ... 继续原有逻辑

    # ⚠️ 新增：每隔一段时间打印调试信息
    if not hasattr(_apply_shared_ws_snapshot, "_last_debug_log"):
        _apply_shared_ws_snapshot._last_debug_log = 0

    now = time.time()
    if now - _apply_shared_ws_snapshot._last_debug_log >= 300:  # 5分钟
        print(f"[DEBUG] 共享WS: ts={ts}, last_shared_ts={last_shared_ts}, "
              f"bid={bid}, ask={ask}, price={last_px}")
        _apply_shared_ws_snapshot._last_debug_log = now
```

---

## 🧪 最简单的临时解决方案（紧急修复）

如果需要快速恢复系统，最简单的方法是**回退到旧版本**（每个子进程独立WS）：

**修改 `poly_maker_autorun.py:735-736`**：
```python
env = os.environ.copy()
# ⚠️ 临时方案：不设置环境变量，让子进程使用独立WS
# env["POLY_WS_SHARED_CACHE"] = str(self._ws_cache_path)
```

**修改 `poly_maker_autorun.py:354`**：
```python
def run_loop(self) -> None:
    self.config.ensure_dirs()
    self._load_handled_topics()
    self._restore_runtime_status()
    print(f"[INIT] autorun start | copytrade_poll={self.config.copytrade_poll_sec}s")
    # ⚠️ 临时方案：不启动WS聚合器
    # self._start_ws_aggregator()
    try:
        # ...
```

**优点**：
- 立即恢复到已知可工作的状态
- 无需调试复杂的共享WS逻辑
- 可以稍后再优化

**缺点**：
- 失去了降低负载的优势
- 并发量受限于WS连接数

---

## 📋 验证修复效果的清单

修复后，按以下清单验证：

- [ ] **WS聚合器启动成功**
  - 日志中有 `[WS] 聚合订阅启动，tokens=N`
  - 没有 `[WARN] 无法启动 WS 聚合器`

- [ ] **ws_cache.json 被创建并更新**
  ```bash
  # 文件存在
  ls -lh /path/to/data/ws_cache.json

  # 内容有效
  jq '.' /path/to/data/ws_cache.json

  # 定期更新（每秒检查一次）
  watch -n 1 'jq .updated_at /path/to/data/ws_cache.json'
  ```

- [ ] **ws_cache.json 包含所有token的数据**
  ```bash
  jq '.tokens | keys' /path/to/data/ws_cache.json
  ```

- [ ] **每个token都有有效的价格和时间戳**
  ```bash
  jq '.tokens[] | {price, best_bid, best_ask, ts}' /path/to/data/ws_cache.json
  ```

- [ ] **子进程日志显示接收到行情**
  ```bash
  # 应该看到 [PX] 日志，而不是一直 [WAIT]
  tail -f /path/to/logs/autorun_*.log
  ```

- [ ] **子进程实际下单**
  - Polymarket网页端看到新订单
  - 或子进程日志中有 `[ORDER]` 相关日志

- [ ] **系统资源占用符合预期**
  ```bash
  # WS连接数应该只有1个（聚合器）
  # 而不是N个（每个子进程一个）
  netstat -an | grep ESTABLISHED | grep polymarket
  ```

---

## 🎓 架构改进建议（长期）

### 建议1：使用进程间通信代替文件共享

**问题**：文件读写有延迟和竞态条件

**方案**：使用消息队列或共享内存
```python
# 使用 multiprocessing.Queue
from multiprocessing import Queue

# 在 poly_maker_autorun.py 中
self._ws_queue = Queue()

def _on_ws_event(self, ev):
    self._ws_queue.put(ev)  # 直接推送事件

# 在子进程中
ws_queue = get_ws_queue()  # 从环境变量或其他方式获取
while not stop_event.is_set():
    try:
        ev = ws_queue.get(timeout=1)
        process_event(ev)
    except queue.Empty:
        pass
```

### 建议2：使用Redis或消息中间件

**方案**：适合多机部署
```python
import redis

# 聚合器写入
redis_client.publish("polymarket:ws", json.dumps(event))

# 子进程订阅
pubsub = redis_client.pubsub()
pubsub.subscribe("polymarket:ws")
for message in pubsub.listen():
    process_event(json.loads(message["data"]))
```

### 建议3：WebSocket 代理模式

**方案**：聚合器作为WebSocket代理服务器
```python
# 聚合器提供本地 WebSocket 服务
# ws://localhost:8765

# 子进程连接到本地代理
ws = websocket.connect("ws://localhost:8765?token_id=xxx")
```

---

## 📞 后续支持

如果按照诊断步骤操作后仍然无法解决，请提供：

1. **WS聚合器日志**：包含启动成功/失败信息
2. **ws_cache.json 内容**：如果存在的话
3. **子进程日志片段**：最后100行
4. **手动测试导入的结果**：是否能成功导入WS模块
5. **环境信息**：
   ```bash
   python3 --version
   pip3 list | grep websocket
   echo $PYTHONPATH
   ```

基于这些信息可以进一步定位问题。

---

## 总结

**最可能的根本原因**：
1. WS聚合器启动失败（导入错误或依赖缺失）
2. 失败是静默的（只打印warning）
3. 子进程依赖共享WS但没有fallback
4. 导致所有子进程无法获取行情数据
5. 因此永远不下单

**最快的解决方案**：
1. 验证 `ws_cache.json` 是否存在
2. 如果不存在，说明WS聚合器失败
3. 临时方案：不设置 `POLY_WS_SHARED_CACHE` 环境变量，让子进程使用独立WS
4. 长期方案：修复WS聚合器启动问题，添加fallback机制

**架构建议**：
- 共享WS是好的优化方向（降低负载）
- 但需要更鲁棒的实现（错误处理、fallback、健康检查）
- 或者考虑使用更成熟的IPC机制（消息队列、Redis等）
