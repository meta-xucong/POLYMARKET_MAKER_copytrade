# config.json 配置说明（中文）

本文档用于说明 `config.json` 各字段含义与默认值，便于在运行 copytrade/maker 相关脚本时进行参数调整。

> 说明：`null` 表示留空，运行时由程序或外部输入覆盖。

## 顶层字段

| 字段 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `target_addresses` | array | `[]` | 跟踪/复制的目标地址列表。为空表示不限定或由外部输入。 |
| `signal_tracking` | object | 见下 | 信号跟踪相关配置。 |
| `maker_strategy` | object | 见下 | 做市策略参数。 |
| `run_params` | object | 见下 | 单次运行覆盖参数。 |
| `scheduler` | object | 见下 | 任务调度与并发控制。 |
| `risk` | object | 见下 | 风控配置。 |
| `cooldown` | object | 见下 | 冷却/去重相关配置。 |
| `orderbook` | object | 见下 | 订单簿刷新与缓存配置。 |
| `config_reload_sec` | number | `600` | 配置热加载周期（秒）。 |
| `execution` | object | 见下 | 下单执行相关参数。 |
| `state` | object | 见下 | 状态持久化配置。 |
| `retry_strategy` | object | 见下 | 通用重试策略。 |
| `monitoring` | object | 见下 | 监控与健康检查配置。 |
| `maker_strategy_defaults` | object | 见下 | 做市默认参数与分主题配置。 |
| `logging` | object | 见下 | 日志配置。 |

## signal_tracking

| 字段 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `poll_interval_sec` | number | `5` | 拉取/扫描信号的轮询间隔（秒）。 |

## maker_strategy

| 字段 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `order_size` | number | `10` | 默认下单数量。 |
| `price_spread_bps` | number | `50` | 做市价差（基点），1bp=0.01%。 |
| `min_order_size` | number | `5` | 最小下单数量。 |
| `min_quote_amount` | number | `1` | 最小报价金额。 |
| `poll_interval_sec` | number | `10` | 做市策略轮询间隔（秒）。 |
| `refresh_interval_sec` | number | `5` | 订单刷新间隔（秒）。 |
| `sell_mode` | string | `conservative` | 卖出模式（如 conservative / aggressive）。 |
| `aggressive_step` | number | `0.01` | 激进模式调整步长。 |
| `aggressive_timeout` | number | `300` | 激进模式超时（秒）。 |
| `exit_floor_price` | number | `0` | 退出策略的最低价格。 |
| `exit_sell_mode` | string | `aggressive` | 退出时的卖出模式。 |
| `buy_price_threshold` | number/null | `null` | 触发买入的价格阈值（留空表示不限定）。 |
| `drop_window_minutes` | number | `60` | 跌幅统计窗口（分钟）。 |
| `drop_pct` | number | `0.001` | 跌幅阈值比例。 |
| `profit_pct` | number | `0.005` | 盈利目标比例。 |
| `enable_incremental_drop_pct` | boolean | `true` | 是否启用递增跌幅阈值。 |
| `incremental_drop_pct_step` | number | `0.0002` | 递增跌幅步长。 |
| `incremental_drop_pct_cap` | number | `0.2` | 递增跌幅上限。 |
| `disable_duplicate_signal` | boolean | `true` | 是否去重信号。 |
| `disable_sell_signals` | boolean | `false` | 是否禁用卖出信号。 |
| `min_price` | number | `0.0` | 允许的最小价格。 |
| `max_price` | number | `1.0` | 允许的最大价格。 |
| `min_market_order_size` | number/null | `null` | 市价单最小数量。 |
| `countdown` | object | 见下 | 倒计时/临近截止配置。 |

### maker_strategy.countdown

| 字段 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `minutes_before_end` | number | `300` | 距离结束多少分钟触发倒计时逻辑。 |
| `absolute_time` | string/null | `null` | 绝对时间触发点（如 `YYYY-MM-DD HH:mm`）。 |
| `timezone` | string/null | `null` | 时区（如 `America/New_York`）。 |

## run_params

| 字段 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `market_url` | string/null | `null` | 指定市场 URL。 |
| `timezone` | string | `America/New_York` | 运行时区。 |
| `deadline_override_ts` | number/null | `null` | 覆盖截止时间戳。 |
| `disable_deadline_checks` | boolean | `false` | 是否禁用截止时间检查。 |
| `deadline_policy` | object | 见下 | 截止时间策略。 |
| `side` | string/null | `null` | 交易方向（如 `buy`/`sell`）。 |
| `order_size` | number/null | `null` | 覆盖下单数量。 |
| `order_size_is_target` | boolean | `true` | `order_size` 是否为目标仓位。 |
| `sell_mode` | string | `aggressive` | 覆盖卖出模式。 |

### run_params.deadline_policy

| 字段 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `override_choice` | string/null | `null` | 覆盖的选择项（程序自定义）。 |
| `disable_deadline` | boolean | `false` | 是否禁用截止时间策略。 |
| `timezone` | string | `America/New_York` | 截止时间策略时区。 |
| `default_deadline` | object | 见下 | 默认截止时间。 |

#### run_params.deadline_policy.default_deadline

| 字段 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `time` | string | `12:59` | 默认截止时间（HH:mm）。 |
| `timezone` | string | `America/New_York` | 默认截止时间时区。 |

## scheduler

| 字段 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `max_concurrent_jobs` | number | `2` | 最大并发任务数。 |
| `max_concurrent_exit_jobs` | number | `2` | 最大并发退出任务数。 |
| `topic_start_cooldown_sec` | number | `3` | 主题启动冷却时间（秒）。 |
| `poll_interval_seconds` | number | `5` | 调度轮询间隔（秒）。 |

## risk

| 字段 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `blacklist_token_keys` | array | `[]` | 黑名单 token 列表。 |
| `max_notional_per_token` | number | `10` | 单 token 最大名义敞口。 |
| `max_notional_total` | number | `100` | 总名义敞口上限。 |
| `allow_short` | boolean | `false` | 是否允许做空。 |

## cooldown

| 字段 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `cooldown_sec_per_token` | number | `20` | 单 token 冷却时间（秒）。 |
| `orphan_ignore_sec` | number | `120` | 孤立信号忽略时长（秒）。 |
| `exit_ignore_cooldown` | boolean | `true` | 退出时是否忽略冷却。 |

## orderbook

| 字段 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `refresh_sec` | number | `2` | 订单簿刷新间隔（秒）。 |
| `max_fetch_per_loop` | number | `30` | 每次循环最大抓取数量。 |
| `cache_max_items` | number | `2000` | 缓存最大条目数。 |

## execution

| 字段 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `order_slice_min` | number | `1.0` | 拆单最小数量。 |
| `order_slice_max` | number | `2.0` | 拆单最大数量。 |
| `retry_attempts` | number | `8` | 下单重试次数。 |
| `price_tolerance_step` | number | `0.0075` | 价格容忍步长。 |
| `wait_seconds` | number | `6.0` | 等待间隔（秒）。 |
| `poll_interval_seconds` | number | `1.0` | 执行轮询间隔（秒）。 |
| `order_interval_seconds` | number | `0.5` | 连续下单间隔（秒）。 |
| `min_quote_amount` | number | `1.0` | 最小报价金额。 |
| `min_market_order_size` | number | `0.0` | 市价单最小数量。 |

## state

| 字段 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `path` | string | `state/copytrade_state.json` | 状态文件路径。 |
| `save_interval_sec` | number | `30` | 状态保存间隔（秒）。 |

## retry_strategy

| 字段 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `max_attempts` | number | `4` | 最大重试次数。 |
| `initial_backoff_seconds` | number | `2.0` | 初始退避秒数。 |
| `backoff_multiplier` | number | `2.0` | 退避倍数。 |
| `max_backoff_seconds` | number | `60.0` | 最大退避秒数。 |
| `jitter_fraction` | number | `0.3` | 抖动比例。 |

## monitoring

| 字段 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `metrics_flush_interval_seconds` | number | `120` | 指标刷新间隔（秒）。 |
| `healthcheck_interval_seconds` | number | `180` | 健康检查间隔（秒）。 |

## maker_strategy_defaults

| 字段 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `default` | object | 见下 | 全局默认做市参数。 |
| `topics` | object | `{}` | 主题级别的参数覆盖。 |

### maker_strategy_defaults.default

| 字段 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `min_edge` | number | `0.02` | 最小边际/收益阈值。 |
| `max_position_per_market` | number | `10.0` | 单市场最大仓位。 |
| `order_size` | number | `10.0` | 默认下单数量。 |
| `spread_target` | number | `0.01` | 目标价差比例。 |
| `refresh_interval_seconds` | number | `30` | 刷新间隔（秒）。 |
| `max_open_orders` | number | `20` | 最大挂单数。 |

## logging

| 字段 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `level` | string | `INFO` | 日志级别。 |
| `path` | string | `logs/app.log` | 日志文件路径。 |
