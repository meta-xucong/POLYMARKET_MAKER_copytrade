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
| `state` | object | 见下 | 状态持久化配置。 |
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
| `buy_price_threshold` | number/null | `null` | 触发买入的价格阈值（留空表示不限定）。 |
| `drop_window_minutes` | number | `60` | 跌幅统计窗口（分钟）。 |
| `drop_pct` | number | `0.001` | 跌幅阈值比例（小数表示百分比，例如 `0.001` = `0.1%`）。 |
| `profit_pct` | number | `0.005` | 盈利目标比例（小数表示百分比，例如 `0.005` = `0.5%`）。 |
| `enable_incremental_drop_pct` | boolean | `true` | 是否启用递增跌幅阈值。 |
| `incremental_drop_pct_step` | number | `0.0002` | 递增跌幅步长（小数表示百分比，例如 `0.0002` = `0.02%` = `2bps`）。 |
| `incremental_drop_pct_cap` | number | `0.2` | 递增跌幅上限（小数表示百分比，例如 `0.2` = `20%`）。 |
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
| `order_size` | number/null | `null` | 覆盖下单数量。 |
| `sell_mode` | string | `aggressive` | 覆盖卖出模式。 |

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
| `low_price` | object | 见下 | 低价彩头单参数（低于阈值时下少量）。 |
| `topics` | object | `{}` | 主题级别的参数覆盖。 |

### maker_strategy_defaults.low_price

| 字段 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `price_threshold` | number | `0.15` | 低价阈值（低于该价格触发低价彩头单）。 |
| `order_size` | number | `5.0` | 低价彩头单下单数量。 |

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
