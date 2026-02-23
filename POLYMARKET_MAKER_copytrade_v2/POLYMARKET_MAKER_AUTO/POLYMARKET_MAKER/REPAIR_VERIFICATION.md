# 修复验证文档

## 修复内容总结

### 1. 最小买入价格失效修复
- **问题**: `StrategyConfig` 未接收 `min_price` 参数
- **修复**: 添加 `min_buy_price` 配置读取和传递
- **默认值**: 代码 0.01，配置文件 0.05

### 2. 低余额风控失效修复（资金回笼机制）
- **问题**: 余额低于阈值时，运行中子进程继续买入
- **修复**: 信号机制 + SELL-ONLY 模式 + 立即卖出启动

## 核心机制流程

### 场景 A: 运行中子进程触发低余额

```
余额 < 120U
    ↓
调度层 _is_buy_paused_by_balance() 
    ├── 阻止新token启动（原有）
    └── 发送信号文件 low_balance_signal_{token_id}.json
        ↓
执行层主循环检测 _low_balance_signal_active()
    ├── 撤掉所有买单 _cancel_open_buy_orders_before_exit()
    ├── 进入SELL-ONLY模式 _activate_sell_only()
    │   └── 立即启动卖出线程 _execute_sell()
    │       └── 超时机制生效（1-4小时后ABANDONED退出）
    └── 清理待执行买入信号
```

### 场景 B: 新token启动时资金不足

```
新token启动
    ↓
INIT 检测 _low_balance_signal_active()
    ├── 有持仓 → 进入SELL-ONLY + 立即启动卖出
    └── 无持仓 → 快速退出（释放队列）
```

## 测试验证步骤

### 测试 1: 最小买入价格
```bash
# 配置 min_buy_price: 0.05
# 启动一个价格为 0.03 的token
# 预期: 不生成BUY信号，日志显示价格低于阈值
```

### 测试 2: 低余额信号检测（运行中）
```bash
# 1. 启动正常token，等待买入持仓
# 2. 模拟余额下降（或手动创建信号文件）
#    touch data/low_balance_signal_{token_id}.json
# 3. 观察日志:
#    - [BUY_GATE][SIGNAL] 检测到账户低余额信号
#    - [SELL-ONLY] 立即启动卖出监控
#    - 买单被撤销
# 4. 验证: 子进程不再尝试买入，只监控卖出
```

### 测试 3: 新token启动时资金不足（有持仓）
```bash
# 1. 创建信号文件模拟低余额状态
# 2. 启动新token（REFILL场景，有持仓恢复）
# 3. 观察日志:
#    - [INIT][BUY_GATE] 检测到账户资金不足信号
#    - [INIT][LOW_BALANCE] 当前有持仓，立即进入SELL-ONLY模式
#    - [SELL-ONLY] 立即启动卖出监控
# 4. 验证: 直接尝试卖出，不等待买入信号
```

### 测试 4: 新token启动时资金不足（无持仓）
```bash
# 1. 创建信号文件模拟低余额状态
# 2. 启动新token（无持仓）
# 3. 观察日志:
#    - [INIT][BUY_GATE] 检测到账户资金不足信号
#    - [INIT][LOW_BALANCE] 当前无持仓，快速退出释放队列
#    - [EXIT] 释放队列：低余额快速退出
# 4. 验证: 进程快速退出，slot立即释放
```

### 测试 5: 超时退出与回填
```bash
# 1. 触发低余额进入SELL-ONLY
# 2. 等待1-4小时（取决于entry_price）
# 3. 观察日志:
#    - [RELEASE] 卖出挂单长期无动作，准备退出
#    - [RELEASE] ABANDONED 退出前撤销 X 个残留挂单
#    - [EXIT] 释放队列：SELL_ABANDONED
# 4. 验证: 子进程退出，新token可以进入
```

### 测试 6: 余额恢复
```bash
# 1. 确保有子进程在低余额SELL-ONLY模式
# 2. 恢复余额到阈值以上
# 3. 观察调度层日志:
#    - [BUY_GATE] 余额恢复，已清理 X 个低余额暂停买入信号文件
# 4. 验证: 新token可以正常启动
```

## 预期日志输出

### 低余额触发时（运行中）
```
[BUY_GATE] 余额门禁状态切换: 暂停买入 | free_balance=110.0000 M=120.0000
[BUY_GATE] 已向 3 个运行中的子进程发送低余额暂停买入信号

[子进程日志]
[BUY_GATE][SIGNAL] 检测到账户低余额信号，进入仅卖出模式并撤销所有买单
[EXIT] LOW_BALANCE_PAUSE -> 已撤销 BUY 挂单数量=1
[COUNTDOWN] 已进入仅卖出模式：倒计时窗口内不再买入。
[COUNTDOWN] 仍有持仓，将继续等待卖出，清仓后停止脚本。
[SELL-ONLY] 立即启动卖出监控，地板价参考: 0.4850
```

### 新token启动（资金不足）
```
[INIT][BUY_GATE] 检测到账户资金不足信号，启动快速资金回笼模式
[INIT][LOW_BALANCE] 当前有持仓，立即进入SELL-ONLY模式尝试卖出
[COUNTDOWN] 已进入仅卖出模式：倒计时窗口内不再买入。
[SELL-ONLY] 立即启动卖出监控，地板价参考: 0.7320
```

### ABANDONED 超时退出
```
[RELEASE] 卖出挂单长期无动作，准备退出。
[RELEASE] ABANDONED 退出前撤销 1 个残留挂单
[EXIT] 释放队列：SELL_ABANDONED
```

## 关键配置参数

```json
{
  "min_buy_price": 0.05,
  "max_buy_price": 0.98,
  "sell_inactive_hours": 1.0,
  "buy_pause_min_free_balance": 120.0
}
```

## 注意事项

1. **线程安全**: SELL-ONLY 模式下使用线程异步执行卖出，主循环继续运行
2. **重复触发**: `_activate_sell_only` 有防重入检查（`if sell_only_event.is_set(): return`）
3. **信号清理**: 子进程退出时会自动清理信号文件
4. **余额恢复**: 只影响新启动token，已在SELL-ONLY的子进程保持原状态
