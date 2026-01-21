# 5秒周期方案 - 快速验证指南

## ✅ 修改已完成

**文件：** `POLYMARKET_MAKER_copytrade_v2/POLYMARKET_MAKER_AUTO/POLYMARKET_MAKER/Volatility_arbitrage_run.py`
**行号：** 2722行
**修改内容：** 将策略更新周期从10秒改为5秒

```python
# 修改前：
if time_since_last_tick >= 10.0:  # 从5秒改为10秒，避免过于频繁

# 修改后：
if time_since_last_tick >= 5.0:  # 优化为5秒周期，平衡性能和响应速度
```

---

## 🚀 部署步骤

### 1. 拉取最新代码
```bash
cd /path/to/POLYMARKET_MAKER_copytrade
git checkout claude/debug-ws-order-conditions-mFXnC
git pull origin claude/debug-ws-order-conditions-mFXnC
```

### 2. 停止所有运行中的实例
```bash
# 根据你的实际启动脚本，停止autorun和所有子进程
pkill -f poly_maker_autorun.py
pkill -f Volatility_arbitrage_run.py

# 或使用你的停止脚本
# ./stop_all.sh
```

### 3. 重新启动
```bash
# 根据你的实际启动脚本
cd POLYMARKET_MAKER_copytrade_v2/POLYMARKET_MAKER_AUTO
python poly_maker_autorun.py

# 或使用你的启动脚本
# ./start_all.sh
```

---

## 📊 验证效果

### 修改前（预期）
```
[WS][SHARED] seq=20, updates=1 (2.0/min), bid=0.14, ask=0.17
窗口价格(mid): 高 0.6850 / 低 0.6850
```

### 修改后（预期）
```
[WS][SHARED] seq=25, updates=6 (12.0/min), bid=0.14, ask=0.17
窗口价格(mid): 高 0.6900 / 低 0.6800  ← 能捕捉到更多价格变化
```

---

## 🔍 实时监控命令

### 1. 监控策略更新频率
```bash
# 查看updates统计（应该从2.0/min提升到12.0/min左右）
tail -f logs/autorun/autorun_*.log | grep "updates=" | ts '[%Y-%m-%d %H:%M:%S]'
```

**期望看到：**
```
[2026-01-21 14:00:00] [WS][SHARED] seq=30, updates=6 (12.0/min), bid=0.14, ask=0.17
[2026-01-21 14:00:30] [WS][SHARED] seq=36, updates=6 (12.0/min), bid=0.142, ask=0.168
```

### 2. 监控下单信号
```bash
# 查看是否有更多的买入/卖出信号
tail -f logs/autorun/autorun_*.log | grep -E "BUY|SELL|检测到下单条件|窗口跌幅"
```

**期望看到：**
```
窗口跌幅: 当前 0.50% / 最大 1.20% / 阈值 0.10%
[STRATEGY] 检测到下单条件: drop_ratio=1.2% >= threshold=0.1%
```

### 3. 监控CPU使用率
```bash
# 监控Python进程CPU（应该增加5-10%）
top -b -n 1 -o %CPU | grep python | head -10
```

**期望看到：**
- 修改前：每个进程约1-2% CPU
- 修改后：每个进程约2-3% CPU（增加约1-2%）

### 4. 监控系统整体负载
```bash
# 实时监控
htop

# 或简单查看
top
```

**健康指标：**
- CPU总使用率 < 70%：健康
- 内存使用 < 80%：正常
- Load Average < CPU核心数：正常

---

## 📈 关键指标对比

### 30分钟后对比

| 指标 | 修改前 | 修改后（预期） | 提升 |
|------|--------|---------------|------|
| 策略更新频率 | 2/分钟 | 12/分钟 | 6倍 |
| 5分钟窗口数据点 | 10个 | 60个 | 6倍 |
| 波动检测准确度 | 低 | 显著提高 | - |
| 下单信号次数 | 很少 | 显著增加 | - |
| CPU使用率 | 基准 | +5% | 可接受 |

### 1小时后对比

统计以下数据：
- 总的`on_tick`调用次数
- 检测到的下单条件次数
- 实际下单次数
- 平均CPU使用率

---

## ⚠️ 注意事项

### 正常现象
1. **updates频率不是精确的12/min**
   - 实际可能在10-15/min之间浮动
   - 取决于市场活跃度和seq更新频率

2. **不同token的updates频率可能不同**
   - 活跃token：可能达到15-20/min
   - 横盘token：可能只有6-8/min

3. **CPU使用率略有增加**
   - 单个进程增加1-2%是正常的
   - 总体系统负载增加5-10%

### 异常情况处理

#### 如果updates频率没有提升（仍然是2/min）
```bash
# 1. 确认代码确实已更新
cd POLYMARKET_MAKER_copytrade_v2/POLYMARKET_MAKER_AUTO/POLYMARKET_MAKER
grep -n "time_since_last_tick >= 5.0" Volatility_arbitrage_run.py

# 2. 确认进程使用的是新代码
ps aux | grep Volatility_arbitrage_run.py

# 3. 重新启动所有实例（可能旧进程未完全停止）
pkill -9 -f Volatility_arbitrage_run.py
# 然后重新启动
```

#### 如果CPU使用率过高（>80%）
```bash
# 1. 检查是否有进程泄漏（多个重复进程）
ps aux | grep python | wc -l

# 2. 考虑减少同时运行的token数量
# 或回退到7秒周期（sed -i '2722s/5\.0/7.0/' Volatility_arbitrage_run.py）

# 3. 检查是否有其他资源瓶颈
iostat -x 1 5  # 检查磁盘I/O
free -h        # 检查内存
```

#### 如果仍然不下单
```bash
# 1. 查看策略窗口数据
tail -f logs/autorun/autorun_*.log | grep "窗口"

# 2. 检查是否有其他配置问题（如阈值设置过高）
cat POLYMARKET_MAKER_copytrade_v2/POLYMARKET_MAKER_AUTO/POLYMARKET_MAKER/config/run_params.json

# 3. 可能需要进一步降低周期到3秒或2秒（但先观察当前5秒的效果）
```

---

## 🎯 成功标准

### 修复成功的判断标准
经过1小时运行后，应该满足以下条件：

✅ **更新频率提升：** 从2/min提升到10-15/min
✅ **窗口数据增加：** 能看到价格高低点变化（不再是固定值）
✅ **下单信号增加：** 至少有几次检测到下单条件
✅ **系统稳定：** CPU<70%，无进程崩溃

### 如果未达到预期
1. 确认代码已更新且进程已重启
2. 检查是否有其他瓶颈（如token本身确实长时间横盘）
3. 可以考虑进一步降低周期（3秒或2秒）
4. 或应用更激进的修复方案（扩展seq事件类型）

---

## 📞 后续优化

### 如果5秒效果显著且CPU可接受
可以尝试进一步优化：

1. **降低到3秒：** `sed -i '2722s/5\.0/3.0/' Volatility_arbitrage_run.py`
2. **降低到2秒：** `sed -i '2722s/5\.0/2.0/' Volatility_arbitrage_run.py`
3. **应用智能触发方案：** 见 `BALANCED_FIX_GUIDE.md`

### 如果CPU过高或想优化
1. **提高到7秒：** `sed -i '2722s/5\.0/7.0/' Volatility_arbitrage_run.py`
2. **优化日志输出：** 减少日志频率
3. **分批运行：** 将token分成多个批次，错峰运行

---

## 📁 相关文档

- **BALANCED_FIX_GUIDE.md** - 完整的负载分析和方案对比
- **EXECUTIVE_SUMMARY.md** - 执行总结
- **shared_ws_analysis.md** - 深度技术分析
- **version_comparison_analysis.md** - 版本对比

---

## ✅ 快速验证命令一键脚本

```bash
#!/bin/bash
# 保存为 verify_fix.sh 并执行

echo "========== 验证5秒周期修复效果 =========="
echo ""

echo "1. 检查代码是否已更新..."
cd POLYMARKET_MAKER_copytrade_v2/POLYMARKET_MAKER_AUTO/POLYMARKET_MAKER
if grep -q "time_since_last_tick >= 5.0" Volatility_arbitrage_run.py; then
    echo "✅ 代码已更新为5秒周期"
else
    echo "❌ 代码未更新，请先拉取最新代码"
    exit 1
fi
echo ""

echo "2. 检查运行中的进程..."
PROC_COUNT=$(ps aux | grep -E "Volatility_arbitrage_run|poly_maker_autorun" | grep -v grep | wc -l)
echo "当前运行进程数: $PROC_COUNT"
echo ""

echo "3. 监控策略更新频率（30秒）..."
timeout 30 tail -f logs/autorun/autorun_*.log 2>/dev/null | grep --line-buffered "updates=" | head -5
echo ""

echo "4. 检查CPU使用率..."
top -b -n 1 -o %CPU | grep python | head -5
echo ""

echo "========== 验证完成 =========="
echo "如果updates频率在10-15/min之间，说明修复成功！"
```

---

好了！修改已完成并推送到GitHub。
按照上面的步骤重启你的实例后，应该能看到明显的效果提升！
