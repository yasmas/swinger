# LazySwing ETH CMF Flip-Gate Experiment

**Branch**: `lazy-swing-cmf-gate`
**Base**: ETH HOF 30m (ST atr=25 / mult=1.75, `config/strategies/lazy_swing/eth_30m_hof.yaml`)
**Question**: Can a CMF filter on ST flips lift ETH LazySwing returns / winrate?

## Design

Gate is armed when Supertrend wants to flip. On every 5m bar until flip fires or N hourly bars pass:

- Compute CMF on-demand over last `cmf_period` hourly bars + synthetic partial bucket from current 5m window.
- **VETO mode (level check)**: suppress flip only if CMF still shows extreme opposing pressure.
  - `long` flip confirmed if `cmf >= cmf_level_min`
  - `short` flip confirmed if `cmf <= -cmf_level_min`
- If ST flips back within window → cancel (whipsaw absorbed).
- If N bars pass without confirmation → cancel.

Negative `cmf_level_min` (e.g. -0.15) = veto only extreme contrarian CMF. Positive = require strong confirming flow.

Implementation: `src/strategies/lazy_swing.py::_cmf_gate_evaluate` and `_resolve_flip`.

## In-Sample Results (ETH 2025-01-01 → 2026-01-01)

Baseline (no gate): **+389.98%**, WR 38.7%, Sharpe +2.07, DD -28.3%.

Slope-based gate (early version): all configs lost to baseline — removed winners.
Positive level threshold: still underperformed.
**Negative (veto) thresholds: won.**

Stage-2 sweep (cmf_period × level × N window):

| Config | Return | Lift |
|---|---|---|
| **p15_L-0.15_n4** | **+438.48%** | +48.5pp |
| p15_L-0.20_n4 | +433.54% | +43.6pp |
| p15_L-0.20_n12 | +430.61% | +40.6pp |
| baseline | +389.98% | — |

Stage-3 ST sweep with gate held fixed confirmed HOF ST params (atr=25, mult=1.75) are optimal for this asset/timeframe.

## Out-of-Sample Results

**2024 OOS** (2023-08-31 → 2024-12-31, 16 months):

| Config | Return | WR | Sharpe | DD |
|---|---|---|---|---|
| p15_L-0.15_n4 | +37.87% | 35.2% | +0.55 | -49.7% |
| baseline | +34.14% | 35.1% | +0.52 | -49.6% |
| p15_L-0.20_n4 | +33.98% | 35.1% | +0.52 | -48.3% |
| p15_L-0.20_n12 | +32.86% | 35.1% | +0.51 | -48.6% |

**2026 Forward OOS** (2026-01-01 → 2026-04-17, 3.5 months):

| Config | Return | WR | Sharpe | DD |
|---|---|---|---|---|
| p15_L-0.20_n4 | +40.54% | 38.1% | +1.65 | -24.6% |
| p15_L-0.20_n12 | +40.54% | 38.1% | +1.65 | -24.6% |
| baseline | +38.16% | 38.6% | +1.58 | -23.7% |
| p15_L-0.15_n4 | +35.96% | 37.2% | +1.51 | -25.1% |

## Conclusion — **Do not promote**

1. **In-sample lift (+48pp) collapses OOS** to +3.7pp (2024) and −2.2pp (2026). Likely curve-fit to 2025 regime.
2. **Rankings flip between OOS periods**: stage-2 champ L=-0.15 is best in 2024, worst in 2026. L=-0.20 is opposite.
3. **2024 DD is catastrophic (~−49%)** across all variants — the underlying strategy has a bigger regime-dependence problem than the gate can solve.
4. **Next step**: drop the CMF gate. Investigate why 2024 vol regime breaks the strategy before adding more filters.

## Artifacts

- Grid scripts: `scripts/grid_search_lazyswing_cmf_gate.py` (in-sample stages), `scripts/oos_lazyswing_cmf_gate.py` (OOS).
- Reports: `reports/grid-cmf-veto-eth-hof-stage*/`, `reports/oos-cmf-gate-eth-hof/summary.csv`.
- Data: `data/ETH-PERP-INTX-5m-2023-2024.csv` (140,517 rows, downloaded for OOS).
