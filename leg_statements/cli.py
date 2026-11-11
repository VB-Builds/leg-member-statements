"""Command line: demo, bill, simulate, fetch-tariffs."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import config, meterdata


def _cmd_demo(a) -> int:
    from .demo import run

    res = run(Path(a.out), None if a.no_figures else Path(a.docs), bench=not a.no_bench, bench_members=a.bench_members, seed=a.seed)
    show = {k: res[k] for k in ("energy_kwh", "checks", "money_chf", "key_spread_chf") if k in res}
    show["key_spread_chf"] = {k: v for k, v in show["key_spread_chf"].items() if k != "per_member"}
    if "benchmark" in res:
        show["benchmark"] = res["benchmark"]
    print(json.dumps(show, indent=2, default=str))
    return 0


def _cmd_bill(a) -> int:
    from .pipeline import process, write_outputs

    comm = config.load(a.config)
    readings = meterdata.read_csv(a.readings)
    res = process(comm, readings, a.start, a.end or a.start, key=a.key, max_interp=a.max_interp)
    info = write_outputs(comm, res, Path(a.out), pdfs=not a.no_pdf, key=a.key)
    print(json.dumps({**res.run.checks, **info, "unknown_meters": res.clean.unknown_meters}, indent=2))
    return 0 if res.run.checks["max_abs_kwh_error_wh"] == 0 and res.run.checks["max_abs_chf_error_cents"] == 0 else 2


def _cmd_simulate(a) -> int:
    from .simulate import simulate

    sim = simulate(seed=a.seed, defects=not a.no_defects)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    sim.readings.to_csv(out / "readings.csv", index=False)
    config.save(sim.community, out / "community.json")
    print(f"wrote {out / 'readings.csv'} ({len(sim.readings)} rows) and {out / 'community.json'}; defects: {sim.defects}")
    return 0


def _cmd_fetch(a) -> int:
    from .opendata import fetch_elcom

    print(fetch_elcom(a.operator, a.municipality, "H4", a.year))
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="leg-statements", description="Monthly member statements for a Swiss LEG / vZEV.")
    sub = p.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("demo", help="simulated community, all README numbers, figures")
    d.add_argument("--out", default="out")
    d.add_argument("--docs", default="docs")
    d.add_argument("--seed", type=int, default=7)
    d.add_argument("--no-bench", action="store_true")
    d.add_argument("--bench-members", type=int, default=200)
    d.add_argument("--no-figures", action="store_true")
    d.set_defaults(fn=_cmd_demo)
    b = sub.add_parser("bill", help="bill a community from a readings CSV")
    b.add_argument("--config", required=True)
    b.add_argument("--readings", required=True)
    b.add_argument("--start", required=True, help="first month, YYYY-MM")
    b.add_argument("--end", help="last month, YYYY-MM (default: start)")
    b.add_argument("--key", choices=config.KEYS, help="override the configured allocation key")
    b.add_argument("--max-interp", type=int, default=8, help="longest gap (intervals) filled by interpolation")
    b.add_argument("--out", default="out/bill")
    b.add_argument("--no-pdf", action="store_true")
    b.set_defaults(fn=_cmd_bill)
    s = sub.add_parser("simulate", help="write the simulated community and readings")
    s.add_argument("--out", default="out/sim")
    s.add_argument("--seed", type=int, default=7)
    s.add_argument("--no-defects", action="store_true")
    s.set_defaults(fn=_cmd_simulate)
    f = sub.add_parser("fetch-tariffs", help="re-download the ElCom tariff row (needs internet)")
    f.add_argument("--operator", type=int, default=486)
    f.add_argument("--municipality", type=int, default=101)
    f.add_argument("--year", type=int, default=2026)
    f.set_defaults(fn=_cmd_fetch)
    a = p.parse_args(argv)
    try:
        return a.fn(a)
    except (config.ConfigError, ValueError, FileNotFoundError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
