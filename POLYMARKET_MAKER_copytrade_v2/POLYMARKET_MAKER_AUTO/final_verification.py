#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
最终验证脚本 - 检查所有关键修复点
"""

import sys
import json
import tempfile
import os
from pathlib import Path

sys.path.insert(0, 'POLYMARKET_MAKER')

from market_state_checker import (
    MarketStateChecker,
    MarketClosedCleaner,
    MarketStatus,
    MarketState,
)

print("=" * 60)
print("最终验证 - 检查所有修复点")
print("=" * 60)

errors = []

# 1. 检查 _remove_from_copytrade_tokens 支持 {"tokens": []} 格式
print("\n[1] 检查 copytrade_tokens.json 格式支持...")
try:
    cleaner = MarketClosedCleaner()
    
    # 创建临时文件模拟 copytrade_tokens.json
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        test_data = {
            "tokens": [
                {"token_id": "token1", "name": "Test 1"},
                {"token_id": "token2", "name": "Test 2"},
            ],
            "updated_at": "2024-01-01T00:00:00Z"
        }
        json.dump(test_data, f)
        temp_path = f.name
    
    # 测试清理
    result = {"cleaned_files": [], "errors": []}
    cleaner._remove_from_copytrade_tokens(temp_path, "token1", result)
    
    # 验证结果
    with open(temp_path, 'r') as f:
        result_data = json.load(f)
    
    assert len(result_data["tokens"]) == 1, "应该只剩1个token"
    assert result_data["tokens"][0]["token_id"] == "token2", "剩下的应该是token2"
    assert "updated_at" in result_data, "应该保留updated_at"
    
    print("  [OK] {\"tokens\": [...]} 格式支持正常")
    
    os.unlink(temp_path)
    
except Exception as e:
    print(f"  [FAIL] {e}")
    errors.append(f"copytrade_tokens format: {e}")

# 2. 检查 _remove_from_copytrade_state 支持 {"targets": {}} 格式
print("\n[2] 检查 copytrade_state.json 格式支持...")
try:
    cleaner = MarketClosedCleaner()
    
    # 创建临时文件模拟 copytrade_state.json
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        test_data = {
            "targets": {
                "token1": {"status": "active"},
                "token2": {"status": "active"},
            }
        }
        json.dump(test_data, f)
        temp_path = f.name
    
    # 测试清理
    result = {"cleaned_files": [], "errors": []}
    cleaner._remove_from_copytrade_state(temp_path, "token1", result)
    
    # 验证结果
    with open(temp_path, 'r') as f:
        result_data = json.load(f)
    
    assert "token1" not in result_data["targets"], "token1 应该被删除"
    assert "token2" in result_data["targets"], "token2 应该保留"
    
    print("  [OK] {\"targets\": {...}} 格式支持正常")
    
    os.unlink(temp_path)
    
except Exception as e:
    print(f"  [FAIL] {e}")
    errors.append(f"copytrade_state format: {e}")

# 3. 检查 MarketState 序列化
print("\n[3] 检查 MarketState 序列化...")
try:
    state = MarketState(
        status=MarketStatus.CLOSED,
        condition_id="cond123",
        token_id="token456",
        data={"bids_count": 5, "asks_count": 3},
        checked_at=1234567890.0,
        is_tradeable=True,
        refillable=False,
        http_status=200,
    )
    
    # 测试 to_dict
    d = state.to_dict()
    assert d["status"] == "closed"
    assert d["condition_id"] == "cond123"
    assert d["data"]["bids_count"] == 5
    
    # 测试 from_dict
    restored = MarketState.from_dict(d)
    assert restored.status == MarketStatus.CLOSED
    assert restored.is_permanently_closed == True
    
    print("  [OK] MarketState 序列化/反序列化正常")
    
except Exception as e:
    print(f"  [FAIL] {e}")
    errors.append(f"MarketState serialization: {e}")

# 4. 检查永久关闭状态识别
print("\n[4] 检查永久关闭状态识别...")
try:
    permanent_statuses = [
        MarketStatus.NOT_FOUND,
        MarketStatus.RESOLVED,
        MarketStatus.ARCHIVED,
    ]
    
    for status in permanent_statuses:
        state = MarketState(
            status=status,
            condition_id="test",
            token_id="test",
            data={},
            checked_at=0,
        )
        assert state.is_permanently_closed, f"{status.value} 应该被识别为永久关闭"
        assert not state.refillable, f"{status.value} 应该不可回填"
    
    # 检查 ACTIVE 和 LOW_LIQUIDITY
    for status in [MarketStatus.ACTIVE, MarketStatus.LOW_LIQUIDITY]:
        state = MarketState(
            status=status,
            condition_id="test",
            token_id="test",
            data={},
            checked_at=0,
            is_tradeable=True,
            refillable=True,
        )
        assert not state.is_permanently_closed, f"{status.value} 不应该被识别为永久关闭"
        assert state.refillable, f"{status.value} 应该可回填"
    
    print("  [OK] 永久关闭状态识别正常")
    
except Exception as e:
    print(f"  [FAIL] {e}")
    errors.append(f"Permanent closed check: {e}")

# 5. 检查 API 端点配置
print("\n[5] 检查 API 端点配置...")
try:
    from market_state_checker import Config
    
    assert Config.GAMMA_API_BASE == "https://gamma-api.polymarket.com"
    assert Config.CLOB_API_BASE == "https://clob.polymarket.com"
    assert Config.MARKET_STATE_CACHE_TTL == 300
    assert Config.GAMMA_API_TIMEOUT == 10
    assert Config.CLOB_BOOK_TIMEOUT == 5
    
    print("  [OK] API 端点配置正确")
    
except Exception as e:
    print(f"  [FAIL] {e}")
    errors.append(f"API config: {e}")

# 6. 检查线程安全（锁共享）
print("\n[6] 检查线程安全（锁共享）...")
try:
    import threading
    
    test_lock = threading.RLock()
    
    # 设置锁
    Config.set_file_lock(test_lock)
    
    # 创建检测器和清理器
    checker = MarketStateChecker(file_lock=test_lock)
    cleaner = MarketClosedCleaner(file_lock=test_lock)
    
    # 验证锁是同一个对象
    assert Config.get_file_lock() is test_lock
    
    print("  [OK] 锁共享机制正常")
    
except Exception as e:
    print(f"  [FAIL] {e}")
    errors.append(f"Lock sharing: {e}")

print("\n" + "=" * 60)
if errors:
    print(f"[FAIL] 发现 {len(errors)} 个错误:")
    for err in errors:
        print(f"  - {err}")
    sys.exit(1)
else:
    print("[PASS] 所有验证通过！")
    print("=" * 60)
    sys.exit(0)
