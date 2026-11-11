"""Read -> clean -> allocate -> bill -> write CSV and PDF outputs."""

from __future__ import annotations

import json
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from . import meterdata, pdf
from .billing import BillingRun, bill
from .config import Community

CENT_COLS = [
    "a_community_energy", "a_admin_fee", "a_vat", "a_total", "b_energy", "b_grid_usage", "b_reduction", "b_levies",
    "b_surcharge", "b_fixed", "b_vat", "b_total", "total", "without_community_total", "saving",
    "credit_community", "credit_feed_in", "credit_total", "collected_community_energy", "admin_fees",
    "producer_credits", "feed_in_credits", "members_total",
]


@dataclass
class Result:
    clean: meterdata.CleanResult
    run: BillingRun


def month_bounds(start: str, end: str) -> tuple[str, str]:
    """'2026-01', '2026-12' -> local dates '2026-01-01', '2027-01-01' (end exclusive)."""
    s = pd.Period(start, freq="M")
    e = pd.Period(end, freq="M") + 1
    return s.start_time.strftime("%Y-%m-%d"), e.start_time.strftime("%Y-%m-%d")


def process(community: Community, readings: pd.DataFrame, start: str, end: str, key: str | None = None,
            max_interp: int = 8) -> Result:
    a, b = month_bounds(start, end)
    grid = meterdata.period_grid(a, b, community.timezone)
    meters = {m.meter: m.max_kw for m in community.members}
    cr = meterdata.clean(readings, meters, grid, community.timezone, max_interp)
    return Result(clean=cr, run=bill(community, cr, key))


def _money(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for c in out.columns:
        if c in CENT_COLS:
            out[f"{c}_chf"] = out[c].map(
                lambda v: "" if pd.isna(v) else f"{int(v) / 100:.2f}")
            out = out.drop(columns=c)
        elif c.endswith("_wh"):
            out[c[:-3] + "_kwh"] = out[c].map(lambda v: "" if pd.isna(v) else f"{int(v) / 1000:.3f}")
            out = out.drop(columns=c)
    return out


def write_outputs(community: Community, res: Result, out: Path, pdfs: bool = True, key: str | None = None,
                  csv: bool = True, workers: int = 1) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    key = key or community.key
    st, sm = res.run.statements, res.run.summary
    if csv:
        _money(st).to_csv(out / "member_statements.csv", index=False)
        _money(sm).to_csv(out / "community_summary.csv", index=False)
        res.clean.report_frame().to_csv(out / "quality_report.csv", index=False)
        (out / "checks.json").write_text(json.dumps(res.run.checks, indent=2) + "\n", encoding="utf-8")
    n = 0
    if pdfs:
        recs = [_clean_nan(r) for r in st.to_dict("records")]
        jobs = [(community, r, key, out / "pdf" / r["month"] / f"{r['member_id']}.pdf") for r in recs]
        if workers > 1 and len(jobs) > 50:
            with ProcessPoolExecutor(max_workers=workers) as ex:
                chunk = max(1, len(jobs) // (workers * 4))
                n += sum(1 for _ in ex.map(_write_one, jobs, chunksize=chunk))
        else:
            n += sum(1 for _ in map(_write_one, jobs))
        for s in sm.to_dict("records"):
            rows = [r for r in recs if r["month"] == s["month"]]
            pdf.write_summary(community, s["month"], rows, s, key, out / "pdf" / s["month"] / "_community_summary.pdf")
            n += 1
    return {"pdf_files": n}


def _write_one(job) -> Path:
    community, r, key, path = job
    return pdf.write_statement(community, r, key, path)


def _clean_nan(r: dict) -> dict:
    return {k: (int(v) if isinstance(v, float) and v == v and float(v).is_integer() and k != "name" else v)
            for k, v in r.items() if not (isinstance(v, float) and v != v)}
