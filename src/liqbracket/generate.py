"""Synthetic tier tables and accounts, deterministic for a given seed.

The shapes imitate public venue tables: geometric notional caps, maintenance rates
that step up with size, leverage from 1x to 125x. Positions are deliberately
placed near tier floors often, since that is where the shortcut formula fails.
"""

from __future__ import annotations

import random
from fractions import Fraction

from .account import CROSS, ISOLATED, LONG, SHORT, Account, Leg
from .tiers import TierTable

MMR_STEPS = [Fraction(n, 10000) for n in (40, 50, 65, 100, 125, 250, 500, 1000, 1250, 1500, 2500)]


def random_table(rng: random.Random, bounded: bool | None = None) -> TierTable:
    count = rng.randint(1, 8)
    start = rng.choice([Fraction(n) for n in (1000, 5000, 10000, 50000, 250000)])
    rates = sorted(rng.sample(range(len(MMR_STEPS)), count))
    rows: list[tuple[object, object]] = []
    cap = start
    for i, r in enumerate(rates):
        rows.append((cap, MMR_STEPS[r]))
        if i < count - 1:
            cap = cap * rng.choice([2, Fraction(5, 2), 4, 5, 10])
    if bounded is None:
        bounded = rng.random() < 0.5
    if not bounded:
        rows[-1] = (None, rows[-1][1])
    return TierTable(rows)


def _round(value: Fraction, denominator: int) -> Fraction:
    return Fraction(round(value * denominator), denominator)


def _price(rng: random.Random) -> Fraction:
    exponent = rng.randint(-1, 5)
    return Fraction(rng.randint(1000, 9999), 1000) * Fraction(10) ** exponent


def _size_near_floor(rng: random.Random, table: TierTable, price: Fraction) -> Fraction:
    floors = table.floors
    if floors and rng.random() < 0.6:
        target = rng.choice(floors) * (1 + Fraction(rng.randint(-300, 300), 10000))
    else:
        top = table.max_notional or table.tiers[-1].floor * 4 or Fraction(100000)
        target = top * Fraction(rng.randint(1, 900), 1000)
    cap = table.max_notional
    if cap is not None and target > cap:
        target = cap * Fraction(9, 10)
    size = max(_round(target / price, 1000), Fraction(1, 1000))
    while cap is not None and size * price > cap:
        size -= Fraction(1, 1000)
    return size


def random_account(
    rng: random.Random, hedge: bool | None = None, margin_mode: str = CROSS
) -> Account:
    table = random_table(rng)
    if hedge is None:
        hedge = rng.random() < 0.5
    sides = [LONG, SHORT] if hedge else [rng.choice([LONG, SHORT])]
    base = _price(rng)
    if table.max_notional is not None:
        # The smallest size is 0.001, so the price must leave room for it under the cap.
        base = min(base, table.max_notional * 800)
    legs = {}
    total_notional = Fraction(0)
    for side in sides:
        entry = _round(base * (1 + Fraction(rng.randint(-500, 500), 10000)), 10000)
        size = _size_near_floor(rng, table, entry)
        leverage = rng.randint(1, 125)
        legs[side] = Leg(
            side, size, entry, max(_round(size * entry / leverage, 100), Fraction(1, 100))
        )
        total_notional += size * entry
    wallet = _round(total_notional / rng.randint(1, 125), 100)
    return Account(
        table,
        margin_mode,
        wallet,
        legs.get(LONG, Leg(LONG)),
        legs.get(SHORT, Leg(SHORT)),
    )


def generate_accounts(
    seed: int, count: int, hedge: bool | None = None, margin_mode: str = CROSS
) -> list[Account]:
    rng = random.Random(seed)
    if margin_mode not in (CROSS, ISOLATED):
        raise ValueError("margin_mode must be 'cross' or 'isolated'")
    return [random_account(rng, hedge, margin_mode) for _ in range(count)]
