# Code Change Tasklist For Cycle / Runtime Refactor

## Purpose

This document is the concrete code-change tasklist derived from:

- [CYCLE_RUNTIME_REFACTOR_GUIDE.md](/D:/AI/vibe_coding/case/POLYMARKET_MAKER_copytrade_v2/CYCLE_RUNTIME_REFACTOR_GUIDE.md)

Its goal is to help Codex modify the codebase in a disciplined sequence without mixing cycle semantics and runtime recovery semantics again.

## Scope

Primary code areas:

- [poly_maker_autorun.py](/D:/AI/vibe_coding/case/POLYMARKET_MAKER_copytrade_v2/POLYMARKET_MAKER_AUTO/poly_maker_autorun.py)
- [Volatility_arbitrage_run.py](/D:/AI/vibe_coding/case/POLYMARKET_MAKER_copytrade_v2/POLYMARKET_MAKER_AUTO/POLYMARKET_MAKER/Volatility_arbitrage_run.py)
- [Volatility_arbitrage_strategy.py](/D:/AI/vibe_coding/case/POLYMARKET_MAKER_copytrade_v2/POLYMARKET_MAKER_AUTO/POLYMARKET_MAKER/Volatility_arbitrage_strategy.py)
- related tests under [tests](/D:/AI/vibe_coding/case/POLYMARKET_MAKER_copytrade_v2/POLYMARKET_MAKER_AUTO/POLYMARKET_MAKER/tests)

Secondary files likely affected:

- runtime JSON writers/readers in manager
- autorun status payload
- stoploss state transitions
- startup reconcile and sell cleanup paths

## Success Criteria

End-state behavior:

1. `cycle_round` only affects next-round wait and next-round thresholds.
2. `SELL_ABANDONED` never advances cycle state.
3. dust never causes endless recover/requeue loops.
4. child process may stay alive across rounds, but must enforce inter-cycle cooldown itself.
5. manager and child must use the same position-truth classification.

## Work Sequence

Recommended execution order:

1. Add shared position-truth model.
2. Purify cycle-state update logic.
3. Refactor `SELL_ABANDONED` handling.
4. Add child-side inter-cycle cooldown state.
5. Clean up resume/reentry/startup reconcile flows.
6. Add tests and log assertions.

Do not start from reentry/refill first.

## Task Group A: Shared Position Truth Model

### Goal

Create one canonical position classifier used by all manager and child decision paths.

### Add

Add a shared helper, likely in manager first and optionally mirrored or imported into child:

- `classify_position_truth(position_size, market_min_order_size, ...) -> ZERO | DUST_NON_ACTIONABLE | ACTIONABLE`

If code sharing between manager and child is practical, move this helper into a dedicated utility module instead of duplicating it.

### Candidate Locations

- [poly_maker_autorun.py](/D:/AI/vibe_coding/case/POLYMARKET_MAKER_copytrade_v2/POLYMARKET_MAKER_AUTO/poly_maker_autorun.py)
- or a new utility module under `POLYMARKET_MAKER_AUTO`

### Replace Existing Ad Hoc Checks

Search and replace any logic equivalent to:

- `size > 0`
- `size <= dust_floor`
- `size >= min_order_size`
- `has_position` derived from loose float checks

Apply unified classification to:

- startup reconcile paths
- sell signal local gating
- exit cleanup suppression
- stoploss post-liquidation confirmation
- autorun task requeue decision
- status reporting

### Must Review

- [poly_maker_autorun.py](/D:/AI/vibe_coding/case/POLYMARKET_MAKER_copytrade_v2/POLYMARKET_MAKER_AUTO/poly_maker_autorun.py)
  Relevant zones:
  - startup reconcile requeue logic near line 3918
  - sell signal local gate near lines 3399-3425
  - full sell recheck near lines 11160-11237
  - trigger sell exit near lines 11238+
- [Volatility_arbitrage_run.py](/D:/AI/vibe_coding/case/POLYMARKET_MAKER_copytrade_v2/POLYMARKET_MAKER_AUTO/POLYMARKET_MAKER/Volatility_arbitrage_run.py)
  Relevant zones:
  - position sync and missing avg price handling around lines 4949-5029
  - stoploss child ack and follow-up paths around lines 4594-4629

### Deliverable

After this step, all position-sensitive decisions must route through the same classification function.

## Task Group B: Purify Cycle State Updates

### Goal

Make cycle state updates occur in exactly one place and only after confirmed round close.

### Existing Function To Refactor

- [`_advance_token_cycle_state_on_cleanup()`](/D:/AI/vibe_coding/case/POLYMARKET_MAKER_copytrade_v2/POLYMARKET_MAKER_AUTO/poly_maker_autorun.py#L3197)

### Problems To Fix

- current naming implies generic cleanup can advance cycle state
- current callers may be too broad
- round advancement may be reached through paths that are not true cycle closure

### Required Changes

1. Rename or wrap the function so the contract is explicit.
   Suggested name:
   - `_advance_token_cycle_state_on_round_close()`

2. Restrict callers so only confirmed close paths can invoke it.

3. Make the function operate only on:
   - `cycle_round`
   - `next_buy_allowed_ts`
   - `last_cycle_completed_ts`
   - `next_drop_pct`
   - `next_profit_pct`

4. Prevent side effects into:
   - reentry
   - refill
   - task scheduling
   - sell signal state

### Add

Add a helper such as:

- `_confirm_round_close_from_position_truth(...)`

This helper should decide whether cycle close is valid before cycle advancement is called.

### Must Review

- [`_apply_token_cycle_buy_gate_and_drop_override()`](/D:/AI/vibe_coding/case/POLYMARKET_MAKER_copytrade_v2/POLYMARKET_MAKER_AUTO/poly_maker_autorun.py#L3289)
- [`_mark_token_cycle_local_start()`](/D:/AI/vibe_coding/case/POLYMARKET_MAKER_copytrade_v2/POLYMARKET_MAKER_AUTO/poly_maker_autorun.py#L3346)
- [`_mark_token_cycle_invalidated()`](/D:/AI/vibe_coding/case/POLYMARKET_MAKER_copytrade_v2/POLYMARKET_MAKER_AUTO/poly_maker_autorun.py#L3367)

### Deliverable

There must be exactly one authoritative path from "round closed" to "round advanced".

## Task Group C: Redefine `SELL_ABANDONED`

### Goal

Stop treating `SELL_ABANDONED` as a pseudo-exit that contaminates cycle semantics.

### Existing Hotspot

- [`_append_exit_token_record()`](/D:/AI/vibe_coding/case/POLYMARKET_MAKER_copytrade_v2/POLYMARKET_MAKER_AUTO/poly_maker_autorun.py#L7489)

### Problems To Fix

Current behavior strongly suggests:

- `SELL_ABANDONED` is recorded as a refillable exit
- reentry eligibility and unmanaged rearm are attached too early
- position may still be actionable when manager starts treating the token as if it exited

### Required Refactor

Split outcome evaluation into three layers:

1. execution result
2. remote position truth
3. cycle/runtime result

Suggested internal result names:

- `SELL_FAILED_ZERO_AFTER_CHECK`
- `SELL_FAILED_DUST_AFTER_CHECK`
- `SELL_FAILED_ACTIONABLE_REMAINS`

### Rules

- `SELL_ABANDONED` alone must not:
  - mark cycle closed
  - advance cycle state
  - reset next buy gate
  - increment next thresholds

- if remote truth is actionable:
  - remain in current-round recovery
  - enter `RECOVER_SELL_ONLY`
  - allow restart/requeue if needed

- if remote truth is dust:
  - finalize to dust terminal state
  - do not keep sell recovery alive

- if remote truth is zero:
  - allow proper round close handling

### Must Review

- [`_is_reentry_eligible_exit()`](/D:/AI/vibe_coding/case/POLYMARKET_MAKER_copytrade_v2/POLYMARKET_MAKER_AUTO/poly_maker_autorun.py#L5908)
- [`_mark_reentry_eligible_token()`](/D:/AI/vibe_coding/case/POLYMARKET_MAKER_copytrade_v2/POLYMARKET_MAKER_AUTO/poly_maker_autorun.py#L5925)
- [`_clear_reentry_eligible_token()`](/D:/AI/vibe_coding/case/POLYMARKET_MAKER_copytrade_v2/POLYMARKET_MAKER_AUTO/poly_maker_autorun.py#L5937)
- any `SELL_ABANDONED` hydration logic near lines 10197-10232

### Deliverable

`SELL_ABANDONED` becomes a runtime recovery event, not a cycle event.

## Task Group D: Child Inter-Cycle Cooldown

### Goal

Keep child processes long-lived while still enforcing round spacing internally.

### Existing File

- [Volatility_arbitrage_run.py](/D:/AI/vibe_coding/case/POLYMARKET_MAKER_copytrade_v2/POLYMARKET_MAKER_AUTO/POLYMARKET_MAKER/Volatility_arbitrage_run.py)

### Problem To Fix

Current behavior strongly suggests that after a completed sell the child can drift back to buyable flat behavior too early.

### Required Additions

Add explicit child-side cycle phases:

- `IN_CYCLE`
- `WAIT_SELL_CONFIRM`
- `INTER_CYCLE_COOLDOWN`
- `READY_NEXT_BUY`

### Required Rules

1. After sell completion:
   - confirm remote position truth
   - if closed, fetch or update cycle state
   - enter `INTER_CYCLE_COOLDOWN`

2. During `INTER_CYCLE_COOLDOWN`:
   - child may continue market observation
   - child must reject any buy action
   - child must not return to normal `FLAT awaiting=BUY`

3. Only when cooldown expires:
   - switch to `READY_NEXT_BUY`
   - allow buy logic to resume

### Candidate Refactor Areas

- main loop in [`main()`](/D:/AI/vibe_coding/case/POLYMARKET_MAKER_copytrade_v2/POLYMARKET_MAKER_AUTO/POLYMARKET_MAKER/Volatility_arbitrage_run.py#L2340)
- action dispatch around lines 3722 and 4070
- position sync / sell-only activation around lines 4949-5077

### Deliverable

A child process may span many rounds, but no new round buy is possible until internal cooldown is satisfied.

## Task Group E: Runtime State Separation

### Goal

Separate runtime states from cycle fields and make runtime recovery explicit.

### Add

Manager-side runtime state or equivalent flags should distinguish:

- active buyable round
- active holding round
- exit pending
- recover sell only
- inter-cycle cooldown
- dust terminal
- stoploss waiting

### Important Constraint

Do not overload existing `local_cycle_status` with too many incompatible meanings unless necessary.

If extending current fields becomes too confusing, create a separate runtime-status field instead of forcing all semantics into:

- `started_not_bought`
- `position_resume`
- `position_confirmed`
- `sell_pending`
- `cycle_closed`
- `invalidated`

### Must Review

- cycle-status normalization around lines 2268-2368 in [poly_maker_autorun.py](/D:/AI/vibe_coding/case/POLYMARKET_MAKER_copytrade_v2/POLYMARKET_MAKER_AUTO/poly_maker_autorun.py)

### Deliverable

Manager runtime control should no longer depend on inferring too much from cycle fields.

## Task Group F: Startup Reconcile Cleanup

### Goal

Stop endless `startup_reconcile_position` requeue loops caused by weak position checks.

### Existing Symptom

Repeated status notes:

- process finished rc=0
- position remains
- requeued

### Required Rules

- if position truth is `ZERO`, stop requeue
- if position truth is `DUST_NON_ACTIONABLE`, finalize and stop requeue
- if position truth is `ACTIONABLE`, requeue into sell recovery mode only

### Must Review

- requeue logic around line 3918 in [poly_maker_autorun.py](/D:/AI/vibe_coding/case/POLYMARKET_MAKER_copytrade_v2/POLYMARKET_MAKER_AUTO/poly_maker_autorun.py)
- resume-state generation and startup mode selection around lines 8248-8280

### Deliverable

No repeated reconcile loop should survive on dust-only positions.

## Task Group G: Reentry / Refill Decoupling

### Goal

Keep refill and reentry as runtime scheduling concerns only.

### Existing Manager Touchpoints

- self-sell reentry handling around lines 7448-7488
- exit record logic around lines 7489+
- runtime sell signal and startup flows

### Required Rules

- reentry/refill may enqueue or reprioritize tokens
- reentry/refill may choose resume mode
- reentry/refill must not mutate cycle fields
- reentry/refill must not imply round close

### Deliverable

A token may re-enter runtime processing without any automatic `round+1`.

## Task Group H: Stoploss Alignment

### Goal

Make stoploss share the same position-truth and cycle-close semantics.

### Existing Hotspot

- stoploss trigger and postcheck logic around lines 4300-4410 in [poly_maker_autorun.py](/D:/AI/vibe_coding/case/POLYMARKET_MAKER_copytrade_v2/POLYMARKET_MAKER_AUTO/poly_maker_autorun.py)

### Required Rules

- stoploss trigger is runtime control only
- stoploss does not directly advance cycle
- after stoploss execution, reclassify remote position
- only zero or terminal dust can close round
- actionable remains enter stoploss recovery runtime state

### Child-Side Must Review

- stoploss child event journal handling around lines 4594-4629 in [Volatility_arbitrage_run.py](/D:/AI/vibe_coding/case/POLYMARKET_MAKER_copytrade_v2/POLYMARKET_MAKER_AUTO/POLYMARKET_MAKER/Volatility_arbitrage_run.py)
- reentry hold logic around lines 4839-4846, 5675-5698, 6230-6241

### Deliverable

Stoploss can no longer leave round math and runtime math in disagreement.

## Task Group I: Logging And Status Payload

### Goal

Improve observability so the next debugging cycle is cheap.

### Add Or Refine Logs

For every transition, log:

- token id
- runtime state before -> after
- position truth
- cycle state before -> after
- reason source

Especially log:

- when round closes
- when cycle advances
- when child enters inter-cycle cooldown
- when dust terminal classification happens
- when `SELL_ABANDONED` is downgraded into recover-sell-only

### Status Payload Improvements

In [autorun_status.json](/D:/AI/vibe_coding/case/POLYMARKET_MAKER_copytrade_v2/POLYMARKET_MAKER_AUTO/data/autorun_status.json), consider exposing:

- runtime state
- position truth class
- whether token is in inter-cycle cooldown
- cooldown remaining seconds

### Deliverable

Logs should clearly show why a token is waiting, recovering, dust-finalized, or buy-blocked.

## Task Group J: Tests

### Goal

Add regression coverage for the exact failure patterns seen in logs.

### Add Or Update Tests

Under [tests](/D:/AI/vibe_coding/case/POLYMARKET_MAKER_copytrade_v2/POLYMARKET_MAKER_AUTO/POLYMARKET_MAKER/tests):

1. cycle advance tests
   - advance only on confirmed close
   - no advance on `SELL_ABANDONED`

2. dust classification tests
   - dust does not requeue reconcile
   - dust does not trigger endless sell recovery

3. child cooldown tests
   - after completed sell, buy actions are blocked until cooldown expiry

4. stoploss tests
   - stoploss actionable remainder does not close cycle
   - stoploss dust remainder may finalize safely

5. reentry/refill tests
   - runtime restart allowed without cycle increment

6. startup reconcile tests
   - dust position terminates
   - actionable position resumes sell recovery

### Suggested Existing Test Files To Extend

- [test_autorun_strategy_*](/D:/AI/vibe_coding/case/POLYMARKET_MAKER_copytrade_v2/POLYMARKET_MAKER_AUTO/POLYMARKET_MAKER/tests/test_autorun_strategy_compat.py)
- [test_strategy_compat.py](/D:/AI/vibe_coding/case/POLYMARKET_MAKER_copytrade_v2/POLYMARKET_MAKER_AUTO/POLYMARKET_MAKER/tests/test_strategy_compat.py)
- [test_total_liquidation_*](/D:/AI/vibe_coding/case/POLYMARKET_MAKER_copytrade_v2/POLYMARKET_MAKER_AUTO/POLYMARKET_MAKER/tests/test_total_liquidation_manager.py)
- [test_execution.py](/D:/AI/vibe_coding/case/POLYMARKET_MAKER_copytrade_v2/POLYMARKET_MAKER_AUTO/POLYMARKET_MAKER/tests/test_execution.py)

If current test files are a poor fit, create dedicated new files.

## Suggested Implementation Batches

### Batch 1

- shared position truth helper
- startup reconcile dust/actionable split
- no code-path behavior change to cycle yet except replacing loose position checks

### Batch 2

- cycle advance purification
- `SELL_ABANDONED` semantic split
- manager-side runtime state cleanup

### Batch 3

- child inter-cycle cooldown
- child explicit phase handling
- child-side buy blocking during cooldown

### Batch 4

- reentry/refill cleanup
- stoploss alignment
- expanded logging/status

### Batch 5

- tests
- targeted log replay validation against problem tokens

## Validation Tokens

When validating with real logs or replay tooling, prioritize:

- `Will Finland win Eurovision 2026?`
- `Netanyahu out by March 31?`

Expected post-fix behavior:

- no repeated minute-level `SELL -> BUY -> SELL` cycles unless cooldown truly expired
- no endless `SELL_ABANDONED` loop on actionable remainder without clear recovery state
- no requeue driven by dust-only position

## Anti-Goals

Do not:

- add more scattered `if token_id == ...` fixes
- add more local dust thresholds without centralization
- let reentry/refill mutate cycle fields indirectly
- equate exit record creation with cycle close
- rely on child-local flat state alone to infer round completion

## Final Check Before Coding

Before each batch, verify:

1. Does this change affect cycle semantics or runtime semantics?
2. If it affects cycle semantics, is it strictly one of the three allowed round consequences?
3. If it affects runtime semantics, does it avoid mutating cycle state?
4. Does it use unified position truth instead of ad hoc float checks?

If any answer is unclear, stop and resolve the ownership of that logic first.
