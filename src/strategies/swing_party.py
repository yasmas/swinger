"""SwingParty — multi-asset rotation strategy using LazySwing signals.

Manages N assets with up to I simultaneous positions. Each asset runs its
own LazySwing instance for signal generation. When more assets want entry
than slots are available, a pluggable scorer ranks candidates and can
evict the weakest holding.
"""

import logging
from dataclasses import dataclass, field

import pandas as pd

from .lazy_swing import LazySwingStrategy
from .base import Action, ActionType, PortfolioView
from .scorers.registry import SCORER_REGISTRY
from .scorers.relative_strength import RelativeStrengthScorer
from .scorers.adx_combo import RelativeStrengthADX

logger = logging.getLogger(__name__)


@dataclass
class EvictionEvent:
    """Tracks one eviction for forward PnL analysis."""
    date: pd.Timestamp
    evicted_symbol: str
    evicted_direction: str
    evicted_price: float       # price at eviction
    evicted_score: float
    entered_symbol: str
    entered_direction: str
    entered_price: float       # price at entry
    entered_score: float
    # Resolved later when each asset hits its next ST flip
    evicted_exit_price: float = 0.0
    evicted_exit_date: pd.Timestamp = None
    entered_exit_price: float = 0.0
    entered_exit_date: pd.Timestamp = None
    resolved: bool = False


class EvictionTracker:
    """Tracks eviction events and resolves forward PnL post-hoc.

    Resolution is done after the backtest by scanning each asset's Supertrend
    data forward from the eviction timestamp to find the next ST flip. This
    avoids the problem of the evicted strategy being reset mid-backtest.
    """

    def __init__(self):
        self.events: list[EvictionEvent] = []

    def record(self, event: EvictionEvent) -> None:
        self.events.append(event)

    def on_exit(self, symbol: str, price: float, date: pd.Timestamp) -> None:
        """Called when a symbol exits via ST flip. Resolves entered-side events."""
        for ev in self.events:
            if ev.entered_exit_price == 0.0 and ev.entered_symbol == symbol and ev.date < date:
                ev.entered_exit_price = price
                ev.entered_exit_date = date
                self._check_resolved(ev)

    def resolve_from_data(self, datasets: dict[str, pd.DataFrame],
                          resample_interval: str, st_atr_period: int,
                          st_multiplier: float) -> None:
        """Resolve all eviction events post-hoc using ST flips from the data.

        For each event, scans the evicted and entered asset's resampled data
        forward from the eviction timestamp to find the next ST direction change.
        """
        from .intraday_indicators import compute_supertrend

        # Pre-compute ST for each asset
        st_cache: dict[str, pd.Series] = {}
        close_cache: dict[str, pd.Series] = {}
        for symbol, df in datasets.items():
            resampled = df.resample(resample_interval).agg({
                "open": "first", "high": "max", "low": "min",
                "close": "last", "volume": "sum",
            }).dropna()
            if len(resampled) < st_atr_period * 3:
                continue
            _, st_bullish = compute_supertrend(
                resampled["high"], resampled["low"], resampled["close"],
                st_atr_period, st_multiplier,
            )
            st_cache[symbol] = st_bullish
            close_cache[symbol] = resampled["close"]

        for ev in self.events:
            # Resolve evicted side: find next ST flip after eviction date
            if ev.evicted_exit_price == 0.0:
                ev.evicted_exit_price, ev.evicted_exit_date = self._find_next_flip(
                    ev.evicted_symbol, ev.evicted_direction, ev.date,
                    st_cache, close_cache,
                )

            # Resolve entered side: find next ST flip after entry
            if ev.entered_exit_price == 0.0:
                ev.entered_exit_price, ev.entered_exit_date = self._find_next_flip(
                    ev.entered_symbol, ev.entered_direction, ev.date,
                    st_cache, close_cache,
                )

            self._check_resolved(ev)

    def _find_next_flip(self, symbol: str, direction: str, after_date: pd.Timestamp,
                        st_cache: dict, close_cache: dict) -> tuple[float, pd.Timestamp]:
        """Find the close price at the next ST flip for a symbol after a date."""
        if symbol not in st_cache:
            return 0.0, None

        st_bullish = st_cache[symbol]
        closes = close_cache[symbol]

        # Find bars after the eviction date
        mask = st_bullish.index > after_date
        future_st = st_bullish[mask]
        future_close = closes[mask]

        if future_st.empty:
            # Use last available price
            return float(closes.iloc[-1]), closes.index[-1]

        # Current direction: long = bullish, short = bearish
        is_bullish = (direction == "long")

        for i in range(len(future_st)):
            if pd.isna(future_st.iloc[i]):
                continue
            if bool(future_st.iloc[i]) != is_bullish:
                # ST flipped — this is the exit point
                return float(future_close.iloc[i]), future_close.index[i]

        # No flip found — use last price
        return float(future_close.iloc[-1]), future_close.index[-1]

    def _check_resolved(self, ev: EvictionEvent) -> None:
        if ev.evicted_exit_price > 0 and ev.entered_exit_price > 0:
            ev.resolved = True

    def force_resolve_at_end(self, final_prices: dict[str, float], end_date: pd.Timestamp) -> None:
        """Resolve any remaining open events with final prices."""
        for ev in self.events:
            if ev.evicted_exit_price == 0.0 and ev.evicted_symbol in final_prices:
                ev.evicted_exit_price = final_prices[ev.evicted_symbol]
                ev.evicted_exit_date = end_date
            if ev.entered_exit_price == 0.0 and ev.entered_symbol in final_prices:
                ev.entered_exit_price = final_prices[ev.entered_symbol]
                ev.entered_exit_date = end_date
            self._check_resolved(ev)

    def compute_compound_pnl(self) -> dict:
        """Compute compound PnL for entered vs evicted across all resolved events.

        Returns dict with:
            entered_compound_pnl: compound return of all entered trades
            evicted_compound_pnl: compound return of all evicted (missed) trades
            net_compound_pnl: entered - evicted (positive = scorer added value)
            n_events: total events
            n_resolved: resolved events
            n_correct: events where entered PnL > evicted PnL
            events: list of per-event details
        """
        entered_compound = 1.0
        evicted_compound = 1.0
        n_resolved = 0
        n_correct = 0
        event_details = []

        for ev in self.events:
            if not ev.resolved:
                continue
            # Skip degenerate events with zero prices
            if ev.entered_price <= 0 or ev.entered_exit_price <= 0 \
                    or ev.evicted_price <= 0 or ev.evicted_exit_price <= 0:
                continue
            n_resolved += 1

            # Compute PnL for each leg
            if ev.entered_direction == "long":
                entered_ret = (ev.entered_exit_price / ev.entered_price) - 1
            else:
                entered_ret = (ev.entered_price / ev.entered_exit_price) - 1

            if ev.evicted_direction == "long":
                evicted_ret = (ev.evicted_exit_price / ev.evicted_price) - 1
            else:
                evicted_ret = (ev.evicted_price / ev.evicted_exit_price) - 1

            entered_compound *= (1 + entered_ret)
            evicted_compound *= (1 + evicted_ret)

            if entered_ret > evicted_ret:
                n_correct += 1

            event_details.append({
                "date": str(ev.date),
                "evicted": ev.evicted_symbol,
                "entered": ev.entered_symbol,
                "entered_ret_pct": round(entered_ret * 100, 2),
                "evicted_ret_pct": round(evicted_ret * 100, 2),
                "diff_pct": round((entered_ret - evicted_ret) * 100, 2),
            })

        return {
            "entered_compound_pnl": round((entered_compound - 1) * 100, 2),
            "evicted_compound_pnl": round((evicted_compound - 1) * 100, 2),
            "net_compound_pnl": round((entered_compound - evicted_compound) * 100, 2),
            "n_events": len(self.events),
            "n_resolved": n_resolved,
            "n_correct": n_correct,
            "accuracy": round(n_correct / n_resolved * 100, 1) if n_resolved > 0 else 0,
            "events": event_details,
        }


class SwingPartyCoordinator:
    """Collects LazySwing signals across N assets, manages I rotation slots."""

    display_name = "SwingParty"

    def __init__(self, config: dict):
        self.max_positions = config.get("max_positions", 3)
        self.resample_interval = config.get("resample_interval", "1h")
        self.eviction_cooldown_bars = config.get("eviction_cooldown_bars", 0)

        # Build per-asset LazySwing instances
        self.assets = config.get("assets", [])
        self.strategies: dict[str, LazySwingStrategy] = {}
        for symbol in self.assets:
            strat_config = {
                "symbol": symbol,
                "resample_interval": self.resample_interval,
                "supertrend_atr_period": config.get("supertrend_atr_period", 10),
                "supertrend_multiplier": config.get("supertrend_multiplier", 2.0),
                "hmacd_fast": config.get("hmacd_fast", 24),
                "hmacd_slow": config.get("hmacd_slow", 51),
                "hmacd_signal": config.get("hmacd_signal", 12),
                "cost_per_trade_pct": config.get("cost_per_trade_pct", 0.05),
            }
            self.strategies[symbol] = LazySwingStrategy(strat_config)

        # Build scorer
        scorer_config = config.get("scorer", {"type": "volume_breakout", "params": {}})
        scorer_cls = SCORER_REGISTRY[scorer_config["type"]]
        self.scorer = scorer_cls(scorer_config.get("params", {}))

        # Slot tracking: symbol -> {direction, entry_price, score, entry_bar}
        self.slots: dict[str, dict] = {}

        # Eviction tracking
        self.eviction_tracker = EvictionTracker()

    def prepare(self, datasets: dict[str, pd.DataFrame]) -> None:
        """Call prepare() on each LazySwing with its full dataset."""
        for symbol, data in datasets.items():
            if symbol in self.strategies:
                self.strategies[symbol].prepare(data)

        # Wire universe data for RelativeStrengthScorer
        if isinstance(self.scorer, (RelativeStrengthScorer, RelativeStrengthADX)):
            self.scorer.set_universe_data(datasets)

    def warmup_bar(
        self,
        date: pd.Timestamp,
        rows: dict[str, pd.Series],
        datasets_so_far: dict[str, pd.DataFrame],
        is_last_bar: bool,
    ) -> None:
        """Advance LazySwing bar/ST state before the simulated start_date (no trades or slots)."""
        if isinstance(self.scorer, (RelativeStrengthScorer, RelativeStrengthADX)):
            self.scorer.set_universe_data(datasets_so_far)

        for symbol, row in rows.items():
            if symbol not in self.strategies:
                continue
            df = datasets_so_far.get(symbol)
            if df is None:
                continue
            self.strategies[symbol].warmup_bar(date, row, df, is_last_bar)

    def on_bar(self, date: pd.Timestamp, rows: dict[str, pd.Series],
               datasets_so_far: dict[str, pd.DataFrame],
               is_last_bar: bool, portfolio) -> list[tuple[str, Action]]:
        """Process one timestamp across all assets."""
        # Force-close all open positions on the last bar of the backtest
        if is_last_bar:
            close_actions = []
            for sym, slot in list(self.slots.items()):
                pos = portfolio.positions.get(sym)
                short = portfolio.short_positions.get(sym)
                if pos and pos.quantity > 0:
                    close_actions.append((sym, Action(
                        ActionType.SELL, pos.quantity,
                        {"exit_reason": "last_bar"},
                    )))
                elif short and short.quantity > 0:
                    close_actions.append((sym, Action(
                        ActionType.COVER, short.quantity,
                        {"exit_reason": "last_bar"},
                    )))
            if close_actions:
                self.slots.clear()
                return close_actions

        # Update universe data for RelativeStrengthScorer
        if isinstance(self.scorer, (RelativeStrengthScorer, RelativeStrengthADX)):
            self.scorer.set_universe_data(datasets_so_far)

        # Collect current prices for portfolio valuation
        current_prices = {}
        for sym in rows:
            current_prices[sym] = rows[sym]["close"]
        for sym in list(portfolio.positions.keys()) + list(portfolio.short_positions.keys()):
            if sym not in current_prices and sym in datasets_so_far:
                df = datasets_so_far[sym]
                if not df.empty:
                    current_prices[sym] = df.iloc[-1]["close"]

        total_value = portfolio.total_value(current_prices)
        slot_cash = total_value / self.max_positions

        # Phase 1: Run each LazySwing, capture proposed actions
        proposals = {}
        for symbol, row in rows.items():
            if symbol not in self.strategies:
                continue

            strategy = self.strategies[symbol]
            saved_state = strategy.export_state()

            pos = portfolio.positions.get(symbol)
            short = portfolio.short_positions.get(symbol)
            synthetic_pv = PortfolioView(
                cash=slot_cash,
                position_qty=pos.quantity if pos else 0.0,
                position_avg_cost=pos.avg_cost if pos else 0.0,
                short_qty=short.quantity if short else 0.0,
                short_avg_cost=short.avg_cost if short else 0.0,
            )

            data_so_far = datasets_so_far[symbol]
            action = strategy.on_bar(date, row, data_so_far, is_last_bar, synthetic_pv)
            proposals[symbol] = (action, saved_state)

        # Phase 2: Separate exits and entries
        exits = []
        entries = []

        for symbol, (action, saved_state) in proposals.items():
            if action.action in (ActionType.SELL, ActionType.COVER):
                exits.append((symbol, action, saved_state))
            elif action.action in (ActionType.BUY, ActionType.SHORT):
                entries.append((symbol, action, saved_state))

        result_actions = []

        # Phase 3: Execute all exits first (frees slots)
        for symbol, action, _saved_state in exits:
            if symbol in self.slots:
                del self.slots[symbol]
            result_actions.append((symbol, action))
            # Notify eviction tracker of exit (resolves pending events)
            price = rows[symbol]["close"] if symbol in rows else 0
            self.eviction_tracker.on_exit(symbol, price, date)

        # Phase 4: Score entries, sort by score descending
        scored_entries = []
        for symbol, action, saved_state in entries:
            direction = "long" if action.action == ActionType.BUY else "short"
            score = self.scorer.score(
                symbol, datasets_so_far[symbol], direction, self.resample_interval
            )
            scored_entries.append((score, symbol, action, saved_state, direction))

        scored_entries.sort(key=lambda x: x[0], reverse=True)

        def _px(sym: str) -> float:
            if sym in rows:
                return float(rows[sym]["close"])
            return float(current_prices.get(sym, 0.0) or 0.0)

        # Cash after strategy exits (phase 3), before any fills this bar — used to size
        # multiple entries without oversubscribing the same portfolio.cash twice.
        work_cash = float(portfolio.cash)
        for sym, act in result_actions:
            px = _px(sym)
            if px <= 0:
                continue
            if act.action == ActionType.SELL:
                work_cash += act.quantity * px
            elif act.action == ActionType.COVER:
                work_cash -= act.quantity * px

        # Phase 5: Fill free slots or evict weakest
        free_slots = self.max_positions - len(self.slots)

        for score, symbol, action, saved_state, direction in scored_entries:
            if free_slots > 0:
                self.slots[symbol] = {
                    "direction": direction,
                    "entry_price": rows[symbol]["close"],
                    "score": score,
                    "entry_date": date,
                }
                close = float(rows[symbol]["close"])
                available = min(slot_cash, work_cash)
                qty = available * 0.9999 / close
                if qty > 0:
                    new_action = Action(action.action, qty, {
                        **action.details,
                        "score": round(score, 4),
                        "slot": len(self.slots),
                    })
                    result_actions.append((symbol, new_action))
                    if action.action == ActionType.BUY:
                        work_cash -= qty * close
                    else:
                        work_cash += qty * close
                    free_slots -= 1
                else:
                    self.strategies[symbol].import_state(saved_state)
                    del self.slots[symbol]
            else:
                if not self.slots:
                    self.strategies[symbol].import_state(saved_state)
                    continue

                # Re-score all holdings, find globally weakest
                resample_td = pd.Timedelta(self.resample_interval)
                weakest_sym = None
                weakest_score = float("inf")
                weakest_protected = False
                for held_sym, slot_info in self.slots.items():
                    if held_sym not in datasets_so_far:
                        continue
                    h_score = self.scorer.score_holding(
                        held_sym, datasets_so_far[held_sym],
                        slot_info["direction"], self.resample_interval
                    )
                    slot_info["score"] = h_score
                    if h_score < weakest_score:
                        weakest_score = h_score
                        weakest_sym = held_sym
                        if self.eviction_cooldown_bars > 0:
                            bars_held = (date - slot_info["entry_date"]) / resample_td
                            weakest_protected = bars_held < self.eviction_cooldown_bars
                        else:
                            weakest_protected = False

                if weakest_sym and score > weakest_score and not weakest_protected:
                    evicted_slot = self.slots.pop(weakest_sym)
                    evict_price = current_prices.get(weakest_sym, 0)

                    # Record eviction event for PnL tracking
                    eviction_event = EvictionEvent(
                        date=date,
                        evicted_symbol=weakest_sym,
                        evicted_direction=evicted_slot["direction"],
                        evicted_price=evict_price,
                        evicted_score=weakest_score,
                        entered_symbol=symbol,
                        entered_direction=direction,
                        entered_price=rows[symbol]["close"],
                        entered_score=score,
                    )
                    self.eviction_tracker.record(eviction_event)

                    # Generate eviction exit action
                    evicted_pos = portfolio.positions.get(weakest_sym)
                    evicted_short = portfolio.short_positions.get(weakest_sym)
                    if evicted_pos and evicted_pos.quantity > 0:
                        evict_action = Action(ActionType.SELL, evicted_pos.quantity, {
                            "exit_reason": "evicted",
                            "evicted_by": symbol,
                            "evicted_score": round(weakest_score, 4),
                            "new_score": round(score, 4),
                        })
                        result_actions.append((weakest_sym, evict_action))
                    elif evicted_short and evicted_short.quantity > 0:
                        evict_action = Action(ActionType.COVER, evicted_short.quantity, {
                            "exit_reason": "evicted",
                            "evicted_by": symbol,
                            "evicted_score": round(weakest_score, 4),
                            "new_score": round(score, 4),
                        })
                        result_actions.append((weakest_sym, evict_action))

                    if weakest_sym in self.strategies:
                        self.strategies[weakest_sym].reset_position()

                    ev_px = _px(weakest_sym)
                    if evicted_pos and evicted_pos.quantity > 0 and ev_px > 0:
                        work_cash += evicted_pos.quantity * ev_px
                    elif evicted_short and evicted_short.quantity > 0 and ev_px > 0:
                        work_cash -= evicted_short.quantity * ev_px

                    close = float(rows[symbol]["close"])
                    available = min(slot_cash, work_cash)
                    qty = available * 0.9999 / close
                    if qty > 0:
                        self.slots[symbol] = {
                            "direction": direction,
                            "entry_price": close,
                            "score": score,
                            "entry_date": date,
                        }
                        new_action = Action(action.action, qty, {
                            **action.details,
                            "score": round(score, 4),
                            "evicted": weakest_sym,
                            "slot": len(self.slots),
                        })
                        result_actions.append((symbol, new_action))
                        if action.action == ActionType.BUY:
                            work_cash -= qty * close
                        else:
                            work_cash += qty * close
                    else:
                        self.strategies[symbol].import_state(saved_state)
                else:
                    self.strategies[symbol].import_state(saved_state)

        return result_actions

    def force_close_symbol(self, symbol: str, portfolio) -> list[tuple[str, Action]]:
        """Force-close a symbol's position (e.g. data gap). Returns actions."""
        actions = []
        if symbol in self.slots:
            del self.slots[symbol]

        pos = portfolio.positions.get(symbol)
        short = portfolio.short_positions.get(symbol)

        if pos and pos.quantity > 0:
            actions.append((symbol, Action(ActionType.SELL, pos.quantity,
                                           {"exit_reason": "data_gap"})))
        if short and short.quantity > 0:
            actions.append((symbol, Action(ActionType.COVER, short.quantity,
                                           {"exit_reason": "data_gap"})))

        if symbol in self.strategies:
            self.strategies[symbol].reset_position()

        return actions
