# STOPLOSS_REENTRY_REQUIREMENTS_V4_SUPPLEMENT

## 目的
本文档是对 `STOPLOSS_REENTRY_REQUIREMENTS_V4.md` 的补充口径（以我们逐章核验结论为准），用于指导代码落地与回归验证。

## A. 卖出后再买回的规则口径
1. 允许：任意路径下，卖出后按原价（或更高）买回。
2. 禁止：买入后再按原价卖出（平进平出）。
3. 硬限制：除“清仓路径”和“止损路径”外，卖出价必须满足
   - `sell_price >= max(entry_price, last_buy_price) + 2*tick`
4. 对于“地板价 + 盈利阈值”卖出路径，上述硬限制必须强制生效。

## B. 回补价格区间与先下探后回弹
1. 回补线：`reentry_line = stop_exit_price - 2*tick`（区间上界）。
2. 区间下界：`stop_exit_price * (1 - 2%)`。
3. 回补只允许在区间内触发：
   - `lower <= taker_buy_price <= upper`
4. 必须先满足“有效下探”再允许回补：
   - `probe_line = reentry_line * (1 - 5%)`
   - 先出现 `price <= probe_line`
   - 再回弹进入回补区间
5. 下探确认与回弹确认均为连续命中（默认 2 次轮询）。

## C. 时间门控
1. 清仓后到回补最短间隔：`2h`。
2. 回补后到下一次可清仓最短间隔：`30min`。
3. 第 5 次清仓后：每次回补等待额外 `+24h`。
4. 仅影响“清仓->回补”；不影响“回补->下一次清仓”。

## D. 轮次递进
1. 每轮“回补后再次清仓”，下一轮止损阈值 `+1%`。
2. 不设上限，不衰减。
3. 市场关闭时直接清理记录，不再回到 base 阈值。

## E. 熔断（token 级）
1. 粒度：仅 token 级，不是全局。
2. 条件：token 当天累计已实现亏损 `>=10%`。
3. 动作：仅该 token 当天暂停回补。
4. 次日自动重置该 token 的日累计。

## F. 状态机与持久化
1. 状态：
   - `NORMAL_MAKER`
   - `STOPLOSS_EXITED_WAITING_WINDOW`
   - `STOPLOSS_EXITED_WAITING_PROBE`
   - `STOPLOSS_EXITED_WAITING_REBOUND`
   - `REENTRY_HOLD`
2. `CLOSED_FINAL` 不再保留为终态；市场关闭时立即清理记录。
3. 持久化主文件：`copytrade/stoploss_reentry_state.json`
4. 备份文件：`copytrade/stoploss_reentry_state.bak.json`
5. 启动恢复：优先主文件，损坏回退 `.bak`，并回写主文件同步。

## G. 8.1 / 8.2 / 8.3 最终口径
### 8.1 跟随账户清仓
1. 目标账户清仓且本地无仓：清理该 token 状态。
2. 目标账户清仓但本地有仓：走现有 sell/cleanup 链路清仓；标记 `source_detached=true`，不走并行新清仓分支。
3. 复用链路：
   - `_apply_sell_signals -> pending_exit_topics -> _schedule_pending_exit_cleanup -> _start_exit_cleanup`

### 8.2 市场关闭
1. `CLOSED/RESOLVED`：立即清理该 token 的 stoploss/reentry 记录。

### 8.3 防抖清理
1. `source_detached=true` 且本地无仓时，不立即删。
2. 记录 `source_detached_since_ts`，延迟 1 个 stoploss 轮询周期后再清理。

## H. 安全与容错
1. 持仓/报价缺失时，本轮仅观测不推进关键状态。
2. 回补失败下轮可重试，不能卡死在“安全路径”。
3. API/缓存抖动时以“保守不交易、保留状态”为默认策略。
