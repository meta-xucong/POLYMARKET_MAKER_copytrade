#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
启动测试 - 验证程序能正常初始化
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
print("\n[1/8] 测试 market_state_checker 模块导入...")
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

# 2. 测试 poly_maker_autorun 关键导入
print("\n[2/8] 测试 poly_maker_autorun 关键导入...")
try:
    # 模拟 poly_maker_autorun 的导入逻辑
    sys.path.insert(0, str(PROJECT_ROOT))
    
    # 测试基础依赖
    import json
    import threading
    import time
    from typing import Dict, Any, Optional, List
    from dataclasses import dataclass, field
    
    print("  [OK] 基础依赖导入成功")
    
    # 测试 market_state_checker 导入（在 sys.path 修改后）
    from market_state_checker import (
        MarketStateChecker,
        MarketClosedCleaner,
        MarketStatus,
        MarketState,
        check_market_state,
        clean_closed_market,
        should_refill_token,
        init_market_state_checker,
        init_cleaner,
    )
    MARKET_STATE_CHECKER_AVAILABLE = True
    print("  [OK] market_state_checker 在 sys.path 修改后导入成功")
    
except Exception as e:
    print(f"  [FAIL] {e}")
    errors.append(f"poly_maker_autorun imports: {e}")
    MARKET_STATE_CHECKER_AVAILABLE = False

# 3. 测试 Config 参数
print("\n[3/8] 测试 Config 参数...")
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

# 4. 测试 MarketStateChecker 初始化
print("\n[4/8] 测试 MarketStateChecker 初始化...")
try:
    from market_state_checker import MarketStateChecker
    
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

# 5. 测试 MarketClosedCleaner 初始化
print("\n[5/8] 测试 MarketClosedCleaner 初始化...")
try:
    from market_state_checker import MarketClosedCleaner
    
    cleaner = MarketClosedCleaner(file_lock=file_lock)
    
    print("  [OK] MarketClosedCleaner 初始化成功")
    
except Exception as e:
    print(f"  [FAIL] {e}")
    errors.append(f"MarketClosedCleaner init: {e}")

# 6. 测试文件格式支持
print("\n[6/8] 测试文件格式支持...")
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

# 7. 测试 AutoRunManager 关键方法签名
print("\n[7/8] 测试 AutoRunManager 关键方法签名...")
try:
    # 无法真正实例化 AutoRunManager（需要太多依赖），但我们可以检查语法
    # 通过尝试编译来验证
    import py_compile
    py_compile.compile(str(PROJECT_ROOT / 'poly_maker_autorun.py'), doraise=True)
    print("  [OK] poly_maker_autorun.py 语法正确")
    
    # 检查关键方法存在
    import poly_maker_autorun
    assert hasattr(poly_maker_autorun, 'AutoRunManager')
    assert hasattr(poly_maker_autorun.AutoRunManager, '_get_condition_id_for_token')
    assert hasattr(poly_maker_autorun.AutoRunManager, '_filter_refillable_tokens')
    assert hasattr(poly_maker_autorun.AutoRunManager, '_evict_stale_pending_topics')
    print("  [OK] AutoRunManager 关键方法存在")
    
except Exception as e:
    print(f"  [FAIL] {e}")
    errors.append(f"AutoRunManager methods: {e}")

# 8. 测试向后兼容
print("\n[8/8] 测试向后兼容...")
try:
    # 模拟模块导入失败的情况
    from poly_maker_autorun import MARKET_STATE_CHECKER_AVAILABLE
    
    if MARKET_STATE_CHECKER_AVAILABLE:
        print("  [OK] MARKET_STATE_CHECKER_AVAILABLE = True")
    else:
        print("  [WARN] MARKET_STATE_CHECKER_AVAILABLE = False")
        warnings.append("MARKET_STATE_CHECKER_AVAILABLE is False")
    
    # 验证常量存在
    from poly_maker_autorun import DEFAULT_GLOBAL_CONFIG
    assert "enable_slot_refill" in DEFAULT_GLOBAL_CONFIG
    assert "refill_cooldown_minutes" in DEFAULT_GLOBAL_CONFIG
    print("  [OK] 原有配置常量存在")
    
except Exception as e:
    print(f"  [FAIL] {e}")
    errors.append(f"Backward compatibility: {e}")

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
    sys.exit(0)
