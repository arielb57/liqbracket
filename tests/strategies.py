"""Hypothesis strategies for small exact tier tables and accounts."""

from __future__ import annotations

from fractions import Fraction

from hypothesis import strategies as st

from liqbracket import CROSS, LONG, SHORT, Account, Leg, TierTable

mmrs = st.integers(min_value=0, max_value=200).map(lambda n: Fraction(n, 1000))
sizes = st.integers(min_value=1, max_value=80).map(lambda n: Fraction(n, 4))
entries = st.integers(min_value=1, max_value=60).map(Fraction)
wallets = st.integers(min_value=-200, max_value=2000).map(lambda n: Fraction(n, 4))


@st.composite
def tier_rows(draw, max_tiers: int = 5, bounded: bool | None = None):
    count = draw(st.integers(min_value=1, max_value=max_tiers))
    rates = sorted(draw(st.lists(mmrs, min_size=count, max_size=count)))
    cap = Fraction(draw(st.integers(min_value=1, max_value=60)))
    rows = []
    for rate in rates:
        rows.append((cap, rate))
        cap = cap * draw(st.sampled_from([Fraction(3, 2), 2, 3, 5]))
    if bounded is None:
        bounded = draw(st.booleans())
    if not bounded:
        rows[-1] = (None, rows[-1][1])
    return rows


@st.composite
def tables(draw, max_tiers: int = 5, bounded: bool | None = None):
    return TierTable(draw(tier_rows(max_tiers, bounded)))


@st.composite
def legs(draw, side: str, table: TierTable, allow_zero: bool = True):
    if allow_zero and draw(st.booleans()):
        return Leg(side)
    size = draw(sizes)
    entry = draw(entries)
    cap = table.max_notional
    if cap is not None and size * entry > cap:
        # Keep the opening notional inside the table so the position is one the venue allows.
        entry = cap / size
    return Leg(side, size, entry)


@st.composite
def cross_accounts(draw, table=None, shape: str = "any"):
    table = table if table is not None else draw(tables())
    if shape == "long":
        long, short = draw(legs(LONG, table, False)), Leg(SHORT)
    elif shape == "short":
        long, short = Leg(LONG), draw(legs(SHORT, table, False))
    elif shape == "hedge":
        long, short = draw(legs(LONG, table, False)), draw(legs(SHORT, table, False))
    else:
        long, short = draw(legs(LONG, table)), draw(legs(SHORT, table))
    return Account(table, CROSS, draw(wallets), long, short)
