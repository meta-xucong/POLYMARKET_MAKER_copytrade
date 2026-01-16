# copytrade + maker 改造蓝图（仅方案）

> 目标：在 `POLYMARKET_MAKER_copytrade_v2` 中新增 `copytrade/` 目录与脚本（只负责生成配置、监听目标账户并产出 token 记录），同时将 `POLYMARKET_MAKER_AUTO/POLYMARKET_MAKER` 的“按配置筛选 topics”改为“读取 copytrade 产生的 token 记录”，其余做市逻辑不变。

## 1. 当前现状与改造边界

### 1.1 现有 autorun 流程

- `POLYMARKET_MAKER_AUTO/poly_maker_autorun.py` 通过 `Customize_fliter_blacklist.py` 输出的筛选结果（topics）驱动自动下单流程：
  - 定时调用 `run_filter_once()` 生成 topics 列表并写入 `topics_filtered.json`。
  - 解析 topic 信息并塞入队列，启动 `Volatility_arbitrage_run.py` 子进程执行做市策略。
- 策略本身（`POLYMARKET_MAKER/Volatility_arbitrage_*`）已经完备，本次只替换“topics 的来源”，严禁改动 maker 执行逻辑。

### 1.2 参考材料

- `代码参考材料/copytrade_v3_muti` 目录：提供多账户 copytrade 的数据抓取与状态组织方式，可复用其结构（如账户循环抓取、positions/ trades 解析、token_key 标识等）。

## 2. 新增 copytrade 目录结构设计

> 在 `POLYMARKET_MAKER_copytrade_v2` 下新增 `copytrade/` 目录，只实现：
> 1) 新建配置文件（多目标账户）；
> 2) 记录目标账户买入/卖出 token；
> 3) 日志输出到 `copytrade/logs`；
> 4) 抓取到的 token 另存到专属 json 文件。

### 2.1 目录规划

```
POLYMARKET_MAKER_copytrade_v2/
└─ copytrade/
   ├─ copytrade_run.py          # 主脚本（只做抓取/记录/落盘）
   ├─ copytrade_config.json     # 配置文件（多目标账户）
   ├─ copytrade_state.json      # 运行状态/偏移量（可选，用于去重）
   ├─ tokens_from_copytrade.json# 抓取到的 token 结果输出
   └─ logs/
      └─ copytrade_YYYYMMDD.log  # 运行日志
```

> 说明：`copytrade_state.json` 可选但建议保留，用于记录上次抓取的时间戳或交易游标，避免重复写入。

### 2.2 copytrade_config.json 设计

```json
{
  "poll_interval_sec": 30,
  "initial_lookback_sec": 3600,
  "targets": [
    {
      "account": "0x123...",
      "min_size": 1.0,
      "enabled": true
    },
    {
      "account": "0x456...",
      "min_size": 0.5,
      "enabled": true
    }
  ]
}
```

- `targets` 支持多个目标账户；
- `min_size`（可选）用于过滤过小成交；
- `initial_lookback_sec` 用于首次启动时回溯抓取区间；
- 产出的 token、sell 信号、状态文件路径固定在 `copytrade/` 目录内，方便用户不用额外配置。

### 2.3 tokens_from_copytrade.json 输出结构

```json
{
  "updated_at": "2024-01-01T12:00:00Z",
  "tokens": [
    {
      "token_id": "<token_id>",
      "source_account": "0x123...",
      "last_seen": "2024-01-01T12:00:00Z"
    }
  ]
}
```

- 该文件由 copytrade 脚本更新，供 maker 侧读取；
- 实际字段以 `token_id` 为核心，供 maker 侧直接发起做市；
- 卖出信号另写入 `copytrade_sell_signals.json`，用于触发停止做市并清仓。

### 2.4 copytrade_run.py 功能蓝图

- 读取 `copytrade_config.json`，按 `targets` 循环抓取目标账户的 positions 或 trades；
- 参考 `copytrade_v3_muti` 的数据解析逻辑，识别 BUY/SELL；
- 过滤后写入 `tokens_from_copytrade.json`：
  - 去重：同一 `token_id` 仅保留最新时间；
  - 记录 `source_account` 和 `last_seen`；
- 输出日志到 `copytrade/logs/`：
  - 轮转或按日期写入；
  - 记录抓取次数、命中 token 数量、错误等信息。

> 只实现以上功能，不引入任何 maker 执行逻辑。

## 3. maker 侧改造方案（替换 topics 筛选来源）

### 3.1 改造目标

- 删除 `poly_maker_autorun.py` 中“filter 参数 + `Customize_fliter_blacklist.py`”筛选流程；
- 替换为读取 `copytrade/tokens_from_copytrade.json` 生成待执行列表；
- 下单/做市逻辑不改动，仅将 topic 入口改为 token 入口（或使用 token 数据构造 topic_info）。

### 3.2 数据接入策略

- 新增配置项（建议）到 autorun 配置中：
  - `copytrade_tokens_path`: 指向 `copytrade/tokens_from_copytrade.json`；
  - `copytrade_poll_sec`: 读取 token 文件的轮询周期；
- 在 `AutoRunManager._refresh_topics()` 中：
  - 删除 `run_filter_once()`；
  - 替换为 `_load_copytrade_tokens()` 读取 token JSON；
  - 将 token 转换为“伪 topic”条目，放入 `latest_topics` 和 `topic_details`。

### 3.3 token -> topic 的映射规则

- **目标**：满足 `_build_run_config()` 对 `topic_id` 等字段的依赖。
- 设计建议：
  - `topic_id` 直接使用 `token_id` 作为唯一 key；
  - `topic_details[token_id]` 中可填入：
    - `slug`（若能从 API 获取则写入；否则 fallback 为 `token_id`）。
- 若无法获得 `slug`，就保持字段为空，让 maker 侧逻辑自行处理（不得改动 maker 逻辑）。

### 3.4 需要调整的代码位置（仅说明）

- `POLYMARKET_MAKER_AUTO/poly_maker_autorun.py`
  - 移除/禁用 `FilterConfig` 与 `run_filter_once()` 相关逻辑；
  - 增加 `copytrade token` 解析函数（例如 `_load_copytrade_tokens()`）；
  - `GlobalConfig` 中新增 copytrade 路径与轮询参数。
- `POLYMARKET_MAKER/config` 若需要：
  - 新增 `autorun` 配置模板（指向 token 文件）或扩展现有配置文件字段。

> 注意：`Volatility_arbitrage_*` 与 `maker_execution.py` 不做改动。

## 4. 实施步骤（后续编码时执行）

1. 在 `copytrade/` 新增脚本与配置：
   - 基于参考代码，实现账户数据抓取、token 解析、日志与 JSON 输出。
2. 修改 `poly_maker_autorun.py`：
   - 移除 filter 逻辑，接入 `copytrade/tokens_from_copytrade.json`；
   - 确保与现有调度/子进程/日志逻辑兼容。
3. 补充配置说明与示例（可放到 `copytrade/README.md` 或 `config_params.md` 中）。
4. 验证流程：
   - 运行 copytrade 脚本 → 生成 tokens 文件；
   - 启动 autorun → 读取 tokens 并触发 maker 运行。

## 5. 风险与注意事项

- **数据缺失风险**：若 copytrade 数据源不提供 `slug`，保持字段为空，让 maker 逻辑自行处理。
- **去重策略**：必须有稳定的 token key，否则 autorun 可能重复触发；建议使用 `token_id` 作为主键。
- **兼容性**：所有改动仅影响 topics 来源，其他逻辑不变。

---

> 本文仅为设计蓝图，下一步根据该方案逐步落地代码。
