import json
from fractions import Fraction

from liqbracket import Account, solve
from liqbracket.cli import main
from liqbracket.generate import generate_accounts

ACCOUNT = {
    "margin_mode": "cross",
    "wallet_balance": "1500",
    "long": {"size": "102", "entry_price": "100"},
    "tiers": [
        {"max_notional": "10000", "mmr": "0.01", "maintenance_amount": "0"},
        {"max_notional": "100000", "mmr": "0.05", "maintenance_amount": "400"},
        {"max_notional": None, "mmr": "0.1"},
    ],
}


def _write(tmp_path, data):
    path = tmp_path / "account.json"
    path.write_text(json.dumps(data))
    return str(path)


def test_solve_reports_exact_and_naive(tmp_path, capsys):
    assert main(["solve", _write(tmp_path, ACCOUNT), "--mode", "both"]) == 0
    out = json.loads(capsys.readouterr().out)
    (group,) = out["groups"]
    (boundary,) = group["exact"]["boundaries"]
    assert Fraction(boundary["price"]["exact"]) == Fraction(8700) / (102 * Fraction(99, 100))
    assert boundary["side"] == "lower" and boundary["tiers_at_boundary"] == {"long": 0}
    assert boundary["price"]["decimal"] == "86.15567439"
    assert group["naive"]["price_inside_entry_tiers"] is False
    assert Fraction(group["naive"]["price"]["exact"]) == Fraction(8300) / (102 * Fraction(95, 100))
    assert group["exact"]["safe_intervals"][0]["high"] is None


def test_solve_accepts_json_numbers_exactly(tmp_path, capsys):
    raw = json.dumps(ACCOUNT).replace('"0.01"', "0.01")
    path = tmp_path / "numbers.json"
    path.write_text(raw)
    assert main(["solve", str(path)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert "naive" not in out["groups"][0]
    assert out["groups"][0]["exact"]["boundaries"][0]["price"]["decimal"] == "86.15567439"


def test_solve_rejects_inconsistent_table_with_exit_code_2(tmp_path, capsys):
    bad = json.loads(json.dumps(ACCOUNT))
    bad["tiers"][1]["maintenance_amount"] = "401"
    assert main(["solve", _write(tmp_path, bad)]) == 2
    assert "inconsistent" in capsys.readouterr().err


def test_divergence_sweep_flags_sizes_over_the_floor(tmp_path, capsys):
    path = _write(tmp_path, ACCOUNT)
    args = ["divergence", path, "--leg", "long", "--from", "90", "--to", "110", "--steps", "4"]
    assert main([*args, "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert [r["size"] for r in rows] == ["90", "95", "100", "105", "110"]
    by_size = {r["size"]: r for r in rows}
    assert by_size["90"]["flags"] == [] and by_size["95"]["flags"] == []
    assert by_size["100"]["entry_tier"] == 1
    assert "outside_tier" in by_size["105"]["flags"]
    for r in rows:
        exact = [Fraction(p["exact"]) for p in r["exact"]]
        naive = Fraction(r["naive"]["exact"])
        assert (naive in exact) == ("wrong_price" not in r["flags"])
    assert main(args) == 0
    text = capsys.readouterr().out
    assert "naive formula disagrees" in text


def test_generate_is_deterministic_and_round_trips(capsys):
    assert main(["generate", "--count", "3", "--seed", "11", "--legs", "hedge"]) == 0
    first = capsys.readouterr().out
    assert main(["generate", "--count", "3", "--seed", "11", "--legs", "hedge"]) == 0
    assert capsys.readouterr().out == first
    accounts = [Account.from_dict(d) for d in json.loads(first)]
    assert all(a.long.is_open and a.short.is_open for a in accounts)
    originals = generate_accounts(11, 3, hedge=True)
    for parsed, original in zip(accounts, originals, strict=False):
        assert [b.price for b in solve(parsed)[0].boundaries] == [
            b.price for b in solve(original)[0].boundaries
        ]


def test_generated_positions_open_inside_their_table():
    for account in generate_accounts(5, 5000):
        cap = account.table.max_notional
        for leg in (account.long, account.short):
            if leg.is_open and cap is not None:
                assert leg.size * leg.entry_price <= cap
        solve(account)
