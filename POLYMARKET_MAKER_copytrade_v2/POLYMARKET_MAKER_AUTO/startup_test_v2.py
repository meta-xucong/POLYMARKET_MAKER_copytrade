#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
启动测试 - 验证程序能正常初始化（Windows 兼容版）
"""

import sys
import os
from pathlib import Path

# 设置路径
PROJECT_ROOT = Path(__file__).resolve().parent
MAKER_ROOT = PROJECT_ROOT / "POLYMARKET_MAKER"
if str(MAKER_ROOT) not in sys.path:
    sys.path.insert(0, str(MAKER_ROOT))

print("=" * 70)
print("启动测试 - 验证程序能正常初始化")
print("=" * 70)

errors = []
warnings = []

# 1. 测试 market_state_checker 模块导入
print("\n[1/9] 测试 market_state_checker 模块导入...")
try:
    from market_state_checker import (
        MarketStateChecker,
        MarketClosedCleaner,
        MarketStatus,
        MarketState,
        Config,
    )
    print("  [OK] market_state_checker 模块导入成功")
except Exception as e:
    print(f"  [FAIL] {e}")
    errors.append(f"market_state_checker import: {e}")

# 2. 测试 Config 参数
print("\n[2/9] 测试 Config 参数...")
try:
    from market_state_checker import Config
    
    # 验证关键参数
    assert Config.GAMMA_API_BASE == "https://gamma-api.polymarket.com"
    assert Config.CLOB_API_BASE == "https://clob.polymarket.com"
    assert Config.MARKET_STATE_CACHE_TTL == 300
    assert Config.GAMMA_API_TIMEOUT == 10
    assert Config.CLOB_BOOK_TIMEOUT == 5
    assert Config.MAX_RETRY_ATTEMPTS == 3
    
    print(f"  [OK] Gamma API: {Config.GAMMA_API_BASE}")
    print(f"  [OK] CLOB API: {Config.CLOB_API_BASE}")
    print(f"  [OK] Cache TTL: {Config.MARKET_STATE_CACHE_TTL}s")
    
except Exception as e:
    print(f"  [FAIL] {e}")
    errors.append(f"Config params: {e}")

# 3. 测试 MarketStateChecker 初始化
print("\n[3/9] 测试 MarketStateChecker 初始化...")
try:
    import threading
    from market_state_checker import MarketStateChecker, Config
    
    # 创建文件锁
    file_lock = threading.RLock()
    Config.set_file_lock(file_lock)
    
    # 初始化检测器
    checker = MarketStateChecker(file_lock=file_lock)
    
    print("  [OK] MarketStateChecker 初始化成功")
    
    # 测试统计方法
    stats = checker.get_stats()
    print(f"  [OK] Stats: {stats}")
    
except Exception as e:
    print(f"  [FAIL] {e}")
    errors.append(f"MarketStateChecker init: {e}")

# 4. 测试 MarketClosedCleaner 初始化
print("\n[4/9] 测试 MarketClosedCleaner 初始化...")
try:
    from market_state_checker import MarketClosedCleaner
    
    cleaner = MarketClosedCleaner(file_lock=file_lock)
    
    print("  [OK] MarketClosedCleaner 初始化成功")
    
except Exception as e:
    print(f"  [FAIL] {e}")
    errors.append(f"MarketClosedCleaner init: {e}")

# 5. 测试文件格式支持
print("\n[5/9] 测试文件格式支持...")
try:
    import tempfile
    import json
    
    from market_state_checker import MarketClosedCleaner
    
    cleaner = MarketClosedCleaner()
    
    # 测试 {"tokens": [...]} 格式
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        json.dump({"tokens": [{"token_id": "t1"}, {"token_id": "t2"}]}, f)
        tokens_path = f.name
    
    result = {"cleaned_files": [], "errors": []}
    cleaner._remove_from_copytrade_tokens(tokens_path, "t1", result)
    
    with open(tokens_path, 'r') as f:
        data = json.load(f)
    assert len(data["tokens"]) == 1
    os.unlink(tokens_path)
    print("  [OK] {\"tokens\": [...]} 格式支持")
    
    # 测试 {"targets": {...}} 格式
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        json.dump({"targets": {"t1": {}, "t2": {}}}, f)
        state_path = f.name
    
    result = {"cleaned_files": [], "errors": []}
    cleaner._remove_from_copytrade_state(state_path, "t1", result)
    
    with open(state_path, 'r') as f:
        data = json.load(f)
    assert "t1" not in data["targets"]
    os.unlink(state_path)
    print("  [OK] {\"targets\": {...}} 格式支持")
    
except Exception as e:
    print(f"  [FAIL] {e}")
    errors.append(f"File format support: {e}")

# 6. 测试 MarketState 序列化和状态识别
print("\n[6/9] 测试 MarketState 序列化和状态识别...")
try:
    from market_state_checker import MarketState, MarketStatus
    
    # 测试序列化
    state = MarketState(
        status=MarketStatus.CLOSED,
        condition_id="cond123",
        token_id="token456",
        data={"bids_count": 5},
        checked_at=1234567890.0,
        is_tradeable=False,
        refillable=False,
    )
    
    d = state.to_dict()
    restored = MarketState.from_dict(d)
    assert restored.status == MarketStatus.CLOSED
    
    # 测试永久关闭识别
    assert restored.is_permanently_closed == True
    
    active_state = MarketState(
        status=MarketStatus.ACTIVE,
        condition_id="test",
        token_id="test",
        data={},
        checked_at=0,
        is_tradeable=True,
        refillable=True,
    )
    assert active_state.is_permanently_closed == False
    
    print("  [OK] MarketState 序列化和状态识别正常")
    
except Exception as e:
    print(f"  [FAIL] {e}")
    errors.append(f"MarketState: {e}")

# 7. 测试语法检查
print("\n[7/9] 测试语法检查...")
try:
    import py_compile
    py_compile.compile(str(PROJECT_ROOT / 'poly_maker_autorun.py'), doraise=True)
    print("  [OK] poly_maker_autorun.py 语法正确")
    
    py_compile.compile(str(MAKER_ROOT / 'market_state_checker.py'), doraise=True)
    print("  [OK] market_state_checker.py 语法正确")
    
except Exception as e:
    print(f"  [FAIL] {e}")
    errors.append(f"Syntax check: {e}")

# 8. 测试关键函数签名（通过 inspect）
print("\n[8/9] 测试关键函数签名...")
try:
    import inspect
    from market_state_checker import MarketClosedCleaner
    
    # 检查 clean_closed_market 参数
    sig = inspect.signature(MarketClosedCleaner.clean_closed_market)
    params = list(sig.parameters.keys())
    
    assert 'token_id' in params
    assert 'condition_id' in params
    assert 'exit_reason' in params
    assert 'copytrade_file' in params
    assert 'copytrade_state_file' in params
    assert 'exit_tokens_file' in params
    
    print(f"  [OK] clean_closed_market 参数完整: {params}")
    
except Exception as e:
    print(f"  [FAIL] {e}")
    errors.append(f"Function signature: {e}")

# 9. 测试线程安全
print("\n[9/9] 测试线程安全...")
try:
    import threading
    import time
    from market_state_checker import MarketStateChecker, Config
    
    lock = threading.RLock()
    Config.set_file_lock(lock)
    
    checker = MarketStateChecker()
    
    # 多线程访问测试
    def worker():
        for _ in range(10):
            checker.get_stats()
            time.sleep(0.001)
    
    threads = [threading.Thread(target=worker) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    
    print("  [OK] 3线程并发访问无错误")
    
except Exception as e:
    print(f"  [FAIL] {e}")
    errors.append(f"Thread safety: {e}")

# 总结
print("\n" + "=" * 70)
if errors:
    print(f"[FAIL] 发现 {len(errors)} 个错误:")
    for err in errors:
        print(f"  - {err}")
    print("=" * 70)
    sys.exit(1)
else:
    print("[PASS] 所有测试通过！程序可以正常启动。")
    if warnings:
        print(f"\n[WARN] 有 {len(warnings)} 个警告:")
        for warn in warnings:
            print(f"  - {warn}")
    print("=" * 70)
    print("\n注意: fcntl 模块在 Windows 上不可用，这是正常的。")
    print("      在 Linux 上运行时将正常导入。")
    sys.exit(0)
