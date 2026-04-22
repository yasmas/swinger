#!/usr/bin/env python3
"""Backfill live diagnostics.csv with flip-vol ratio fields from strategy history."""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from strategies.registry import STRATEGY_REGISTRY  # noqa: E402
from trading.strategy_runner import DIAG_COLUMNS, StrategyRunner  # noqa: E402


def _load_bot_config(bot_yaml: Path) -> dict:
    with bot_yaml.open() as f:
        return yaml.safe_load(f)


def _resolve_strategy_params(bot_yaml: Path, bot_cfg: dict) -> tuple[str, dict]:
    strat_ref = bot_cfg["strategy"]["config"]
    strat_path = Path(strat_ref)
    if not strat_path.is_absolute():
        candidate = (bot_yaml.parent / strat_ref).resolve()
        strat_path = candidate if candidate.exists() else (ROOT / strat_ref).resolve()
    with strat_path.open() as f:
        strategy_yaml = yaml.safe_load(f)
    strat = strategy_yaml["strategies"][0]
    params = dict(strat["params"])
    params["symbol"] = bot_cfg["bot"]["symbol"]
    return strat["type"], params


def _load_5m_data(data_dir: Path, symbol: str) -> pd.DataFrame:
    files = sorted(data_dir.glob(f"{symbol}-5m-*.csv"))
    if not files:
        raise FileNotFoundError(f"No 5m files found in {data_dir} for {symbol}")

    frames: list[pd.DataFrame] = []
    for file in files:
        df = pd.read_csv(file)
        ts = pd.to_datetime(df["open_time"].astype(float), unit="ms", utc=True).dt.tz_localize(None)
        frame = pd.DataFrame(
            {
                "open": df["open"].astype(float).to_numpy(),
                "high": df["high"].astype(float).to_numpy(),
                "low": df["low"].astype(float).to_numpy(),
                "close": df["close"].astype(float).to_numpy(),
                "volume": df["volume"].astype(float).to_numpy(),
            },
            index=pd.DatetimeIndex(ts, name="date"),
        )
        frames.append(frame)

    out = pd.concat(frames).sort_index()
    out = out[~out.index.duplicated(keep="last")]
    return out


def _fmt(value: object, decimals: int = 4) -> str:
    if value is None:
        return ""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return ""
    if pd.isna(v):
        return ""
    return f"{v:.{decimals}f}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("bot_yaml", help="Path to live bot YAML (e.g. data/yasmas/etp_live.yaml)")
    args = ap.parse_args()

    bot_yaml = (ROOT / args.bot_yaml).resolve() if not Path(args.bot_yaml).is_absolute() else Path(args.bot_yaml)
    bot_cfg = _load_bot_config(bot_yaml)
    data_dir = (ROOT / bot_cfg["bot"]["data_dir"]).resolve()
    diagnostics_path = data_dir / "diagnostics.csv"
    if not diagnostics_path.exists():
        raise FileNotFoundError(f"Missing diagnostics file: {diagnostics_path}")

    StrategyRunner._migrate_diagnostics_header(  # type: ignore[attr-defined]
        diagnostics_path,
        diagnostics_path.open().readline().strip().split(","),
    )

    strategy_type, strategy_params = _resolve_strategy_params(bot_yaml, bot_cfg)
    strategy_cls = STRATEGY_REGISTRY[strategy_type]
    strategy = strategy_cls(strategy_params)
    df_5m = _load_5m_data(data_dir, bot_cfg["bot"]["symbol"])
    strategy.prepare(df_5m)

    with diagnostics_path.open(newline="") as f:
        rows = list(csv.DictReader(f))

    updated = 0
    for row in rows:
        try:
            hourly_idx = int(row.get("hourly_idx") or "")
        except ValueError:
            continue

        info = strategy._flip_vol_ratio_info(hourly_idx)  # noqa: SLF001
        row["flip_vol_ratio"] = _fmt(info.get("ratio"))
        row["flip_vol_ratio_threshold"] = _fmt(
            info.get("active_ratio_min") if info.get("active_ratio_min") is not None else info.get("ratio_min")
        )
        held_stop = row.get("held_flip_stop_pct") or ""
        if not held_stop and row.get("reason") in {
            "st_flip_ratio_rejected_hold",
            "holding_long_rejected_flip",
            "holding_short_rejected_flip",
        }:
            held_stop = _fmt(info.get("active_stop_pct"))
        row["held_flip_stop_pct"] = held_stop
        row["flip_vol_ratio_regime_mode"] = str(info.get("regime_mode") or "")
        row["flip_vol_ratio_regime_weight"] = _fmt(info.get("regime_weight"))
        updated += 1

    with diagnostics_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=DIAG_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Backfilled {updated} diagnostics rows: {diagnostics_path}")


if __name__ == "__main__":
    main()
