# 日志巡检结论（autorun_main_20260209_173826.log）

- 未发现 `Traceback` / `ERROR` / `Exception` 级别的硬错误。
- 程序整体处于“运行中”状态：末尾显示 20 个 topic 正在 running，且持续有 WS seq 增长。
- 主要异常信号为：
  1. 大量 SELL 信号因“未找到持仓记录”被忽略（启动初期集中出现）。
  2. 存在 `NO_DATA_TIMEOUT` / `SIGNAL_TIMEOUT` 触发的回填。
  3. 多次出现“缓存数据过期（软降级）”与少量“等待 WS 缓存超时(1/2)”提示。

## 建议

1. 继续观察持仓同步：若“持仓检查失败”长期持续，建议核对持仓来源账户、token 映射与 data-api 查询参数。
2. 优化 WS 数据新鲜度：可排查缓存刷新频率、订阅粒度与 topic 启动节奏，降低过期缓存软降级频率。
3. 将 `NO_DATA_TIMEOUT`/`SIGNAL_TIMEOUT` 增长纳入监控阈值告警，便于提前发现行情/连接劣化。
