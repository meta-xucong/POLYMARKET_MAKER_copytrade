#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
市场状态检测模块集成测试
验证与 poly_maker_autorun 的兼容性
"""

import sys
import time
import threading
from pathlib import Path

# 添加路径
PROJECT_ROOT = Path(__file__).resolve().parent
MAKER_ROOT = PROJECT_ROOT / "POLYMARKET_MAKER"
if str(MAKER_ROOT) not in sys.path:
    sys.path.insert(0, str(MAKER_ROOT))

# 测试导入
try:
    from market_state_checker import (
        MarketStateChecker,
        MarketClosedCleaner,
        MarketStatus,
        MarketState,
        init_market_state_checker,
        init_cleaner,
    )
    print("[OK] 市场状态检测模块导入成功")
except ImportError as e:
    print(f"[FAIL] 导入失败: {e}")
    sys.exit(1)


def test_market_state_enum():
    """测试市场状态枚举"""
    print("\n--- 测试 MarketStatus 枚举 ---")
    
    # 测试永久关闭状态识别
    permanent_statuses = [
        MarketStatus.NOT_FOUND,
        MarketStatus.RESOLVED,
        MarketStatus.ARCHIVED,
    ]
    
    for status in permanent_statuses:
        state = MarketState(
            status=status,
            condition_id="test_cond",
            token_id="test_token",
            data={},
            checked_at=time.time(),
        )
        assert state.is_permanently_closed, f"{status.value} 应该被识别为永久关闭"
        assert not state.refillable, f"{status.value} 应该不可回填"
        print(f"  [OK] {status.value}: 正确识别为永久关闭")
    
    # 测试可交易状态
    tradeable_state = MarketState(
        status=MarketStatus.ACTIVE,
        condition_id="test_cond",
        token_id="test_token",
        data={"bids_count": 5, "asks_count": 5},
        checked_at=time.time(),
        is_tradeable=True,
        refillable=True,
    )
    assert not tradeable_state.is_permanently_closed
    assert tradeable_state.refillable
    print("  [OK] ACTIVE: 正确识别为可交易")
    
    # 测试低流动性状态
    low_liq_state = MarketState(
        status=MarketStatus.LOW_LIQUIDITY,
        condition_id="test_cond",
        token_id="test_token",
        data={"bids_count": 0, "asks_count": 0},
        checked_at=time.time(),
        is_tradeable=True,
        refillable=True,
    )
    assert not low_liq_state.is_permanently_closed
    assert low_liq_state.refillable
    print("  [OK] LOW_LIQUIDITY: 正确识别为可回填")


def test_market_state_checker_init():
    """测试检测器初始化"""
    print("\n--- 测试 MarketStateChecker 初始化 ---")
    
    # 测试无锁初始化
    checker = MarketStateChecker()
    assert checker is not None
    print("  [OK] 无锁初始化成功")
    
    # 测试带锁初始化
    test_lock = threading.RLock()
    checker_with_lock = MarketStateChecker(file_lock=test_lock)
    assert checker_with_lock is not None
    print("  [OK] 带锁初始化成功")
    
    # 测试单例初始化
    init_checker = init_market_state_checker(file_lock=test_lock)
    assert init_checker is not None
    print("  [OK] 单例初始化成功")


def test_market_closed_cleaner():
    """测试清理器初始化"""
    print("\n--- 测试 MarketClosedCleaner 初始化 ---")
    
    test_lock = threading.RLock()
    cleaner = MarketClosedCleaner(file_lock=test_lock)
    assert cleaner is not None
    print("  [OK] 清理器初始化成功")
    
    init_cleaner_instance = init_cleaner(file_lock=test_lock)
    assert init_cleaner_instance is not None
    print("  [OK] 清理器单例初始化成功")


def test_config_values():
    """测试配置参数"""
    print("\n--- 测试配置参数 ---")
    from market_state_checker import Config
    
    # 验证关键配置存在
    assert hasattr(Config, 'GAMMA_API_BASE')
    assert hasattr(Config, 'CLOB_API_BASE')
    assert hasattr(Config, 'MARKET_STATE_CACHE_TTL')
    assert hasattr(Config, 'GAMMA_API_TIMEOUT')
    assert hasattr(Config, 'CLOB_BOOK_TIMEOUT')
    
    print(f"  [OK] Gamma API: {Config.GAMMA_API_BASE}")
    print(f"  [OK] CLOB API: {Config.CLOB_API_BASE}")
    print(f"  [OK] Cache TTL: {Config.MARKET_STATE_CACHE_TTL}s")
    
    # 验证文件锁
    lock = Config.get_file_lock()
    assert isinstance(lock, type(threading.RLock()))
    print("  [OK] 文件锁获取成功")


def test_market_state_serialization():
    """测试 MarketState 序列化"""
    print("\n--- 测试 MarketState 序列化 ---")
    
    original = MarketState(
        status=MarketStatus.CLOSED,
        condition_id="cond123",
        token_id="token456",
        data={"test": "data", "nested": {"key": "value"}},
        checked_at=time.time(),
        is_tradeable=False,
        refillable=False,
        http_status=200,
    )
    
    # 测试 to_dict
    d = original.to_dict()
    assert d["status"] == "closed"
    assert d["condition_id"] == "cond123"
    assert d["token_id"] == "token456"
    print("  [OK] to_dict 成功")
    
    # 测试 from_dict
    restored = MarketState.from_dict(d)
    assert restored.status == MarketStatus.CLOSED
    assert restored.condition_id == "cond123"
    assert restored.token_id == "token456"
    assert not restored.refillable
    print("  [OK] from_dict 成功")


def test_thread_safety():
    """测试线程安全"""
    print("\n--- 测试线程安全 ---")
    
    checker = MarketStateChecker()
    results = []
    errors = []
    
    def worker(worker_id):
        try:
            # 模拟并发访问缓存
            for i in range(10):
                checker.get_stats()
                time.sleep(0.01)
            results.append(worker_id)
        except Exception as e:
            errors.append((worker_id, str(e)))
    
    threads = [threading.Thread(target=worker, args=(i,)) for i in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    
    assert len(errors) == 0, f"线程错误: {errors}"
    assert len(results) == 5
    print(f"  [OK] 5线程并发访问无错误")


def test_integration_with_autorun():
    """测试与 autorun 的集成"""
    print("\n--- 测试与 autorun 集成 ---")
    
    try:
        # 尝试导入 autorun 中的关键部分
        sys.path.insert(0, str(PROJECT_ROOT))
        from poly_maker_autorun import MARKET_STATE_CHECKER_AVAILABLE
        
        if MARKET_STATE_CHECKER_AVAILABLE:
            print("  [OK] autorun 中 MARKET_STATE_CHECKER_AVAILABLE = True")
        else:
            print("  [WARN]  autorun 中 MARKET_STATE_CHECKER_AVAILABLE = False (模块可能未安装)")
        
        # 验证类引用存在
        from poly_maker_autorun import AutoRunManager
        
        # 检查 AutoRunManager 是否有新方法
        assert hasattr(AutoRunManager, '_get_condition_id_for_token')
        print("  [OK] AutoRunManager._get_condition_id_for_token 存在")
        
    except ImportError as e:
        print(f"  [WARN]  autorun 导入跳过: {e}")


def main():
    """主测试函数"""
    print("=" * 60)
    print("市场状态检测模块集成测试")
    print("=" * 60)
    
    try:
        test_market_state_enum()
        test_market_state_checker_init()
        test_market_closed_cleaner()
        test_config_values()
        test_market_state_serialization()
        test_thread_safety()
        test_integration_with_autorun()
        
        print("\n" + "=" * 60)
        print("[OK] 所有测试通过")
        print("=" * 60)
        return 0
        
    except AssertionError as e:
        print(f"\n[FAIL] 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return 1
    except Exception as e:
        print(f"\n[FAIL] 意外错误: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
