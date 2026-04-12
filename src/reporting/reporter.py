import math
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from jinja2 import Environment, FileSystemLoader

from trade_log import TradeLogReader


TEMPLATES_DIR = Path(__file__).parent / "templates"


def posix_utc_seconds(ts: pd.Timestamp | np.datetime64) -> int:
    """POSIX seconds for Lightweight Charts (UTC). Naive timestamps are UTC wall clocks."""
    t = pd.Timestamp(ts)
    if t.tzinfo is None:
        t = t.tz_localize("UTC")
    else:
        t = t.tz_convert("UTC")
    return int(t.timestamp())


def compute_stats(
    trade_log: pd.DataFrame, initial_cash: float, cost_per_trade_pct: float = 0.05
) -> dict:
    """Compute backtest performance statistics from a trade log."""
    if trade_log.empty:
        return {
            "initial_cash": initial_cash,
            "final_value": initial_cash,
            "total_return": 0.0,
            "annualized_return": 0.0,
            "max_drawdown": 0.0,
            "sharpe_ratio": 0.0,
            "num_buys": 0,
            "num_sells": 0,
            "num_shorts": 0,
            "num_covers": 0,
            "num_trades": 0,
            "total_costs": 0.0,
            "cost_per_trade_pct": cost_per_trade_pct,
            "after_cost_return": 0.0,
            "after_cost_cagr": 0.0,
        }

    final_value = trade_log.iloc[-1]["portfolio_value"]
    total_return = (final_value / initial_cash - 1) * 100

    start = trade_log.iloc[0]["date"]
    end = trade_log.iloc[-1]["date"]
    days = (end - start).days
    years = days / 365.25 if days > 0 else 1.0
    annualized = ((final_value / initial_cash) ** (1 / years) - 1) * 100 if years > 0 else 0.0

    portfolio_values = trade_log["portfolio_value"]
    cummax = portfolio_values.cummax()
    drawdown = (portfolio_values - cummax) / cummax * 100
    max_drawdown = drawdown.min()

    daily_values = trade_log.set_index("date")["portfolio_value"].resample("D").last().dropna()
    if len(daily_values) > 1:
        daily_returns = daily_values.pct_change().dropna()
        sharpe = (daily_returns.mean() / daily_returns.std() * math.sqrt(252)
                  if daily_returns.std() > 0 else 0.0)
    else:
        sharpe = 0.0

    buys = (trade_log["action"] == "BUY").sum()
    sells = (trade_log["action"] == "SELL").sum()
    shorts = (trade_log["action"] == "SHORT").sum()
    covers = (trade_log["action"] == "COVER").sum()

    actions = trade_log[trade_log["action"].isin(["BUY", "SELL", "SHORT", "COVER"])]
    total_costs = (actions["price"] * actions["quantity"] * cost_per_trade_pct / 100).sum()
    after_cost_value = final_value - total_costs
    after_cost_return = (after_cost_value / initial_cash - 1) * 100
    after_cost_cagr = (
        ((after_cost_value / initial_cash) ** (1 / years) - 1) * 100
        if years > 0 else 0.0
    )

    return {
        "initial_cash": initial_cash,
        "final_value": final_value,
        "total_return": total_return,
        "annualized_return": annualized,
        "max_drawdown": max_drawdown,
        "sharpe_ratio": sharpe,
        "num_buys": int(buys),
        "num_sells": int(sells),
        "num_shorts": int(shorts),
        "num_covers": int(covers),
        "num_trades": int(buys + sells + shorts + covers),
        "total_costs": total_costs,
        "cost_per_trade_pct": cost_per_trade_pct,
        "after_cost_return": after_cost_return,
        "after_cost_cagr": after_cost_cagr,
    }


def _resample_indicators(price_data: pd.DataFrame) -> pd.DataFrame:
    """Resample 5m OHLCV to 1h and compute MACD + RSI indicators."""
    h1 = price_data.resample("1h").agg({
        "open": "first", "high": "max", "low": "min",
        "close": "last", "volume": "sum",
    }).dropna()

    closes = h1["close"]

    # MACD (12, 26, 9)
    ema12 = closes.ewm(span=12, adjust=False).mean()
    ema26 = closes.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()
    histogram = macd - signal

    # RSI (14)
    delta = closes.diff()
    gain = delta.where(delta > 0, 0.0).ewm(com=13, adjust=False).mean()
    loss = (-delta).where(delta < 0, 0.0).ewm(com=13, adjust=False).mean()
    rs = gain / loss.replace(0, float("nan"))
    rsi = 100 - (100 / (1 + rs))

    h1["macd"] = macd
    h1["macd_signal"] = signal
    h1["macd_hist"] = histogram
    h1["rsi"] = rsi
    return h1


def _trade_marker(fig, rows, action, color, border_color, symbol_shape, customdata):
    if rows.empty:
        return
    fig.add_trace(
        go.Scatter(
            x=rows["date"],
            y=rows["price"],
            mode="markers",
            name=action,
            marker=dict(symbol=symbol_shape, size=12, color=color,
                        line=dict(width=1.5, color=border_color)),
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


def build_chart(trade_log: pd.DataFrame, price_data: pd.DataFrame, symbol: str) -> str:
    """Build a 4-panel Plotly chart: Price+Volume, RSI, MACD, Portfolio."""
    ind = _resample_indicators(price_data)

    fig = make_subplots(
        rows=5, cols=1,
        shared_xaxes=True,
        vertical_spacing=0.03,
        row_heights=[0.38, 0.12, 0.18, 0.14, 0.18],
        subplot_titles=(
            f"{symbol} Price",
            "RSI (14)",
            "MACD (12 / 26 / 9)",
            "% Invested",
            "Portfolio Value",
        ),
        specs=[
            [{"secondary_y": True}],
            [{"secondary_y": False}],
            [{"secondary_y": False}],
            [{"secondary_y": False}],
            [{"secondary_y": False}],
        ],
    )

    # ── Row 1: Volume bars (secondary y, behind price) ────────────────────────
    max_vol = float(ind["volume"].max())
    vol_colors = [
        "rgba(102,187,106,0.35)" if c >= o else "rgba(239,83,80,0.35)"
        for c, o in zip(ind["close"], ind["open"])
    ]
    fig.add_trace(
        go.Bar(
            x=ind.index, y=ind["volume"],
            name="Volume", marker_color=vol_colors,
            hovertemplate="Vol: %{y:,.2f}<extra></extra>",
        ),
        row=1, col=1, secondary_y=True,
    )

    # ── Row 1: Price line + trade markers ─────────────────────────────────────
    fig.add_trace(
        go.Scatter(
            x=price_data.index, y=price_data["close"],
            mode="lines", name=symbol,
            line=dict(color="#42A5F5", width=1.2),
            hovertemplate="$%{y:,.2f}<extra></extra>",
        ),
        row=1, col=1, secondary_y=False,
    )

    _trade_marker(fig, trade_log[trade_log["action"] == "BUY"],
                  "BUY",   "#4CAF50", "#1B5E20", "triangle-up",  None)
    _trade_marker(fig, trade_log[trade_log["action"] == "SELL"],
                  "SELL",  "#EF5350", "#B71C1C", "triangle-down", None)
    _trade_marker(fig, trade_log[trade_log["action"] == "SHORT"],
                  "SHORT", "#FF7043", "#BF360C", "diamond",       None)
    _trade_marker(fig, trade_log[trade_log["action"] == "COVER"],
                  "COVER", "#66BB6A", "#2E7D32", "diamond",       None)

    # ── Row 2: RSI ─────────────────────────────────────────────────────────────
    fig.add_hline(y=70, line=dict(color="rgba(239,83,80,0.45)",   dash="dot", width=1), row=2, col=1)
    fig.add_hline(y=30, line=dict(color="rgba(102,187,106,0.45)", dash="dot", width=1), row=2, col=1)
    fig.add_hline(y=50, line=dict(color="rgba(150,150,150,0.25)", dash="dot", width=1), row=2, col=1)
    fig.add_trace(
        go.Scatter(
            x=ind.index, y=ind["rsi"],
            mode="lines", name="RSI",
            line=dict(color="#CE93D8", width=1.5),
            hovertemplate="RSI: %{y:.1f}<extra></extra>",
        ),
        row=2, col=1,
    )

    # ── Row 3: MACD ────────────────────────────────────────────────────────────
    hist_colors = [
        "rgba(102,187,106,0.7)" if v >= 0 else "rgba(239,83,80,0.7)"
        for v in ind["macd_hist"]
    ]
    fig.add_trace(
        go.Bar(
            x=ind.index, y=ind["macd_hist"],
            name="Histogram", marker_color=hist_colors,
            hovertemplate="Hist: %{y:.2f}<extra></extra>",
        ),
        row=3, col=1,
    )
    fig.add_trace(
        go.Scatter(
            x=ind.index, y=ind["macd"],
            mode="lines", name="MACD",
            line=dict(color="#26C6DA", width=1.5),
            hovertemplate="MACD: %{y:.2f}<extra></extra>",
        ),
        row=3, col=1,
    )
    fig.add_trace(
        go.Scatter(
            x=ind.index, y=ind["macd_signal"],
            mode="lines", name="Signal",
            line=dict(color="#FFA726", width=1.5),
            hovertemplate="Signal: %{y:.2f}<extra></extra>",
        ),
        row=3, col=1,
    )

    # ── Row 4: % Invested ────────────────────────────────────────────────────
    if not trade_log.empty:
        pct_long = trade_log.apply(
            lambda r: max(0, (1 - r["cash_balance"] / r["portfolio_value"]) * 100)
            if r["portfolio_value"] > 0 else 0.0,
            axis=1,
        )
        pct_short = trade_log.apply(
            lambda r: max(0, (r["cash_balance"] / r["portfolio_value"] - 1) * 100)
            if r["portfolio_value"] > 0 else 0.0,
            axis=1,
        )
        fig.add_trace(
            go.Scatter(
                x=trade_log["date"], y=pct_long,
                mode="lines", name="% Long",
                fill="tozeroy",
                line=dict(color="#4CAF50", width=1),
                fillcolor="rgba(76, 175, 80, 0.3)",
                hovertemplate="Long: %{y:.0f}%<extra></extra>",
            ),
            row=4, col=1,
        )
        fig.add_trace(
            go.Scatter(
                x=trade_log["date"], y=-pct_short,
                mode="lines", name="% Short",
                fill="tozeroy",
                line=dict(color="#F44336", width=1),
                fillcolor="rgba(244, 67, 54, 0.3)",
                hovertemplate="Short: %{y:.0f}%<extra></extra>",
            ),
            row=4, col=1,
        )

    # ── Row 5: Portfolio value ─────────────────────────────────────────────────
    if not trade_log.empty:
        fig.add_trace(
            go.Scatter(
                x=trade_log["date"], y=trade_log["portfolio_value"],
                mode="lines", name="Portfolio",
                line=dict(color="#AB47BC", width=1.5),
                fill="tozeroy", fillcolor="rgba(171,71,188,0.12)",
                hovertemplate="$%{y:,.0f}<extra></extra>",
            ),
            row=5, col=1,
        )

    # ── Axes ───────────────────────────────────────────────────────────────────
    fig.update_yaxes(title_text="Price ($)", row=1, col=1, secondary_y=False, tickformat="$,.0f")
    fig.update_yaxes(
        secondary_y=True, row=1, col=1,
        range=[0, max_vol * 4.5],
        showticklabels=False, showgrid=False, zeroline=False,
    )
    fig.update_yaxes(title_text="RSI",         row=2, col=1, range=[0, 100], tickvals=[30, 50, 70])
    fig.update_yaxes(title_text="MACD",        row=3, col=1)
    fig.update_yaxes(title_text="% Invested",  row=4, col=1, range=[-55, 105])
    fig.update_yaxes(title_text="Value ($)",   row=5, col=1, tickformat="$,.0f")
    fig.update_xaxes(showticklabels=False, row=1, col=1)
    fig.update_xaxes(showticklabels=False, row=2, col=1)
    fig.update_xaxes(showticklabels=False, row=3, col=1)
    fig.update_xaxes(showticklabels=False, row=4, col=1)
    fig.update_xaxes(title_text="Date",        row=5, col=1)

    fig.update_layout(
        height=1150,
        hovermode="x unified",
        paper_bgcolor="#FAFAFA",
        plot_bgcolor="#FFFFFF",
        font=dict(family="-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif", size=12),
        legend=dict(
            orientation="h", yanchor="bottom", y=1.01,
            xanchor="right", x=1,
            bgcolor="rgba(255,255,255,0.85)",
            bordercolor="#E0E0E0", borderwidth=1,
        ),
        margin=dict(l=70, r=40, t=70, b=50),
        bargap=0.15,
    )

    for i in range(1, 6):
        fig.update_xaxes(gridcolor="#F0F0F0", gridwidth=1, zeroline=False, row=i, col=1)
        fig.update_yaxes(
            gridcolor="#F0F0F0", gridwidth=1,
            zeroline=True, zerolinecolor="#E0E0E0", zerolinewidth=1,
            row=i, col=1, secondary_y=False,
        )

    return fig.to_html(full_html=False, include_plotlyjs="cdn")


class Reporter:
    """Generates an HTML report from a trade log and optional price data."""

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
    ) -> str:
        """Generate HTML report and return the output file path.

        Args:
            auto_refresh_seconds: If set, adds a meta refresh tag for auto-reload
                                  (used by paper trading reports).
        """
        trade_log = TradeLogReader.read(trade_log_path)

        stats = compute_stats(trade_log, initial_cash)

        first_price = float(price_data["close"].iloc[0])
        last_price = float(price_data["close"].iloc[-1])
        bnh_return = (last_price / first_price - 1) * 100
        days = (price_data.index[-1] - price_data.index[0]).days
        bnh_years = days / 365.25 if days > 0 else 1.0
        bnh_cagr = ((last_price / first_price) ** (1 / bnh_years) - 1) * 100 if bnh_years > 0 else 0.0
        stats["bnh_return"] = bnh_return
        stats["bnh_cagr"] = bnh_cagr

        chart_html = build_chart(trade_log, price_data, symbol)

        if not trade_log.empty:
            start_date = str(trade_log.iloc[0]["date"].date())
            end_date = str(trade_log.iloc[-1]["date"].date())
        else:
            start_date = str(price_data.index[0].date())
            end_date = str(price_data.index[-1].date())

        env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))
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
            ver = f"_{version}" if version else ""
            output_filename = f"{strategy_name}_{symbol}_{start_date}_{end_date}{ver}.html"
        output_path = self.output_dir / output_filename

        with open(output_path, "w") as f:
            f.write(html)

        return str(output_path)
