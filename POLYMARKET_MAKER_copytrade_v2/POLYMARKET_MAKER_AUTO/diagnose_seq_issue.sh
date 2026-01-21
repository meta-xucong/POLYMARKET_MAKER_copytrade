#!/bin/bash

# 诊断seq不更新问题的脚本

echo "=========================================="
echo "诊断 seq 不更新问题"
echo "=========================================="
echo ""

# 1. 检查聚合器状态
echo "[1] 检查聚合器状态"
echo "-------------------"
if pgrep -f "poly_maker_autorun.py" > /dev/null; then
    echo "✓ 聚合器进程正在运行"
    # 查看最新的聚合器日志
    echo "聚合器最新日志（最后5行）："
    ps aux | grep poly_maker_autorun.py | grep -v grep | head -1
else
    echo "✗ 聚合器进程未运行！"
fi
echo ""

# 2. 检查ws_cache.json
echo "[2] 检查 ws_cache.json"
echo "----------------------"
WS_CACHE="/home/trader/polymarket_api/POLYMARKET_MAKER_copytrade_v2/POLYMARKET_MAKER_AUTO/data/ws_cache.json"
if [ -f "$WS_CACHE" ]; then
    # 文件修改时间
    AGE=$(stat -c %Y "$WS_CACHE")
    NOW=$(date +%s)
    DIFF=$((NOW - AGE))
    echo "✓ ws_cache.json 存在"
    echo "  最后更新: ${DIFF}秒前"

    # 统计tokens数量
    TOKEN_COUNT=$(cat "$WS_CACHE" | grep -o '"seq"' | wc -l)
    echo "  token数量: ${TOKEN_COUNT}"

    # 显示最大的seq值
    MAX_SEQ=$(cat "$WS_CACHE" | grep '"seq"' | grep -oP '\d+' | sort -n | tail -1)
    echo "  最大seq值: ${MAX_SEQ}"

    if [ $DIFF -gt 120 ]; then
        echo "  ⚠ 警告: 文件超过2分钟未更新！"
    fi
else
    echo "✗ ws_cache.json 不存在！"
fi
echo ""

# 3. 检查子进程
echo "[3] 检查子进程状态"
echo "-------------------"
CHILD_PIDS=$(pgrep -f "Volatility_arbitrage_run.py")
if [ -z "$CHILD_PIDS" ]; then
    echo "✗ 没有运行中的子进程"
else
    CHILD_COUNT=$(echo "$CHILD_PIDS" | wc -l)
    echo "✓ 运行中的子进程数: ${CHILD_COUNT}"
    echo ""

    # 随机选择一个子进程检查日志
    RANDOM_PID=$(echo "$CHILD_PIDS" | head -1)
    CMDLINE=$(ps -p $RANDOM_PID -o args --no-headers)

    # 提取token_id
    if [[ $CMDLINE =~ run_params_([0-9]+)\.json ]]; then
        TOKEN_ID="${BASH_REMATCH[1]}"
        LOG_FILE="/home/trader/polymarket_api/POLYMARKET_MAKER_copytrade_v2/POLYMARKET_MAKER_AUTO/logs/autorun_${TOKEN_ID}.log"

        echo "检查子进程 PID=$RANDOM_PID (token=${TOKEN_ID:0:12}...)"
        echo ""

        if [ -f "$LOG_FILE" ]; then
            echo "子进程日志最后30行："
            echo "===================="
            tail -30 "$LOG_FILE" | grep -E "\[WS\]|\[DEBUG\]" || echo "  (无WS或DEBUG日志)"
            echo ""

            # 检查是否有DEBUG日志
            if grep -q "\[WS\]\[SHARED\]\[DEBUG\]" "$LOG_FILE"; then
                echo "✓ 发现DEBUG日志"
                echo "最新DEBUG信息："
                grep "\[WS\]\[SHARED\]\[DEBUG\]" "$LOG_FILE" | tail -3
            else
                echo "✗ 未发现DEBUG日志（可能是旧代码）"
            fi
            echo ""

            # 统计seq出现次数
            SEQ_COUNT=$(grep -o 'seq=[0-9]\+' "$LOG_FILE" | wc -l)
            echo "日志中seq出现次数: ${SEQ_COUNT}"

            # 显示所有不同的seq值
            echo "日志中的seq值变化："
            grep -oP 'seq=\K\d+' "$LOG_FILE" | sort -u | head -10

        else
            echo "✗ 日志文件不存在: $LOG_FILE"
        fi
    fi
fi
echo ""

# 4. 对比缓存和子进程
echo "[4] 数据一致性检查"
echo "-------------------"
if [ -f "$WS_CACHE" ] && [ ! -z "$CHILD_PIDS" ]; then
    # 提取一个token检查
    FIRST_TOKEN=$(cat "$WS_CACHE" | grep -oP '"[0-9]{70,80}"' | head -1 | tr -d '"')
    if [ ! -z "$FIRST_TOKEN" ]; then
        CACHE_SEQ=$(cat "$WS_CACHE" | grep -A 10 "\"$FIRST_TOKEN\"" | grep '"seq"' | grep -oP '\d+')
        echo "示例token: ${FIRST_TOKEN:0:12}..."
        echo "  缓存中的seq: ${CACHE_SEQ}"

        # 查找对应的子进程日志
        LOG_FILE="/home/trader/polymarket_api/POLYMARKET_MAKER_copytrade_v2/POLYMARKET_MAKER_AUTO/logs/autorun_${FIRST_TOKEN}.log"
        if [ -f "$LOG_FILE" ]; then
            LAST_SEQ=$(grep -oP 'seq=\K\d+' "$LOG_FILE" | tail -1)
            echo "  子进程中的seq: ${LAST_SEQ}"

            if [ "$CACHE_SEQ" != "$LAST_SEQ" ]; then
                echo "  ✗ 不匹配！子进程未读取到最新seq"
            else
                echo "  ✓ 匹配"
            fi
        fi
    fi
fi
echo ""

echo "=========================================="
echo "诊断建议"
echo "=========================================="
echo ""
echo "如果看到以下问题："
echo "  1. ws_cache.json 超过2分钟未更新 → 检查聚合器"
echo "  2. 未发现DEBUG日志 → 子进程使用旧代码，需要重启"
echo "  3. seq不匹配 → 子进程读取有问题"
echo ""
echo "建议操作："
echo "  1. pkill -f Volatility_arbitrage_run.py  # 停止所有子进程"
echo "  2. 等待聚合器重新启动子进程"
echo "  3. 再次运行此脚本检查"
echo ""
