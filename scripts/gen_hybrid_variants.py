"""Generate Hybrid Idea 1+3 configs: proactive ATR-distance fast_exit
gated by 5m ER over M bars (proactive-only, doesn't touch crossed triggers)."""
import yaml, copy, os

BASE = "config/strategies/lazy_swing/eth_30m_2024_2026_hofv5_no_fe.yaml"
OUTDIR = "config/strategies/lazy_swing"

with open(BASE) as f:
    base = yaml.safe_load(f)

def make(name, version, overlay):
    cfg = copy.deepcopy(base)
    cfg["backtest"]["name"] = f"ETH 30m 2024-2026 (hybrid {name})"
    cfg["backtest"]["version"] = version
    params = cfg["strategies"][0]["params"]
    params["fast_exit_enabled"] = True
    for k, v in overlay.items():
        params[k] = v
    path = os.path.join(OUTDIR, f"eth_30m_hyb_{name}.yaml")
    with open(path, "w") as f:
        yaml.safe_dump(cfg, f, sort_keys=False)
    print(f"wrote {path}")

# Grid: ATR_mult × proactive_ER_T at M=24
for atr_mult in (0.15, 0.30, 0.50, 0.75):
    for er_t in (0.20, 0.30, 0.40):
        name = f"a{int(atr_mult*100):02d}_t{int(er_t*100):02d}"
        make(name, f"Hybrid 1+3: ATR={atr_mult} proactive_ER_T={er_t} M=24", {
            "fast_exit_proactive_atr_mult": atr_mult,
            "fast_exit_er_gate_period": 24,
            "fast_exit_proactive_er_threshold": er_t,
        })
