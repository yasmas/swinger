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

    def total_value(self, current_prices: dict[str, float]) -> float:
        holdings_value = sum(
            pos.quantity * current_prices[symbol]
            for symbol, pos in self.positions.items()
        )
        return self.cash + holdings_value

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
