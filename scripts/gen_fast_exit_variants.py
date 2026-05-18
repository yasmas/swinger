"""Generate fast_exit experiment variant configs from the HOF v5 base.

Base: config/strategies/lazy_swing/eth_30m_2024_2026_hofv5_no_fe.yaml
   (HOF v5 verbatim, extended to 2024-01-01..2026-05-15)
We re-enable fast_exit and overlay variant-specific knobs.
"""
import yaml, copy, os

BASE = "config/strategies/lazy_swing/eth_30m_2024_2026_hofv5_no_fe.yaml"
OUTDIR = "config/strategies/lazy_swing"

with open(BASE) as f:
    base = yaml.safe_load(f)

def make(name, version, overlay):
    cfg = copy.deepcopy(base)
    cfg["backtest"]["name"] = f"ETH 30m 2024-2026 (fe variant {name})"
    cfg["backtest"]["version"] = version
    params = cfg["strategies"][0]["params"]
    params["fast_exit_enabled"] = True
    for k, v in overlay.items():
        params[k] = v
    path = os.path.join(OUTDIR, f"eth_30m_fe_{name}.yaml")
    with open(path, "w") as f:
        yaml.safe_dump(cfg, f, sort_keys=False)
    print(f"wrote {path}")

# Baseline (HOF v5 with fast_exit ON) — to serve as in-grid reference
make("baseline", "HOF v5 verbatim (fast_exit ON)", {})

# Idea 2: N-bar confirm composed with RVOL
make("nbar2", "Idea 2: min_bars=2 composed with RVOL", {
    "fast_exit_min_bars_with_rvol": True,
    "fast_exit_min_bars": 2,
})
make("nbar3", "Idea 2: min_bars=3 composed with RVOL", {
    "fast_exit_min_bars_with_rvol": True,
    "fast_exit_min_bars": 3,
})

# Idea 3: 5m ER confirm
for M in (24, 48):
    for T in (0.20, 0.30):
        name = f"er{M}_t{int(T*100):02d}"
        make(name, f"Idea 3: 5m ER M={M} T={T}", {
            "fast_exit_er_gate_period": M,
            "fast_exit_er_gate_threshold": T,
        })

# Idea 1: proactive ATR-distance
make("proatr03", "Idea 1: proactive atr_mult=0.3 (requires high-conviction RVOL)", {
    "fast_exit_proactive_atr_mult": 0.3,
})
make("proatr05", "Idea 1: proactive atr_mult=0.5 (requires high-conviction RVOL)", {
    "fast_exit_proactive_atr_mult": 0.5,
})
