"""Tiered maintenance-margin tables with exact cumulative maintenance amounts."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from fractions import Fraction

from .numbers import to_fraction


class TierTableError(ValueError):
    """Raised when a tier table is malformed or its maintenance amounts are inconsistent."""


@dataclass(frozen=True)
class Tier:
    """One tier: notional in [floor, cap), margin = notional * mmr - amount."""

    floor: Fraction
    cap: Fraction | None
    mmr: Fraction
    amount: Fraction

    def maintenance(self, notional: Fraction) -> Fraction:
        return notional * self.mmr - self.amount


class TierTable:
    """An ordered, contiguous tier table starting at notional zero.

    Tiers are given as ``(cap, mmr)`` or ``(cap, mmr, amount)``; a tier's floor is the
    previous tier's cap. The cumulative maintenance amount of tier ``i`` is derived as
    ``amount[i-1] + floor[i] * (mmr[i] - mmr[i-1])``, which is the unique choice that
    keeps maintenance margin continuous at every floor. A supplied amount that differs
    from the derived one is rejected.
    """

    def __init__(self, rows: Sequence[Sequence[object]]):
        if not rows:
            raise TierTableError("a tier table needs at least one tier")
        tiers = []
        floor = Fraction(0)
        amount = Fraction(0)
        prev_mmr: Fraction | None = None
        for index, row in enumerate(rows):
            if len(row) not in (2, 3):
                raise TierTableError(f"tier {index}: expected (cap, mmr[, amount])")
            cap = None if row[0] is None else to_fraction(row[0])
            mmr = to_fraction(row[1])
            if cap is not None and cap <= floor:
                raise TierTableError(f"tier {index}: cap {cap} must exceed floor {floor}")
            if cap is None and index != len(rows) - 1:
                raise TierTableError(f"tier {index}: only the last tier may be unbounded")
            if not (0 <= mmr < 1):
                raise TierTableError(f"tier {index}: mmr {mmr} must be in [0, 1)")
            if prev_mmr is not None:
                if mmr < prev_mmr:
                    raise TierTableError(
                        f"tier {index}: mmr {mmr} is below the previous tier's {prev_mmr}"
                    )
                amount = amount + floor * (mmr - prev_mmr)
            if len(row) == 3 and row[2] is not None:
                given = to_fraction(row[2])
                if given != amount:
                    raise TierTableError(
                        f"tier {index}: maintenance amount {given} is inconsistent; "
                        f"continuity requires {amount}"
                    )
            tiers.append(Tier(floor=floor, cap=cap, mmr=mmr, amount=amount))
            prev_mmr = mmr
            if cap is not None:
                floor = cap
        self.tiers: tuple[Tier, ...] = tuple(tiers)

    @property
    def max_notional(self) -> Fraction | None:
        return self.tiers[-1].cap

    @property
    def floors(self) -> list[Fraction]:
        return [t.floor for t in self.tiers[1:]]

    def index_for(self, notional: Fraction) -> int:
        """Index of the tier containing ``notional`` (a floor belongs to the tier it opens)."""
        if notional < 0:
            raise ValueError("notional must be non-negative")
        cap = self.max_notional
        if cap is not None and notional > cap:
            raise ValueError(f"notional {notional} exceeds the table maximum {cap}")
        for i in range(len(self.tiers) - 1, -1, -1):
            if notional >= self.tiers[i].floor:
                return i
        return 0

    def maintenance(self, notional: Fraction) -> Fraction:
        return self.tiers[self.index_for(notional)].maintenance(notional)

    def to_rows(self) -> list[dict[str, object]]:
        return [
            {
                "max_notional": None if t.cap is None else str(t.cap),
                "mmr": str(t.mmr),
                "maintenance_amount": str(t.amount),
            }
            for t in self.tiers
        ]
