# config.json 配置说明（中文）

本文档用于说明 `config.json` 各字段含义与默认值，便于在运行 copytrade/maker 相关脚本时进行参数调整。

> 说明：`null` 表示留空，运行时由程序或外部输入覆盖。

## 顶层字段

| 字段 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `target_addresses` | array | `["0x79add3f87e377b0899b935472c07d2c3816ba9f1"]` | 跟踪/复制的目标地址列表。为空表示不限定或由外部输入。 |
| `signal_tracking` | object | 见下 | 信号跟踪相关配置。 |
| `maker_strategy` | object | 见下 | 做市策略参数。 |
| `run_params` | object | 见下 | 单次运行覆盖参数。 |
| `scheduler` | object | 见下 | 任务调度与并发控制。 |
| `risk` | object | 见下 | 风控配置。 |
| `cooldown` | object | 见下 | 冷却/去重相关配置。 |
| `orderbook` | object | 见下 | 订单簿刷新与缓存配置。 |
| `config_reload_sec` | number | `600` | 配置热加载周期（秒）。 |
| `state` | object | 见下 | 状态持久化配置。 |
| `maker_strategy_defaults` | object | 见下 | 做市默认参数与分主题配置。 |
| `logging` | object | 见下 | 日志配置。 |

## signal_tracking

| 字段 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `poll_interval_sec` | number | `30` | 拉取/扫描信号的轮询间隔（秒）。 |
| `watch_position_min_usdc` | number | `10.0` | 触发 watchlist 的最小持仓市值（USDC）。 |
| `watchlist_poll_interval_sec` | number | `20` | watchlist 轮询检查间隔（秒）。 |
| `position_poll_interval_sec` | number | `20` | 仓位轮询间隔（秒）。 |
| `position_size_threshold` | number | `0.0` | 仓位过滤阈值（份额）。 |
| `positions_refresh_sec` | number/null | `null` | 仓位缓存刷新间隔（秒），空表示使用默认策略。 |
| `positions_cache_bust_mode` | string | `sec` | 仓位缓存刷新策略（如 `sec`）。 |
| `sell_confirm_max` | number | `5` | 触发强制 SELL 的确认次数。 |
| `sell_confirm_window_sec` | number | `300` | SELL 确认窗口（秒）。 |
| `sell_confirm_force_ratio` | number | `0.5` | 强制 SELL 的最小比例。 |
| `sell_confirm_force_shares` | number | `0.0` | 强制 SELL 的最小份额。 |

## maker_strategy

| 字段 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `order_size` | number | `10` | 默认下单数量。 |
| `price_spread_bps` | number | `100` | 做市价差（基点），1bp=0.01%。 |
| `min_order_size` | number | `5` | 最小下单数量。 |
| `min_quote_amount` | number | `1` | 最小报价金额。 |
| `poll_interval_sec` | number | `30` | 做市策略轮询间隔（秒）。 |
| `refresh_interval_sec` | number | `30` | 订单刷新间隔（秒）。 |
| `sell_mode` | string | `aggressive` | 卖出模式（如 conservative / aggressive）。 |
| `aggressive_step` | number | `0.01` | 激进模式调整步长。 |
| `aggressive_timeout` | number | `300` | 激进模式超时（秒）。 |
| `buy_price_threshold` | number/null | `null` | 触发买入的价格阈值（留空表示不限定）。 |
| `drop_window_minutes` | number | `60` | 跌幅统计窗口（分钟）。 |
| `drop_pct` | number | `0.01` | 跌幅阈值比例（小数表示百分比，例如 `0.01` = `1%`）。 |
| `profit_pct` | number | `0.02` | 盈利目标比例（小数表示百分比，例如 `0.02` = `2%`）。 |
| `profit_ratio` | number | `0.02` | 旧字段兼容（与 `profit_pct` 等价）。 |
| `enable_incremental_drop_pct` | boolean | `true` | 是否启用递增跌幅阈值。 |
| `incremental_drop_pct_step` | number | `0.002` | 递增跌幅步长（小数表示百分比，例如 `0.002` = `0.2%` = `20bps`）。 |
| `incremental_drop_pct_cap` | number | `0.1` | 递增跌幅上限（小数表示百分比，例如 `0.1` = `10%`）。 |
| `disable_duplicate_signal` | boolean | `true` | 是否去重信号。 |
| `disable_sell_signals` | boolean | `false` | 是否禁用卖出信号。 |
| `min_price` | number | `0.01` | 允许的最小价格。 |
| `max_price` | number | `0.99` | 允许的最大价格。 |
| `min_market_order_size` | number/null | `null` | 市价单最小数量。 |
| `max_history_points` | number | `600` | 策略历史窗口最大点数。 |
| `exit_poll_interval_sec` | number/null | `null` | 清仓轮询间隔（秒），空表示沿用策略轮询。 |
| `exit_timeout_sec` | number | `300` | 清仓超时（秒）。 |
| `exit_position_refresh_sec` | number | `10` | 清仓时仓位刷新间隔（秒）。 |
| `exit_tick_size` | number/null | `null` | 清仓 tick size（留空则用 `tick_size`）。 |
| `tick_size` | number/null | `null` | tick size（默认留空）。 |
| `exit_taker_spread_threshold` | number/null | `null` | 清仓 taker 价差阈值（留空则用 `taker_spread_threshold`）。 |
| `taker_spread_threshold` | number | `0.01` | taker 价差阈值。 |
| `exit_min_order_shares` | number/null | `null` | 清仓最小份额（留空则用 `min_order_shares`/`min_order_size`）。 |
| `min_order_shares` | number/null | `null` | 最小份额（清仓与下单共用）。 |
| `exit_taker_enabled` | boolean | `true` | 清仓是否允许 taker。 |
| `exit_taker_order_type` | string/null | `null` | 清仓 taker 订单类型（留空则用 `taker_order_type`）。 |
| `taker_order_type` | string/null | `null` | taker 订单类型。 |
| `exit_maker_only` | boolean | `false` | 清仓是否仅 maker。 |
| `order_size_mode` | string | `fixed_shares` | 下单规模模式（如 `fixed_shares`）。 |
| `slice_min` | number | `0` | 下单切片最小值。 |
| `slice_max` | number | `0` | 下单切片最大值。 |
| `exit_min_order_usd` | number/null | `null` | 清仓最小 USD 订单（留空则用 `min_order_usd`）。 |
| `min_order_usd` | number | `0` | 最小 USD 订单。 |
| `max_order_usd` | number | `0` | 最大 USD 订单。 |
| `exit_deadband_shares` | number/null | `null` | 清仓死区份额（留空则用 `deadband_shares`）。 |
| `deadband_shares` | number | `0` | 死区份额。 |
| `exit_enable_reprice` | boolean | `true` | 清仓是否允许改价。 |
| `exit_reprice_ticks` | number/null | `null` | 清仓改价 tick（留空则用 `reprice_ticks`）。 |
| `reprice_ticks` | number | `1` | 改价 tick。 |
| `exit_reprice_cooldown_sec` | number/null | `null` | 清仓改价冷却（留空则用 `reprice_cooldown_sec`）。 |
| `reprice_cooldown_sec` | number | `0` | 改价冷却（秒）。 |
| `exit_dedupe_place` | boolean | `true` | 清仓下单去重。 |
| `exit_allow_partial` | boolean | `true` | 清仓是否允许部分成交。 |
| `exit_retry_on_insufficient_balance` | boolean | `true` | 清仓余额不足是否重试。 |
| `exit_retry_shrink_factor` | number | `0.5` | 清仓重试缩量比例。 |
| `place_fail_backoff_base_sec` | number | `2` | 下单失败退避基数（秒）。 |
| `place_fail_backoff_cap_sec` | number | `60` | 下单失败退避上限（秒）。 |
| `countdown` | object | 见下 | 倒计时/临近截止配置。 |

### maker_strategy.countdown

| 字段 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `minutes_before_end` | number | `300` | 距离结束多少分钟触发倒计时逻辑。 |
| `absolute_time` | string/null | `null` | 绝对时间触发点（如 `YYYY-MM-DD HH:mm`）。 |
| `timestamp` | string/number/null | `null` | 绝对时间触发点（时间戳或可解析时间字符串）。 |
| `timezone` | string/null | `null` | 时区（如 `America/New_York`）。 |

## run_params

| 字段 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `order_size` | number/null | `null` | 覆盖下单数量。 |
| `sell_mode` | string | `aggressive` | 覆盖卖出模式。 |

## scheduler

| 字段 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `max_concurrent_jobs` | number | `4` | 最大并发任务数。 |
| `max_concurrent_exit_jobs` | number | `4` | 最大并发退出任务数。 |
| `topic_start_cooldown_sec` | number | `30` | 主题启动冷却时间（秒）。 |
| `poll_interval_seconds` | number | `30` | 调度轮询间隔（秒）。 |

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
| `exit_ignore_cooldown` | boolean | `true` | 退出时是否忽略冷却。 |

## orderbook

| 字段 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `refresh_sec` | number | `2` | 订单簿刷新间隔（秒）。 |
| `cache_max_items` | number | `2000` | 缓存最大条目数。 |

## state

| 字段 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `path` | string | `state/copytrade_state.json` | 状态文件路径。 |
| `save_interval_sec` | number | `30` | 状态保存间隔（秒）。 |

## maker_strategy_defaults

| 字段 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `topics` | object | `{}` | 主题级别的参数覆盖。 |
| `low_price` | object | 见下 | 低价彩头单参数（低于阈值时下少量）。 |

### maker_strategy_defaults.low_price

| 字段 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `price_threshold` | number | `0.15` | 低价阈值（低于该价格触发低价彩头单）。 |
| `order_size` | number | `5.0` | 低价彩头单下单数量。 |

## logging

| 字段 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `level` | string | `INFO` | 日志级别。 |
| `path` | string | `logs/app.log` | 日志文件路径。 |

## accounts

| 字段 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `target_accounts` | array | `[]` | 备用目标账户列表（当 `target_addresses` 为空时使用）。 |

## log_dir

| 字段 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `log_dir` | string/null | `null` | 顶层日志目录（当 `logging.path` 未设置时生效）。 |
