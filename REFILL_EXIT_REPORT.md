# Refill 运行日志退出分析报告（供 Claude Code 进一步修复）

## 目标
对 `refill/` 目录内所有运行日志进行归纳，明确：
1. 每个 token 的退出原因与运行状态。
2. 正常原因 vs. 非正常原因（故障/异常）。
3. 重点错误类型与疑似根因，便于后续修复。

> 说明：以下结论来源于日志中 `[EXIT_RECORD]`、`[EXIT]` 以及异常/错误关键字记录。

---

## 一、整体结论概览

### 1) 正常退出原因（业务规则驱动）
- **SIGNAL_TIMEOUT**：长时间无交易信号后主动退出。
- **exit-only cleanup**：收到清仓信号，执行清仓退出（非故障）。

### 2) 非正常退出/故障原因（需重点关注）
- **SELL_ABANDONED**：卖出流程被放弃，通常与卖出路径异常/网络错误关联。
- **STALE_DATA_IN_TRADING**：交易过程中行情/盘口数据过期或滞后。
- **上游 5xx/502 错误**：订单查询出现 502/500，Cloudflare/上游不稳定。
- **Request exception**：请求异常（可能是网络、SDK、上游接口不稳定）。
- **余额不足导致下单失败**：持续缩减买入目标仍失败。
- **代码级异常**：自动卖出逻辑出现变量作用域错误。

---

## 二、逐日志运行状态与退出原因摘要

> 同一日志中可能多次触发重试并记录退出；此处只列出出现过的退出原因类型。

- **autorun_1067819514…**：`SIGNAL_TIMEOUT`
- **autorun_1083269675…**：`SELL_ABANDONED`、`SIGNAL_TIMEOUT`
- **autorun_1130921394…**：`SIGNAL_TIMEOUT`
- **autorun_2439467090…**：`SIGNAL_TIMEOUT`、`SELL_ABANDONED`
- **autorun_3797526508…**：`SELL_ABANDONED`、`SIGNAL_TIMEOUT`
- **autorun_4416972923…**：`SELL_ABANDONED`、`STALE_DATA_IN_TRADING`、`SIGNAL_TIMEOUT`
- **autorun_4776277198…**：`SELL_ABANDONED`、`SIGNAL_TIMEOUT`、`STALE_DATA_IN_TRADING`
- **autorun_4854268143…**：`SELL_ABANDONED`、`STALE_DATA_IN_TRADING`、`SIGNAL_TIMEOUT`
- **autorun_6356107225…**：`SIGNAL_TIMEOUT`、`SELL_ABANDONED`
- **autorun_7335082676…**：`SIGNAL_TIMEOUT`、`SELL_ABANDONED`、`STALE_DATA_IN_TRADING`
- **autorun_8092829940…**：`SELL_ABANDONED`、`SIGNAL_TIMEOUT`
- **autorun_8308141087…**：`SIGNAL_TIMEOUT`、`SELL_ABANDONED`
- **autorun_8865170260…**：`SIGNAL_TIMEOUT`、`SELL_ABANDONED`
- **autorun_9079500392…**：`SELL_ABANDONED`、`SIGNAL_TIMEOUT`
- **autorun_9331882515…**：`SIGNAL_TIMEOUT`
- **autorun_exit_733508…**：收到清仓信号并清仓退出

---

## 三、异常类型细分与证据（重点分析）

### A. SELL_ABANDONED（卖出被放弃）
**现象**：多个 token 在卖出阶段放弃退出，说明卖出链路稳定性不足。

**可能关联异常**：
- 订单状态查询报 5xx/502（上游故障）。
- Request exception（网络/SDK/上游不稳定）。
- 自动卖出代码异常导致卖单逻辑失败。

**影响**：导致仓位未按策略出清，最终进入退出逻辑。

---

### B. STALE_DATA_IN_TRADING（交易数据过期）
**现象**：交易过程中行情/盘口数据不再可用，直接退出。

**可能原因**：
- 行情流更新异常或中断。
- 请求重试/阻塞导致数据过期。

---

### C. 上游 5xx/502 与 Request exception
**现象**：订单查询返回 502/500 或请求异常。

**影响**：触发卖出流程失败、SELL_ABANDONED，甚至与 STALE_DATA_IN_TRADING 叠加。

---

### D. 余额不足导致买单失败
**现象**：日志中显示“疑似余额不足，缩减买入目标后重试”。

**影响**：多次失败后可能触发超时或放弃逻辑。

---

### E. 代码级异常（自动卖出逻辑）
**现象**：自动卖出旧仓位失败，报变量作用域错误（`_execute_sell` 未绑定）。

**影响**：卖出流程无法执行，导致 SELL_ABANDONED 或持仓异常状态。

---

## 四、建议排查优先级（供 Claude Code 修复时参考）

1. **修复自动卖出函数作用域错误**（高优先级）
   - 避免 `_execute_sell` 变量绑定失败导致卖出路径直接异常。
2. **增强卖出流程对 5xx/502 的容错**（高优先级）
   - 引入更稳健的重试与退避策略；考虑切换/备用接口或降级处理。
3. **Request exception 统一处理**（高优先级）
   - 对网络/SDK 异常统一分类、统一重试、统一熔断。
4. **数据过期检测**（中优先级）
   - 对行情/盘口更新时间做更严格校验，避免在 stale 数据上继续交易。
5. **余额不足处理策略**（中优先级）
   - 在多次缩减失败后提前退出或设置安全阈值，避免浪费调用。

---

## 五、补充：日志中清仓退出
- `autorun_exit_733508...` 显示收到清仓信号并执行清仓，这类退出是策略型“正常退出”。

---

如需进一步拆出**每个 token 的时间线**或**错误计数统计表**，可继续扩展本报告。
