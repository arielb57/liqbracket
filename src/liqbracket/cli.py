"""Command-line interface: solve, divergence, generate."""

from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal
from fractions import Fraction
from typing import Any

from .account import ISOLATED, LONG, SHORT, Account, Leg
from .generate import generate_accounts
from .numbers import fmt_decimal, to_fraction
from .solver import GroupResult, NaiveResult, compare_group, naive_group, solve_group
from .tiers import TierTableError


def _price(value: Fraction | None) -> dict[str, str] | None:
    if value is None:
        return None
    return {"decimal": fmt_decimal(value), "exact": str(value)}


def _exact_json(result: GroupResult) -> dict[str, Any]:
    return {
        "boundaries": [
            {"side": b.side, "price": _price(b.price), "tiers_at_boundary": b.tiers}
            for b in result.boundaries
        ],
        "safe_intervals": [
            {"low": _price(i.low), "high": _price(i.high)} for i in result.safe_intervals
        ],
        "price_ceiling": _price(result.price_ceiling),
    }


def _naive_json(result: NaiveResult) -> dict[str, Any]:
    return {
        "price": _price(result.price),
        "entry_tiers": result.entry_tiers,
        "price_inside_entry_tiers": result.in_own_tier,
    }


def _load_account(path: str) -> Account:
    text = sys.stdin.read() if path == "-" else open(path, encoding="utf-8").read()
    data = json.loads(text, parse_float=Decimal)
    return Account.from_dict(data)


def cmd_solve(args: argparse.Namespace) -> int:
    account = _load_account(args.account)
    groups = []
    for group in account.groups():
        entry: dict[str, Any] = {"group": group.name}
        if args.mode in ("exact", "both"):
            entry["exact"] = _exact_json(solve_group(group))
        if args.mode in ("naive", "both"):
            entry["naive"] = _naive_json(naive_group(group))
        groups.append(entry)
    json.dump({"margin_mode": account.margin_mode, "groups": groups}, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


def cmd_divergence(args: argparse.Namespace) -> int:
    account = _load_account(args.account)
    base: Leg = account.long if args.leg == LONG else account.short
    if base.entry_price <= 0:
        raise ValueError(f"the {args.leg} leg needs an entry price to sweep its size")
    lo, hi = to_fraction(args.size_from), to_fraction(args.size_to)
    if not (0 < lo <= hi) or args.steps < 1:
        raise ValueError("need 0 < --from <= --to and --steps >= 1")
    rows = []
    for k in range(args.steps + 1):
        size = lo + (hi - lo) * k / args.steps
        # Isolated margin scales with size so the sweep keeps leverage fixed.
        margin = base.isolated_margin * size / base.size if base.size > 0 else Fraction(0)
        leg = Leg(base.side, size, base.entry_price, margin)
        swept = account.with_leg(leg)
        for group in swept.groups():
            if args.leg not in [g.side for g in group.legs]:
                continue
            c = compare_group(group)
            rows.append(
                {
                    "size": size,
                    "group": group.name,
                    "entry_notional": size * base.entry_price,
                    "entry_tier": c.naive.entry_tiers.get(args.leg),
                    "naive": c.naive.price,
                    "exact": [b.price for b in c.exact.boundaries],
                    "error_bps": c.error_bps,
                    "flags": [
                        name
                        for name, on in (
                            ("wrong_price", c.wrong_price),
                            ("outside_tier", c.outside_tier),
                            ("missed_boundary", c.missed_boundary),
                        )
                        if on
                    ],
                }
            )
    if args.json:
        out = [
            {
                **r,
                "size": fmt_decimal(r["size"]),
                "entry_notional": fmt_decimal(r["entry_notional"]),
                "naive": _price(r["naive"]),
                "exact": [_price(p) for p in r["exact"]],
                "error_bps": None if r["error_bps"] is None else fmt_decimal(r["error_bps"], 4),
            }
            for r in rows
        ]
        json.dump(out, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0
    header = (
        f"{'size':>12} {'notional':>14} {'tier':>4} {'naive':>14} {'exact':>29} "
        f"{'err bps':>9}  flags"
    )
    print(header)
    print("-" * len(header))
    diverged = 0
    for r in rows:
        exact = ", ".join(fmt_decimal(p, 2) for p in r["exact"]) or "none"
        naive_text = "none" if r["naive"] is None else fmt_decimal(r["naive"], 2)
        err = "-" if r["error_bps"] is None else fmt_decimal(r["error_bps"], 2)
        diverged += bool(r["flags"])
        print(
            f"{fmt_decimal(r['size'], 4):>12} {fmt_decimal(r['entry_notional'], 2):>14} "
            f"{r['entry_tier']:>4} {naive_text:>14} {exact:>29} {err:>9}  {','.join(r['flags'])}"
        )
    print(f"\n{diverged} of {len(rows)} sizes: naive formula disagrees with the exact solver")
    return 0


def cmd_generate(args: argparse.Namespace) -> int:
    hedge = {"any": None, "hedge": True, "single": False}[args.legs]
    accounts = generate_accounts(args.seed, args.count, hedge, args.margin_mode)
    payload: Any = [a.to_dict() for a in accounts]
    if args.count == 1:
        payload = payload[0]
    json.dump(payload, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="liqbracket",
        description="Exact liquidation prices under tiered maintenance margin.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("solve", help="liquidation boundaries and safe intervals for an account")
    p.add_argument("account", help="account JSON file, or - for stdin")
    p.add_argument("--mode", choices=["exact", "naive", "both"], default="exact")
    p.set_defaults(func=cmd_solve)

    p = sub.add_parser("divergence", help="sweep one leg's size and compare naive vs exact")
    p.add_argument("account", help="account JSON file, or - for stdin")
    p.add_argument("--leg", choices=[LONG, SHORT], default=LONG)
    p.add_argument("--from", dest="size_from", required=True, help="smallest size")
    p.add_argument("--to", dest="size_to", required=True, help="largest size")
    p.add_argument("--steps", type=int, default=20)
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.set_defaults(func=cmd_divergence)

    p = sub.add_parser("generate", help="synthetic tier tables and accounts")
    p.add_argument("--count", type=int, default=1)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--legs", choices=["any", "hedge", "single"], default="any")
    p.add_argument("--margin-mode", choices=["cross", ISOLATED], default="cross")
    p.set_defaults(func=cmd_generate)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (ValueError, TypeError, TierTableError, OSError, json.JSONDecodeError) as exc:
        print(f"liqbracket: error: {exc}", file=sys.stderr)
        return 2
