"""Property tests of the exact solver against independent oracles."""

from fractions import Fraction

from hypothesis import assume, given
from hypothesis import strategies as st
from strategies import cross_accounts, entries, legs, tables, wallets

from liqbracket import CROSS, ISOLATED, LONG, LOWER, SHORT, UPPER, Account, Leg, TierTable
from liqbracket.solver import compare_group, solve_group


def _only_group(account):
    (group,) = account.groups()
    return group


def _grid(group, result, count=300):
    """Rational grid over the interesting price range, avoiding every reported boundary."""
    marks = [Fraction(1)] + [b.price for b in result.boundaries]
    marks += [floor / leg.size for leg in group.legs for floor in group.table.floors]
    marks += [leg.entry_price for leg in group.legs]
    top = max(marks) * 2 + 1
    ceiling = group.price_ceiling()
    if ceiling is not None:
        top = min(top, ceiling)
    boundary_prices = {b.price for b in result.boundaries}
    n = count
    while True:
        # Strictly inside (0, top): a boundary may sit exactly on the table's price ceiling.
        points = [top * k / (n + 1) for k in range(1, n + 1)]
        if not boundary_prices.intersection(points):
            return top, points
        n += 1


@given(st.sampled_from(["any", "hedge"]).flatmap(lambda shape: cross_accounts(shape=shape)))
def test_excess_is_exactly_zero_at_every_boundary(account):
    group = _only_group(account)
    result = solve_group(group)
    for b in result.boundaries:
        assert group.equity(b.price) == group.maintenance(b.price)
        assert b.price > 0
    assert len(result.boundaries) <= 2


@given(st.sampled_from(["any", "hedge"]).flatmap(lambda shape: cross_accounts(shape=shape)))
def test_grid_scan_finds_the_same_sign_changes_and_no_others(account):
    group = _only_group(account)
    result = solve_group(group)
    top, points = _grid(group, result)
    safe = [group.excess(p) > 0 for p in points]
    for p, is_safe in zip(points, safe, strict=False):
        assert is_safe == any(i.contains(p) for i in result.safe_intervals), p
    prices = [b.price for b in result.boundaries]
    previous_point, previous_safe = Fraction(0), None
    for p, is_safe in zip(points, safe, strict=False):
        inside = sum(1 for b in prices if previous_point < b < p)
        if previous_safe is not None:
            assert (is_safe != previous_safe) == (inside % 2 == 1), (previous_point, p)
        previous_point, previous_safe = p, is_safe
    # Boundaries beyond the scanned range would be unverified; the grid always covers them.
    assert all(b <= top for b in prices)


@given(cross_accounts())
def test_safe_and_unsafe_sides_of_each_boundary(account):
    group = _only_group(account)
    result = solve_group(group)
    for interval in result.safe_intervals:
        high = interval.high if interval.high is not None else interval.low * 2 + 10
        assert group.excess((interval.low + high) / 2) > 0
    for b in result.boundaries:
        eps = Fraction(1, 10**9)
        outside = b.price - eps if b.side == LOWER else b.price + eps
        ceiling = group.price_ceiling()
        if outside > 0 and (ceiling is None or outside <= ceiling):
            assert group.excess(outside) < 0


@given(cross_accounts(), st.integers(min_value=1, max_value=4000).map(lambda n: Fraction(n, 4)))
def test_more_wallet_never_moves_boundaries_inward(account, extra):
    before = solve_group(_only_group(account))
    after = solve_group(_only_group(account.with_wallet(account.wallet_balance + extra)))
    for interval in before.safe_intervals:
        assert any(
            other.low <= interval.low
            and (other.high is None or (interval.high is not None and interval.high <= other.high))
            for other in after.safe_intervals
        )
    lower_before = [b.price for b in before.boundaries if b.side == LOWER]
    lower_after = [b.price for b in after.boundaries if b.side == LOWER]
    upper_before = [b.price for b in before.boundaries if b.side == UPPER]
    upper_after = [b.price for b in after.boundaries if b.side == UPPER]
    if lower_before and lower_after:
        assert lower_after[0] <= lower_before[0]
    if upper_before and upper_after:
        assert upper_after[-1] >= upper_before[-1]
    if not account.short.is_open:
        assert not upper_before, "a lone long cannot be liquidated by a rise"
    if not account.long.is_open:
        assert not lower_before, "a lone short cannot be liquidated by a fall"


def _closed_form(side, size, entry, wallet, tier):
    if side == LONG:
        return (size * entry - wallet - tier.amount) / (size * (1 - tier.mmr))
    return (wallet + size * entry + tier.amount) / (size * (1 + tier.mmr))


@given(st.data(), st.sampled_from([LONG, SHORT]))
def test_single_tier_table_matches_closed_form(data, side):
    table = TierTable([(None, data.draw(st.integers(0, 200).map(lambda n: Fraction(n, 1000))))])
    leg = data.draw(legs(side, table, allow_zero=False))
    wallet = data.draw(wallets)
    account = Account(table, CROSS, wallet).with_leg(leg)
    result = solve_group(_only_group(account))
    expected = _closed_form(side, leg.size, leg.entry_price, wallet, table.tiers[0])
    if expected > 0:
        assert [b.price for b in result.boundaries] == [expected]
        assert result.boundaries[0].side == (LOWER if side == LONG else UPPER)
    else:
        assert result.boundaries == ()


@given(tables(), st.data(), st.sampled_from([LONG, SHORT]))
def test_leg_staying_inside_one_tier_matches_closed_form(table, data, side):
    leg = data.draw(legs(side, table, allow_zero=False))
    wallet = data.draw(wallets)
    account = Account(table, CROSS, wallet).with_leg(leg)
    entry_tier = table.index_for(leg.size * leg.entry_price)
    tier = table.tiers[entry_tier]
    expected = _closed_form(side, leg.size, leg.entry_price, wallet, tier)
    assume(expected > 0)
    notional = leg.size * expected
    assume(tier.floor <= notional and (tier.cap is None or notional <= tier.cap))
    comparison = compare_group(_only_group(account))
    assert [b.price for b in comparison.exact.boundaries] == [expected]
    assert comparison.naive.price == expected
    assert not comparison.diverges


@given(
    st.integers(0, 30).map(lambda n: Fraction(n, 1000)),
    st.integers(1, 170).map(lambda n: Fraction(n, 1000)),
    st.integers(1, 99).map(lambda n: Fraction(n, 100)),
    st.integers(1, 200).map(Fraction),
    st.integers(1, 20).map(lambda n: Fraction(n, 2)),
    entries,
    entries,
)
def test_hedge_built_with_two_boundaries_returns_both(
    m1, bump, t, floor, long_size, e_long, e_short
):
    m2 = m1 + bump
    table = TierTable([(floor, m1), (None, m2)])
    lo = (1 - m2) / (1 + m2)
    hi = (1 - m1) / (1 + m1)
    short_size = long_size * (lo + (hi - lo) * t)
    # Initial slope long*(1-m1) - short*(1+m1) > 0, final slope with m2 < 0, so excess is
    # concave and rises then falls; a wallet between its value at 0 and its peak gives two roots.
    long, short = Leg(LONG, long_size, e_long), Leg(SHORT, short_size, e_short)
    probe = Account(table, CROSS, Fraction(0), long, short)
    group = _only_group(probe)
    kinks = [floor / long_size, floor / short_size]
    peak = max(group.excess(k) for k in kinks)
    at_zero = group.excess(Fraction(0))
    assert peak > at_zero
    wallet = -(at_zero + peak) / 2
    group = _only_group(probe.with_wallet(wallet))
    comparison = compare_group(group)
    result = comparison.exact
    assert [b.side for b in result.boundaries] == [LOWER, UPPER]
    low, high = (b.price for b in result.boundaries)
    assert low < max(kinks, key=group.excess) < high
    assert group.excess(low) == 0 and group.excess(high) == 0
    assert comparison.missed_boundary


@given(tables(bounded=True), st.integers(1, 40).map(lambda n: Fraction(n, 4)), entries)
def test_fully_offset_hedge_within_a_bounded_table_has_no_boundary(table, size, entry):
    cap = table.max_notional
    entry = min(entry, cap / size)
    long, short = Leg(LONG, size, entry), Leg(SHORT, size, entry)
    wallet = 2 * table.maintenance(cap) + 1
    comparison = compare_group(_only_group(Account(table, CROSS, wallet, long, short)))
    result = comparison.exact
    assert result.boundaries == ()
    assert len(result.safe_intervals) == 1
    assert result.safe_intervals[0].low == 0
    assert result.safe_intervals[0].high == cap / size


@given(tables(bounded=False), st.integers(1, 40).map(lambda n: Fraction(n, 4)), entries, wallets)
def test_fully_offset_hedge_on_unbounded_table_keeps_one_upper_boundary(table, size, entry, wallet):
    # Equity is flat but margin on two legs grows without limit, so a rise still liquidates.
    assume(wallet > 0 and table.tiers[-1].mmr > 0)
    long, short = Leg(LONG, size, entry), Leg(SHORT, size, entry)
    result = solve_group(_only_group(Account(table, CROSS, wallet, long, short)))
    assert [b.side for b in result.boundaries] == [UPPER]
    price = result.boundaries[0].price
    assert 2 * table.maintenance(size * price) == wallet


@given(tables(), st.data(), st.sampled_from([LONG, SHORT]))
def test_isolated_leg_equals_cross_account_holding_only_that_margin(table, data, side):
    leg = data.draw(legs(side, table, allow_zero=False))
    margin = data.draw(st.integers(0, 4000).map(lambda n: Fraction(n, 4)))
    isolated_leg = Leg(side, leg.size, leg.entry_price, margin)
    other = Leg(SHORT if side == LONG else LONG, Fraction(3), Fraction(7), Fraction(1))
    isolated = Account(table, ISOLATED, Fraction(10**6)).with_leg(isolated_leg).with_leg(other)
    groups = {g.name: g for g in isolated.groups()}
    assert set(groups) == {LONG, SHORT}
    cross = Account(table, CROSS, margin).with_leg(leg)
    a = solve_group(groups[side])
    b = solve_group(_only_group(cross))
    assert [(x.price, x.side) for x in a.boundaries] == [(x.price, x.side) for x in b.boundaries]
