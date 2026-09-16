from fractions import Fraction

import pytest

from liqbracket import (
    CROSS,
    ISOLATED,
    LONG,
    LOWER,
    SHORT,
    UPPER,
    Account,
    Leg,
    TierTable,
    compare,
    naive,
    solve,
)

F = Fraction
TABLE = TierTable([("10000", "0.01"), ("100000", "0.05"), (None, "0.1")])


def test_long_just_over_a_floor_is_liquidated_earlier_than_naive():
    # Entry notional 10,200 sits in tier 1 (5%); the fall takes it back into tier 0 (1%).
    account = Account(TABLE, CROSS, F(1500), Leg(LONG, F(102), F(100)))
    (c,) = compare(account)
    (b,) = c.exact.boundaries
    assert b.side == LOWER and b.tiers == {LONG: 0}
    assert b.price == F(8700) / (102 * F(99, 100))  # 102 * p * 0.99 = 10200 - 1500
    assert c.naive.price == F(8300) / (102 * F(95, 100))  # 102 * p * 0.95 = 10200 - 1500 - 400
    assert c.naive.entry_tiers == {LONG: 1}
    assert c.naive.price < b.price
    assert c.wrong_price and c.outside_tier and c.missed_boundary
    assert c.error_bps == abs(c.naive.price - b.price) / b.price * 10000


def test_root_exactly_on_a_tier_breakpoint_is_reported_once():
    size = F(50)
    kink = F(10000) / size  # price 200
    wallet = size * 250 - (size * kink - size * kink * F(1, 100))  # excess(200) == 0
    account = Account(TABLE, CROSS, wallet, Leg(LONG, size, F(250)))
    (result,) = solve(account)
    assert [b.price for b in result.boundaries] == [kink]
    assert result.group.excess(kink) == 0
    assert result.safe_intervals[0].low == kink


def test_zero_size_legs_are_ignored_entirely():
    base = Account(TABLE, CROSS, F(1000), Leg(LONG, F(3), F(2000)))
    with_ghost = base.with_leg(Leg(SHORT, F(0), F(0)))
    assert [b.price for b in solve(base)[0].boundaries] == [
        b.price for b in solve(with_ghost)[0].boundaries
    ]
    empty = Account(TABLE, CROSS, F(5))
    (result,) = solve(empty)
    assert result.boundaries == ()
    assert [(i.low, i.high) for i in result.safe_intervals] == [(0, None)]
    assert naive(empty)[0].price is None


def test_boundary_below_zero_price_is_reported_as_none():
    # Fully collateralised long: the equation's root is negative.
    account = Account(TABLE, CROSS, F(10000), Leg(LONG, F(1), F(5000)))
    (c,) = compare(account)
    assert c.exact.boundaries == ()
    assert [(i.low, i.high) for i in c.exact.safe_intervals] == [(0, None)]
    assert c.naive.price is None
    assert not c.diverges


def test_zero_slope_segment_with_zero_excess_is_unsafe_everywhere():
    # Long 101, short 99 at 1% margin: slope 101*0.99 - 99*1.01 == 0 inside tier 0.
    table = TierTable([(None, "0.01")])
    long, short = Leg(LONG, F(101), F(10)), Leg(SHORT, F(99), F(10))
    at_zero = Account(table, CROSS, F(0), long, short)
    (group,) = at_zero.groups()
    wallet = -group.excess(F(0))
    account = at_zero.with_wallet(wallet)
    (result,) = solve(account)
    assert result.segments[0].slope == 0
    assert result.group.excess(F(123)) == 0
    assert result.boundaries == () and result.safe_intervals == ()
    (safe,) = solve(account.with_wallet(wallet + 1))
    assert safe.boundaries == ()
    assert [(i.low, i.high) for i in safe.safe_intervals] == [(0, None)]


def test_zero_slope_segment_bounded_by_tier_change():
    # Flat and safe in tier 0; a higher rate beyond the floor turns the slope negative.
    table = TierTable([("1010", "0.01"), (None, "0.02")])
    long, short = Leg(LONG, F(101), F(10)), Leg(SHORT, F(99), F(10))
    account = Account(table, CROSS, F(100), long, short)
    (result,) = solve(account)
    (b,) = result.boundaries
    assert b.side == UPPER and b.price > 10
    assert result.group.excess(b.price) == 0
    assert result.segments[0].slope == 0 and result.segments[0].intercept > 0


def test_account_liquidated_at_every_price_has_no_boundaries_or_safe_set():
    account = Account(TABLE, CROSS, F(-150), short=Leg(SHORT, F(1), F(100)))
    (result,) = solve(account)
    assert result.boundaries == () and result.safe_intervals == ()


def test_isolated_mode_solves_each_leg_separately():
    account = Account(
        TABLE, ISOLATED, F(0), Leg(LONG, F(1), F(1000), F(100)), Leg(SHORT, F(2), F(1000), F(100))
    )
    long_result, short_result = solve(account)
    assert long_result.group.name == LONG and short_result.group.name == SHORT
    assert [b.price for b in long_result.boundaries] == [F(900, 1) / F(99, 100)]
    assert [b.price for b in short_result.boundaries] == [F(2100) / (2 * F(101, 100))]


def test_naive_price_is_flagged_when_it_leaves_its_tier_on_the_short_side():
    # Short entry notional 9,900 is in tier 0; a rise to the boundary crosses into tier 1.
    account = Account(TABLE, CROSS, F(1000), short=Leg(SHORT, F(99), F(100)))
    (c,) = compare(account)
    (b,) = c.exact.boundaries
    assert b.side == UPPER and b.tiers == {SHORT: 1}
    assert c.outside_tier and c.wrong_price
    assert c.naive.price > b.price  # the naive formula promises more room than exists


@pytest.mark.parametrize(
    "build, message",
    [
        (lambda: Leg(LONG, F(-1), F(10)), "non-negative"),
        (lambda: Leg(LONG, F(1), F(0)), "positive entry"),
        (lambda: Leg(LONG, F(1), F(10), F(-1)), "isolated margin"),
        (lambda: Leg("both", F(1), F(10)), "side must be"),
        (lambda: Account(TABLE, "portfolio"), "margin_mode"),
        (lambda: Account(TABLE, long=Leg(SHORT, F(1), F(10))), "swapped"),
    ],
)
def test_invalid_accounts_are_rejected(build, message):
    with pytest.raises(ValueError, match=message):
        build()


def test_from_dict_rejects_inconsistent_amounts_and_missing_fields():
    good = {
        "wallet_balance": "100",
        "long": {"size": "1", "entry_price": "1000"},
        "tiers": [{"max_notional": "10000", "mmr": "0.01"}, {"max_notional": None, "mmr": "0.05"}],
    }
    assert Account.from_dict(good).table.tiers[1].amount == 400
    bad = dict(good, tiers=[dict(t) for t in good["tiers"]])
    bad["tiers"][1]["maintenance_amount"] = "399"
    with pytest.raises(ValueError, match="inconsistent"):
        Account.from_dict(bad)
    with pytest.raises(ValueError, match="tiers"):
        Account.from_dict({"wallet_balance": "1"})
