# 市场状态检测模块 - 改动总结

## 一、新增文件

### 1. `POLYMARKET_MAKER/market_state_checker.py`
- **作用**: 市场状态检测核心模块
- **功能**:
  - `MarketStateChecker`: 主动查询 Polymarket Gamma API 和 CLOB API
  - `MarketClosedCleaner`: 清理已关闭市场的文件数据
  - `MarketStatus`: 市场状态枚举（active/closed/resolved/archived/not_found/low_liquidity）
  - `MarketState`: 市场状态数据类，支持序列化
- **配置**: 所有参数内嵌在代码中，不依赖外部配置文件
  - `GAMMA_API_BASE`: https://gamma-api.polymarket.com
  - `CLOB_API_BASE`: https://clob.polymarket.com
  - `CACHE_TTL`: 300秒
  - 请求超时: Gamma 10秒, CLOB 5秒

## 二、修改的文件

### 1. `poly_maker_autorun.py`

#### 新增导入 (行 ~31)
```python
from market_state_checker import (
    MarketStateChecker,
    MarketClosedCleaner,
    MarketStatus,  # 注意：需要添加这行
    MarketState,
    check_market_state,
    clean_closed_market,
    should_refill_token,
    init_market_state_checker,
    init_cleaner,
)
```

#### 新增初始化 (AutoRunManager.__init__)
```python
self._market_state_checker: Optional[MarketStateChecker] = None
self._market_closed_cleaner: Optional[MarketClosedCleaner] = None
self._file_io_lock = threading.RLock()

if MARKET_STATE_CHECKER_AVAILABLE:
    self._market_state_checker = init_market_state_checker(file_lock=self._file_io_lock)
    self._market_closed_cleaner = init_cleaner(file_lock=self._file_io_lock)
```

#### 新增辅助方法
- `_get_condition_id_for_token(token_id)`: 从 topic_details/latest_topics 获取 condition_id

#### 修改 `_evict_stale_pending_topics`
- 当 Book 返回 404 时，主动查询市场状态
- 如市场已关闭，记录 `MARKET_CLOSED` 而非 `NO_DATA_TIMEOUT`
- 自动调用清理器删除 copytrade 文件中的数据

#### 修改 `_filter_refillable_tokens`
- 回填前重新验证 `NO_DATA_TIMEOUT`/`LOW_LIQUIDITY_TIMEOUT` 的市场状态
- 如市场已关闭，拒绝回填并清理文件
- 更新 `NON_RETRYABLE_REASONS` 添加永久关闭原因

## 三、核心逻辑流程

### Token 入场流程（新增市场检测）
```
1. 候选 Token -> 2. _get_condition_id_for_token() 
-> 3. MarketStateChecker.check_market_state()
    -> Gamma API 查询市场状态
    -> 如活跃，CLOB Book 查询流动性
-> 4. 根据状态分类:
    - ACTIVE: 正常订阅 WS
    - LOW_LIQUIDITY: 订阅 WS + 定期 Book 探针
    - CLOSED/RESOLVED/ARCHIVED/NOT_FOUND: 拒绝入场，清理文件
```

### Token 退出流程（改进）
```
1. Book 探针返回 404
-> 2. 查询 Gamma API 确认状态
    - 如关闭: 记录 MARKET_CLOSED, refillable=False
              调用 MarketClosedCleaner 清理文件
    - 如低流动性: 记录 LOW_LIQUIDITY_TIMEOUT, refillable=True
    - 如活跃: 记录 NO_DATA_TIMEOUT, refillable=True
```

### Token 回填流程（新增验证）
```
1. 从 exit_tokens.json 加载记录
-> 2. 筛选可回填候选
-> 3. 对于 NO_DATA_TIMEOUT/LOW_LIQUIDITY_TIMEOUT:
    强制刷新市场状态 (use_cache=False)
    - 如关闭: 拒绝回填，更新记录为 refillable=False
    - 如活跃: 允许回填
-> 4. 加入 pending 队列
```

## 四、线程安全设计

### 文件操作锁 (`_file_io_lock`)
- 类型: `threading.RLock()`
- 共享给: `MarketStateChecker`, `MarketClosedCleaner`, `AutoRunManager`
- 保护: 所有 JSON 文件的读写操作

### 缓存锁 (`_cache_lock`)
- 在 `MarketStateChecker` 内部
- 保护: 市场状态缓存的读写

### 使用模式
```python
with self._file_io_lock:
    # 读取/写入 exit_tokens.json
    # 读取/写入 copytrade_tokens.json
    # 读取/写入 copytrade_state.json
```

## 五、文件清理行为

### 触发条件
- 市场状态检测为永久关闭（NOT_FOUND/RESOLVED/ARCHIVED/CLOSED）
- 回填前验证发现市场已关闭

### 清理动作
1. **copytrade_tokens.json**: 删除对应 token 条目
2. **copytrade_state.json**: 删除对应 token 状态
3. **exit_tokens.json**: 更新记录为 `refillable=False`

### 防冲突机制
- 所有文件操作通过 `_file_io_lock` 串行化
- 清理和写入不会同时发生

## 六、兼容性

### 向后兼容
- 模块导入失败时 (`MARKET_STATE_CHECKER_AVAILABLE=False`)，回退到原有逻辑
- 所有新方法都有默认值处理
- 不改变原有配置文件的格式或内容

### 依赖要求
- Python 3.8+
- requests (已存在)
- 无新增外部依赖

## 七、关键参数速查

### 市场状态检测配置 (market_state_checker.py)
| 参数 | 值 | 说明 |
|-----|-----|------|
| GAMMA_API_TIMEOUT | 10秒 | Gamma API 查询超时 |
| CLOB_BOOK_TIMEOUT | 5秒 | CLOB Book 查询超时 |
| CACHE_TTL | 300秒 | 状态缓存时间 |
| MAX_RETRY_ATTEMPTS | 3次 | API 失败重试次数 |
| MIN_LIQUIDITY_BIDS | 1 | 最少买单数量阈值 |
| MIN_LIQUIDITY_ASKS | 1 | 最少卖单数量阈值 |

### 回填配置 (原有)
| 参数 | 值 | 说明 |
|-----|-----|------|
| refill_cooldown_minutes | 5分钟 | 回填冷却时间 |
| max_refill_retries | 3次 | 最大回填重试次数 |
| NO_DATA_TIMEOUT max | 2次 | NO_DATA_TIMEOUT 特殊限制 |

## 八、日志输出示例

### 市场关闭检测
```
[PENDING] 市场已关闭，永久移除: 0x1234... status=not_found
[CLEANER] 已清理关闭市场: 0x1234..., files=['tokens_from_copytrade.json (1 items)']
```

### 低流动性检测
```
[PENDING] 低流动性市场超时: 0x1234... (bids=0, asks=0)
```

### 回填验证
```
[REFILL] 拒绝回填（市场已关闭）: 0x1234... status=resolved
[REFILL] 允许回填: 0x1234... status=active
```

## 九、故障排查

### 模块导入失败
```
[WARNING] market_state_checker 模块导入失败: No module named 'market_state_checker'
```
**解决**: 确保 `market_state_checker.py` 在 `POLYMARKET_MAKER/` 目录下

### API 查询失败
```
[STATE_CHECK] Gamma API 查询失败 (尝试 1/3): ..., error=ConnectionTimeout
```
**行为**: 自动重试3次，如都失败标记为 API_ERROR，允许回填

### 文件清理失败
```
[CLEANER] 清理失败: ..., error=Permission denied
```
**行为**: 记录错误，不影响主流程，下次检测时重试

## 十、测试

运行集成测试:
```bash
cd POLYMARKET_MAKER_AUTO
python test_market_state_integration.py
```

预期输出: 所有测试通过

---

## 改动文件清单

1. **新增**: `POLYMARKET_MAKER/market_state_checker.py` (新模块)
2. **修改**: `poly_maker_autorun.py` (集成代码)
3. **新增**: `test_market_state_integration.py` (测试脚本)

总计: 2 个新增文件, 1 个修改文件
