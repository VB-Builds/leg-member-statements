"""Reproduce every README number: simulated community -> CSV -> statements, key comparison, 200-member timing."""

from __future__ import annotations

import copy
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd

from . import config, meterdata, simulate
from .billing import bill
from .pipeline import process, write_outputs

YEAR = 2026


def key_variants(base: config.Community) -> dict[str, config.Community]:
    """Same community, four allocation keys."""
    out = {}
    c = copy.deepcopy(base)
    c.key = "proportional"
    out["proportional"] = c
    c = copy.deepcopy(base)
    c.key = "static"
    out["static, shares by yearly use"] = c
    c = copy.deepcopy(base)
    c.key = "static"
    for m in c.consumers:
        m.share = 1.0
    out["static, equal shares"] = c
    c = copy.deepcopy(base)
    c.key = "hybrid"
    c.static_fraction = 0.5
    for m in c.consumers:
        m.share = 1.0
    out["hybrid 50/50, equal shares"] = c
    return out


def benchmark(n_consumers: int, n_producers: int, out: Path, pdfs: bool = True, workers: int = 4) -> dict:
    comm, long, grid = simulate.big_community(n_consumers, n_producers)
    t0 = time.perf_counter()
    res = process(comm, long, f"{YEAR}-01", f"{YEAR}-12")
    t1 = time.perf_counter()
    info = write_outputs(comm, res, out, pdfs=False)
    t2 = time.perf_counter()
    if pdfs:
        info = write_outputs(comm, res, out, pdfs=True, csv=False, workers=workers)
    t3 = time.perf_counter()
    return {
        "members": n_consumers + n_producers,
        "consumers": n_consumers,
        "producers": n_producers,
        "readings": int(len(long)),
        "clean_allocate_bill_s": round(t1 - t0, 2),
        "csv_outputs_s": round(t2 - t1, 2),
        "pdf_files": info["pdf_files"] if pdfs else 0,
        "pdf_s": round(t3 - t2, 2) if pdfs else None,
        "pdf_workers": workers,
        "total_s": round(t3 - t0, 2),
        "max_abs_kwh_error_wh": res.run.checks["max_abs_kwh_error_wh"],
        "max_abs_chf_error_cents": res.run.checks["max_abs_chf_error_cents"],
        "statement_lines": res.run.checks["statement_lines"],
    }


def run(out: Path = Path("out"), docs: Path | None = Path("docs"), bench: bool = True, bench_members: int = 200,
        seed: int = 7) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    sim = simulate.simulate(seed=seed, year=YEAR)
    comm = sim.community
    data = out / "data"
    data.mkdir(exist_ok=True)
    sim.readings.to_csv(data / "readings.csv", index=False)
    config.save(comm, out / "community.json")

    comm = config.load(out / "community.json")  # round-trip through the files a user would have
    t0 = time.perf_counter()
    readings = meterdata.read_csv(data / "readings.csv")
    res = process(comm, readings, f"{YEAR}-01", f"{YEAR}-12")
    t_proc = time.perf_counter() - t0
    t0 = time.perf_counter()
    info = write_outputs(comm, res, out / "statements")
    t_pdf = time.perf_counter() - t0

    # data quality: what was found and how close the estimates are to the (simulated) truth
    rep = res.clean.report_frame()
    truth = np.rint(sim.truth_kwh.to_numpy() * 1000).astype(np.int64)
    q = res.clean.quality.to_numpy() != 0
    got = res.clean.wh[sim.truth_kwh.columns].to_numpy()
    qual = {
        "rows": int(rep["rows"].sum()),
        "injected": sim.defects,
        "found": {k: int(rep[k].sum()) for k in ("dup_identical", "dup_conflict", "negative", "implausible", "missing",
                                                  "interpolated", "substituted", "zero_filled")},
        "measured_values_changed": int((got[~q] != truth[~q]).sum()),
        "estimated_values": int(q.sum()),
        "estimated_true_kwh": round(float(truth[q].sum()) / 1000, 3),
        "estimated_abs_error_kwh": round(float(np.abs(got[q] - truth[q]).sum()) / 1000, 3),
        "estimated_net_error_kwh": round(float((got[q] - truth[q]).sum()) / 1000, 3),
        "total_true_kwh": round(float(truth.sum()) / 1000, 1),
    }
    intervals_per_month = res.run.statements[res.run.statements.role == "consumer"].groupby("month")["intervals"].max()

    # allocation keys: same data, four keys
    keys = {}
    annual = {}
    for name, c in key_variants(comm).items():
        r = bill(c, res.clean)  # same cleaned data, different key
        st = r.statements[r.statements.role == "consumer"]
        g = st.groupby("member_id")[["community_wh", "total", "saving"]].sum()
        annual[name] = g
        keys[name] = {
            "checks": r.checks,
            "saving_total_chf": round(st["saving"].sum() / 100, 2),
            "saving_median_chf": round(float(g["saving"].median()) / 100, 2),
            "collected_chf": round(r.summary["collected_community_energy"].sum() / 100, 2),
            "producer_credits_chf": round(r.summary["producer_credits"].sum() / 100, 2),
        }
    totals = pd.DataFrame({k: v["total"] for k, v in annual.items()}) / 100
    comm_kwh = pd.DataFrame({k: v["community_wh"] for k, v in annual.items()}) / 1000
    spread = (totals.max(axis=1) - totals.min(axis=1)).round(2)
    vs_prop = totals.sub(totals["proportional"], axis=0).round(2)

    sm = res.run.summary
    st = res.run.statements
    example = st[(st.member_id == "H11") & (st.month == f"{YEAR}-03")].iloc[0].to_dict()
    results = {
        "data": f"simulated (seed {seed}), {len(comm.consumers)} households + {len(comm.producers)} PV producers, "
                f"{YEAR}, Europe/Zurich",
        "tariffs": comm.tariffs.source,
        "intervals": res.run.checks["intervals"],
        "intervals_per_month": {k: int(v) for k, v in intervals_per_month.items()},
        "energy_kwh": {
            "production": round(sm["production_wh"].sum() / 1000, 1),
            "consumption": round(sm["consumption_wh"].sum() / 1000, 1),
            "community": round(sm["community_wh"].sum() / 1000, 1),
            "export": round(sm["export_wh"].sum() / 1000, 1),
            "grid": round(sm["grid_wh"].sum() / 1000, 1),
        },
        "checks": res.run.checks,
        "money_chf": {
            "collected_community_energy": round(sm["collected_community_energy"].sum() / 100, 2),
            "producer_credits": round(sm["producer_credits"].sum() / 100, 2),
            "members_total": round(sm["members_total"].sum() / 100, 2),
            "statements_sum": round(st.loc[st.role == "consumer", "total"].sum() / 100, 2),
            "saving_total": round(sm["saving"].sum() / 100, 2),
        },
        "quality": qual,
        "keys": keys,
        "key_spread_chf": {
            "max": float(spread.max()), "median": float(spread.median()), "member_max": str(spread.idxmax()),
            "per_member": spread.to_dict(),
        },
        "annual_total_chf": totals.round(2).to_dict(),
        "annual_community_kwh": comm_kwh.round(1).to_dict(),
        "vs_proportional_chf": vs_prop.to_dict(),
        "example_H11_march": {k: (int(v) if isinstance(v, (np.integer, float)) and float(v).is_integer() else v)
                              for k, v in example.items() if not (isinstance(v, float) and v != v)},
        "timing_14_members": {"read_clean_allocate_bill_s": round(t_proc, 2), "csv_and_pdf_s": round(t_pdf, 2),
                              "pdf_files": info["pdf_files"]},
    }
    if bench:
        results["benchmark"] = benchmark(bench_members - 10, 10, out / "bench")
    (out / "results.json").write_text(json.dumps(results, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    if docs is not None:
        from . import plots

        plots.make_figures(results, out / "statements" / "pdf" / f"{YEAR}-03" / "H11.pdf", docs)
    return results
