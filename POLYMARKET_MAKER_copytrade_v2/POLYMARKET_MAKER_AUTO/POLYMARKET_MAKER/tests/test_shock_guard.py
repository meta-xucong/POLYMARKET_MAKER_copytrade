from shock_guard import (
    GateDecision,
    RecoveryConfig,
    ShockGuard,
    ShockGuardConfig,
)


def _build_guard(**kwargs):
    cfg = ShockGuardConfig(
        enabled=True,
        shock_window_sec=20.0,
        shock_drop_pct=0.20,
        observation_hold_sec=30.0,
        recovery=RecoveryConfig(
            rebound_pct_min=0.05,
            reconfirm_sec=10.0,
            spread_cap=0.03,
            require_conditions=2,
        ),
        blocked_cooldown_sec=40.0,
    )
    for k, v in kwargs.items():
        setattr(cfg, k, v)
    return ShockGuard("tok", cfg)


def test_disabled_guard_always_allows():
    guard = ShockGuard("tok", ShockGuardConfig(enabled=False))
    guard.on_market_snapshot(bid=0.5, ask=0.52, ts=1.0)
    result = guard.gate_buy(ts=1.0)
    assert result.decision == GateDecision.ALLOW


def test_pre_buy_observation_always_defers_first_buy():
    guard = _build_guard()
    guard.on_market_snapshot(bid=0.60, ask=0.62, ts=1.0)
    result = guard.gate_buy(ts=2.0)
    assert result.decision == GateDecision.DEFER
    assert "pre-buy" in result.reason


def test_recovery_pass_allows_after_hold():
    guard = _build_guard()
    guard.on_market_snapshot(bid=0.60, ask=0.62, ts=1.0)
    assert guard.gate_buy(ts=2.0).decision == GateDecision.DEFER

    # hold结束后先出现低点，再等待确认窗口并反弹，满足恢复条件
    guard.on_market_snapshot(bid=0.50, ask=0.52, ts=36.0)
    guard.on_market_snapshot(bid=0.58, ask=0.60, ts=48.0)
    result = guard.gate_buy(ts=48.0)
    assert result.decision == GateDecision.ALLOW


def test_recovery_fail_enters_blocked_and_then_restarts_observation():
    guard = _build_guard()
    guard.cfg.recovery.require_conditions = 3
    guard.on_market_snapshot(bid=0.60, ask=0.62, ts=1.0)
    assert guard.gate_buy(ts=2.0).decision == GateDecision.DEFER

    # hold结束但没有反弹+点差过大 -> fail
    guard.on_market_snapshot(bid=0.44, ask=0.54, ts=36.0)
    result_fail = guard.gate_buy(ts=36.0)
    assert result_fail.decision == GateDecision.REJECT

    # blocked期间持续拒绝
    blocked_result = guard.gate_buy(ts=50.0)
    assert blocked_result.decision == GateDecision.REJECT

    # cooldown结束回到normal，但由于每次买前强制观察，应再次 DEFER
    guard.on_market_snapshot(bid=0.48, ask=0.50, ts=80.0)
    final_result = guard.gate_buy(ts=80.0)
    assert final_result.decision == GateDecision.DEFER
