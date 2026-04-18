"""Generate LazySwing HTML + trade CSV for ETH-PERP HoF configs into data/hall-of-fame/lazyswing/eth-perp/."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from config import Config
from controller import Controller
from reporting.lazy_swing_reporter import LazySwingReporter

HOF = ROOT / "data" / "hall-of-fame" / "lazyswing" / "eth-perp"
CONFIGS = [
    (
        HOF / "eth-perp-30m-st20-m15.yaml",
        "ETH-PERP-INTX 30m ST20/1.5",
        "eth-perp-30m-st20-m15-report-2025.html",
        "eth-perp-30m-st20-m15-trades.csv",
    ),
    (
        HOF / "eth-perp-1h-st20-m10.yaml",
        "ETH-PERP-INTX 1h ST20/1.0",
        "eth-perp-1h-st20-m10-report-2025.html",
        "eth-perp-1h-st20-m10-trades.csv",
    ),
]


def main() -> None:
    HOF.mkdir(parents=True, exist_ok=True)
    for yaml_path, display_name, html_name, trades_name in CONFIGS:
        if not yaml_path.is_file():
            print(f"Missing {yaml_path}", file=sys.stderr)
            sys.exit(1)
        cfg = Config.from_yaml(str(yaml_path))
        ctrl = Controller(cfg, output_dir=str(HOF))
        results = ctrl.run()
        r = results[0]
        src = Path(r.trade_log_path)
        dst = HOF / trades_name
        shutil.copy2(src, dst)

        source = ctrl._create_data_source()
        price_data = source.get_data(cfg.symbol, cfg.start_date, cfg.end_date)
        strat_params = cfg.strategies[0].get("params", {})
        rep = LazySwingReporter(output_dir=str(HOF))
        rep.generate(
            trade_log_path=str(dst),
            price_data=price_data,
            strategy_name=display_name,
            symbol=cfg.symbol,
            initial_cash=cfg.initial_cash,
            version=cfg.version or "",
            output_filename=html_name,
            strategy_params=strat_params,
        )
        if src.resolve() != dst.resolve():
            src.unlink(missing_ok=True)
        print(f"OK: {html_name}, {trades_name}")


if __name__ == "__main__":
    main()
