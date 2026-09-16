from decimal import Decimal
from fractions import Fraction

import pytest
from hypothesis import given
from hypothesis import strategies as st
from strategies import tier_rows

from liqbracket import TierTable, TierTableError


def test_amounts_derived_like_venue_tables():
    table = TierTable(
        [("50000", "0.004"), ("250000", "0.005"), ("1000000", "0.01"), (None, "0.025")]
    )
    assert [t.amount for t in table.tiers] == [0, 50, 1300, 16300]
    assert [t.floor for t in table.tiers] == [0, 50000, 250000, 1000000]


def test_given_consistent_amounts_are_accepted():
    table = TierTable([("50000", "0.004", "0"), ("250000", "0.005", "50"), (None, "0.01", "1300")])
    # 250,000 * 0.5% - 50 == 250,000 * 1% - 1,300
    assert table.maintenance(Fraction(250000)) == Fraction(1200)


@pytest.mark.parametrize(
    "rows, message",
    [
        ([("50000", "0.004", "0"), (None, "0.005", "49")], "inconsistent"),
        ([("50000", "0.004", "1")], "inconsistent"),
        ([("100", "0.01"), ("100", "0.02")], "must exceed floor"),
        ([(None, "0.01"), ("100", "0.02")], "only the last tier"),
        ([("100", "0.02"), (None, "0.01")], "below the previous"),
        ([("100", "1")], "must be in"),
        ([("100", "-0.01")], "must be in"),
        ([], "at least one"),
    ],
)
def test_malformed_tables_are_rejected(rows, message):
    with pytest.raises(TierTableError, match=message):
        TierTable(rows)


def test_binary_floats_are_rejected_but_decimals_are_exact():
    with pytest.raises(TypeError, match="inexact"):
        TierTable([(100, 0.01)])
    assert TierTable([(100, Decimal("0.01"))]).tiers[0].mmr == Fraction(1, 100)


def test_floor_belongs_to_the_tier_it_opens_and_cap_is_enforced():
    table = TierTable([("100", "0.01"), ("500", "0.02")])
    assert table.index_for(Fraction(99)) == 0
    assert table.index_for(Fraction(100)) == 1
    assert table.index_for(Fraction(500)) == 1
    with pytest.raises(ValueError, match="exceeds"):
        table.index_for(Fraction(501))


@given(tier_rows(max_tiers=8))
def test_maintenance_is_continuous_at_every_floor(rows):
    table = TierTable(rows)
    for below, above in zip(table.tiers, table.tiers[1:], strict=False):
        floor = above.floor
        assert below.maintenance(floor) == above.maintenance(floor)
        # The margin function is convex: the next tier's line never undercuts inside the lower tier.
        assert above.maintenance(below.floor) <= below.maintenance(below.floor)
    assert table.maintenance(Fraction(0)) == 0


@given(tier_rows(max_tiers=6), st.data())
def test_any_perturbed_amount_is_rejected(rows, data):
    table = TierTable(rows)
    with_amounts = [(t.cap, t.mmr, t.amount) for t in table.tiers]
    assert TierTable(with_amounts).tiers == table.tiers
    index = data.draw(st.integers(min_value=0, max_value=len(with_amounts) - 1))
    delta = data.draw(
        st.fractions(min_value=-1000, max_value=1000, max_denominator=100).filter(lambda d: d != 0)
    )
    cap, mmr, amount = with_amounts[index]
    with_amounts[index] = (cap, mmr, amount + delta)
    with pytest.raises(TierTableError, match="inconsistent"):
        TierTable(with_amounts)
