"""Intraday trend strategy reporter.

Visualises all 4-layer confluence indicators on a 5-panel Plotly chart:
  Row 1: Price + HMA (directional) + Supertrend (bull/bear) +
          Keltner Channels (band fill) + VWAP + Volume + Trade markers
  Row 2: ADX with entry thresholds + ATR% volatility floor (secondary y)
  Row 3: TTM Squeeze state (Bollinger inside Keltner)
  Row 4: % Invested (long vs short)
  Row 5: Portfolio value
"""
import math
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from jinja2 import Environment, FileSystemLoader

from trade_log import TradeLogReader
from reporting.reporter import compute_stats, TEMPLATES_DIR
from strategies.macd_rsi_advanced import compute_adx, compute_atr
from strategies.intraday_indicators import (
    compute_hma,
    compute_supertrend,
    compute_keltner,
    compute_bollinger,
    compute_squeeze,
    compute_vwap_daily,
)


# ── Default params matching v5 config ────────────────────────────────────────
_DEFAULTS = {
    "hma_period": 21,
    "supertrend_atr_period": 10,
    "supertrend_multiplier": 3.0,
    "keltner_ema_period": 15,
    "keltner_atr_period": 10,
    "keltner_atr_multiplier": 2.5,
    "bb_period": 20,
    "bb_stddev": 2.0,
    "adx_period": 14,
    "adx_threshold": 30,
    "short_adx_threshold": 35,
    "volume_avg_period": 20,
    "min_atr_pct": 0.18,
}

# ── Colour palette ─────────────────────────────────────────────────────────
_C = {
    "price":       "#1E88E5",      # blue
    "hma_up":      "#66BB6A",      # green  (rising)
    "hma_dn":      "#EF5350",      # red    (falling)
    "st_bull":     "#26A69A",      # teal   (Supertrend bullish)
    "st_bear":     "#EF5350",      # red    (Supertrend bearish)
    "kc_band":     "rgba(66,165,245,0.09)",
    "kc_border":   "rgba(66,165,245,0.55)",
    "kc_mid":      "rgba(100,181,246,0.75)",
    "vwap":        "#FFD54F",      # amber
    "vol_up":      "rgba(102,187,106,0.30)",
    "vol_dn":      "rgba(239,83,80,0.30)",
    "vol_avg":     "rgba(255,183,77,0.60)",
    "adx":         "#CE93D8",      # purple
    "adx_thresh":  "rgba(239,83,80,0.60)",
    "adx_short":   "rgba(255,152,0,0.65)",
    "atr_pct":     "#26C6DA",      # cyan
    "atr_floor":   "rgba(245,124,0,0.70)",
    "sq_on":       "rgba(239,83,80,0.72)",   # red  — squeeze locked
    "sq_off":      "rgba(102,187,106,0.62)", # green — free to break
    "pct_long":    "#4CAF50",
    "pct_short":   "#F44336",
    "portfolio":   "#AB47BC",
}


# ─────────────────────────────────────────────────────────────────────────────
# Indicator computation
# ─────────────────────────────────────────────────────────────────────────────

def _compute_indicators(price_data: pd.DataFrame, params: dict) -> pd.DataFrame:
    """Resample 5-minute OHLCV to 1-hour bars and compute all strategy indicators."""
    p = {**_DEFAULTS, **{k: v for k, v in params.items() if k in _DEFAULTS}}

    # ── 1h OHLCV resample ─────────────────────────────────────────────────────
    h1 = price_data.resample("1h").agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
    ).dropna(subset=["close"])

    closes  = h1["close"]
    highs   = h1["high"]
    lows    = h1["low"]
    volumes = h1["volume"]

    # ── HMA + slope direction ─────────────────────────────────────────────────
    hma       = compute_hma(closes, int(p["hma_period"]))
    hma_slope = hma.diff()
    h1["hma"]    = hma
    h1["hma_up"] = hma.where(hma_slope >= 0, other=np.nan)
    h1["hma_dn"] = hma.where(hma_slope <  0, other=np.nan)

    # ── Supertrend — split into bull / bear segments ──────────────────────────
    st_line, st_bull = compute_supertrend(
        highs, lows, closes,
        int(p["supertrend_atr_period"]), float(p["supertrend_multiplier"]),
    )
    st_bull_bool  = st_bull.astype(bool)
    h1["st_bull"] = st_line.where(st_bull_bool,  other=np.nan)
    h1["st_bear"] = st_line.where(~st_bull_bool, other=np.nan)

    # ── Keltner Channels ─────────────────────────────────────────────────────
    kc_u, kc_m, kc_l = compute_keltner(
        highs, lows, closes,
        int(p["keltner_ema_period"]),
        int(p["keltner_atr_period"]),
        float(p["keltner_atr_multiplier"]),
    )
    h1["kc_upper"] = kc_u
    h1["kc_mid"]   = kc_m
    h1["kc_lower"] = kc_l

    # ── TTM Squeeze ───────────────────────────────────────────────────────────
    bb_u, bb_m, bb_l = compute_bollinger(closes, int(p["bb_period"]), float(p["bb_stddev"]))
    squeeze          = compute_squeeze(bb_u, bb_l, kc_u, kc_l)
    h1["squeeze_on"] = squeeze.astype(float).fillna(np.nan)

    # ── VWAP (daily reset) ────────────────────────────────────────────────────
    h1["vwap"] = compute_vwap_daily(highs, lows, closes, volumes, h1.index)

    # ── ADX ───────────────────────────────────────────────────────────────────
    h1["adx"] = compute_adx(highs, lows, closes, int(p["adx_period"]))

    # ── ATR% volatility floor ─────────────────────────────────────────────────
    atr_raw  = compute_atr(highs, lows, closes, int(p["supertrend_atr_period"]))
    h1["atr_pct"] = (atr_raw / closes * 100)

    # ── Volume average ────────────────────────────────────────────────────────
    h1["vol_avg"] = volumes.rolling(window=int(p["volume_avg_period"])).mean()

    return h1


# ─────────────────────────────────────────────────────────────────────────────
# Chart builder
# ─────────────────────────────────────────────────────────────────────────────

def _trade_marker(fig, rows: pd.DataFrame, action: str,
                  color: str, border: str, shape: str) -> None:
    """Add a trade entry/exit scatter trace to row 1."""
    if rows.empty:
        return
    fig.add_trace(
        go.Scatter(
            x=rows["date"],
            y=rows["price"],
            mode="markers",
            name=action,
            marker=dict(
                symbol=shape, size=13, color=color,
                line=dict(width=1.5, color=border),
            ),
            customdata=rows[["action", "quantity", "price"]].values,
            hovertemplate=(
                "<b>%{customdata[0]}</b><br>"
                "Date: %{x}<br>"
                "Qty: %{customdata[1]:.6f}<br>"
                "Price: $%{customdata[2]:,.2f}"
                "<extra></extra>"
            ),
        ),
        row=1, col=1, secondary_y=False,
    )


def build_intraday_chart(
    trade_log: pd.DataFrame,
    price_data: pd.DataFrame,
    symbol: str,
    strategy_params: dict | None = None,
) -> str:
    """Build a 5-panel Plotly chart with all intraday strategy indicators."""

    params = strategy_params or {}
    ind    = _compute_indicators(price_data, params)
    p      = {**_DEFAULTS, **{k: v for k, v in params.items() if k in _DEFAULTS}}

    adx_thresh       = float(p["adx_threshold"])
    short_adx_thresh = float(p.get("short_adx_threshold", adx_thresh))
    min_atr_pct      = float(p["min_atr_pct"])

    # ── Subplot scaffold ──────────────────────────────────────────────────────
    fig = make_subplots(
        rows=5, cols=1,
        shared_xaxes=True,
        vertical_spacing=0.025,
        row_heights=[0.43, 0.14, 0.10, 0.10, 0.14],
        subplot_titles=(
            f"{symbol}  ·  HMA  ·  Supertrend  ·  Keltner  ·  VWAP",
            f"ADX ({int(p['adx_period'])})  ·  ATR%  ·  Volatility Filter",
            "TTM Squeeze  (Bollinger inside Keltner  =  red)",
            "% Invested",
            "Portfolio Value",
        ),
        specs=[
            [{"secondary_y": True}],   # price + volume
            [{"secondary_y": True}],   # ADX + ATR%
            [{"secondary_y": False}],  # Squeeze
            [{"secondary_y": False}],  # % Invested
            [{"secondary_y": False}],  # Portfolio
        ],
    )

    # ═══════════════════════════════════════════════════════════════════════════
    # ROW 1 — Price chart with indicator overlays
    # ═══════════════════════════════════════════════════════════════════════════

    # ── Volume bars (secondary y, behind everything) ──────────────────────────
    max_vol   = float(ind["volume"].max())
    vol_cols  = [
        _C["vol_up"] if c >= o else _C["vol_dn"]
        for c, o in zip(ind["close"], ind["open"])
    ]
    fig.add_trace(
        go.Bar(
            x=ind.index, y=ind["volume"],
            name="Volume", marker_color=vol_cols,
            hovertemplate="Vol: %{y:,.0f}<extra></extra>",
            showlegend=True,
        ),
        row=1, col=1, secondary_y=True,
    )

    # Volume average line (secondary y)
    fig.add_trace(
        go.Scatter(
            x=ind.index, y=ind["vol_avg"],
            mode="lines", name="Vol Avg",
            line=dict(color=_C["vol_avg"], width=1.2, dash="dot"),
            hoverinfo="skip",
            showlegend=False,
        ),
        row=1, col=1, secondary_y=True,
    )

    # ── Keltner Channel filled band ──────────────────────────────────────────
    kc_valid = ind[["kc_upper", "kc_lower", "kc_mid"]].dropna()
    if not kc_valid.empty:
        _x  = list(kc_valid.index)
        _xu = list(kc_valid["kc_upper"])
        _xl = list(kc_valid["kc_lower"])
        # Closed polygon: upper forward then lower backward
        fig.add_trace(
            go.Scatter(
                x=_x + _x[::-1],
                y=_xu + _xl[::-1],
                mode="lines",
                name="Keltner Band",
                line=dict(width=0),
                fill="toself",
                fillcolor=_C["kc_band"],
                hoverinfo="skip",
                showlegend=True,
            ),
            row=1, col=1, secondary_y=False,
        )

    # KC Upper / Lower border lines
    fig.add_trace(
        go.Scatter(
            x=ind.index, y=ind["kc_upper"],
            mode="lines", name="KC Upper",
            line=dict(color=_C["kc_border"], width=0.8),
            hovertemplate="KC↑ $%{y:,.0f}<extra></extra>",
            showlegend=False,
        ),
        row=1, col=1, secondary_y=False,
    )
    fig.add_trace(
        go.Scatter(
            x=ind.index, y=ind["kc_lower"],
            mode="lines", name="KC Lower",
            line=dict(color=_C["kc_border"], width=0.8),
            hovertemplate="KC↓ $%{y:,.0f}<extra></extra>",
            showlegend=False,
        ),
        row=1, col=1, secondary_y=False,
    )
    # KC Mid dashed
    fig.add_trace(
        go.Scatter(
            x=ind.index, y=ind["kc_mid"],
            mode="lines", name="KC Mid",
            line=dict(color=_C["kc_mid"], width=1.0, dash="dash"),
            hovertemplate="KC Mid $%{y:,.0f}<extra></extra>",
            showlegend=False,
        ),
        row=1, col=1, secondary_y=False,
    )

    # ── VWAP (dotted amber) ───────────────────────────────────────────────────
    fig.add_trace(
        go.Scatter(
            x=ind.index, y=ind["vwap"],
            mode="lines", name="VWAP",
            line=dict(color=_C["vwap"], width=1.4, dash="dot"),
            hovertemplate="VWAP $%{y:,.0f}<extra></extra>",
        ),
        row=1, col=1, secondary_y=False,
    )

    # ── HMA — rising (green) and falling (red) ────────────────────────────────
    fig.add_trace(
        go.Scatter(
            x=ind.index, y=ind["hma_up"],
            mode="lines", name="HMA ↑",
            line=dict(color=_C["hma_up"], width=2.2),
            hovertemplate="HMA $%{y:,.0f}<extra></extra>",
        ),
        row=1, col=1, secondary_y=False,
    )
    fig.add_trace(
        go.Scatter(
            x=ind.index, y=ind["hma_dn"],
            mode="lines", name="HMA ↓",
            line=dict(color=_C["hma_dn"], width=2.2),
            hovertemplate="HMA $%{y:,.0f}<extra></extra>",
        ),
        row=1, col=1, secondary_y=False,
    )

    # ── Supertrend — bull (teal) and bear (red) segments ─────────────────────
    fig.add_trace(
        go.Scatter(
            x=ind.index, y=ind["st_bull"],
            mode="lines", name="ST Bull",
            line=dict(color=_C["st_bull"], width=2.0),
            hovertemplate="ST $%{y:,.0f}<extra></extra>",
        ),
        row=1, col=1, secondary_y=False,
    )
    fig.add_trace(
        go.Scatter(
            x=ind.index, y=ind["st_bear"],
            mode="lines", name="ST Bear",
            line=dict(color=_C["st_bear"], width=2.0),
            hovertemplate="ST $%{y:,.0f}<extra></extra>",
        ),
        row=1, col=1, secondary_y=False,
    )

    # ── Price line (on top of all indicators) ─────────────────────────────────
    fig.add_trace(
        go.Scatter(
            x=ind.index, y=ind["close"],
            mode="lines", name=symbol,
            line=dict(color=_C["price"], width=1.3),
            hovertemplate="$%{y:,.2f}<extra></extra>",
        ),
        row=1, col=1, secondary_y=False,
    )

    # ── Trade markers ─────────────────────────────────────────────────────────
    _trade_marker(fig, trade_log[trade_log["action"] == "BUY"],
                  "BUY",   "#4CAF50", "#1B5E20", "triangle-up")
    _trade_marker(fig, trade_log[trade_log["action"] == "SELL"],
                  "SELL",  "#EF5350", "#B71C1C", "triangle-down")
    _trade_marker(fig, trade_log[trade_log["action"] == "SHORT"],
                  "SHORT", "#FF7043", "#BF360C", "diamond")
    _trade_marker(fig, trade_log[trade_log["action"] == "COVER"],
                  "COVER", "#66BB6A", "#2E7D32", "diamond")

    # ═══════════════════════════════════════════════════════════════════════════
    # ROW 2 — ADX (primary y) + ATR% (secondary y)
    # ═══════════════════════════════════════════════════════════════════════════

    # ADX thresholds (horizontal reference lines via Scatter across full x range)
    _x0, _x1 = ind.index[0], ind.index[-1]

    fig.add_trace(
        go.Scatter(
            x=[_x0, _x1], y=[adx_thresh, adx_thresh],
            mode="lines", name=f"ADX ≥ {adx_thresh:.0f}",
            line=dict(color=_C["adx_thresh"], width=1.2, dash="dot"),
            hoverinfo="skip", showlegend=True,
        ),
        row=2, col=1, secondary_y=False,
    )
    if short_adx_thresh != adx_thresh:
        fig.add_trace(
            go.Scatter(
                x=[_x0, _x1], y=[short_adx_thresh, short_adx_thresh],
                mode="lines", name=f"Short ADX ≥ {short_adx_thresh:.0f}",
                line=dict(color=_C["adx_short"], width=1.2, dash="dot"),
                hoverinfo="skip", showlegend=True,
            ),
            row=2, col=1, secondary_y=False,
        )

    # ADX line
    fig.add_trace(
        go.Scatter(
            x=ind.index, y=ind["adx"],
            mode="lines", name="ADX",
            line=dict(color=_C["adx"], width=1.6),
            hovertemplate="ADX: %{y:.1f}<extra></extra>",
        ),
        row=2, col=1, secondary_y=False,
    )

    # ATR% floor reference line (secondary y)
    if min_atr_pct > 0:
        fig.add_trace(
            go.Scatter(
                x=[_x0, _x1], y=[min_atr_pct, min_atr_pct],
                mode="lines", name=f"ATR Floor {min_atr_pct:.2f}%",
                line=dict(color=_C["atr_floor"], width=1.3, dash="dash"),
                hoverinfo="skip", showlegend=True,
            ),
            row=2, col=1, secondary_y=True,
        )

    # ATR% line (secondary y)
    fig.add_trace(
        go.Scatter(
            x=ind.index, y=ind["atr_pct"],
            mode="lines", name="ATR%",
            line=dict(color=_C["atr_pct"], width=1.3),
            hovertemplate="ATR%%: %{y:.3f}%%<extra></extra>",
        ),
        row=2, col=1, secondary_y=True,
    )

    # ═══════════════════════════════════════════════════════════════════════════
    # ROW 3 — TTM Squeeze state bars
    # ═══════════════════════════════════════════════════════════════════════════

    # Build bar colors: red = squeeze ON, green = squeeze OFF, gray = NaN/warmup
    sq = ind["squeeze_on"]
    sq_colors = []
    for v in sq:
        if pd.isna(v):
            sq_colors.append("rgba(180,180,180,0.20)")
        elif v > 0.5:
            sq_colors.append(_C["sq_on"])
        else:
            sq_colors.append(_C["sq_off"])

    sq_y = [1.0] * len(sq)  # fixed height bars

    fig.add_trace(
        go.Bar(
            x=ind.index, y=sq_y,
            name="Squeeze",
            marker_color=sq_colors,
            hovertemplate="%{x}<br>Squeeze: %{customdata}<extra></extra>",
            customdata=["ON" if (not pd.isna(v) and v > 0.5) else "off" for v in sq],
            showlegend=True,
        ),
        row=3, col=1,
    )

    # ═══════════════════════════════════════════════════════════════════════════
    # ROW 4 — % Invested
    # ═══════════════════════════════════════════════════════════════════════════

    if not trade_log.empty:
        pct_long = trade_log.apply(
            lambda r: max(0.0, (1 - r["cash_balance"] / r["portfolio_value"]) * 100)
            if r["portfolio_value"] > 0 else 0.0,
            axis=1,
        )
        pct_short = trade_log.apply(
            lambda r: max(0.0, (r["cash_balance"] / r["portfolio_value"] - 1) * 100)
            if r["portfolio_value"] > 0 else 0.0,
            axis=1,
        )
        fig.add_trace(
            go.Scatter(
                x=trade_log["date"], y=pct_long,
                mode="lines", name="% Long",
                fill="tozeroy", line=dict(color=_C["pct_long"], width=1),
                fillcolor="rgba(76,175,80,0.28)",
                hovertemplate="Long: %{y:.0f}%%<extra></extra>",
            ),
            row=4, col=1,
        )
        fig.add_trace(
            go.Scatter(
                x=trade_log["date"], y=-pct_short,
                mode="lines", name="% Short",
                fill="tozeroy", line=dict(color=_C["pct_short"], width=1),
                fillcolor="rgba(244,67,54,0.28)",
                hovertemplate="Short: %{y:.0f}%%<extra></extra>",
            ),
            row=4, col=1,
        )

    # ═══════════════════════════════════════════════════════════════════════════
    # ROW 5 — Portfolio value
    # ═══════════════════════════════════════════════════════════════════════════

    if not trade_log.empty:
        fig.add_trace(
            go.Scatter(
                x=trade_log["date"], y=trade_log["portfolio_value"],
                mode="lines", name="Portfolio",
                line=dict(color=_C["portfolio"], width=1.6),
                fill="tozeroy", fillcolor="rgba(171,71,188,0.11)",
                hovertemplate="$%{y:,.0f}<extra></extra>",
            ),
            row=5, col=1,
        )

    # ═══════════════════════════════════════════════════════════════════════════
    # Axes formatting
    # ═══════════════════════════════════════════════════════════════════════════

    # Row 1: price primary y, volume secondary y
    fig.update_yaxes(title_text="Price ($)",   row=1, col=1, secondary_y=False,
                     tickformat="$,.0f")
    fig.update_yaxes(row=1, col=1, secondary_y=True,
                     range=[0, max_vol * 5.0],
                     showticklabels=False, showgrid=False, zeroline=False)

    # Row 2: ADX primary, ATR% secondary
    fig.update_yaxes(title_text="ADX",   row=2, col=1, secondary_y=False,
                     range=[0, 100], tickvals=[0, 25, 50, 75, 100])
    fig.update_yaxes(title_text="ATR%",  row=2, col=1, secondary_y=True,
                     tickformat=".2f", showgrid=False)

    # Row 3: squeeze bars 0–1
    fig.update_yaxes(row=3, col=1,
                     range=[0, 1.5], showticklabels=False, showgrid=False)

    # Row 4: % invested
    fig.update_yaxes(title_text="% Invested", row=4, col=1, range=[-55, 105])

    # Row 5: portfolio $
    fig.update_yaxes(title_text="Value ($)",  row=5, col=1, tickformat="$,.0f")

    # X-axis: hide tick labels except on bottom row
    for r in range(1, 5):
        fig.update_xaxes(showticklabels=False, row=r, col=1)
    fig.update_xaxes(title_text="Date", row=5, col=1)

    # ── Global layout ─────────────────────────────────────────────────────────
    fig.update_layout(
        height=1250,
        hovermode="x unified",
        paper_bgcolor="#FAFAFA",
        plot_bgcolor="#FFFFFF",
        font=dict(
            family="-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif",
            size=12,
        ),
        legend=dict(
            orientation="h",
            yanchor="bottom", y=1.01,
            xanchor="right",  x=1,
            bgcolor="rgba(255,255,255,0.88)",
            bordercolor="#E0E0E0", borderwidth=1,
            font=dict(size=11),
        ),
        margin=dict(l=72, r=56, t=72, b=50),
        bargap=0.10,
    )

    # Grid + zeroline styling on all rows
    for r in range(1, 6):
        fig.update_xaxes(gridcolor="#F0F0F0", gridwidth=1, zeroline=False,
                         row=r, col=1)
        fig.update_yaxes(
            gridcolor="#F0F0F0", gridwidth=1,
            zeroline=True, zerolinecolor="#E0E0E0", zerolinewidth=1,
            row=r, col=1, secondary_y=False,
        )

    return fig.to_html(full_html=False, include_plotlyjs="cdn")


# ─────────────────────────────────────────────────────────────────────────────
# Reporter class
# ─────────────────────────────────────────────────────────────────────────────

class IntradayReporter:
    """Generates an intraday strategy HTML report with full indicator visualisation."""

    def __init__(self, output_dir: str = "reports"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def generate(
        self,
        trade_log_path: str,
        price_data: pd.DataFrame,
        strategy_name: str,
        symbol: str,
        initial_cash: float,
        version: str = "",
        output_filename: str | None = None,
        auto_refresh_seconds: int | None = None,
        strategy_params: dict | None = None,
    ) -> str:
        """Generate HTML report and return the output file path."""
        trade_log = TradeLogReader.read(trade_log_path)
        stats     = compute_stats(trade_log, initial_cash)

        # Buy-and-hold benchmark
        first_price = float(price_data["close"].iloc[0])
        last_price  = float(price_data["close"].iloc[-1])
        bnh_return  = (last_price / first_price - 1) * 100
        days        = (price_data.index[-1] - price_data.index[0]).days
        bnh_years   = days / 365.25 if days > 0 else 1.0
        bnh_cagr    = (
            ((last_price / first_price) ** (1 / bnh_years) - 1) * 100
            if bnh_years > 0 else 0.0
        )
        stats["bnh_return"] = bnh_return
        stats["bnh_cagr"]   = bnh_cagr

        chart_html = build_intraday_chart(
            trade_log, price_data, symbol, strategy_params=strategy_params,
        )

        if not trade_log.empty:
            start_date = str(trade_log.iloc[0]["date"].date())
            end_date   = str(trade_log.iloc[-1]["date"].date())
        else:
            start_date = str(price_data.index[0].date())
            end_date   = str(price_data.index[-1].date())

        env      = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))
        template = env.get_template("report.html")

        html = template.render(
            strategy_name=strategy_name,
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
            chart_html=chart_html,
            stats=stats,
            version=version,
            auto_refresh_seconds=auto_refresh_seconds,
        )

        if output_filename is None:
            ver             = f"_{version}" if version else ""
            output_filename = (
                f"{strategy_name}_{symbol}_{start_date}_{end_date}{ver}.html"
            )
        output_path = self.output_dir / output_filename

        with open(output_path, "w") as f:
            f.write(html)

        return str(output_path)
