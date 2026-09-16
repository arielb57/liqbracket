"""Account model: one symbol, a long and a short leg, cross or isolated margin."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Any

from .numbers import to_fraction
from .tiers import TierTable

LONG = "long"
SHORT = "short"
CROSS = "cross"
ISOLATED = "isolated"


@dataclass(frozen=True)
class Leg:
    side: str
    size: Fraction = Fraction(0)
    entry_price: Fraction = Fraction(0)
    isolated_margin: Fraction = Fraction(0)

    def __post_init__(self) -> None:
        if self.side not in (LONG, SHORT):
            raise ValueError(f"side must be 'long' or 'short', got {self.side!r}")
        if self.size < 0:
            raise ValueError(f"{self.side}: size must be non-negative")
        if self.size > 0 and self.entry_price <= 0:
            raise ValueError(f"{self.side}: an open leg needs a positive entry price")
        if self.isolated_margin < 0:
            raise ValueError(f"{self.side}: isolated margin must be non-negative")

    @property
    def is_open(self) -> bool:
        return self.size > 0

    @property
    def direction(self) -> int:
        return 1 if self.side == LONG else -1

    def pnl(self, price: Fraction) -> Fraction:
        return self.direction * self.size * (price - self.entry_price)


@dataclass(frozen=True)
class MarginGroup:
    """A set of legs sharing one pool of collateral.

    Cross margin has one group holding both legs and the wallet balance. Isolated
    margin has one group per open leg, holding only that leg's isolated margin.
    """

    name: str
    collateral: Fraction
    legs: tuple[Leg, ...]
    table: TierTable = field(repr=False)

    def equity(self, price: Fraction) -> Fraction:
        return self.collateral + sum((leg.pnl(price) for leg in self.legs), Fraction(0))

    def maintenance(self, price: Fraction) -> Fraction:
        return sum((self.table.maintenance(leg.size * price) for leg in self.legs), Fraction(0))

    def excess(self, price: Fraction) -> Fraction:
        """Equity minus maintenance margin at ``price``, evaluated by direct tier lookup."""
        return self.equity(price) - self.maintenance(price)

    def price_ceiling(self) -> Fraction | None:
        """Highest price at which every leg's notional stays inside the tier table."""
        cap = self.table.max_notional
        if cap is None or not self.legs:
            return None
        return min(cap / leg.size for leg in self.legs)


@dataclass(frozen=True)
class Account:
    table: TierTable
    margin_mode: str = CROSS
    wallet_balance: Fraction = Fraction(0)
    long: Leg = Leg(LONG)
    short: Leg = Leg(SHORT)

    def __post_init__(self) -> None:
        if self.margin_mode not in (CROSS, ISOLATED):
            raise ValueError(f"margin_mode must be 'cross' or 'isolated', got {self.margin_mode!r}")
        if self.long.side != LONG or self.short.side != SHORT:
            raise ValueError("long and short legs are swapped")

    def groups(self) -> list[MarginGroup]:
        open_legs = [leg for leg in (self.long, self.short) if leg.is_open]
        if self.margin_mode == CROSS:
            return [MarginGroup(CROSS, self.wallet_balance, tuple(open_legs), self.table)]
        return [MarginGroup(leg.side, leg.isolated_margin, (leg,), self.table) for leg in open_legs]

    def with_wallet(self, wallet_balance: Fraction) -> Account:
        return Account(self.table, self.margin_mode, wallet_balance, self.long, self.short)

    def with_leg(self, leg: Leg) -> Account:
        if leg.side == LONG:
            return Account(self.table, self.margin_mode, self.wallet_balance, leg, self.short)
        return Account(self.table, self.margin_mode, self.wallet_balance, self.long, leg)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Account:
        if "tiers" not in data:
            raise ValueError("account is missing 'tiers'")
        rows = []
        for i, tier in enumerate(data["tiers"]):
            if "mmr" not in tier or "max_notional" not in tier:
                raise ValueError(f"tier {i} needs 'max_notional' (null for unbounded) and 'mmr'")
            rows.append((tier["max_notional"], tier["mmr"], tier.get("maintenance_amount")))
        table = TierTable(rows)
        mode = data.get("margin_mode", CROSS)

        def leg(side: str) -> Leg:
            raw = data.get(side) or {}
            return Leg(
                side,
                to_fraction(raw.get("size", 0)),
                to_fraction(raw.get("entry_price", 0)),
                to_fraction(raw.get("isolated_margin", 0)),
            )

        return cls(table, mode, to_fraction(data.get("wallet_balance", 0)), leg(LONG), leg(SHORT))

    def to_dict(self) -> dict[str, Any]:
        def leg(item: Leg) -> dict[str, str]:
            out = {"size": str(item.size), "entry_price": str(item.entry_price)}
            if self.margin_mode == ISOLATED:
                out["isolated_margin"] = str(item.isolated_margin)
            return out

        return {
            "margin_mode": self.margin_mode,
            "wallet_balance": str(self.wallet_balance),
            "long": leg(self.long),
            "short": leg(self.short),
            "tiers": self.table.to_rows(),
        }
