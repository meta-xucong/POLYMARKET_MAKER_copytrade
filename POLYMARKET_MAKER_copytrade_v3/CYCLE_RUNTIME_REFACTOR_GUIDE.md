# Cycle And Runtime Refactor Guide

## Purpose

This document is a code-change reference for fixing the recurring token lifecycle issues in the Polymarket maker/copytrade system.

Primary goals:

1. Keep `cycle_round` semantics pure.
2. Separate cycle progression from runtime recovery.
3. Prevent `SELL_ABANDONED`, dust positions, and startup reconcile from polluting round state.
4. Preserve the ability for a child process to run multiple rounds continuously.
5. Enforce inter-cycle pause and incremental thresholds even inside a long-lived child process.

This document is intentionally implementation-oriented so Codex can use it as an execution guide.

## Non-Negotiable Principle

`cycle_round += 1` only affects:

1. The next round's buy wait interval.
2. The next round's drop threshold increment.
3. The next round's profit threshold increment.

Nothing else should depend on round advancement.

Specifically, `cycle_round` must not directly control:

- refill eligibility
- reentry eligibility
- startup reconcile
- stoploss recovery scheduling
- sell signal consumption
- `SELL_ABANDONED` retry behavior
- dust cleanup behavior

## Core Design

The system must be split into two independent layers:

1. Cycle state
2. Runtime state

These two layers may interact, but they must not overwrite each other's meaning.

## Layer 1: Cycle State

Cycle state exists only to describe the next round's trading parameters.

Recommended fields:

- `cycle_round`
- `next_buy_allowed_ts`
- `last_cycle_completed_ts`
- `next_drop_pct`
- `next_profit_pct`

Rules:

- These fields may only be updated by a single cycle-advance function.
- That function may only be called after a round is confirmed closed.
- `SELL_ABANDONED`, `STOPPED`, reconcile, stoploss waiting state, and dust retries must not mutate these fields.

### Round Close Condition

A round is closed only when one of the following is true:

1. Sell fill is confirmed and remote position is effectively zero.
2. Remote position is confirmed zero even if execution reporting was noisy.
3. Remote position is confirmed dust-only and classified as terminal non-actionable.

Not sufficient:

- sell order abandoned
- sell order stopped
- local state became `FLAT`
- exit record was written
- child process ended

### Cycle Advance Function

Create or refactor a single function with this responsibility:

- input:
  - token id
  - current cycle state
  - current run config
  - current timestamp
- output:
  - next `cycle_round`
  - next `next_buy_allowed_ts`
  - next `next_drop_pct`
  - next `next_profit_pct`

Behavior:

- `next_round = current_round + 1`
- wait interval uses the current exponential rule
- threshold increments only apply if configured step is greater than zero
- no runtime-recovery side effects

This function must not:

- enqueue refill
- mark reentry
- restart a task
- inspect sell signals

## Layer 2: Runtime State

Runtime state answers a different question:

What should the system do right now for this token?

Suggested runtime states:

- `ACTIVE_CYCLE_BUYABLE`
- `ACTIVE_CYCLE_HOLDING`
- `EXIT_PENDING`
- `RECOVER_SELL_ONLY`
- `INTER_CYCLE_COOLDOWN`
- `DUST_TERMINAL`
- `STOPLOSS_WAITING`
- `STOPLOSS_REENTRY_ARMED`

Notes:

- `INTER_CYCLE_COOLDOWN` means the previous round is already closed, and the process is waiting for the next round.
- `RECOVER_SELL_ONLY` means the round is not closed, and the system is still trying to finish the current round's exit.
- These two states must never be merged.

## Unified Position Truth Model

All code paths must use one shared position classification function.

The function should classify remote position size into:

1. `ZERO`
2. `DUST_NON_ACTIONABLE`
3. `ACTIONABLE`

Recommended inputs:

- `position_size`
- `market_min_order_size`
- optional safety multipliers

Recommended behavior:

- `ZERO`
  - treat as no position
- `DUST_NON_ACTIONABLE`
  - do not restart sell recovery
  - do not keep reconcile loops alive
  - may be treated as cycle closed if operationally safe
- `ACTIONABLE`
  - continue sell recovery or reconcile as needed

### Important Constraint

No module may use its own private rule such as:

- `size > 0`
- `size > 1e-6`
- `size >= min_order_size`
- `size >= dust_floor`

All such checks must route through the unified classifier.

## Dust Policy

Dust must not be treated as a normal live position.

Target behavior for dust:

- do not requeue `startup_reconcile_position`
- do not trigger continuous maker sell retries
- do not block cycle closure if the remaining size is operationally non-actionable
- do not let manager and child disagree about whether the token still has a real position

Example motivation:

- a residue like `0.000464` should almost certainly be classified as dust, not as a recoverable live position

## Child Process Design

The child process may remain long-lived and handle multiple rounds.

That is allowed and preferred for efficiency.

However, the child process must explicitly implement inter-cycle control.

### Required Child Phases

Suggested internal child phases:

- `IN_CYCLE`
- `WAIT_SELL_CONFIRM`
- `INTER_CYCLE_COOLDOWN`
- `READY_NEXT_BUY`

Rules:

1. `IN_CYCLE`
   - normal strategy buy/sell logic runs

2. `WAIT_SELL_CONFIRM`
   - after sell execution finishes, confirm remote position truth
   - do not immediately re-enter buyable flat mode

3. `INTER_CYCLE_COOLDOWN`
   - round already closed
   - child remains alive
   - child must not emit any buy action
   - child may keep reading market data and syncing state

4. `READY_NEXT_BUY`
   - entered only when `now >= next_buy_allowed_ts`
   - only then may next-cycle buy logic activate

### Critical Requirement

After a completed sell, the child must not naturally fall back to:

- `FLAT awaiting=BUY`

unless cycle cooldown has already expired.

This is the main place where previous fixes likely leaked.

## `SELL_ABANDONED` Semantics

`SELL_ABANDONED` is not a cycle-close event.

It is only an execution failure event.

It may affect:

- runtime recovery
- requeue decisions
- sell-only recovery mode
- diagnostics and alerting

It must not affect:

- `cycle_round`
- `next_buy_allowed_ts`
- `next_drop_pct`
- `next_profit_pct`

### Recommended Refactor

Instead of treating `SELL_ABANDONED` as a refillable exit, split it into:

1. execution result
2. remote position truth
3. cycle result

Suggested derived outcomes:

- `SELL_FAILED_ZERO_AFTER_CHECK`
- `SELL_FAILED_DUST_AFTER_CHECK`
- `SELL_FAILED_ACTIONABLE_REMAINS`

Only the first two may be allowed to close the cycle.

The third must remain in current-round recovery.

## Refill And Reentry

Refill and reentry should remain as runtime scheduling concepts only.

They may decide:

- whether to restart a token
- which resume mode to use
- whether a token gets priority in a queue

They must not decide:

- that a round is complete
- that thresholds should increment
- that cycle gate should reset

### Valid Runtime Resume Modes

Examples:

- `recover_sell_only`
- `startup_reconcile`
- `stoploss_waiting`
- `normal_next_cycle`

These modes should be interpreted by runtime control, not by cycle progression.

## Startup Reconcile

`startup_reconcile_position` must be constrained by unified position truth.

Recommended rules:

- if position class is `ZERO`, do not restart
- if position class is `DUST_NON_ACTIONABLE`, finalize to dust terminal state, do not requeue
- if position class is `ACTIONABLE`, restart into sell recovery mode

This should stop the repeated loop:

- process exits cleanly
- position remains according to loose check
- requeue
- repeat

## Stoploss Integration

Stoploss logic should use the same separation:

- stoploss trigger is runtime control
- cycle advancement still depends only on confirmed position closure

Recommended rules:

- stoploss exit attempt does not itself advance round
- after stoploss exit attempt, reclassify remote position
- only `ZERO` or terminal dust can close the round
- actionable remainder stays in a stoploss recovery runtime state

## Unified Execution Priority

When processing a token, evaluation priority should be:

1. refresh remote position truth
2. apply high-priority risk control
3. decide whether current round is closed
4. decide runtime recovery path
5. apply cycle buy gate if entering a new round
6. run normal strategy signals

This ordering is required to prevent stale local state from front-running remote truth.

## Implementation Checklist

### Phase 1: Position Truth

- add a single shared position classification function
- replace all ad hoc `size > 0` checks with unified classification
- apply it to:
  - startup reconcile
  - sell signal gating
  - sell recovery
  - stoploss post-exit checks
  - autorun status reporting

### Phase 2: Cycle Purification

- isolate cycle advance into one function
- ensure only confirmed round close can call it
- prevent `SELL_ABANDONED` and similar paths from mutating cycle fields

### Phase 3: `SELL_ABANDONED` Refactor

- stop treating it as implicitly refillable cycle completion
- classify remote position immediately after abandon
- route to:
  - cycle close
  - dust terminal
  - recover sell only

### Phase 4: Child Inter-Cycle Gate

- add explicit child phases
- implement `INTER_CYCLE_COOLDOWN`
- block all child-side buy actions until cooldown expires

### Phase 5: Resume/Reentry Cleanup

- keep refill/reentry runtime-only
- remove any hidden coupling from refill/reentry into cycle state mutation

## Acceptance Criteria

### Round Integrity

- after each completed sell, `cycle_round` increments exactly once
- `next_buy_allowed_ts` changes exactly once per completed round
- `next_drop_pct` and `next_profit_pct` change only when configured increments are positive

### Buy Pause Integrity

- in actual trade history, time from completed sell to next buy matches the expected round cooldown
- this must hold even when the same child process stays alive

### `SELL_ABANDONED` Integrity

- `SELL_ABANDONED` no longer causes incorrect cycle closure
- `SELL_ABANDONED` no longer causes incorrect threshold advancement
- actionable remainder after `SELL_ABANDONED` stays in recovery, not next round

### Dust Integrity

- dust positions do not trigger endless reconcile
- dust positions do not trigger repeated sell recovery tasks
- manager and child agree on dust handling

### Stoploss Integrity

- stoploss attempts do not directly mutate cycle state
- post-stoploss path uses unified position truth
- only confirmed zero or terminal dust closes the round

## Anti-Patterns To Avoid

Do not add more one-off guards such as:

- special-case ignore flags for one token path
- duplicate dust thresholds in multiple files
- extra stale-signal heuristics that silently mutate cycle fields
- hidden `round+1` in refill/reentry/reconcile paths

The correct fix is architectural separation, not more localized patches.

## Summary

The intended end state is:

- cycle state is pure and minimal
- runtime recovery is independent from round math
- dust is explicitly modeled
- `SELL_ABANDONED` is execution failure, not pseudo-cycle-close
- long-lived child processes are allowed
- child processes enforce inter-cycle cooldown internally

This preserves the system's real goal:

- round advancement only controls next-round wait and thresholds
- everything else is handled by unified runtime control
