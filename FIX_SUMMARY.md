# WS 共享架构修复总结

## 修复完成时间
2026-01-19

## 问题总结

在将系统从"每个token独立WS连接"改为"单一WS聚合器+数据分发"架构后，系统运行24小时不下单。

**根本原因**：WS聚合器启动失败但静默运行，所有子进程等待不存在的数据源。

---

## ✅ 已实施的修复

### 1. WS 聚合器启动验证 (最高优先级)

**问题**：WS聚合器导入失败只打印警告，程序继续运行，导致所有子进程无数据源。

**修复**：
- ✅ 添加明确的 `[ERROR]` 消息，而不是 `[WARN]`
- ✅ 验证 `websocket-client` 依赖是否安装
- ✅ 启动后等待2秒检查线程是否存活
- ✅ 失败时提示用户"子进程将使用独立 WS 连接"

**代码位置**：`poly_maker_autorun.py:421-462`

### 2. 子进程 Fallback 机制 (最高优先级)

**问题**：即使WS聚合器失败，子进程仍依赖共享WS，永远收不到数据。

**修复**：
- ✅ 只在WS聚合器真正运行时才设置 `POLY_WS_SHARED_CACHE` 环境变量
- ✅ 子进程启动时检查文件是否存在
- ✅ 检查文件是否过期（>5分钟未更新）
- ✅ 自动降级到独立WS模式

**代码位置**：
- `poly_maker_autorun.py:758-764` (主进程)
- `Volatility_arbitrage_run.py:2462-2481` (子进程)

### 3. 序列号去重机制 (高优先级)

**问题**：使用时间戳去重导致相同时间戳的价格更新被跳过。

**修复**：
- ✅ 每个价格更新添加单调递增的序列号 `seq`
- ✅ 子进程使用序列号而不是时间戳判断数据是否更新
- ✅ 确保时间戳为 None 时使用 `time.time()`

**代码位置**：
- `poly_maker_autorun.py:511-523` (添加序列号)
- `Volatility_arbitrage_run.py:2439-2448` (序列号去重)

### 4. 原子文件写入 (中优先级)

**问题**：直接写入文件可能导致子进程读取到不完整的JSON。

**修复**：
- ✅ 先写入临时文件 `.tmp`
- ✅ 使用原子 `replace()` 操作重命名
- ✅ 出错时清理临时文件

**代码位置**：`poly_maker_autorun.py:548-567`

### 5. 健康检查和监控 (中优先级)

**问题**：无法检测WS聚合器运行状态和数据新鲜度。

**修复**：
- ✅ 每60秒执行一次健康检查
- ✅ 检查WS线程是否存活，自动重启
- ✅ 检测订阅但无数据的token
- ✅ 检测过期数据（>5分钟）
- ✅ 检查文件是否存在和新鲜度

**代码位置**：
- `poly_maker_autorun.py:414-418` (定期检查)
- `poly_maker_autorun.py:577-618` (检查逻辑)

### 6. 增强日志 (中优先级)

**修复**：
- ✅ 明确区分 `[ERROR]`, `[WARN]`, `[INFO]`, `[DEBUG]`
- ✅ 子进程每5分钟输出调试信息（seq, ts, bid, ask, price）
- ✅ 文件缺失警告只打印一次，避免日志泛滥
- ✅ 子进程启动时打印使用的WS模式

**代码位置**：
- `Volatility_arbitrage_run.py:2416-2427` (缺失文件警告)
- `Volatility_arbitrage_run.py:2460-2466` (调试日志)

---

## 🚀 使用指南

### 方式1：直接拉取修复代码（推荐）

```bash
cd /home/trader/polymarket_api/POLYMARKET_MAKER_copytrade_v2
git pull origin claude/debug-polymarket-copytrade-kxArO
```

### 方式2：重新部署

1. 备份当前配置：
```bash
cp copytrade/copytrade_config.json copytrade/copytrade_config.json.backup
cp POLYMARKET_MAKER_AUTO/POLYMARKET_MAKER/config/global_config.json \
   POLYMARKET_MAKER_AUTO/POLYMARKET_MAKER/config/global_config.json.backup
```

2. 拉取最新代码：
```bash
cd /path/to/POLYMARKET_MAKER_copytrade
git fetch origin
git checkout claude/debug-polymarket-copytrade-kxArO
git pull
```

3. 恢复配置：
```bash
cp copytrade/copytrade_config.json.backup copytrade/copytrade_config.json
cp POLYMARKET_MAKER_AUTO/POLYMARKET_MAKER/config/global_config.json.backup \
   POLYMARKET_MAKER_AUTO/POLYMARKET_MAKER/config/global_config.json
```

4. 重启服务：
```bash
# 停止现有进程
pkill -f copytrade_run.py
pkill -f poly_maker_autorun.py

# 启动服务
python3 /path/to/copytrade/copytrade_run.py &
python3 /path/to/POLYMARKET_MAKER_AUTO/poly_maker_autorun.py &
```

---

## 📋 验证修复效果

### 步骤1：检查主进程日志

```bash
# 查看主进程输出
tail -f /path/to/logs/*.log | grep -E "\[WS\]|\[ERROR\]|\[WARN\]"

# 期望看到：
# [WS] 聚合订阅启动，tokens=N          ✅ 成功
# 或
# [ERROR] 无法导入 WS 模块: ...         ⚠️ 失败但有明确提示
# [ERROR] 子进程将使用独立 WS 连接      ⚠️ Fallback 生效
```

### 步骤2：检查 ws_cache.json

```bash
# 查找文件
find /path/to/project -name "ws_cache.json"

# 如果存在，查看内容
cat /path/to/data/ws_cache.json

# 检查是否有数据和序列号
jq '.tokens[] | {seq, price, best_bid, best_ask}' /path/to/data/ws_cache.json

# 监控更新（应该每秒更新）
watch -n 1 'jq .updated_at /path/to/data/ws_cache.json'
```

### 步骤3：检查子进程日志

```bash
# 查看子进程是否接收到数据
tail -50 /path/to/logs/autorun_*.log

# 期望看到：
# [WS] 使用共享 WS 模式               ✅ 共享模式
# [WS] 使用独立 WS 模式               ✅ Fallback模式
# [PX] ...                             ✅ 接收到价格数据

# 不应该一直看到：
# [WAIT] 尚未收到行情，继续等待…      ❌ 无数据源
```

### 步骤4：验证下单功能

1. **检查网页端**：登录 Polymarket，查看是否有新订单
2. **检查子进程日志**：搜索 `[ORDER]` 关键字
3. **监控持仓变化**：观察是否有新建持仓

---

## 🔍 故障排查

### 问题1：仍然不下单

**排查步骤**：

1. **确认 copytrade_run.py 正常工作**：
```bash
cat /path/to/copytrade/tokens_from_copytrade.json
# 应该有 tokens 数组，不为空
```

2. **确认子进程已启动**：
```bash
ps aux | grep Volatility_arbitrage_run.py
# 应该看到多个进程
```

3. **检查是否收到行情**：
```bash
grep -r "PX\|收到行情" /path/to/logs/autorun_*.log
```

4. **检查策略是否触发**：
```bash
grep -r "BUY\|SELL\|ACTION" /path/to/logs/autorun_*.log
```

### 问题2：WS 聚合器启动失败

**错误信息**：
```
[ERROR] 无法导入 WS 模块: No module named 'Volatility_arbitrage_main_ws'
```

**解决方法**：
- 检查文件是否存在：`ls POLYMARKET_MAKER_AUTO/POLYMARKET_MAKER/Volatility_arbitrage_main_ws.py`
- 检查路径设置：确保 `MAKER_ROOT` 在 `sys.path` 中

**错误信息**：
```
[ERROR] 缺少依赖 websocket-client
```

**解决方法**：
```bash
pip install websocket-client
```

**结果**：
- 即使WS聚合器失败，子进程会自动 fallback 到独立WS
- 系统仍然可以正常工作（虽然并发量受限）

### 问题3：文件权限问题

**错误信息**：
```
[ERROR] 写入 WS 聚合缓存失败: [Errno 13] Permission denied
```

**解决方法**：
```bash
# 检查目录权限
ls -ld /path/to/data/

# 修复权限
chmod 755 /path/to/data/
chown youruser:yourgroup /path/to/data/
```

### 问题4：ws_cache.json 存在但子进程不使用

**检查环境变量**：
```bash
# 查看子进程环境变量
ps aux | grep Volatility_arbitrage_run.py
# 获取进程ID，比如 12345

cat /proc/12345/environ | tr '\0' '\n' | grep POLY_WS_SHARED_CACHE
```

**检查文件新鲜度**：
```bash
stat /path/to/data/ws_cache.json
# 如果超过5分钟未更新，子进程会自动 fallback
```

---

## 📊 性能对比

| 指标 | 独立WS（旧版） | 共享WS（新版，修复后） |
|------|---------------|---------------------|
| WS连接数 | N个（每token一个） | 1个 |
| 内存占用 | 高 | 低 |
| CPU使用率 | 中等 | 低 |
| 并发上限 | 受WS连接数限制 | 仅受系统资源限制 |
| 单点故障 | 否 | 是（但有fallback） |
| 可观测性 | 一般 | 好（健康检查） |

---

## 🎯 预期效果

修复后的系统应该：

1. ✅ **启动时有明确提示**
   - 成功：`[WS] 聚合订阅启动，tokens=N`
   - 失败：`[ERROR] 无法导入 WS 模块 ... 子进程将使用独立 WS 连接`

2. ✅ **自动降级**
   - WS聚合器失败时，子进程自动使用独立WS
   - 不会因为聚合器问题导致所有子进程失效

3. ✅ **数据更可靠**
   - 使用序列号去重，不会错过价格更新
   - 原子文件写入，避免读取到损坏的JSON

4. ✅ **可观测性更好**
   - 每60秒健康检查，主动发现问题
   - 每5分钟调试日志，便于诊断
   - 明确的错误和警告信息

5. ✅ **能够正常下单**
   - 子进程能接收到行情数据
   - 策略正常触发
   - 订单能够成功创建

---

## 📚 相关文档

- [WS_SHARED_ARCHITECTURE_BUG_REPORT.md](./WS_SHARED_ARCHITECTURE_BUG_REPORT.md) - 详细的BUG分析报告
- [BUG_ANALYSIS_REPORT.md](./BUG_ANALYSIS_REPORT.md) - 初始分析报告（后发现方向错误）

---

## 🤝 需要帮助？

如果修复后仍有问题，请提供以下信息：

1. **主进程日志**（最后100行）：
```bash
tail -100 /path/to/logs/poly_maker_autorun.log
```

2. **子进程日志**（任意一个，最后100行）：
```bash
tail -100 /path/to/logs/autorun_*.log | head -100
```

3. **ws_cache.json 内容**（如果存在）：
```bash
cat /path/to/data/ws_cache.json
```

4. **环境信息**：
```bash
python3 --version
pip list | grep websocket
echo $PYTHONPATH
```

5. **进程状态**：
```bash
ps aux | grep -E "copytrade|poly_maker|Volatility"
```

---

## ✨ 总结

这次修复解决了WS共享架构的核心问题：

1. **从静默失败到明确报错** - 问题更容易发现
2. **从单点故障到优雅降级** - 系统更加健壮
3. **从数据丢失到可靠传输** - 不会错过价格更新
4. **从黑盒到可观测** - 更容易监控和诊断

修复后的系统既保留了共享WS的优势（降低负载、增加并发），又增加了容错能力（自动fallback），确保不会因为单一组件失败导致整个系统停止工作。
