"""SwingTrendState — all mutable state for the swing trend strategy.

The strategy accesses state via self.state.field_name.
Serialization: state.to_dict() → plain dict (YAML/JSON safe).
Deserialization: SwingTrendState.from_dict(d) → new instance.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from datetime import date


@dataclass
class SwingTrendState:
    """Holds ALL mutable state that changes during on_bar() execution.

    Config (immutable after __init__) and precomputed indicators (rebuilt on
    prepare()) live on the strategy object itself, NOT here.
    """

    # --- Position state ---
    in_position: bool = False
    direction: str | None = None          # "LONG" | "SHORT" | None
    entry_price: float | None = None
    entry_hourly_idx: int | None = None
    hard_stop: float | None = None
    peak_price: float | None = None       # max price since entry (MFE tracking)
    trough_price: float | None = None     # min price since entry (MAE tracking)

    # --- Entry trigger & confirmation ---
    entry_trigger: str | None = None      # which trigger fired
    st_confirmed: bool = True             # False for MACD/override entries until ST agrees
    is_macd_reentry: bool = False         # True if entry was MACD trend re-entry

    # --- MACD re-entry tracking ---
    last_macd_exit_profitable: bool = False
    macd_bars_since_exit: int = 999
    pending_macd_cross_bars: int = 0      # cross confirmation countdown

    # --- RSI & HMA tracking ---
    prev_rsi_val: float | None = None     # for overbought/oversold reversal detection
    hma_against_count: int = 0            # consecutive bars HMA against trade

    # --- Daily circuit breaker ---
    day_start_value: float | None = None
    daily_stop_hit: bool = False
    current_day: date | None = None       # serialized as ISO string

    # --- Cooldown ---
    last_exit_hourly_idx: int = -999

    # --- Bar tracking ---
    bar_idx: int = 0
    prev_hourly_idx: int = -1

    def to_dict(self) -> dict:
        """Serialize to a plain dict safe for YAML/JSON."""
        d = dataclasses.asdict(self)
        # Convert date to ISO string
        if d["current_day"] is not None:
            d["current_day"] = d["current_day"].isoformat()
        # Convert numpy scalars to Python natives (YAML can't serialize numpy)
        for k, v in d.items():
            if hasattr(v, 'item'):  # numpy scalar
                d[k] = v.item()
        return d

    @classmethod
    def from_dict(cls, d: dict) -> SwingTrendState:
        """Deserialize from a dict. Missing keys get dataclass defaults."""
        # Parse current_day back to date
        if "current_day" in d and d["current_day"] is not None:
            if isinstance(d["current_day"], str):
                d = dict(d)  # don't mutate caller's dict
                d["current_day"] = date.fromisoformat(d["current_day"])

        # Only pass keys that are valid fields (ignore stale keys)
        valid_fields = {f.name for f in dataclasses.fields(cls)}
        filtered = {k: v for k, v in d.items() if k in valid_fields}
        return cls(**filtered)
