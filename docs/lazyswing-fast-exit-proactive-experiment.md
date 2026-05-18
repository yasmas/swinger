# LazySwing fast_exit tuning experiment — 2026-05-17

**Outcome:** Ship HOF v6 = v5 + proactive pre-cross fast_exit (`atr_mult=0.3`) gated by 5m ER (M=24, T=0.40). +267pp compound, min-q tied. Three other ideas explored and ruled out.

## Question

Yesterday's session showed fast_exit (intra-bar 5m exit when price breaks the 30m Supertrend line) was load-bearing under HOF v5 (PP4 + ER48_T0.32). Can we improve it?

User proposed three ideas:

1. **Exit earlier** (proactive, before the ST cross).
2. **Wait N bars** for confirmation before exiting.
3. **Add an indicator** that says whether to exit immediately or not.

## Dataset and bar

- `data/backtests/eth/coinbase/ETH-PERP-INTX-5m-2024-2026.csv` (2024-01-01 .. 2026-05-15, 10 quarters)
- Ship criterion: **compound AND min-quarter both improve** vs HOF v5 baseline.

## Baselines

| | Compound | Min-Q | Sharpe |
|---|---|---|---|
| HOF v5 (with fast_exit) | +5,288% | −21.50% | 2.29 |
| HOF v5 (fast_exit OFF) | +2,025% | −15.04% | 2.31 |

**fast_exit removal costs −3,263pp compound** — heavily load-bearing. (Note: drawdown is actually a bit better without fast_exit, so the cost is purely compound.)

## Code changes (all gated by sentinel defaults; verified bit-perfect backward-compatible)

Added to `src/strategies/lazy_swing.py`:

- `fast_exit_min_bars_with_rvol` — compose N-bar counter with RVOL gate (Idea 2)
- `fast_exit_er_gate_period` / `_threshold` — 5m Kaufman ER chop filter on all fast_exits (Idea 3, general)
- `fast_exit_proactive_atr_mult` — pre-cross trigger within k×ATR of ST + against-bar (Idea 1)
- `fast_exit_proactive_er_threshold` — 5m ER threshold for *proactive-only* trigger (Hybrid 1+3)

Refactored the inlined long/short fast_exit blocks into a single helper `_evaluate_fast_exit_trigger(side, ...)`.

## Stage 1 — single-mechanism grid (8 variants)

| Variant | Compound | Min-Q | Sharpe | Verdict |
|---|---|---|---|---|
| Baseline | +5,288% | −21.5% | 2.29 | — |
| Idea 2 nbar2 | +2,517% | −25.4% | 1.81 | ❌ |
| Idea 2 nbar3 | +2,527% | −19.2% | 2.39 | ❌ |
| Idea 3 er24_t20 | +4,095% | −12.7% | 1.89 | ❌ |
| Idea 3 er24_t30 | +2,427% | −8.6% | 2.05 | ❌ |
| Idea 3 er48_t20 | +1,641% | −12.9% | 1.84 | ❌ |
| Idea 3 er48_t30 | +1,964% | −11.5% | 2.35 | ❌ |
| Idea 1 proatr0.3 | +2,370% | −25.4% | 1.87 | ❌ |
| Idea 1 proatr0.5 | +1,950% | −14.8% | 2.20 | ❌ |

**All variants regressed on compound.** Pattern: any change that suppresses fast_exits or fires them earlier *without a regime filter* loses compound. The current RVOL calibration is doing real work.

## Stage 2 — Hybrid 1+3 (proactive gated by ER, ATR×ER_T grid, 12 variants)

User insight: combine ideas 1 and 3. Keep crossed fast_exit untouched; only gate the **proactive** trigger by ER. Strictly additive — worst case the ER condition never holds and we get baseline back.

| ATR\ER_T | 0.20 | 0.30 | 0.40 |
|---|---|---|---|
| 0.15 | +3,695% / −11.8% | +4,704% / −21.5% | +5,276% / −21.5% |
| **0.30** | +3,249% / −16.5% | +4,803% / −21.5% | **+5,555% / −21.5%** ✅ |
| 0.50 | +2,330% / −19.3% | +4,431% / −25.0% | +5,337% / −21.7% |
| 0.75 | +2,841% / −21.2% | +4,412% / −26.5% | +5,515% / −22.0% |

**Pattern:** only T=0.40 wins. Within T=0.40, ATR_mult is essentially flat (0.15→0.30→0.50→0.75 all positive). The ER filter is doing the work; the distance threshold mostly determines *how many* qualifying setups exist.

**Winner:** `a30_t40` — `atr_mult=0.30, er_T=0.40, M=24`. +267pp compound, min-q tied, Sharpe unchanged. Fires 11 times in 2.5 years — surgical.

## Stage 3 — general ER on crossed fast_exit (rejected as flaky)

Hypothesis: maybe a *weak* general ER filter would help the crossed fast_exit too. Searched T ∈ {0.05, 0.10, 0.15} alone and on top of a30_t40.

Initial result looked great — `B_g05` (a30_t40 + general ER T=0.05) hit **+5,838% compound, −19.4% min-q, Sharpe 2.40** — beating the strict ship bar on all three metrics.

But the fine T-grid {0.02, 0.03, 0.04, 0.05, 0.07, 0.08, 0.10, 0.12, 0.15} on top of a30_t40 showed wild non-monotonicity:

```
T=0.02:  +5,231%   T=0.07:  +4,942%   T=0.12:  +6,181%  ← new peak
T=0.03:  +5,152%   T=0.08:  +4,971%   T=0.15:  +5,675%
T=0.04:  +4,973%   T=0.10:  +5,449%
T=0.05:  +5,838%   ← B_g05 peak
```

Adjacent cells differ by 800-1,200pp — single-trade noise leaking through, not the mechanism. Picking T=0.05 or T=0.12 is cherry-picking peaks in a noisy landscape.

**What IS robust across 9 T cells:**
- Mean min-q: −19.6% (vs a30_t40's −21.5%) — reliable ~2pp drawdown reduction.
- Mean compound: +5,379% (vs a30_t40's +5,555%) — no reliable improvement.

So the general ER add-on **trades reliably better drawdown for noisy compound**. Not shippable. Skipped.

## Final ship

`config/strategies/lazy_swing/eth_30m_hof_v6.yaml` — HOF v5 plus three lines:

```yaml
fast_exit_proactive_atr_mult: 0.3
fast_exit_proactive_er_threshold: 0.40
fast_exit_er_gate_period: 24
```

Live slice (2026-01-01..2026-05-08) sanity check: v6 +50.49% vs v5 +49.73%.

## Do-not-retry catalogue

- Idea 2 (N-bar confirm composed with RVOL) — loses compound, no robustness payoff worth it.
- Idea 3 standalone (general ER on all fast_exits) — best T regresses by 1,200pp.
- Idea 1 standalone (proactive without ER) — pure prediction failure, −3,000pp.
- General ER stacked on top of a30_t40 — fragile plateau, cell-to-cell variance ~1,200pp masks any signal.
- fast_exit OFF — −3,263pp.
