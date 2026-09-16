"""How often the entry-tier shortcut formula is wrong, over generated accounts.

    python benchmarks/accuracy.py --accounts 100000 --seed 1

Every account is solved twice: exactly (piecewise, in Fractions) and with the common
formula that fixes each leg's tier at its entry notional. Counts are exact comparisons,
not tolerances.
"""

from __future__ import annotations

import argparse
import os
import platform
import statistics
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from fractions import Fraction

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from liqbracket.generate import generate_accounts  # noqa: E402
from liqbracket.numbers import fmt_decimal  # noqa: E402
from liqbracket.solver import compare  # noqa: E402

KEYS = (
    "groups",
    "diverges",
    "wrong_price",
    "outside_tier",
    "missed_boundary",
    "two_boundaries",
    "hedge_groups",
    "hedge_missed",
    "exact_has_boundary",
    "naive_none_but_exact_has",
)


def _chunk(job: tuple[int, int, int]) -> tuple[dict[str, int], Fraction, str, dict[str, list]]:
    seed, start, count = job
    # Each chunk reseeds from (seed, start) so the totals do not depend on the worker count.
    accounts = generate_accounts(seed * 1_000_003 + start, count)
    stats = dict.fromkeys(KEYS, 0)
    worst = Fraction(0)
    worst_desc = ""
    errors: dict[str, list[float]] = {"single": [], "hedge": []}
    for account in accounts:
        for c in compare(account):
            stats["groups"] += 1
            stats["diverges"] += c.diverges
            stats["wrong_price"] += c.wrong_price
            stats["outside_tier"] += c.outside_tier
            stats["missed_boundary"] += c.missed_boundary
            stats["two_boundaries"] += len(c.exact.boundaries) == 2
            stats["exact_has_boundary"] += bool(c.exact.boundaries)
            stats["naive_none_but_exact_has"] += c.naive.price is None and bool(c.exact.boundaries)
            if len(c.exact.group.legs) == 2:
                stats["hedge_groups"] += 1
                stats["hedge_missed"] += c.missed_boundary
            if c.wrong_price and c.error_bps is not None:
                kind = "hedge" if len(c.exact.group.legs) == 2 else "single"
                errors[kind].append(float(c.error_bps))
            if c.error_bps is not None and c.error_bps > worst:
                worst = c.error_bps
                worst_desc = (
                    f"naive {fmt_decimal(c.naive.price, 6)} vs exact "
                    f"{[fmt_decimal(b.price, 6) for b in c.exact.boundaries]}"
                )
    return stats, worst, worst_desc, errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--accounts", type=int, default=100_000)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--chunk", type=int, default=2_000)
    parser.add_argument("--workers", type=int, default=os.cpu_count() or 1)
    args = parser.parse_args()

    jobs = [
        (args.seed, start, min(args.chunk, args.accounts - start))
        for start in range(0, args.accounts, args.chunk)
    ]
    began = time.perf_counter()
    totals = dict.fromkeys(KEYS, 0)
    worst, worst_desc = Fraction(0), ""
    errors: dict[str, list[float]] = {"single": [], "hedge": []}
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        for stats, w, desc, errs in pool.map(_chunk, jobs):
            for kind in errors:
                errors[kind].extend(errs[kind])
            for key in KEYS:
                totals[key] += stats[key]
            if w > worst:
                worst, worst_desc = w, desc
    elapsed = time.perf_counter() - began

    def share(n: int, d: int) -> str:
        return f"{n:>7} / {d:<7} {100 * n / d:6.2f}%" if d else f"{n:>7} / 0"

    g = totals["groups"]
    print(f"accounts: {args.accounts}  seed: {args.seed}  margin groups: {g}")
    print(f"naive wrong in any way      {share(totals['diverges'], g)}")
    print(f"  price not a true boundary {share(totals['wrong_price'], g)}")
    print(f"  price outside its tier    {share(totals['outside_tier'], g)}")
    print(f"  a true boundary missed    {share(totals['missed_boundary'], g)}")
    print(f"hedge groups missing one    {share(totals['hedge_missed'], totals['hedge_groups'])}")
    print(f"exact finds two boundaries  {share(totals['two_boundaries'], g)}")
    for kind in ("single", "hedge"):
        values = sorted(errors[kind])
        if values:
            print(
                f"error when wrong ({kind + ' leg' if kind == 'single' else 'hedge'}): "
                f"median {statistics.median(values):.2f} bps, "
                f"p90 {values[int(0.9 * (len(values) - 1))]:.2f} bps, "
                f"max {values[-1]:.2f} bps"
            )
    print(f"worst price error           {fmt_decimal(worst, 2)} bps  ({worst_desc})")
    print(
        f"elapsed {elapsed:.1f}s on {platform.machine()} / Python {platform.python_version()}"
        f" / {args.workers} workers"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
