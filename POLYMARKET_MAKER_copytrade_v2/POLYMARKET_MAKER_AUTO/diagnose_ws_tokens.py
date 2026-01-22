#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
诊断脚本：检查WebSocket聚合器缓存中的token_id
用于验证缓存中的token是否匹配订阅列表
"""

import json
import sys
from pathlib import Path

# 设置路径
SCRIPT_DIR = Path(__file__).parent
DATA_DIR = SCRIPT_DIR / "data"
WS_CACHE_FILE = DATA_DIR / "ws_cache.json"

def diagnose():
    print("=" * 80)
    print("WebSocket 缓存诊断工具")
    print("=" * 80)

    if not WS_CACHE_FILE.exists():
        print(f"❌ 缓存文件不存在: {WS_CACHE_FILE}")
        return

    try:
        with open(WS_CACHE_FILE, "r", encoding="utf-8") as f:
            cache = json.load(f)
    except Exception as e:
        print(f"❌ 无法读取缓存文件: {e}")
        return

    print(f"✅ 缓存文件: {WS_CACHE_FILE}")
    print(f"✅ 缓存更新时间: {cache.get('updated_at', 'N/A')}")
    print()

    tokens = cache.get("tokens", {})
    if not tokens:
        print("⚠️  缓存中没有token数据")
        return

    print(f"📊 缓存中的token数量: {len(tokens)}")
    print()

    # 显示所有token的详细信息
    for idx, (token_id, data) in enumerate(tokens.items(), 1):
        print(f"[Token {idx}] ID: {token_id}")
        print(f"  - seq: {data.get('seq', 'N/A')}")
        print(f"  - price: {data.get('price', 'N/A')}")
        print(f"  - best_bid: {data.get('best_bid', 'N/A')}")
        print(f"  - best_ask: {data.get('best_ask', 'N/A')}")
        print(f"  - updated_at: {data.get('updated_at', 'N/A')}")
        print(f"  - event_type: {data.get('event_type', 'N/A')}")
        print()

    # 检查是否有相同价格的token（可能是YES/NO配对）
    prices = {}
    for token_id, data in tokens.items():
        price_key = (data.get('best_bid'), data.get('best_ask'))
        if price_key not in prices:
            prices[price_key] = []
        prices[price_key].append(token_id)

    print("🔍 价格分组分析:")
    for price_key, token_list in prices.items():
        bid, ask = price_key
        if len(token_list) > 1:
            print(f"⚠️  发现 {len(token_list)} 个token有相同价格 (bid={bid}, ask={ask}):")
            for tid in token_list:
                print(f"    - {tid}")
        else:
            print(f"✅ bid={bid}, ask={ask} → {len(token_list)} 个token")
    print()

    # 从copytrade配置中读取期望的token
    copytrade_file = SCRIPT_DIR.parent / "copytrade" / "tokens_from_copytrade.json"
    if copytrade_file.exists():
        try:
            with open(copytrade_file, "r", encoding="utf-8") as f:
                copytrade_data = json.load(f)

            expected_tokens = set()
            for entry in copytrade_data:
                if isinstance(entry, dict):
                    token_id = (
                        entry.get("token_id")
                        or entry.get("tokenId")
                        or entry.get("asset_id")
                        or entry.get("topic_id")
                    )
                    if token_id:
                        expected_tokens.add(str(token_id))

            print(f"📝 期望订阅的token (从copytrade配置): {len(expected_tokens)} 个")
            cached_tokens = set(tokens.keys())

            # 检查匹配情况
            matched = expected_tokens & cached_tokens
            missing = expected_tokens - cached_tokens
            extra = cached_tokens - expected_tokens

            print(f"  ✅ 匹配: {len(matched)} 个")
            print(f"  ❌ 缺失: {len(missing)} 个")
            print(f"  ⚠️  额外: {len(extra)} 个 (可能是配对token)")

            if missing:
                print("\n❌ 缺失的token (订阅了但缓存中没有):")
                for tid in missing:
                    print(f"    - {tid}")

            if extra:
                print("\n⚠️  额外的token (缓存中有但未订阅):")
                for tid in extra:
                    print(f"    - {tid}")
                    # 检查是否可能是YES/NO配对
                    data = tokens[tid]
                    print(f"       seq={data.get('seq')}, bid={data.get('best_bid')}, ask={data.get('best_ask')}")
        except Exception as e:
            print(f"⚠️  无法读取copytrade配置: {e}")

    print()
    print("=" * 80)
    print("诊断完成")
    print("=" * 80)

if __name__ == "__main__":
    diagnose()
