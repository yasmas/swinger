import math
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from jinja2 import Environment, FileSystemLoader

from trade_log import TradeLogReader


TEMPLATES_DIR = Path(__file__).parent / "templates"


def compute_stats(trade_log: pd.DataFrame, initial_cash: float) -> dict:
    """Compute backtest performance statistics from a trade log."""
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
    }


def build_chart(trade_log: pd.DataFrame, price_data: pd.DataFrame, symbol: str) -> str:
    """Build a two-panel Plotly chart and return it as an HTML div string."""
    fig = make_subplots(
        rows=3, cols=1,
        shared_xaxes=True,
        vertical_spacing=0.06,
        row_heights=[0.6, 0.15, 0.25],
        subplot_titles=("Asset Price", "% Invested", "Portfolio Value"),
    )

    fig.add_trace(
        go.Scatter(
            x=price_data.index,
            y=price_data["close"],
            mode="lines",
            name=symbol,
            line=dict(color="#2196F3", width=1),
            hoverinfo="x+y",
        ),
        row=1, col=1,
    )

    buy_rows = trade_log[trade_log["action"] == "BUY"]
    if not buy_rows.empty:
        fig.add_trace(
            go.Scatter(
                x=buy_rows["date"],
                y=buy_rows["price"],
                mode="markers",
                name="BUY",
                marker=dict(
                    symbol="triangle-up",
                    size=12,
                    color="#4CAF50",
                    line=dict(width=1, color="darkgreen"),
                ),
                customdata=buy_rows[["action", "quantity", "price"]].values,
                hovertemplate=(
                    "<b>%{customdata[0]}</b><br>"
                    "Date: %{x}<br>"
                    "Qty: %{customdata[1]:.6f}<br>"
                    "Price: $%{customdata[2]:,.2f}"
                    "<extra></extra>"
                ),
            ),
            row=1, col=1,
        )

    sell_rows = trade_log[trade_log["action"] == "SELL"]
    if not sell_rows.empty:
        fig.add_trace(
            go.Scatter(
                x=sell_rows["date"],
                y=sell_rows["price"],
                mode="markers",
                name="SELL",
                marker=dict(
                    symbol="triangle-down",
                    size=12,
                    color="#F44336",
                    line=dict(width=1, color="darkred"),
                ),
                customdata=sell_rows[["action", "quantity", "price"]].values,
                hovertemplate=(
                    "<b>%{customdata[0]}</b><br>"
                    "Date: %{x}<br>"
                    "Qty: %{customdata[1]:.6f}<br>"
                    "Price: $%{customdata[2]:,.2f}"
                    "<extra></extra>"
                ),
            ),
            row=1, col=1,
        )

    short_rows = trade_log[trade_log["action"] == "SHORT"]
    if not short_rows.empty:
        fig.add_trace(
            go.Scatter(
                x=short_rows["date"],
                y=short_rows["price"],
                mode="markers",
                name="SHORT",
                marker=dict(
                    symbol="diamond",
                    size=12,
                    color="#FF5722",
                    line=dict(width=1, color="#BF360C"),
                ),
                customdata=short_rows[["action", "quantity", "price"]].values,
                hovertemplate=(
                    "<b>%{customdata[0]}</b><br>"
                    "Date: %{x}<br>"
                    "Qty: %{customdata[1]:.6f}<br>"
                    "Price: $%{customdata[2]:,.2f}"
                    "<extra></extra>"
                ),
            ),
            row=1, col=1,
        )

    cover_rows = trade_log[trade_log["action"] == "COVER"]
    if not cover_rows.empty:
        fig.add_trace(
            go.Scatter(
                x=cover_rows["date"],
                y=cover_rows["price"],
                mode="markers",
                name="COVER",
                marker=dict(
                    symbol="diamond",
                    size=12,
                    color="#8BC34A",
                    line=dict(width=1, color="#33691E"),
                ),
                customdata=cover_rows[["action", "quantity", "price"]].values,
                hovertemplate=(
                    "<b>%{customdata[0]}</b><br>"
                    "Date: %{x}<br>"
                    "Qty: %{customdata[1]:.6f}<br>"
                    "Price: $%{customdata[2]:,.2f}"
                    "<extra></extra>"
                ),
            ),
            row=1, col=1,
        )

    pct_invested = trade_log.apply(
        lambda r: (1 - r["cash_balance"] / r["portfolio_value"]) * 100
        if r["portfolio_value"] > 0 else 0.0,
        axis=1,
    )

    fig.add_trace(
        go.Scatter(
            x=trade_log["date"],
            y=pct_invested,
            mode="lines",
            name="% Invested",
            fill="tozeroy",
            line=dict(color="#FF9800", width=1),
            fillcolor="rgba(255, 152, 0, 0.3)",
        ),
        row=2, col=1,
    )

    fig.add_trace(
        go.Scatter(
            x=trade_log["date"],
            y=trade_log["portfolio_value"],
            mode="lines",
            name="Portfolio Value",
            line=dict(color="#9C27B0", width=1),
            fill="tozeroy",
            fillcolor="rgba(156, 39, 176, 0.15)",
        ),
        row=3, col=1,
    )

    fig.update_yaxes(title_text="Price ($)", row=1, col=1)
    fig.update_yaxes(title_text="% Invested", range=[0, 105], row=2, col=1)
    fig.update_yaxes(title_text="Value ($)", row=3, col=1)
    fig.update_xaxes(title_text="Date", row=3, col=1)

    fig.update_layout(
        height=850,
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        margin=dict(l=60, r=30, t=60, b=40),
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
    ) -> str:
        """Generate HTML report and return the output file path."""
        trade_log = TradeLogReader.read(trade_log_path)

        stats = compute_stats(trade_log, initial_cash)
        chart_html = build_chart(trade_log, price_data, symbol)

        start_date = str(trade_log.iloc[0]["date"].date())
        end_date = str(trade_log.iloc[-1]["date"].date())

        env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))
        template = env.get_template("report.html")

        html = template.render(
            strategy_name=strategy_name,
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
            chart_html=chart_html,
            stats=stats,
        )

        if output_filename is None:
            ver = f"_{version}" if version else ""
            output_filename = f"{strategy_name}_{symbol}_{start_date}_{end_date}{ver}.html"
        output_path = self.output_dir / output_filename

        with open(output_path, "w") as f:
            f.write(html)

        return str(output_path)
