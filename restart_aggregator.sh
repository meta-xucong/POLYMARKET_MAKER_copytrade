#!/bin/bash

echo "======================================================"
echo "聚合器重启脚本"
echo "======================================================"
echo ""

cd /home/user/POLYMARKET_MAKER_copytrade/POLYMARKET_MAKER_copytrade_v2/POLYMARKET_MAKER_AUTO

# 1. 查找并停止旧进程
echo "【1】查找运行中的聚合器进程..."
AGGREGATOR_PID=$(ps aux | grep -E 'poly_maker_autorun\.py|python.*autorun' | grep -v grep | head -1 | awk '{print $2}')

if [ -z "$AGGREGATOR_PID" ]; then
    echo "ℹ️  未找到运行中的聚合器进程"
else
    echo "找到进程 PID: $AGGREGATOR_PID"
    echo "停止进程..."
    kill $AGGREGATOR_PID

    # 等待进程退出
    for i in {1..10}; do
        if ! ps -p $AGGREGATOR_PID > /dev/null 2>&1; then
            echo "✅ 进程已停止"
            break
        fi
        echo "等待进程退出... ($i/10)"
        sleep 1
    done

    # 如果还没退出，强制杀死
    if ps -p $AGGREGATOR_PID > /dev/null 2>&1; then
        echo "⚠️  进程未正常退出，使用 SIGKILL..."
        kill -9 $AGGREGATOR_PID
        sleep 1
    fi
fi

echo ""

# 2. 验证代码版本
echo "【2】验证代码版本..."
if grep -q 'VERSION.*支持book/tick事件处理' poly_maker_autorun.py; then
    echo "✅ 代码包含最新版本标识"
else
    echo "❌ 代码不包含版本标识，请确认代码已更新"
    exit 1
fi

echo ""

# 3. 提示启动命令
echo "【3】准备启动新版本..."
echo ""
echo "请使用以下命令启动聚合器:"
echo ""
echo "  cd /home/user/POLYMARKET_MAKER_copytrade/POLYMARKET_MAKER_copytrade_v2/POLYMARKET_MAKER_AUTO"
echo "  python poly_maker_autorun.py [your-args] > aggregator.log 2>&1 &"
echo ""
echo "或者，如果你有特定的启动脚本，请使用你的启动脚本"
echo ""
echo "启动后，使用以下命令验证版本:"
echo "  tail -50 aggregator.log | grep VERSION"
echo ""
echo "应该看到: [VERSION] 支持book/tick事件处理 (2026-01-21)"
echo ""
echo "======================================================"
