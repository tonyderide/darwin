# Darwin Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the 17 critical gaps identified by the 6-agent review (2 quant + 2 scalper + 2 trader) so Darwin can produce genuinely robust trading strategies instead of overfit lottery-ticket champions.

**Architecture:** Six independently mergeable phases. Each phase produces working, green-tested software and can be shipped on its own. Phases are ordered to unblock dependents: bugs → science → realism → signals → DNA → mechanics.

**Tech Stack:** Python 3.12, pytest, pure stdlib (no pandas/numpy dependency introduced). Existing file layout preserved.

---

## Phase summary

| # | Phase | Scope | Files touched |
|---|---|---|---|
| A | Critical bug fixes | `bb_squeeze` self-inclusion, unseeded RNG, lookahead fill-at-close | `indicators.py`, `evolution.py`, `arena.py` |
| B | Fitness + walk-forward | Sharpe/DD fitness, min-trades floor, temporal 70/30 split, OOS leaderboard | `arena.py`, `evolution.py` |
| C | Execution realism | Fill at next-open, slippage, maker/taker mix, funding, min-trade floor | `arena.py` |
| D | Indicators | ATR, MACD, VWAP, OBV, Stochastic + session/relative-volume filter fields | `indicators.py` |
| E | Agent DNA | ATR-scaled stops, short-side skills, position-sizing genes, continuous hyperparams | `agent.py` |
| F | Evolution mechanics | Segmented crossover, mutation decay, niching, larger default pop | `evolution.py` |

---

## Phase A — Critical bug fixes

### Task A1: Fix `bb_squeeze` self-inclusion

**Files:**
- Modify: `indicators.py:147`
- Test: `test_indicators.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# test_indicators.py
from indicators import enrich_candles

def test_bb_squeeze_excludes_current_bar():
    candles = [
        {"timestamp": i, "open": 100, "high": 100.1, "low": 99.9, "close": 100, "volume": 1000}
        for i in range(30)
    ]
    candles[29]["close"] = 50  # extreme bar — squeeze should be judged on history, not this bar
    enrich_candles(candles)
    # percentile computed from 28 prior bars (tight) — today's huge move must NOT pull the percentile down
    assert candles[29]["bb_squeeze"] is False
```

- [ ] **Step 2: Run test to verify it fails**
Run: `pytest test_indicators.py::test_bb_squeeze_excludes_current_bar -v`

- [ ] **Step 3: Fix `indicators.py:147`**
Change `range(max(0, i - 100), i + 1)` to `range(max(0, i - 100), i)` and adjust the `len(lookback) >= 10` branch guard to account for empty lookback on early bars.

- [ ] **Step 4: Run tests**
Run: `pytest test_indicators.py -v && pytest test_arena.py -v` — both green.

- [ ] **Step 5: Commit**
```bash
git add indicators.py test_indicators.py
git commit -m "fix: bb_squeeze must exclude current bar from its own percentile"
```

### Task A2: Seed RNG for reproducibility

**Files:**
- Modify: `evolution.py` (add `seed` param), `arena.py` (pass through if present)
- Test: `test_evolution.py`

- [ ] **Step 1: Add failing test**

```python
def test_evolution_is_deterministic_with_seed():
    import random
    random.seed(42)
    from agent import Agent
    agents = [Agent(f"a{i}", {"buy-on-dip-3pct": 0.5}) for i in range(6)]
    for i, a in enumerate(agents):
        a.fitness = float(i)
    g1 = evolve_generation(agents, target_size=6)
    ids1 = [a.agent_id for a in g1]
    random.seed(42)
    agents = [Agent(f"a{i}", {"buy-on-dip-3pct": 0.5}) for i in range(6)]
    for i, a in enumerate(agents):
        a.fitness = float(i)
    g2 = evolve_generation(agents, target_size=6)
    ids2 = [a.agent_id for a in g2]
    assert ids1 == ids2
```

- [ ] **Step 2: Run → fails (because `uuid.uuid4()` is non-deterministic)**

- [ ] **Step 3: Replace `uuid.uuid4().hex[:6]` in `evolution.py:37` with a deterministic generator seeded from `random`**

```python
# evolution.py top
def _agent_id() -> str:
    return f"agent-{random.getrandbits(24):06x}"
```
Use `_agent_id()` instead of `uuid.uuid4().hex[:6]`.

- [ ] **Step 4: Run test → green**

- [ ] **Step 5: Commit**
```bash
git commit -am "fix: deterministic agent IDs — evolution now reproducible under random.seed()"
```

### Task A3: Document lookahead fill semantics

**Files:** `arena.py` docstring, README

- [ ] **Step 1: Add test to `test_arena.py` pinning current (lookahead-prone) behavior with a comment that Phase C fixes it**

```python
def test_arena_grid_fills_on_same_candle():
    """Intentional baseline: current arena fills on same-bar high/low.
    Phase C moves fills to next-bar open. Test kept to force that change to update this."""
    ...
```

- [ ] **Step 2: Commit test to pin behavior**
```bash
git commit -am "test: pin current same-bar fill behavior (replaced in Phase C)"
```

---

## Phase B — Fitness + walk-forward

### Task B1: Track equity curve to enable Sharpe/DD

**Files:**
- Modify: `arena.py` — each mode appends `equity` point per candle on `agent.equity_curve`
- Test: `test_arena.py`

- [ ] **Step 1: Failing test**
```python
def test_agent_equity_curve_populated():
    a = Agent("a1", {"buy-on-dip-3pct": 0.5})
    arena = Arena(candles=FAKE_CANDLES, initial_capital=100.0)
    arena.evaluate([a])
    assert len(a.equity_curve) == len(FAKE_CANDLES) - 1
    assert all(isinstance(x, (int, float)) for x in a.equity_curve)
```

- [ ] **Step 2: Add `agent.equity_curve = []` init + append `capital + unrealized` each candle in every `_run_*` mode**

- [ ] **Step 3: Run tests, green**

- [ ] **Step 4: Commit**
```bash
git commit -am "feat(arena): track equity curve per agent across all modes"
```

### Task B2: Replace fitness with risk-adjusted metric

**Files:** `arena.py`

- [ ] **Step 1: Failing test**
```python
def test_fitness_prefers_steady_over_lottery():
    """Two agents: same terminal PnL, different drawdown. Steady must rank higher."""
    a_steady = Agent("steady", {}); a_steady.equity_curve = [100, 101, 102, 103, 104]; a_steady.trades = 10
    a_lottery = Agent("lotto", {}); a_lottery.equity_curve = [100, 80, 50, 90, 104]; a_lottery.trades = 2
    from arena import compute_fitness
    assert compute_fitness(a_steady) > compute_fitness(a_lottery)
```

- [ ] **Step 2: Add `compute_fitness(agent)` to `arena.py`**
```python
def compute_fitness(agent, min_trades: int = 5) -> float:
    eq = agent.equity_curve
    if not eq or getattr(agent, "trades", 0) < min_trades:
        return float("-inf")
    # Sharpe-ish on equity returns
    rets = [(eq[i]-eq[i-1])/eq[i-1] if eq[i-1] else 0 for i in range(1, len(eq))]
    if not rets:
        return float("-inf")
    import statistics
    mean = statistics.fmean(rets)
    std = statistics.pstdev(rets) or 1e-9
    sharpe = mean / std
    # Max drawdown
    peak, max_dd = eq[0], 0.0
    for v in eq:
        peak = max(peak, v)
        max_dd = max(max_dd, (peak - v) / peak if peak else 0)
    return sharpe * (1.0 - max_dd)
```

- [ ] **Step 3: Wire `agent.fitness = compute_fitness(agent)` in `evaluate()`**

- [ ] **Step 4: Update existing tests that assumed raw PnL fitness (assert `isinstance(a.fitness, float)` instead of exact value)**

- [ ] **Step 5: Commit**
```bash
git commit -am "feat(arena): fitness = Sharpe × (1 - MaxDD), floor min_trades=5"
```

### Task B3: Walk-forward train/test split

**Files:** `arena.py`, `evolution.py`, `server.py` (wiring only)

- [ ] **Step 1: Failing test**
```python
def test_arena_splits_candles_70_30_temporal():
    arena = Arena(candles=FAKE_CANDLES, initial_capital=100.0, config={"train_test_split": 0.7})
    assert len(arena.train_candles) == 14
    assert len(arena.test_candles) == 6
    # Contiguous temporal
    assert arena.train_candles[-1]["timestamp"] < arena.test_candles[0]["timestamp"]
```

- [ ] **Step 2: Split in `Arena.__init__` — store `train_candles`, `test_candles`. Default split 0.7 if config key present, else full-stream backwards-compat**

- [ ] **Step 3: Add `evaluate_oos(agent)` that runs the mode against `test_candles` only; expose `agent.oos_fitness`. Selection uses `oos_fitness` when present, falls back to `fitness`**

- [ ] **Step 4: Update `evolution.select_survivors` to rank on `oos_fitness` if set**

- [ ] **Step 5: Tests green, commit**
```bash
git commit -am "feat(arena): temporal 70/30 split + OOS fitness for selection"
```

---

## Phase C — Execution realism

### Task C1: Fill at next-bar open (kill intra-bar lookahead)

**Files:** `arena.py` — all 4 modes

- [ ] **Step 1: Failing test verifying decision at bar `i` fills at `candles[i+1]["open"]`**

- [ ] **Step 2: Refactor each `_run_*` to defer fill to `i+1`. Decisions computed on bar `i`, executed on bar `i+1` open. Last bar: ignore pending decisions (no execution possible)**

- [ ] **Step 3: Green**

- [ ] **Step 4: Commit**
```bash
git commit -am "fix(arena): fill trades at next-bar open (eliminates lookahead)"
```

### Task C2: Slippage + maker/taker fee model

**Files:** `arena.py`

- [ ] **Step 1: Failing test**
```python
def test_slippage_applied_on_entry_and_exit():
    # With slippage_bps=10 and maker_fee=0.02%, taker_fee=0.05%, known entry/exit must match expected
    ...
```

- [ ] **Step 2: Add to `Arena.__init__`**
```python
self.slippage_bps = self.config.get("slippage_bps", 5) / 10000
self.maker_fee = self.config.get("maker_fee_pct", 0.02) / 100
self.taker_fee = self.config.get("taker_fee_pct", 0.05) / 100
```
Apply on every fill: `fill_price = base_price * (1 + slippage_bps)` for longs entry / shorts exit, inverse otherwise. Fee = taker on market fills, maker on limit fills that rested ≥1 bar.

- [ ] **Step 3: Update grid mode — limit orders that sat ≥1 candle get maker fee; market orders (close-on-stop) get taker**

- [ ] **Step 4: Green + commit**
```bash
git commit -am "feat(arena): maker/taker fee split + configurable slippage_bps"
```

### Task C3: Funding rate for perp positions

**Files:** `arena.py`

- [ ] **Step 1: Failing test — long position held 24h on 0.01%/8h funding must lose 0.03% of notional**

- [ ] **Step 2: Add `funding_rate_per_8h_pct` config (default 0.01). Every 8 candles of held position (or pro-rata by timestamp), deduct `notional * funding_rate * direction_sign` from PnL**

- [ ] **Step 3: Green + commit**

---

## Phase D — Indicators

### Task D1: ATR(14)

**Files:** `indicators.py`, `test_indicators.py`

- [ ] **Step 1: Failing test `test_atr_period_14_positive`**

- [ ] **Step 2: Implement `atr(candles, period=14)` using Wilder smoothing; wire into `enrich_candles` → `c["atr"]`**

- [ ] **Step 3: Commit**

### Task D2: MACD(12,26,9)

- [ ] Failing test → implement `macd(prices)` returning `(line, signal, hist)` → wire into enrich → commit.

### Task D3: VWAP (session-reset at 00:00 UTC)

- [ ] Failing test → implement `vwap(candles)` with daily reset → commit.

### Task D4: OBV + Stochastic(14,3)

- [ ] One test per indicator → implement → commit.

### Task D5: Session + relative-volume fields on each candle

- [ ] Add `c["hour_utc"]`, `c["session"]` in {ASIA, LON, NY, OFF}, `c["rvol"] = volume / rolling_20_mean(volume)` → test + commit.

---

## Phase E — Agent DNA

### Task E1: Add continuous hyperparameter gene vector

**Files:** `agent.py`, `evolution.py`

- [ ] **Step 1: Failing test — two agents with same skill set but different `params` must produce different decisions**

- [ ] **Step 2: Extend `Agent.__init__` with `params: dict = None` defaulting to**
```python
{
    "rsi_buy": 30.0, "rsi_sell": 70.0,
    "dip_pct": 0.02, "pump_pct": 0.02,
    "trail_pct": 0.015, "tp_pct": 0.02, "sl_pct": 0.03,
    "ema_fast": 8, "ema_slow": 21,
    "risk_per_trade": 0.01,
    "min_hold_bars": 3,
    "atr_stop_mult": 2.0,
}
```
Make skill functions in `agent.py` read from `self.params` instead of hardcoded constants.

- [ ] **Step 3: Extend `crossover` to also mix `params` via BLX-alpha (α=0.5)**

- [ ] **Step 4: Extend `mutate` to apply Gaussian noise to each param with `rate` probability, clipped to sane bounds**

- [ ] **Step 5: Green + commit**

### Task E2: ATR-scaled stop skill

- [ ] Add skill `stop-atr-2x` that uses `c["atr"] * agent.params["atr_stop_mult"]` as stop distance → test on candles with variable volatility → commit.

### Task E3: Short-side symmetric skills

- [ ] Add `short-on-breakdown`, `short-rsi-overbought`, `cover-on-dip`, `short-ema-deathcross`, `short-trailing-1pct` → test short-only agent on down-trending candles makes positive PnL → commit.

### Task E4: Position-sizing skills

- [ ] Add `size-vol-target-atr` (risk 1% equity / ATR), `size-fixed-fractional`, `size-kelly-lite` — gate which sizing applies via agent skill set → test → commit.

### Task E5: Regime-gated voting

**Files:** `agent.py:367-382` (`decide()`)

- [ ] **Step 1: Failing test — agent with both trend and mean-rev skills should only fire mean-rev when `abs(ADX) < 25` and trend when `ADX > 25`**

- [ ] **Step 2: Rework `decide()` — classify each skill as TREND/MEAN_REV/BREAKOUT/RISK; gate vote contribution by `c["adx"]` regime**

- [ ] **Step 3: Commit**

---

## Phase F — Evolution mechanics

### Task F1: Segmented crossover (preserve trait clusters)

**Files:** `evolution.py`

- [ ] **Step 1: Failing test — after crossover, either ALL `buy-*` skills come from p1 or ALL from p2 (not mixed)**

- [ ] **Step 2: Group skill names into blocks by prefix (`buy-`, `sell-`, `short-`, `hold-`, `stop-`, `tp-`, `size-`). Crossover swaps whole blocks, not individual skills**

- [ ] **Step 3: Green + commit**

### Task F2: Mutation rate decay + exploration floor

- [ ] Add `generation` arg to `mutate`, compute `effective_rate = max(0.05, base_rate * 0.95 ** generation)` → test decay → commit.

### Task F3: Niching via fitness sharing

- [ ] Implement `fitness_sharing(agents, sigma=0.3)` — divide each agent's fitness by count of neighbors within skill-set Jaccard distance `sigma` → apply before `select_survivors` → test that two identical best-fitness agents get penalized vs two different best-fitness agents → commit.

### Task F4: Default population ≥ 50

**Files:** `server.py` UI defaults only (still allow user override)

- [ ] Update default `population_size` in server config from current (≤20) to 50. Test server starts, commit.

---

## Verification gate (post-phase)

After each phase:

1. All tests green: `pytest -v`
2. `python server.py` starts without error
3. Run one end-to-end evolution (5 gen, pop 20, 1h candles, 30 days) and confirm OOS fitness is reported and non-zero
4. `git log --oneline` shows phase-scoped commits only

After all 6 phases:

- [ ] Re-dispatch the 6-agent panel (2 quant + 2 scalper + 2 trader) with the new code — target: ≥4/6 PASS verdicts
- [ ] Update README: document walk-forward, Sharpe/DD fitness, realistic fees, ATR stops, short-side capability

---

## Out of scope (defer)

- Multi-timeframe signal composition (1h signals + 4h regime filter)
- Deflated Sharpe / White's Reality Check multiple-testing gates
- Bootstrap CI on OOS PnL
- Island model / parallel sub-populations with migration
- Daily-loss kill-switch, correlation caps (post-hoc risk overlay, not part of evolution loop)

These are real gaps but require architectural reshaping (multi-stream candles, multi-fold eval). Capture as a follow-up plan `2026-05-XX-darwin-multitf-stats.md` once Phase A-F is in.
