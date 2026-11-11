# paper-trading v1 · M0 baseline

Milestone 0 of the paper-trading plan (11–16 Nov 2026). This milestone changes
no bot behaviour. It adds tests that record the known defects and a guard
against live orders, and it writes down the starting point.

## Commits

| Role | Branch | Commit |
| --- | --- | --- |
| Base for `paper-v1` (parent of M0) | `regime-fixes-and-coin-inspector` | `05e14f98025950e2535bd8c810692a035b529de5` |
| Reference, the branch read by the first review | `main` | `587cfa573b693318e78e28df209564294c522b52` |

The M0 commit itself is the tip of `paper-v1`. Use `git log paper-v1` to find it.
This file is part of that commit, so it cannot name its own hash.

## Environment

- Python 3.13.15, pytest 9.2.0, pandas 3.1.1, ccxt 4.5.92
- Installed from `requirements.txt` into a fresh virtualenv. The file uses minimum
  versions, not pins, so a future install can resolve differently.

## Test counts

| Run | Passed | xfailed | Collected |
| --- | --- | --- | --- |
| Base `05e14f9` (before M0) | 311 | 0 | 311 |
| `paper-v1` after M0 | 345 | 16 | 361 |

The 50 new collected tests are 16 pinned defect tests, 16 correct-behaviour
tests marked xfail, and 18 guard tests. The 311 existing tests are unchanged.

Command: `python -m pytest -q`

## Known-defect tests

File: `tests/review/test_known_defects.py`. Each defect has two kinds of test:

- **Pinned** (`test_Tn_documents_…`): asserts the current wrong behaviour, so
  it passes today. Delete it when the defect is fixed.
- **Correct** (`test_Tn_correct_…`): asserts the right behaviour, marked
  `xfail(strict=True, raises=AssertionError)`. It fails today. When the defect
  is fixed it passes, and strict mode then fails the suite until the marker is
  removed and the pinned twin deleted.

| ID | Defect | Where | Status on base |
| --- | --- | --- | --- |
| T1 | Whale retest order is silently dropped under the committed config (`max_open=0`) | `tracker.py` | pinned, correct xfail |
| T2 | Retest fill is booked at a later, lower price (97) rather than the limit (100) | `tracker.py` | pinned, correct xfail |
| T3 | Backtest BTC regime at 04:15 changes when only the 07:45 close changes | `backtest.py` | pinned, correct xfail |
| T4 | Training-trade result changes when only holdout candles change | `backtest.py` | pinned, correct xfail |
| T5 | Gapped stop fills at the stop (95), not the candle open (90) | `backtest.py` | pinned, correct xfail |
| T6 | Downloaded history has a hole, at 21, 30, 45 and 60 days (4 cases) | `backtest.py` | pinned, correct xfail |
| T7 | `whale.enabled: false` still runs the 15-minute whale entry path | `scanner.py` | pinned, correct xfail |
| T8 | Two overlapping entry checks both open positions under a cap of 1 | `scanner.py` | pinned, correct xfail |
| T9 | Trailing exit that closes red is booked at the stop level (90), not the price seen (98) | `paper_trading.py` | pinned, correct xfail |
| T10 | Scaled trade that loses money overall is labelled `win` | `paper_trading.py` | pinned, correct xfail |
| T11 | Journal row is blank for a close once more than 100 positions exist | `tracker.py` | pinned, correct xfail |
| T13 | Order-book gate passes a book holding about $10 a side for a $1,000 order | `gates.py`, `market_data.py` | pinned, correct xfail |
| T14 | Position with no price is closed at entry as a 0% timeout | `tracker.py` | pinned, correct xfail |

There is no T12. `repro.py` has 13 cases numbered T1 to T14 with T12 absent, so
the count is 13. The skipped number is not recoverable from the files provided.

Every correct-behaviour test was run with `--runxfail` and fails on its own
assertion, not on an exception. For example, T2 reports `97.0 == 100.0` and T9
reports `90.0 == 98.0`.

## Guard against live orders

File: `tests/test_no_live_orders.py`. A static check of the source code, which
runs offline and never imports the exchange library. It fails if:

1. any call to an order-placing or order-changing method appears, such as
   `create_*order`, `cancel_*`, `edit_order`, or any `private_*` call. A string
   literal with one of these names is flagged too;
2. any exchange client is built with `apiKey`, `secret`, `password`, `uid` or
   `privateKey`;
3. any exchange-named credential is read from the environment;
4. a runtime exchange client from `MarketData` carries a key or secret.

The detector is tested on snippets. The guard was also checked by planting an
order call and a key in a temporary module. It failed, and the module was then
removed.

## Reproduction, re-run on this commit

`repro.py` was run today against `05e14f9` with `PYTHONPATH=.`. It printed
13 of 13 confirmed, the same as `repro_output.txt`.

Caveats on that script, recorded so the tests are not read as more than they are:

- It catches every exception and records it as "uncertain", and it exits 0
  whatever the count. A zero exit does not show the claims held.
- The T11 and T10 branches print "n/a" on builds that lack the feature. On this
  commit none of them took that branch.
- `repro_output.txt` puts `main` at 11 of 13. The two that do not confirm are
  T1, "not reproduced" because `main`'s committed config uses `entry_mode=chase`
  with `max_open=6`, and T11, "n/a" because `main` has no trade journal.

## Data files: not yet on disk

The plan asks for the three 2026 quarterly Kraken candle archives on the local
disk. A search of `~/Downloads`, `~/Desktop` and the iCloud `CryptoBot` folder
found no `*OHLCVT*` or `*Kraken*` files. This item is **open**. It is a
prerequisite for M1 and is not an M0 code change.

## Limitations of M0

- Tests use synthetic prices and mocks. Nothing contacts Kraken, Binance or
  CoinGecko, and no order can be placed.
- The guard is static. A call whose name is assembled at runtime, such as
  `getattr(ex, prefix + "order")`, cannot be seen. Dependency upgrades that
  change a method name are not covered either.
- The pinned tests record today's defects; they are not endorsements. Some
  pinned assertions match exact values from the repro output, so a change in
  the synthetic fixtures will need them updated.
- The T13 correct-behaviour test calls `book_gate(cfg, stats)`, the current
  signature. A fix that changes that signature will make the test error, which
  is intended: it then needs rewriting, not a silent pass.
- The defect tests do not show that the fixes will be right. They only show
  that the wrong behaviour is recorded and the right one is written down.
- Suite runtime is about 10 seconds on the machine above.
