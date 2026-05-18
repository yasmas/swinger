"""Search smaller general-ER thresholds on the crossed (regular) fast_exit.

Group A: HOF v5 baseline + general ER at T ∈ {0.05, 0.10, 0.15}.
Group B: a30_t40 winner + general ER at T ∈ {0.05, 0.10, 0.15}.

General ER threshold applies to ALL fast_exits (including crossed).
Proactive ER threshold (in group B) is independent and stays at 0.40.
M=24 throughout (M=48 already shown worse).
"""
import yaml, copy, os

BASE = "config/strategies/lazy_swing/eth_30m_2024_2026_hofv5_no_fe.yaml"
OUTDIR = "config/strategies/lazy_swing"

with open(BASE) as f:
    base = yaml.safe_load(f)

def make(name, version, overlay):
    cfg = copy.deepcopy(base)
    cfg["backtest"]["name"] = f"ETH 30m 2024-2026 (gerS {name})"
    cfg["backtest"]["version"] = version
    params = cfg["strategies"][0]["params"]
    params["fast_exit_enabled"] = True
    for k, v in overlay.items():
        params[k] = v
    path = os.path.join(OUTDIR, f"eth_30m_gerS_{name}.yaml")
    with open(path, "w") as f:
        yaml.safe_dump(cfg, f, sort_keys=False)
    print(f"wrote {path}")

# Group A: baseline + general ER only
for t in (0.05, 0.10, 0.15):
    make(f"A_g{int(t*100):02d}", f"Baseline + general ER T={t} M=24", {
        "fast_exit_er_gate_period": 24,
        "fast_exit_er_gate_threshold": t,
    })

# Group B: a30_t40 winner + general ER
for t in (0.05, 0.10, 0.15):
    make(f"B_g{int(t*100):02d}", f"a30_t40 + general ER T={t} M=24", {
        "fast_exit_proactive_atr_mult": 0.3,
        "fast_exit_proactive_er_threshold": 0.40,
        "fast_exit_er_gate_period": 24,
        "fast_exit_er_gate_threshold": t,
    })
