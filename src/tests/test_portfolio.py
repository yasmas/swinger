import pytest

from portfolio import Portfolio, Position


class TestPortfolioInit:
    def test_initial_cash(self):
        p = Portfolio(100000)
        assert p.cash == 100000
        assert p.initial_cash == 100000

    def test_no_initial_positions(self):
        p = Portfolio(100000)
        assert len(p.positions) == 0

    def test_negative_cash_raises(self):
        with pytest.raises(ValueError):
            Portfolio(-100)


class TestPortfolioBuy:
    def test_buy_deducts_cash(self):
        p = Portfolio(10000)
        p.buy("BTC", 1.0, 5000)
        assert p.cash == pytest.approx(5000.0)

    def test_buy_creates_position(self):
        p = Portfolio(10000)
        p.buy("BTC", 2.0, 3000)
        assert "BTC" in p.positions
        assert p.positions["BTC"].quantity == 2.0
        assert p.positions["BTC"].avg_cost == 3000.0

    def test_buy_updates_avg_cost(self):
        p = Portfolio(100000)
        p.buy("BTC", 1.0, 40000)
        p.buy("BTC", 1.0, 50000)
        assert p.positions["BTC"].quantity == 2.0
        assert p.positions["BTC"].avg_cost == pytest.approx(45000.0)

    def test_buy_insufficient_cash_raises(self):
        p = Portfolio(1000)
        with pytest.raises(ValueError, match="Insufficient cash"):
            p.buy("BTC", 1.0, 5000)

    def test_buy_allows_small_margin_within_pct_of_trade(self):
        # cost 10_000; cash 9_900 → shortfall 100 ≤ 2% of 10_000 = 200
        p = Portfolio(9900)
        p.buy("BTC", 1.0, 10000)
        assert p.cash == pytest.approx(-100.0)
        assert p.positions["BTC"].quantity == 1.0

    def test_buy_zero_quantity_raises(self):
        p = Portfolio(10000)
        with pytest.raises(ValueError, match="positive"):
            p.buy("BTC", 0, 100)

    def test_buy_negative_price_raises(self):
        p = Portfolio(10000)
        with pytest.raises(ValueError, match="positive"):
            p.buy("BTC", 1.0, -100)

    def test_buy_all_cash(self):
        p = Portfolio(50000)
        p.buy("BTC", 1.0, 50000)
        assert p.cash == pytest.approx(0.0)
        assert p.positions["BTC"].quantity == 1.0


class TestPortfolioSell:
    def test_sell_adds_cash(self):
        p = Portfolio(10000)
        p.buy("BTC", 1.0, 5000)
        p.sell("BTC", 1.0, 6000)
        assert p.cash == pytest.approx(11000.0)

    def test_sell_removes_position(self):
        p = Portfolio(10000)
        p.buy("BTC", 1.0, 5000)
        p.sell("BTC", 1.0, 5000)
        assert "BTC" not in p.positions

    def test_sell_partial(self):
        p = Portfolio(10000)
        p.buy("BTC", 2.0, 3000)
        p.sell("BTC", 1.0, 4000)
        assert p.positions["BTC"].quantity == pytest.approx(1.0)
        assert p.cash == pytest.approx(4000 + 4000)

    def test_sell_more_than_held_raises(self):
        p = Portfolio(10000)
        p.buy("BTC", 1.0, 5000)
        with pytest.raises(ValueError, match="Cannot sell"):
            p.sell("BTC", 2.0, 5000)

    def test_sell_no_position_raises(self):
        p = Portfolio(10000)
        with pytest.raises(ValueError, match="No position"):
            p.sell("ETH", 1.0, 3000)

    def test_sell_zero_quantity_raises(self):
        p = Portfolio(10000)
        p.buy("BTC", 1.0, 5000)
        with pytest.raises(ValueError, match="positive"):
            p.sell("BTC", 0, 5000)


class TestPortfolioTotalValue:
    def test_cash_only(self):
        p = Portfolio(10000)
        assert p.total_value({}) == 10000

    def test_with_position(self):
        p = Portfolio(10000)
        p.buy("BTC", 1.0, 5000)
        assert p.total_value({"BTC": 6000}) == pytest.approx(5000 + 6000)

    def test_multiple_positions(self):
        p = Portfolio(100000)
        p.buy("BTC", 1.0, 40000)
        p.buy("ETH", 10.0, 3000)
        value = p.total_value({"BTC": 45000, "ETH": 3500})
        expected = (100000 - 40000 - 30000) + 45000 + 35000
        assert value == pytest.approx(expected)
