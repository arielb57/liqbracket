# liqbracket

Exact liquidation prices for perpetual futures under tiered margin, including hedge mode.

## The problem

Perpetual futures venues charge maintenance margin from a tier table: the rate, and a cumulative amount subtracted from it, depend on the position's notional at the mark price. The notional moves with the price, so the liquidation equation is piecewise. Most bots and calculators pick the tier from the entry notional and solve one linear equation. Near a tier boundary that gives a wrong price, often one that is not even inside the tier it was solved in. In hedge mode, with a long and a short open at once, the account can be liquidated from both sides, and a single equation finds at most one of them.

## How it works

For one symbol and one margin pool, define `excess(p) = Equity(p) − MaintenanceMargin(p)` at mark price `p`:

```
Equity(p) = collateral + L·(p − E_long) + S·(E_short − p)
MM(p)     = Σ over open legs of  size·p·mmr[t] − amount[t],   t = tier containing size·p
```

The account is safe where `excess(p) > 0`.

1. **Tier table and cumulative amounts.** Tier `i` covers notional `[floor_i, cap_i)`. The amount is derived, not trusted: `amount_i = amount_{i−1} + floor_i · (mmr_i − mmr_{i−1})`. This is the only choice that makes margin continuous at every floor. If a table supplies amounts that disagree, it is rejected with the value continuity needs. Rates must not decrease from one tier to the next, and the last tier may be unbounded.
2. **Breakpoints.** `excess` is linear except where a leg's notional crosses a floor, at `p = floor / size`. Every leg's breakpoints are collected and sorted into segments, together with 0 and the table's price ceiling (`max_notional / size`, if the last tier has a cap).
3. **Per-segment solve in exact arithmetic.** On each segment the tiers are fixed, so `excess(p) = a + b·p` with `a` and `b` as `fractions.Fraction`s. The solver finds the closed sub-interval where `a + b·p ≤ 0`: all of the segment when `b = 0` and `a ≤ 0`, nothing when `b = 0` and `a > 0`, otherwise the part on one side of the root `−a/b`. A root outside its own segment is dropped.
4. **Merge.** Adjacent unsafe pieces are joined. A root that lands exactly on a breakpoint shows up in two segments and becomes one point. The complement within `(0, ceiling]` gives the safe intervals. Each finite endpoint where `excess = 0` is a boundary, marked `lower` (a fall through it liquidates) or `upper` (a rise does). Before returning, every boundary is checked with an independent tier lookup to confirm `excess` is exactly zero there.

Because rates never decrease, `MM` is convex and `excess` is concave. The safe set is therefore a single interval, which means zero, one or two boundaries. Two boundaries only happen in hedge mode: with the long larger, a rise helps at first, until the higher tiers' margin on both legs outgrows the net profit.

In cross margin both legs share the wallet balance. In isolated margin each open leg is its own pool holding only its isolated margin, and each is solved separately. Each leg's tier is looked up from that leg's own notional.

`naive` mode reproduces the common formula: take each leg's tier at its entry notional, solve `a + b·p = 0` once, and return that root if it is positive.

### Worked example: a long just over a tier boundary

Tiers: up to 10,000 at 1%, up to 100,000 at 5% (derived amount 400), then 10% above that. Wallet 1,500, long 0.408 at 25,000, so the entry notional is 10,200: just into tier 1.

- **Naive:** tier 1 at entry, so `1500 + 0.408·(p − 25000) − (0.408·p·0.05 − 400) = 0` gives **p = 21,413.83**. At that price the notional is 8,736.84, which is in tier 0, not tier 1. The price contradicts the tier it was solved in.
- **Exact:** the fall takes the notional back under 10,000. Tier 1's line `N·5% − 400` gives 39.39 at N = 8,787.88, but the real margin is `N·1%` = 87.88. Solving on the tier-0 segment gives **p = 21,538.92** (exactly `36250000/1683`).

The position is liquidated 125.09 higher than the naive formula says, about 58 bps sooner.

## Install and usage

Requires Python 3.10+. No runtime dependencies.

From a checkout of this repository:

```
python -m pip install -e ".[test]"
python -m pytest -q
```

### `liqbracket solve`

Input is a JSON account. Give numbers as decimal strings. Bare JSON numbers are also read exactly. `maintenance_amount` is optional, and is checked if you give it.

```json
{
  "margin_mode": "cross",
  "wallet_balance": "1500",
  "long": {"size": "0.408", "entry_price": "25000"},
  "tiers": [
    {"max_notional": "10000", "mmr": "0.01"},
    {"max_notional": "100000", "mmr": "0.05"},
    {"max_notional": null, "mmr": "0.10"}
  ]
}
```

```
$ liqbracket solve examples/long_near_boundary.json --mode both
{
  "margin_mode": "cross",
  "groups": [
    {
      "group": "cross",
      "exact": {
        "boundaries": [
          {
            "side": "lower",
            "price": {
              "decimal": "21538.91859774",
              "exact": "36250000/1683"
            },
            "tiers_at_boundary": {
              "long": 0
            }
          }
        ],
        "safe_intervals": [
          {
            "low": {
              "decimal": "21538.91859774",
              "exact": "36250000/1683"
            },
            "high": null
          }
        ],
        "price_ceiling": null
      },
      "naive": {
        "price": {
          "decimal": "21413.82868937",
          "exact": "20750000/969"
        },
        "entry_tiers": {
          "long": 1
        },
        "price_inside_entry_tiers": false
      }
    }
  ]
}
```

A hedge with two sides (`examples/hedge_two_sided.json`) has wallet 60, long 1 and short 0.9 both at 1,000, and tiers of 1% up to 1,000 notional and 20% above. The exact solver returns a `lower` boundary at `40000/81` ≈ 493.83 and an `upper` boundary at `8500/7` ≈ 1,214.29, with safe interval (493.83, 1214.29). The naive formula returns one price, 1,376.15. It misses the lower boundary completely, puts the upper one 1,333 bps too high, and that price is outside the tiers it assumed.

`--mode` is `exact` (default), `naive`, or `both`. An invalid table or account exits with status 2 and a message such as `tier 1: maintenance amount 401 is inconsistent; continuity requires 400`.

### `liqbracket divergence`

Sweeps one leg's size, keeps everything else fixed, and compares the two answers.

```
$ liqbracket divergence examples/long_near_boundary.json --leg long --from 0.36 --to 0.44 --steps 8
        size       notional tier          naive                         exact   err bps  flags
----------------------------------------------------------------------------------------------
        0.36           9000    0       21043.77                      21043.77         0  
        0.37           9250    0       21157.52                      21157.52         0  
        0.38           9500    0       21265.28                      21265.28         0  
        0.39           9750    0       21367.52                      21367.52         0  
         0.4          10000    1       21315.79                      21464.65     69.35  wrong_price,outside_tier,missed_boundary
        0.41          10250    1       21437.74                      21557.03     55.34  wrong_price,outside_tier,missed_boundary
        0.42          10500    1       21553.88                      21645.02     42.11  wrong_price,outside_tier,missed_boundary
        0.43          10750    1       21664.63                      21728.92     29.59  wrong_price,outside_tier,missed_boundary
        0.44          11000    1       21770.33                         21809     17.73  wrong_price,outside_tier,missed_boundary

5 of 9 sizes: naive formula disagrees with the exact solver
```

The naive answer is exact while the entry sits in tier 0. It is wrong from the moment the entry crosses the floor, and the error shrinks as the entry moves further from the floor. `--json` gives the same rows with exact fractions. In isolated mode the sweep scales the leg's isolated margin with its size, which keeps leverage fixed.

### `liqbracket generate`

```
liqbracket generate --count 100 --seed 7 --legs hedge --margin-mode cross > accounts.json
```

This produces deterministic synthetic accounts. Tables have 1–8 tiers with geometric caps and rising rates between 0.4% and 25%, and half of them have a capped last tier. Leverage runs from 1x to 125x. About 60% of legs are sized within ±3% of a tier floor. `--count 1` prints a single account that `solve` accepts directly. `python -m liqbracket` works the same as the `liqbracket` script.

## Results

The benchmark sets the naive formula against the exact solver over 100,000 generated cross-margin accounts, about half of them hedges. Comparisons are exact `Fraction` equality with no tolerance.

```
$ python benchmarks/accuracy.py --accounts 100000 --seed 1
accounts: 100000  seed: 1  margin groups: 100000
naive wrong in any way        32204 / 100000   32.20%
  price not a true boundary   31315 / 100000   31.32%
  price outside its tier      31315 / 100000   31.32%
  a true boundary missed      29636 / 100000   29.64%
hedge groups missing one      21497 / 50226    42.80%
exact finds two boundaries      480 / 100000    0.48%
error when wrong (single leg): median 10.43 bps, p90 99.01 bps, max 3065.20 bps
error when wrong (hedge): median 20.98 bps, p90 2561.98 bps, max 16209397.38 bps
worst price error           16209397.38 bps  (naive 75.005042 vs exact ['0.046244'])
elapsed 5.0s on arm64 / Python 3.13.0 / 8 workers
```

Measured on an Apple Silicon Mac (arm64, 8 worker processes), Python 3.13.0. Results are the same for any worker count, because each chunk of accounts is seeded from `(seed, chunk start)`.

How to read the numbers:

- **Wrong price and outside its tier are the same 31,315 accounts, and that is expected.** If the naive root lies inside the tiers it assumed, it satisfies the true equation, and because `excess` is concave it is a true boundary. If the root lies outside those tiers, the true margin there is different, so the root is wrong.
- **A true boundary missed** also counts cases where naive finds no positive root but the exact solver does, and hedges where the exact solver finds two.
- **Error in bps** is measured against the nearest exact boundary. The single-leg figures are the useful ones: the median is about 10 bps and the tail reaches about 3,000 bps. The hedge maximum is extreme because the true boundary is at a price near zero (0.046) while the naive root is at 75.
- **The rates depend on the generator.** It places most positions near tier floors on purpose, because that is where the shortcut breaks. Real books with positions far from any floor will see lower rates. What does not change is the kind of error: wherever an entry and its liquidation price fall in different tiers, the naive answer is wrong.

## Design notes

**The solver works on sets, not roots.** A version that only collected roots would need special cases for a root exactly on a breakpoint (found twice), a segment with zero slope (no root, or a whole interval of zeros), and a negative root. Instead, each segment contributes the closed interval where `excess ≤ 0`, the intervals are merged, and the boundaries are read off the complement. Those three edge cases then need no special code, and each one has its own test. Using `Fraction` throughout is what makes this sound: merging relies on `lo ≤ hi` comparisons at shared breakpoints, and floating point would let two computations of the same breakpoint differ in the last bit and split one boundary into two. It is slower than floats, but 100k accounts still take seconds, and the tests can compare prices with `==` instead of a tolerance.

**Amounts are derived and checked, never trusted.** Venue tables publish the cumulative maintenance amount next to each rate. A copied table with a typo in that column produces a margin function with a jump, and then "the" liquidation price may not exist. Deriving the amounts from the rates removes that failure mode. Rejecting a supplied amount that does not match, rather than silently replacing it, tells the user their source table is wrong. Requiring rates to rise from tier to tier has the same effect: it is true of real tables, and it is what guarantees at most two boundaries.

**Testing.** The Hypothesis property tests in `tests/test_properties.py` check the solver against things it cannot share bugs with. Every boundary gives `Equity == MM` by direct tier lookup. A 300-point rational grid scan of `excess` finds the same safe/unsafe regions and the same sign changes, and no others. Adding wallet balance never moves a lower boundary up or an upper boundary down. A leg that stays inside one tier matches the closed-form single-tier formula. Hedges built analytically to have two boundaries return both. A fully offset hedge in a capped table returns none. Shrinking is bounded (`max_examples`, a 5 s `deadline`), so a failure reports its input in minutes. As a check on the tests themselves, deliberately breaking the segment-tier lookup makes 19 of the 46 tests fail.

## Limitations

- **One symbol, one account.** Cross margin here means one symbol's legs share the wallet. Unrealised PnL and margin from other symbols, as in real portfolio cross margin, are not modelled; fold them into `wallet_balance` yourself if they are fixed.
- **No fees, funding, liquidation clearance fees or insurance-fund buffers.** Venues often liquidate a little before `equity = MM` for these reasons. liqbracket gives the point where `equity = MM` exactly.
- **Mark price only.** Index/mark divergence and price bands are not modelled.
- **Hedge-mode margin is per leg, each at its own notional.** Some venues net hedged positions or charge only the larger side. Such rules would need a different `MM(p)`.
- **Beyond the table's maximum notional the model is undefined.** With a capped last tier, results stop at the price ceiling. An offset hedge there reports no boundary, meaning safe everywhere the table applies. With an unbounded last tier, margin grows without limit, so even a fully offset hedge has an upper boundary. There is a test for that case.
- **Tier tables must have non-decreasing rates.** Tables whose rates fall at a higher tier are rejected, not solved.
- **The benchmark measures synthetic accounts**, deliberately concentrated near tier floors. It shows how the shortcut fails, not how often it fails on any real venue's order book.

## License

MIT. See [LICENSE](LICENSE).
