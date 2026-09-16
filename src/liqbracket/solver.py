"""Exact piecewise-linear liquidation solver and the naive single-tier formula."""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction

from .account import Account, MarginGroup

LOWER = "lower"
UPPER = "upper"


@dataclass(frozen=True)
class Segment:
    """Price range [low, high] (high None = unbounded) on which excess = intercept + slope * p."""

    low: Fraction
    high: Fraction | None
    intercept: Fraction
    slope: Fraction
    tiers: tuple[int, ...]


@dataclass(frozen=True)
class Boundary:
    price: Fraction
    side: str  # LOWER: a fall through this price liquidates; UPPER: a rise does.
    tiers: dict[str, int]


@dataclass(frozen=True)
class Interval:
    """Open price interval (low, high); high None means unbounded."""

    low: Fraction
    high: Fraction | None

    def contains(self, price: Fraction) -> bool:
        return self.low < price and (self.high is None or price < self.high)


@dataclass(frozen=True)
class GroupResult:
    group: MarginGroup
    boundaries: tuple[Boundary, ...]
    safe_intervals: tuple[Interval, ...]
    price_ceiling: Fraction | None
    segments: tuple[Segment, ...]


def _linear_on(group: MarginGroup, tiers: tuple[int, ...]) -> tuple[Fraction, Fraction]:
    intercept = group.collateral
    slope = Fraction(0)
    for leg, index in zip(group.legs, tiers, strict=False):
        tier = group.table.tiers[index]
        intercept += -leg.direction * leg.size * leg.entry_price + tier.amount
        slope += leg.direction * leg.size - leg.size * tier.mmr
    return intercept, slope


def segments(group: MarginGroup) -> list[Segment]:
    """Split the price axis at every leg's tier floors and linearise excess on each piece."""
    ceiling = group.price_ceiling()
    points = {Fraction(0)}
    for leg in group.legs:
        for floor in group.table.floors:
            price = floor / leg.size
            if ceiling is None or price < ceiling:
                points.add(price)
    ordered = sorted(points)
    uppers: list[Fraction | None] = ordered[1:] + [ceiling]
    out = []
    for low, high in zip(ordered, uppers, strict=False):
        # Probe the segment's interior: at a breakpoint the notional sits exactly on a floor,
        # where both neighbouring tiers give the same margin but different slopes.
        probe = low + 1 if high is None else (low + high) / 2
        tiers = tuple(group.table.index_for(leg.size * probe) for leg in group.legs)
        intercept, slope = _linear_on(group, tiers)
        out.append(Segment(low, high, intercept, slope, tiers))
    return out


def _unsafe_parts(segs: list[Segment]) -> list[tuple[Fraction, Fraction | None]]:
    """Closed sub-intervals of each segment where excess <= 0, i.e. liquidation applies."""
    parts: list[tuple[Fraction, Fraction | None]] = []
    for seg in segs:
        if seg.slope == 0:
            if seg.intercept <= 0:
                parts.append((seg.low, seg.high))
            continue
        root = -seg.intercept / seg.slope
        if seg.slope > 0:
            # excess <= 0 for p <= root
            if root >= seg.low:
                hi = root if seg.high is None or root < seg.high else seg.high
                parts.append((seg.low, hi))
        else:
            # excess <= 0 for p >= root
            if seg.high is None or root <= seg.high:
                parts.append((max(root, seg.low), seg.high))
    merged: list[tuple[Fraction, Fraction | None]] = []
    for lo, hi in parts:
        if merged:
            mlo, mhi = merged[-1]
            if mhi is None:
                break
            if lo <= mhi:
                merged[-1] = (mlo, None if hi is None else max(mhi, hi))
                continue
        merged.append((lo, hi))
    return merged


def _tier_map(group: MarginGroup, price: Fraction) -> dict[str, int]:
    return {leg.side: group.table.index_for(leg.size * price) for leg in group.legs}


def solve_group(group: MarginGroup) -> GroupResult:
    ceiling = group.price_ceiling()
    if not group.legs:
        safe = (Interval(Fraction(0), None),) if group.collateral > 0 else ()
        return GroupResult(group, (), safe, None, ())
    segs = segments(group)
    unsafe = _unsafe_parts(segs)
    safe: list[Interval] = []
    start: Fraction | None = Fraction(0)
    for lo, hi in unsafe:
        if start is not None and lo > start:
            safe.append(Interval(start, lo))
        start = hi
        if start is None:
            break
    if start is not None and (ceiling is None or start < ceiling):
        safe.append(Interval(start, ceiling))

    boundaries: list[Boundary] = []
    for interval in safe:
        if interval.low > 0:
            boundaries.append(Boundary(interval.low, LOWER, _tier_map(group, interval.low)))
        # A safe interval may end at the table's price ceiling without the account being
        # liquidated there; only a zero of excess is a boundary.
        if interval.high is not None and group.excess(interval.high) == 0:
            boundaries.append(Boundary(interval.high, UPPER, _tier_map(group, interval.high)))
    for b in boundaries:
        if group.excess(b.price) != 0:
            raise AssertionError(f"internal error: excess at {b.price} is not zero")
    return GroupResult(group, tuple(boundaries), tuple(safe), ceiling, tuple(segs))


def solve(account: Account) -> list[GroupResult]:
    """Every liquidation boundary and safe interval, per margin group."""
    return [solve_group(group) for group in account.groups()]


@dataclass(frozen=True)
class NaiveResult:
    price: Fraction | None
    entry_tiers: dict[str, int]
    in_own_tier: bool | None


def naive_group(group: MarginGroup) -> NaiveResult:
    """The common shortcut: take each leg's tier at its entry notional and solve once."""
    if not group.legs:
        return NaiveResult(None, {}, None)
    tiers = tuple(group.table.index_for(leg.size * leg.entry_price) for leg in group.legs)
    entry_tiers = {leg.side: t for leg, t in zip(group.legs, tiers, strict=False)}
    intercept, slope = _linear_on(group, tiers)
    if slope == 0:
        return NaiveResult(None, entry_tiers, None)
    price = -intercept / slope
    if price <= 0:
        return NaiveResult(None, entry_tiers, None)
    # Closed on both ends: at a tier edge the neighbouring formulas agree on the margin.
    in_tier = all(
        group.table.tiers[t].floor <= leg.size * price
        and (group.table.tiers[t].cap is None or leg.size * price <= group.table.tiers[t].cap)
        for leg, t in zip(group.legs, tiers, strict=False)
    )
    return NaiveResult(price, entry_tiers, in_tier)


def naive(account: Account) -> list[NaiveResult]:
    return [naive_group(group) for group in account.groups()]


@dataclass(frozen=True)
class Comparison:
    exact: GroupResult
    naive: NaiveResult
    wrong_price: bool
    outside_tier: bool
    missed_boundary: bool
    error_bps: Fraction | None

    @property
    def diverges(self) -> bool:
        return self.wrong_price or self.outside_tier or self.missed_boundary


def compare_group(group: MarginGroup) -> Comparison:
    exact = solve_group(group)
    shortcut = naive_group(group)
    prices = [b.price for b in exact.boundaries]
    q = shortcut.price
    wrong = q is not None and q not in prices
    outside = shortcut.in_own_tier is False
    missed = any(p != q for p in prices)
    error = None
    if q is not None and prices:
        nearest = min(prices, key=lambda p: abs(p - q))
        error = abs(q - nearest) / nearest * 10000
    return Comparison(exact, shortcut, wrong, outside, missed, error)


def compare(account: Account) -> list[Comparison]:
    return [compare_group(group) for group in account.groups()]
