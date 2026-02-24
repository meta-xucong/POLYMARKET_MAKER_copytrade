# 市场状态检测 - 快速参考

## 改动总览

本次改动实现了以下核心功能：
1. **入场前市场检测**: Token 加入队列前验证市场是否开放
2. **404 智能识别**: Book API 返回 404 时，主动查询 Gamma API 区分"市场关闭"vs"低流动性"
3. **回填前验证**: 重新激活 Token 前再次确认市场状态
4. **自动文件清理**: 已关闭市场自动从 copytrade JSON 文件中删除

## 关键改动点

### 1. 新增模块
- `POLYMARKET_MAKER/market_state_checker.py` (28KB)
  - MarketStateChecker: 检测市场状态
  - MarketClosedCleaner: 清理已关闭市场数据

### 2. 修改 poly_maker_autorun.py
- 导入市场状态检测模块 (~20 行)
- AutoRunManager.__init__ 初始化检测器 (~15 行)
- 新增 `_get_condition_id_for_token()` 辅助方法
- 修改 `_evict_stale_pending_topics()` 添加状态检测逻辑 (~40 行)
- 修改 `_filter_refillable_tokens()` 回填前验证 (~30 行)

## 工作流程

### Token 生命周期（新）

```
入场阶段:
  候选 Token 
      ↓
  检测市场状态 (Gamma API + CLOB Book)
      ↓
  分类处理:
    - ACTIVE: 正常进入 pending，订阅 WS
    - LOW_LIQUIDITY: 进入 pending，订阅 WS + 定期 Book 探针
    - CLOSED/RESOLVED/ARCHIVED/NOT_FOUND: 拒绝入场，清理文件

运行阶段:
  Book 探针 404
      ↓
  查询 Gamma API 确认状态
      ↓
  分类退出:
    - 市场关闭: MARKET_CLOSED (不可回填)
    - 低流动性: LOW_LIQUIDITY_TIMEOUT (可回填)
    - 其他: NO_DATA_TIMEOUT (可回填)

回填阶段:
  从 exit_tokens.json 加载
      ↓
  筛选可回填候选
      ↓
  重新验证市场状态 (强制刷新缓存)
      ↓
  如市场已关闭: 拒绝回填，清理文件
  如市场活跃: 允许回填，进入 pending
```

## 配置参数（内嵌代码）

### 市场状态检测 (market_state_checker.py)
```python
GAMMA_API_BASE = "https://gamma-api.polymarket.com"
CLOB_API_BASE = "https://clob.polymarket.com"
CACHE_TTL = 300  # 5分钟
GAMMA_API_TIMEOUT = 10  # 秒
CLOB_BOOK_TIMEOUT = 5   # 秒
MAX_RETRY_ATTEMPTS = 3
```

### 回填控制 (原有配置)
```python
refill_cooldown_minutes = 5  # 冷却时间
max_refill_retries = 3       # 最大重试
NO_DATA_TIMEOUT max = 2      # NO_DATA 特殊限制
```

## 文件清理行为

### 触发条件
- Gamma API 返回市场状态为: closed, resolved, archived, not_found
- 回填前验证发现市场已关闭

### 清理文件
1. `tokens_from_copytrade.json` - 删除 token 条目
2. `copytrade_state.json` - 删除 token 状态
3. `exit_tokens.json` - 更新为 `refillable: false`

### 线程安全
所有文件操作通过 `_file_io_lock` (RLock) 串行化，避免读写冲突

## 退出原因对照表

| 退出原因 | 含义 | 可回填 | 备注 |
|---------|------|-------|------|
| MARKET_CLOSED | 市场已关闭 | 否 | 新增强制检测 |
| MARKET_RESOLVED | 市场已结算 | 否 | 新增强制检测 |
| MARKET_ARCHIVED | 市场已归档 | 否 | 新增强制检测 |
| MARKET_NOT_FOUND | 市场不存在 | 否 | 新增强制检测 |
| LOW_LIQUIDITY_TIMEOUT | 低流动性超时 | 是 | 新增分类 |
| NO_DATA_TIMEOUT | 无数据超时 | 是(限2次) | 原有逻辑 |
| MARKET_CLOSED_ON_REFILL | 回填时关闭 | 否 | 回填验证发现 |

## 日志关键词

```bash
# 市场关闭检测
"[PENDING] 市场已关闭，永久移除"
"[CLEANER] 已清理关闭市场"

# 低流动性检测
"[PENDING] 低流动性市场超时"

# 回填验证
"[REFILL] 拒绝回填（市场已关闭）"
"[REFILL] 允许回填"

# API 错误
"[STATE_CHECK] Gamma API 查询失败"
"[STATE_CHECK] Book 查询异常"
```

## 故障排查

### 问题1: 模块导入失败
```
[WARNING] market_state_checker 模块导入失败
```
**检查**: `market_state_checker.py` 是否在 `POLYMARKET_MAKER/` 目录

### 问题2: 检测不生效
**检查**: 
- 日志是否有 `[STATE_CHECK]` 输出
- `MARKET_STATE_CHECKER_AVAILABLE` 是否为 True

### 问题3: 文件未清理
**检查**: 
- 日志是否有 `[CLEANER]` 输出
- 文件锁是否正常工作

## 测试

```bash
cd POLYMARKET_MAKER_AUTO
python test_market_state_integration.py
```

预期: 所有测试通过

## 回滚方案

如需回滚：
1. 删除 `POLYMARKET_MAKER/market_state_checker.py`
2. 恢复 `poly_maker_autorun.py` 到改动前版本

或临时禁用:
```python
# 在 poly_maker_autorun.py 开头
MARKET_STATE_CHECKER_AVAILABLE = False
```

## 兼容性

- Python 3.8+
- 向后兼容: 模块导入失败时自动回退到原有逻辑
- 不改变配置文件格式
- 不新增外部依赖

---

文档版本: 1.0
更新日期: 2026-02-25
