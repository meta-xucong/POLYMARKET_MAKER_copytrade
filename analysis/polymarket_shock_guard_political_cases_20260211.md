# Polymarket 政治盘急跌样本（用于 shock_guard 参数建议）

数据来源：
- 市场元数据：`https://gamma-api.polymarket.com/markets?slug=...`
- 分钟级价格：`https://clob.polymarket.com/prices-history?market=...&startTs=...&endTs=...&fidelity=1`

> 说明：`fidelity=1` 对应约 1 分钟粒度，适合估计“重大里程碑事件”时的急跌幅度与持续时间。

## 样本1：Biden 民主党提名 YES（退选节点）
- 市场：`will-joe-biden-win-the-us-2024-democratic-presidential-nomination`
- 观察区间：2024-07-21 14:00 ~ 2024-07-22 02:00 UTC
- 结果：
  - 最大 1 分钟跌幅：**96.15%**（0.13 -> 0.005）
  - 最大 30 分钟跌幅：**98.36%**（0.305 -> 0.005）
  - 从高点到 <=0.01 用时：约 **2140 秒（35.7 分钟）**

## 样本2：Trump 总统大选 NO（胜选路径确认）
- 市场：`will-donald-trump-win-the-2024-us-presidential-election`（NO token）
- 观察区间：2024-11-06 00:00 ~ 2024-11-06 12:00 UTC
- 结果：
  - 最大 1 分钟跌幅：**66.67%**
  - 最大 30 分钟跌幅：**80.00%**
  - 接近归零阶段出现多次 30%~60% 的分钟级下杀

## 样本3：Kamala 总统大选 YES（结果路径反向确认）
- 市场：`will-kamala-harris-win-the-2024-us-presidential-election`（YES token）
- 观察区间：2024-11-06 00:00 ~ 2024-11-06 12:00 UTC
- 结果：
  - 最大 1 分钟跌幅：**66.67%**
  - 最大 30 分钟跌幅：**71.19%**（0.0295 -> 0.0085）
  - 从局部高点到 <=0.01 约 **1680 秒（28 分钟）**

---

## 基于样本的参数建议（政治盘）
建议初始值：

```json
"shock_guard": {
  "enabled": true,
  "shock_window_sec": 90,
  "shock_drop_pct": 0.35,
  "shock_velocity_pct_per_sec": null,
  "shock_abs_floor": 0.05,
  "observation_hold_sec": 180,
  "recovery": {
    "rebound_pct_min": 0.12,
    "reconfirm_sec": 90,
    "spread_cap": 0.04,
    "require_conditions": 2
  },
  "blocked_cooldown_sec": 900,
  "max_pending_buy_age_sec": 240
}
```

### 参数解释（简）
- `shock_drop_pct=0.35`：样本里的里程碑急跌远高于 35%，可较稳定拦截“消息落地式坠崖”。
- `observation_hold_sec=180`：给市场 3 分钟做真假反弹分离，避免第一刀误抄底。
- `rebound_pct_min=0.12` + `reconfirm_sec=90`：要求明确恢复迹象，不让“弱反抽后再破底”放行。
- `shock_abs_floor=0.05`：对于贴地价格，直接提高警惕，减少归零段抄底。
- `blocked_cooldown_sec=900`：恢复失败后冷却 15 分钟，防止连续逆势抄底。

