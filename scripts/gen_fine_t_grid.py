"""Fine T-grid around 0.05 on top of a30_t40 winner."""
import yaml, copy, os

BASE = "config/strategies/lazy_swing/eth_30m_2024_2026_hofv5_no_fe.yaml"
OUTDIR = "config/strategies/lazy_swing"

with open(BASE) as f:
    base = yaml.safe_load(f)

def make(t):
    cfg = copy.deepcopy(base)
    name = f"t{int(round(t*100)):03d}"
    cfg["backtest"]["name"] = f"ETH 30m 2024-2026 (fineT {name})"
    cfg["backtest"]["version"] = f"a30_t40 + general ER T={t} M=24"
    params = cfg["strategies"][0]["params"]
    params["fast_exit_enabled"] = True
    params["fast_exit_proactive_atr_mult"] = 0.3
    params["fast_exit_proactive_er_threshold"] = 0.40
    params["fast_exit_er_gate_period"] = 24
    params["fast_exit_er_gate_threshold"] = float(t)
    path = os.path.join(OUTDIR, f"eth_30m_fineT_{name}.yaml")
    with open(path, "w") as f:
        yaml.safe_dump(cfg, f, sort_keys=False)
    print(f"wrote {path}")

for t in (0.02, 0.03, 0.04, 0.07, 0.08, 0.12):
    make(t)
