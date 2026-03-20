from dataclasses import dataclass, field


@dataclass
class Position:
    symbol: str
    quantity: float
    avg_cost: float

    @property
    def cost_basis(self) -> float:
        return self.quantity * self.avg_cost


class Portfolio:
    def __init__(self, initial_cash: float):
        if initial_cash < 0:
            raise ValueError("Initial cash cannot be negative")
        self.cash: float = initial_cash
        self.initial_cash: float = initial_cash
        self.positions: dict[str, Position] = {}
        self.short_positions: dict[str, Position] = {}

    def total_value(self, current_prices: dict[str, float]) -> float:
        long_value = sum(
            pos.quantity * current_prices[symbol]
            for symbol, pos in self.positions.items()
        )
        short_liability = sum(
            pos.quantity * current_prices[symbol]
            for symbol, pos in self.short_positions.items()
        )
        return self.cash + long_value - short_liability

    def buy(self, symbol: str, quantity: float, price: float) -> None:
        if quantity <= 0:
            raise ValueError(f"Buy quantity must be positive, got {quantity}")
        if price <= 0:
            raise ValueError(f"Price must be positive, got {price}")

        cost = quantity * price
        if cost > self.cash + 1e-9:
            raise ValueError(
                f"Insufficient cash: need {cost:.2f}, have {self.cash:.2f}"
            )

        self.cash -= cost

        if symbol in self.positions:
            pos = self.positions[symbol]
            total_qty = pos.quantity + quantity
            pos.avg_cost = (pos.cost_basis + cost) / total_qty
            pos.quantity = total_qty
        else:
            self.positions[symbol] = Position(
                symbol=symbol, quantity=quantity, avg_cost=price
            )

    def sell(self, symbol: str, quantity: float, price: float) -> None:
        if quantity <= 0:
            raise ValueError(f"Sell quantity must be positive, got {quantity}")
        if price <= 0:
            raise ValueError(f"Price must be positive, got {price}")
        if symbol not in self.positions:
            raise ValueError(f"No position in {symbol}")

        pos = self.positions[symbol]
        if quantity > pos.quantity + 1e-9:
            raise ValueError(
                f"Cannot sell {quantity} of {symbol}, only hold {pos.quantity}"
            )

        self.cash += quantity * price
        pos.quantity -= quantity

        if pos.quantity < 1e-12:
            del self.positions[symbol]

    def short_sell(self, symbol: str, quantity: float, price: float) -> None:
        """Open or add to a short position. Proceeds go to cash."""
        if quantity <= 0:
            raise ValueError(f"Short quantity must be positive, got {quantity}")
        if price <= 0:
            raise ValueError(f"Price must be positive, got {price}")

        self.cash += quantity * price

        if symbol in self.short_positions:
            pos = self.short_positions[symbol]
            total_qty = pos.quantity + quantity
            pos.avg_cost = (pos.cost_basis + quantity * price) / total_qty
            pos.quantity = total_qty
        else:
            self.short_positions[symbol] = Position(
                symbol=symbol, quantity=quantity, avg_cost=price
            )

    def cover(self, symbol: str, quantity: float, price: float) -> None:
        """Close (cover) a short position by buying back."""
        if quantity <= 0:
            raise ValueError(f"Cover quantity must be positive, got {quantity}")
        if price <= 0:
            raise ValueError(f"Price must be positive, got {price}")
        if symbol not in self.short_positions:
            raise ValueError(f"No short position in {symbol}")

        pos = self.short_positions[symbol]
        if quantity > pos.quantity + 1e-9:
            raise ValueError(
                f"Cannot cover {quantity} of {symbol}, only short {pos.quantity}"
            )

        cost = quantity * price
        # Allow small cash overdraft for short covers — the position was already
        # sized at entry, adverse price moves can exceed the original proceeds.
        # A 1% tolerance prevents crashes on large portfolios while still catching bugs.
        tolerance = max(1e-9, abs(self.cash) * 0.01)
        if cost > self.cash + tolerance:
            raise ValueError(
                f"Insufficient cash to cover: need {cost:.2f}, have {self.cash:.2f}"
            )

        self.cash -= cost
        pos.quantity -= quantity

        if pos.quantity < 1e-12:
            del self.short_positions[symbol]
