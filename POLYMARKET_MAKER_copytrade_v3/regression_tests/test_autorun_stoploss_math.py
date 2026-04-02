def test_default_stoploss_reentry_state_applies_min_threshold(manager_factory):
    manager = manager_factory()
    manager.config.stoploss_base_drawdown_pct = 0.0

    state = manager._default_stoploss_reentry_state("tok")

    assert state["token_id"] == "tok"
    assert state["version"] == 4
    assert state["state"] == "NORMAL_MAKER"
    assert state["next_stoploss_threshold_pct"] == 0.001
    assert state["wide_spread_stoploss_confirm_hits"] == 0
    assert state["wide_spread_stoploss_first_ts"] == 0.0


def test_normalize_stoploss_state_clamps_values_and_cleans_release_fields(
    manager_factory,
):
    manager = manager_factory()
    raw = {
        "state": "NORMAL_MAKER",
        "stoploss_cycle_count": -5,
        "stoploss_confirm_hits": -3,
        "daily_stoploss_full_clear_count": -2,
        "next_stoploss_threshold_pct": -0.2,
        "reentry_quote_missing_hits": -1,
        "market_status_last": "source_detached_guard_hold",
        "last_error": "source_detached guard hold (within grace)",
        "pending_stoploss_before_size": 3.0,
        "pending_stoploss_after_size": 1.0,
    }

    state = manager._normalize_stoploss_reentry_state_record("tok", raw)

    assert state["stoploss_cycle_count"] == 0
    assert state["stoploss_confirm_hits"] == 0
    assert state["daily_stoploss_full_clear_count"] == 0
    assert state["next_stoploss_threshold_pct"] >= 0.001
    assert state["reentry_quote_missing_hits"] == 0
    assert state["market_status_last"] == "source_detached"
    assert state["last_error"] == ""
    assert "pending_stoploss_before_size" not in state
    assert "pending_stoploss_after_size" not in state
    assert state["reentry_target_size"] == 0.0
    assert state["reentry_maker_order_id"] == ""


def test_rollover_stoploss_daily_fields(autorun_mod):
    state = {
        "loss_date_utc": "2026-03-27",
        "daily_stoploss_full_clear_count": 3,
        "today_realized_loss_pct": 0.12,
        "reentry_paused_for_day": True,
    }

    assert autorun_mod.AutoRunManager._rollover_stoploss_daily_fields(state, "2026-03-28") is True
    assert state["loss_date_utc"] == "2026-03-28"
    assert state["daily_stoploss_full_clear_count"] == 0
    assert state["today_realized_loss_pct"] == 0.0
    assert state["reentry_paused_for_day"] is False
    assert autorun_mod.AutoRunManager._rollover_stoploss_daily_fields(state, "2026-03-28") is False


def test_sync_stoploss_pause_status_fields(autorun_mod):
    state = {"reentry_paused_for_day": True, "market_status_last": ""}
    autorun_mod.AutoRunManager._sync_stoploss_pause_status_fields(state)
    assert state["market_status_last"] == "reentry_paused_for_day"

    state = {"reentry_paused_for_day": False, "market_status_last": "reentry_paused_for_day"}
    autorun_mod.AutoRunManager._sync_stoploss_pause_status_fields(state)
    assert state["market_status_last"] == ""


def test_stoploss_threshold_ticks_increase_with_cycle_count(manager_factory):
    manager = manager_factory()
    manager.config.stoploss_reference_tick_size = 0.01
    manager.config.stoploss_drawdown_step_per_cycle_ticks = 1

    base = manager._stoploss_threshold_ticks(
        anchor_price=0.5,
        threshold_pct=0.05,
        tick=0.01,
        cycle_count=0,
    )
    higher = manager._stoploss_threshold_ticks(
        anchor_price=0.5,
        threshold_pct=0.05,
        tick=0.01,
        cycle_count=3,
    )

    assert base >= 1
    assert higher > base


def test_stoploss_spread_extra_ticks_applies_tiers(manager_factory):
    manager = manager_factory()
    manager.config.stoploss_spread_extra_tick_tier_1_pct = 0.03
    manager.config.stoploss_spread_extra_tick_tier_1_ticks = 1
    manager.config.stoploss_spread_extra_tick_tier_2_pct = 0.05
    manager.config.stoploss_spread_extra_tick_tier_2_ticks = 2
    manager.config.stoploss_spread_extra_tick_tier_3_pct = 0.08
    manager.config.stoploss_spread_extra_tick_tier_3_ticks = 4

    assert manager._stoploss_spread_extra_ticks(0.02) == 0
    assert manager._stoploss_spread_extra_ticks(0.03) == 1
    assert manager._stoploss_spread_extra_ticks(0.06) == 2
    assert manager._stoploss_spread_extra_ticks(0.09) == 4


def test_wide_spread_stoploss_confirmation_requires_window_and_rounds(manager_factory):
    manager = manager_factory()
    manager.config.stoploss_wide_spread_confirm_window_sec = 30.0
    manager.config.stoploss_wide_spread_confirm_rounds = 3
    state = manager._default_stoploss_reentry_state("tok")

    hits, elapsed, ready = manager._advance_wide_spread_stoploss_confirmation(
        state,
        now=100.0,
    )
    assert (hits, elapsed, ready) == (1, 0.0, False)

    hits, elapsed, ready = manager._advance_wide_spread_stoploss_confirmation(
        state,
        now=115.0,
    )
    assert (hits, elapsed, ready) == (2, 15.0, False)

    hits, elapsed, ready = manager._advance_wide_spread_stoploss_confirmation(
        state,
        now=130.0,
    )
    assert (hits, elapsed, ready) == (3, 30.0, True)


def test_reset_wide_spread_stoploss_confirmation_clears_state(manager_factory):
    manager = manager_factory()
    state = manager._default_stoploss_reentry_state("tok")
    state["wide_spread_stoploss_confirm_hits"] = 2
    state["wide_spread_stoploss_first_ts"] = 123.0

    assert manager._reset_wide_spread_stoploss_confirmation(state) is True
    assert state["wide_spread_stoploss_confirm_hits"] == 0
    assert state["wide_spread_stoploss_first_ts"] == 0.0
    assert manager._reset_wide_spread_stoploss_confirmation(state) is False


def test_force_stoploss_on_wide_spread_uses_current_threshold_multiplier(manager_factory):
    manager = manager_factory()
    manager.config.stoploss_wide_spread_force_trigger_multiplier = 2.0

    assert (
        manager._should_force_stoploss_on_wide_spread(
            drawdown_pct=-0.099,
            threshold_pct=0.05,
        )
        is False
    )
    assert (
        manager._should_force_stoploss_on_wide_spread(
            drawdown_pct=-0.10,
            threshold_pct=0.05,
        )
        is True
    )
    assert (
        manager._should_force_stoploss_on_wide_spread(
            drawdown_pct=-0.08,
            threshold_pct=0.04,
        )
        is True
    )


def test_wide_spread_stoploss_confirmation_needs_full_30s_window(manager_factory):
    manager = manager_factory()
    manager.config.stoploss_wide_spread_confirm_window_sec = 30.0
    manager.config.stoploss_wide_spread_confirm_rounds = 3
    state = manager._default_stoploss_reentry_state("tok")

    hits, elapsed, ready = manager._advance_wide_spread_stoploss_confirmation(
        state,
        now=100.0,
    )
    assert (hits, elapsed, ready) == (1, 0.0, False)

    hits, elapsed, ready = manager._advance_wide_spread_stoploss_confirmation(
        state,
        now=110.0,
    )
    assert (hits, elapsed, ready) == (2, 10.0, False)

    hits, elapsed, ready = manager._advance_wide_spread_stoploss_confirmation(
        state,
        now=120.0,
    )
    assert (hits, elapsed, ready) == (3, 20.0, False)

    assert manager._reset_wide_spread_stoploss_confirmation(state) is True
    assert state["wide_spread_stoploss_confirm_hits"] == 0
    assert state["wide_spread_stoploss_first_ts"] == 0.0


def test_wide_spread_stoploss_confirmation_allows_exit_after_persistent_weakness(
    manager_factory,
):
    manager = manager_factory()
    manager.config.stoploss_wide_spread_confirm_window_sec = 30.0
    manager.config.stoploss_wide_spread_confirm_rounds = 3
    state = manager._default_stoploss_reentry_state("tok")

    sequence = [
        manager._advance_wide_spread_stoploss_confirmation(state, now=200.0),
        manager._advance_wide_spread_stoploss_confirmation(state, now=215.0),
        manager._advance_wide_spread_stoploss_confirmation(state, now=230.0),
    ]

    assert sequence[0] == (1, 0.0, False)
    assert sequence[1] == (2, 15.0, False)
    assert sequence[2] == (3, 30.0, True)


def test_build_stoploss_reentry_band_is_monotonic(manager_factory):
    manager = manager_factory()
    manager.config.stoploss_reference_tick_size = 0.01
    manager._estimate_token_tick_size = lambda token_id, position_row=None: 0.01

    line, lower, probe = manager._build_stoploss_reentry_band(
        token_id="tok",
        exec_price=0.60,
        line_ticks=2,
        zone_lower_pct=0.02,
        probe_break_pct=0.05,
    )

    assert 0.0 <= probe < lower < line < 0.60


def test_stoploss_market_closed_detection(manager_factory):
    manager = manager_factory()
    manager._ws_cache = {"tok": {"is_closed": True}, "tok2": {"closed": False}}

    assert manager._stoploss_is_market_closed("tok") is True
    assert manager._stoploss_is_market_closed("tok2") is False


def test_taker_fallback_gets_independent_retry_window(manager_factory):
    manager = manager_factory()
    manager.config.stoploss_reentry_taker_retry_cooldown_sec = 60.0
    manager._has_actionable_position = lambda token_id, size, row=None: size > 0
    manager._total_liquidation = type(
        "FakeLiquidation",
        (),
        {
            "_get_cached_client": staticmethod(lambda: None),
            "_clear_open_orders_for_token": staticmethod(lambda client, token_id: {"cleared": True}),
            "reenter_single_token_taker": staticmethod(lambda *args, **kwargs: {"ok": False, "filled_size": 0.0, "after_size": 0.0, "error": "stale taker ask quote"}),
        },
    )()
    state = manager._default_stoploss_reentry_state("tok")
    state["state"] = "STOPLOSS_REENTRY_MAKER_WORKING"
    state["reentry_target_size"] = 10.0
    state["last_stoploss_size"] = 10.0
    state["reentry_reference_price"] = 0.79
    state["reentry_deadline_ts"] = 100.0

    changed = manager._advance_stoploss_reentry_execution(
        "tok",
        state,
        now=100.0,
        position_row=None,
    )

    assert changed is True
    assert state["state"] == "STOPLOSS_REENTRY_TAKER_PENDING"
    assert state["reentry_deadline_ts"] == 700.0


def test_taker_fallback_resets_to_waiting_rebound_after_window_expiry(manager_factory):
    manager = manager_factory()
    manager.config.stoploss_reentry_taker_retry_cooldown_sec = 60.0
    manager._has_actionable_position = lambda token_id, size, row=None: size > 0
    manager._total_liquidation = type(
        "FakeLiquidation",
        (),
        {
            "_get_cached_client": staticmethod(lambda: None),
            "_clear_open_orders_for_token": staticmethod(lambda client, token_id: {"cleared": True}),
            "reenter_single_token_taker": staticmethod(lambda *args, **kwargs: {"ok": False, "filled_size": 0.0, "after_size": 0.0, "error": "stale taker ask quote"}),
        },
    )()
    state = manager._default_stoploss_reentry_state("tok")
    state["state"] = "STOPLOSS_REENTRY_TAKER_PENDING"
    state["reentry_target_size"] = 10.0
    state["last_stoploss_size"] = 10.0
    state["reentry_reference_price"] = 0.79
    state["reentry_deadline_ts"] = 500.0
    state["reentry_maker_order_id"] = "oid"

    changed = manager._advance_stoploss_reentry_execution(
        "tok",
        state,
        now=501.0,
        position_row=None,
    )

    assert changed is True
    assert state["state"] == "STOPLOSS_EXITED_WAITING_REBOUND"
    assert state["reentry_deadline_ts"] == 0.0
    assert state["reentry_maker_order_id"] == ""
    assert "window expired" in state["last_error"]
