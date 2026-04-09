"""Scorer registry for SwingParty."""

from .volume_breakout import VolumeBreakoutScorer
from .momentum import MomentumScorer
from .vol_adj_momentum import VolAdjMomentumScorer
from .trend_strength import TrendStrengthScorer
from .relative_strength import RelativeStrengthScorer
from .adx_combo import VolumeBreakoutADX, RelativeStrengthADX

SCORER_REGISTRY = {
    "volume_breakout": VolumeBreakoutScorer,
    "momentum": MomentumScorer,
    "vol_adj_momentum": VolAdjMomentumScorer,
    "trend_strength": TrendStrengthScorer,
    "relative_strength": RelativeStrengthScorer,
    "volume_breakout_adx": VolumeBreakoutADX,
    "relative_strength_adx": RelativeStrengthADX,
}
