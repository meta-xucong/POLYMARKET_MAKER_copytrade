#!/bin/bash

echo "==================================================================="
echo "         子进程代码版本验证工具"
echo "==================================================================="
echo ""

# 1. 检查磁盘上的代码是否包含 updated_at 修复
echo "[1] 检查磁盘代码版本..."
if grep -q "updated_at = snapshot.get(\"updated_at\"" \
   /home/trader/polymarket_api/POLYMARKET_MAKER_copytrade_v2/POLYMARKET_MAKER_AUTO/POLYMARKET_MAKER/Volatility_arbitrage_run.py 2>/dev/null; then
    echo "    ✓ 磁盘代码：新版本（包含 updated_at 修复）"
else
    echo "    ✗ 磁盘代码：旧版本（未包含修复）"
fi
echo ""

# 2. 查找正在运行的子进程
echo "[2] 查找运行中的子进程..."
PIDS=$(ps aux | grep "Volatility_arbitrage_run.py" | grep -v grep | awk '{print $2}')

if [ -z "$PIDS" ]; then
    echo "    ✗ 未找到运行中的子进程"
    echo ""
    echo "==================================================================="
    echo "结论：无运行中的子进程，可以直接启动新版本"
    echo "==================================================================="
    exit 0
fi

echo "    ✓ 找到 $(echo "$PIDS" | wc -l) 个子进程"
echo ""

# 3. 检查子进程启动时间
echo "[3] 检查子进程启动时间..."
for pid in $PIDS; do
    START_TIME=$(ps -p $pid -o lstart= 2>/dev/null)
    if [ -n "$START_TIME" ]; then
        echo "    PID $pid: $START_TIME"
    fi
done
echo ""

# 4. 检查最近一次代码提交/修改时间
echo "[4] 检查代码最后修改时间..."
CODE_FILE="/home/trader/polymarket_api/POLYMARKET_MAKER_copytrade_v2/POLYMARKET_MAKER_AUTO/POLYMARKET_MAKER/Volatility_arbitrage_run.py"
if [ -f "$CODE_FILE" ]; then
    CODE_MTIME=$(stat -c '%y' "$CODE_FILE" 2>/dev/null || stat -f '%Sm' "$CODE_FILE" 2>/dev/null)
    echo "    代码文件修改时间: $CODE_MTIME"
else
    echo "    ✗ 代码文件不存在"
fi
echo ""

# 5. 分析子进程日志中的 seq 变化（核心判断）
echo "[5] 分析日志中的 seq 值变化..."
echo "    说明: 新代码会持续递增，旧代码会卡在低值"
echo ""

LOG_DIR="/home/trader/polymarket_api/POLYMARKET_MAKER_copytrade_v2/POLYMARKET_MAKER_AUTO"
if [ ! -d "$LOG_DIR" ]; then
    echo "    ✗ 日志目录不存在: $LOG_DIR"
else
    # 找最近修改的3个日志文件
    RECENT_LOGS=$(find "$LOG_DIR" -name "autorun_*.log" -type f -printf '%T@ %p\n' 2>/dev/null | sort -rn | head -3 | awk '{print $2}')

    if [ -z "$RECENT_LOGS" ]; then
        echo "    ✗ 未找到子进程日志文件"
    else
        for log in $RECENT_LOGS; do
            LOG_NAME=$(basename "$log")
            echo "    --- $LOG_NAME ---"

            # 提取所有 seq 值
            SEQ_VALUES=$(grep -o "seq=[0-9]*" "$log" 2>/dev/null | grep -o "[0-9]*" | tail -10)

            if [ -z "$SEQ_VALUES" ]; then
                echo "        无 seq 数据"
            else
                SEQ_ARRAY=($SEQ_VALUES)
                SEQ_COUNT=${#SEQ_ARRAY[@]}
                FIRST_SEQ=${SEQ_ARRAY[0]}
                LAST_SEQ=${SEQ_ARRAY[$((SEQ_COUNT-1))]}

                echo "        最近 10 次 seq 值: $(echo $SEQ_VALUES | tr '\n' ',' | sed 's/,/, /g' | sed 's/, $//')"
                echo "        首次 seq: $FIRST_SEQ, 最新 seq: $LAST_SEQ"

                # 判断是否递增
                if [ $LAST_SEQ -gt $((FIRST_SEQ + 5)) ]; then
                    echo "        ✓ seq 持续递增 -> 可能使用新代码"
                elif [ $LAST_SEQ -eq $FIRST_SEQ ]; then
                    echo "        ✗ seq 完全不变 -> 肯定使用旧代码（已卡死）"
                else
                    echo "        ? seq 增长缓慢 -> 需要更长时间观察"
                fi
            fi
            echo ""
        done
    fi
fi

# 6. 最终判断
echo "==================================================================="
echo "                         综合判断"
echo "==================================================================="
echo ""
echo "【判断标准】："
echo "  - 如果子进程启动时间 < 代码修改时间 -> 使用旧代码"
echo "  - 如果日志中 seq 值完全不变或卡在低值 -> 使用旧代码"
echo "  - 如果日志中 seq 值持续递增（差值>100） -> 使用新代码"
echo ""
echo "【建议操作】："
echo "  使用旧代码 -> 立即执行 pkill 重启所有进程"
echo "  使用新代码 -> 继续运行，观察 2 小时后验证效果"
echo ""
echo "==================================================================="
