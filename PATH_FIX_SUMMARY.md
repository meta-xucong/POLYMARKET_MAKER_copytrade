# 日志和数据路径修复说明

## 修改时间
2026-01-19

## 问题
之前 `poly_maker_autorun.py` 使用的是相对路径（`logs/autorun` 和 `data`），会相对于运行脚本时的当前工作目录（CWD），导致：
- 从不同目录运行脚本时，日志和数据文件会散落在不同位置
- 难以找到和管理日志文件
- 可能在多个目录创建 logs 和 data 文件夹

## 修改内容

### 1. 修改代码中的默认配置
**文件**: `POLYMARKET_MAKER_AUTO/poly_maker_autorun.py`

**修改前**:
```python
DEFAULT_GLOBAL_CONFIG = {
    "log_dir": str(Path("logs") / "autorun"),      # 相对路径
    "data_dir": str(Path("data")),                  # 相对路径
    "handled_topics_path": str(Path("data") / "handled_topics.json"),
    "runtime_status_path": str(Path("data") / "autorun_status.json"),
}
```

**修改后**:
```python
DEFAULT_GLOBAL_CONFIG = {
    "log_dir": str(PROJECT_ROOT / "logs" / "autorun"),      # 绝对路径
    "data_dir": str(PROJECT_ROOT / "data"),                  # 绝对路径
    "handled_topics_path": str(PROJECT_ROOT / "data" / "handled_topics.json"),
    "runtime_status_path": str(PROJECT_ROOT / "data" / "autorun_status.json"),
}
```

其中 `PROJECT_ROOT = Path(__file__).resolve().parent`，即 `POLYMARKET_MAKER_AUTO` 目录。

### 2. 简化配置文件
**文件**: `POLYMARKET_MAKER_AUTO/POLYMARKET_MAKER/config/global_config.json`

**修改前**:
```json
{
  "scheduler": { ... },
  "paths": {
    "log_directory": "logs/autorun",
    "data_directory": "data",
    "run_state_file": "data/autorun_status.json",
    ...
  }
}
```

**修改后**:
```json
{
  "scheduler": { ... }
}
```

删除了 `paths` 部分，让代码使用默认的绝对路径配置。

## 效果

### 修改后的目录结构
```
POLYMARKET_MAKER_copytrade_v2/
├── copytrade/
│   ├── copytrade_run.py
│   ├── logs/                    ← copytrade的日志
│   └── ...
└── POLYMARKET_MAKER_AUTO/
    ├── poly_maker_autorun.py
    ├── logs/                    ← poly_maker_autorun的日志
    │   └── autorun/
    │       ├── autorun_12345.log
    │       ├── autorun_67890.log
    │       └── ...
    ├── data/                    ← poly_maker_autorun的数据
    │   ├── ws_cache.json
    │   ├── handled_topics.json
    │   ├── autorun_status.json
    │   └── run_params_*.json
    └── POLYMARKET_MAKER/
        └── ...
```

### 无论从哪里运行都一致

**场景1**: 从项目根目录运行
```bash
cd /home/trader/polymarket_api/POLYMARKET_MAKER_copytrade_v2
python3 POLYMARKET_MAKER_AUTO/poly_maker_autorun.py
# 日志位置: POLYMARKET_MAKER_AUTO/logs/autorun/
# 数据位置: POLYMARKET_MAKER_AUTO/data/
```

**场景2**: 从 POLYMARKET_MAKER_AUTO 目录运行
```bash
cd /home/trader/polymarket_api/POLYMARKET_MAKER_copytrade_v2/POLYMARKET_MAKER_AUTO
python3 poly_maker_autorun.py
# 日志位置: ./logs/autorun/（同一位置）
# 数据位置: ./data/（同一位置）
```

**场景3**: 使用绝对路径运行
```bash
cd /tmp
python3 /home/trader/polymarket_api/POLYMARKET_MAKER_copytrade_v2/POLYMARKET_MAKER_AUTO/poly_maker_autorun.py
# 日志位置: /home/trader/.../POLYMARKET_MAKER_AUTO/logs/autorun/（同一位置）
# 数据位置: /home/trader/.../POLYMARKET_MAKER_AUTO/data/（同一位置）
```

## 优点

1. ✅ **一致性**：无论从哪里运行脚本，日志和数据都在同一位置
2. ✅ **易于管理**：所有日志和数据集中在 POLYMARKET_MAKER_AUTO 目录下
3. ✅ **避免污染**：不会在其他目录创建 logs 和 data 文件夹
4. ✅ **便于查找**：知道确切的日志位置，便于调试和监控
5. ✅ **符合预期**：用户期望日志在程序目录下，而不是运行目录

## 验证

运行以下命令验证路径配置：

```bash
cd POLYMARKET_MAKER_copytrade_v2/POLYMARKET_MAKER_AUTO

python3 -c "
from poly_maker_autorun import GlobalConfig, DEFAULT_GLOBAL_CONFIG
import json

with open('POLYMARKET_MAKER/config/global_config.json') as f:
    config = GlobalConfig.from_dict(json.load(f))

print('日志目录:', config.log_dir)
print('数据目录:', config.data_dir)
print('是绝对路径:', config.log_dir.is_absolute())
"
```

期望输出：
```
日志目录: /path/to/POLYMARKET_MAKER_AUTO/logs/autorun
数据目录: /path/to/POLYMARKET_MAKER_AUTO/data
是绝对路径: True
```

## 注意事项

1. **旧日志文件**：如果之前在其他位置生成了日志文件，需要手动清理或迁移
2. **权限**：确保 POLYMARKET_MAKER_AUTO 目录有写权限
3. **配置兼容**：如果用户自定义了配置文件中的 `paths`，会覆盖默认路径（但不推荐）

## 迁移指南

如果你之前从其他目录运行过脚本，可能在那些目录下有 `logs/` 和 `data/` 文件夹。

### 清理旧文件
```bash
# 查找并删除其他位置的 logs 和 data 目录（谨慎操作）
find /home/trader -name "logs" -type d -path "*/autorun" 2>/dev/null | grep -v POLYMARKET_MAKER_AUTO
find /home/trader -name "data" -type d 2>/dev/null | grep -v POLYMARKET_MAKER_AUTO

# 手动删除确认无用的目录
rm -rf /path/to/old/logs
rm -rf /path/to/old/data
```

### 迁移旧日志（可选）
```bash
# 如果想保留旧日志，可以迁移到新位置
cd POLYMARKET_MAKER_copytrade_v2/POLYMARKET_MAKER_AUTO
mkdir -p logs/autorun

# 复制旧日志
cp /old/path/logs/autorun/*.log logs/autorun/ 2>/dev/null || true
```

## 总结

✅ **修改已完成并推送到仓库**

✅ **所有日志和数据现在统一存储在 POLYMARKET_MAKER_AUTO 目录下**

✅ **无论从哪里运行脚本，行为都一致**
