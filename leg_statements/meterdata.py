"""Read and clean 15-minute meter readings.

Input format (CSV, one reading per row)::

    meter_id,timestamp,kwh
    M-H01,2026-03-01 00:00,0.112
    M-H01,2026-03-01T00:15:00+01:00,0.098

* ``timestamp`` is the START of the 15-minute interval. It may carry a UTC offset (ISO 8601) or be naive local time
  in the community time zone. In naive local time the repeated autumn hour appears twice: the first occurrence is
  read as summer time, the second as standard time (file order). Naive times inside the skipped spring hour do not
  exist and are rejected.
* ``kwh`` is the energy of that interval (consumption for consumers, feed-in for producers), ``.`` or ``,`` decimal.

Cleaning, in this order: unparseable rows and off-grid timestamps are dropped; duplicate timestamps keep the LAST
row (identical and conflicting duplicates are counted separately); negative values and values above
``max_kw * 0.25`` kWh are rejected; gaps of up to ``max_interp`` intervals between two good values are linearly
interpolated; longer gaps take the value of the same quarter-hour one week earlier (or one week later), otherwise 0.
Every interval keeps a quality code so statements can show how many values were estimated.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

import numpy as np
import pandas as pd

INTERVAL = pd.Timedelta(minutes=15)
WEEK = 7 * 96
MEASURED, INTERPOLATED, SUBSTITUTED, ZERO_FILLED = 0, 1, 2, 3
_OFFSET_RE = r"(?:Z|[+-]\d{2}:?\d{2})$"


@dataclass
class MeterReport:
    meter: str
    rows: int = 0
    invalid_rows: int = 0  # unparseable timestamp or value
    nonexistent_local: int = 0  # naive local time inside the spring DST gap
    off_grid: int = 0  # timestamp not on a quarter-hour boundary
    out_of_period: int = 0
    dup_identical: int = 0
    dup_conflict: int = 0
    negative: int = 0
    implausible: int = 0
    missing: int = 0  # intervals without a usable value (gaps + rejected values)
    interpolated: int = 0
    substituted: int = 0
    zero_filled: int = 0

    @property
    def estimated(self) -> int:
        return self.interpolated + self.substituted + self.zero_filled


@dataclass
class CleanResult:
    wh: pd.DataFrame  # index: UTC interval starts, columns: meters, int64 Wh
    quality: pd.DataFrame  # same shape, int8 quality code
    reports: dict[str, MeterReport] = field(default_factory=dict)
    unknown_meters: list[str] = field(default_factory=list)

    def report_frame(self) -> pd.DataFrame:
        rows = []
        for r in self.reports.values():
            d = asdict(r)
            d["estimated"] = r.estimated
            rows.append(d)
        return pd.DataFrame(rows)


def period_grid(start_local: str, end_local: str, tz: str = "Europe/Zurich") -> pd.DatetimeIndex:
    """UTC interval starts from local midnight ``start_local`` (inclusive) to ``end_local`` (exclusive)."""
    s = pd.Timestamp(start_local).tz_localize(tz).tz_convert("UTC")
    e = pd.Timestamp(end_local).tz_localize(tz).tz_convert("UTC")
    return pd.date_range(s, e, freq=INTERVAL, inclusive="left")


def read_csv(path) -> pd.DataFrame:
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    missing = {"meter_id", "timestamp", "kwh"} - set(df.columns)
    if missing:
        raise ValueError(f"readings file lacks columns: {sorted(missing)}")
    return df[["meter_id", "timestamp", "kwh"]]


def parse_timestamps(meter: pd.Series, ts: pd.Series, tz: str) -> tuple[pd.Series, pd.Series]:
    """Return (UTC timestamps with NaT for invalid, mask of naive times that do not exist locally)."""
    n = len(ts)
    if pd.api.types.is_datetime64_any_dtype(ts):
        t = ts if getattr(ts.dt, "tz", None) is not None else ts.dt.tz_localize(tz, ambiguous="NaT", nonexistent="NaT")
        return t.dt.tz_convert("UTC"), pd.Series(False, index=ts.index)
    # Parse each distinct string once (all meters share the same timestamps), then map back to rows.
    codes, uniq = pd.factorize(ts, use_na_sentinel=True)
    u = pd.Series(uniq, dtype=object).astype(str).str.strip()
    has_off = u.str.contains(_OFFSET_RE, regex=True).to_numpy()
    utc_dst = pd.Series(pd.NaT, index=u.index, dtype="datetime64[ns, UTC]")  # reading if ambiguous -> summer time
    utc_std = utc_dst.copy()  # reading if ambiguous -> standard time
    amb_u = np.zeros(len(u), dtype=bool)
    nonex_u = np.zeros(len(u), dtype=bool)
    if has_off.any():
        p = pd.to_datetime(u[has_off], utc=True, errors="coerce", format="ISO8601")
        utc_dst[has_off] = p
        utc_std[has_off] = p
    if (~has_off).any():
        naive = pd.to_datetime(u[~has_off], errors="coerce", format="ISO8601")
        ok = naive.notna().to_numpy()
        nat_amb = naive.dt.tz_localize(tz, ambiguous="NaT", nonexistent="NaT")
        as_dst = naive.dt.tz_localize(tz, ambiguous=np.ones(len(naive), dtype=bool), nonexistent="NaT")
        as_std = naive.dt.tz_localize(tz, ambiguous=np.zeros(len(naive), dtype=bool), nonexistent="NaT")
        utc_dst[~has_off] = as_dst.dt.tz_convert("UTC")
        utc_std[~has_off] = as_std.dt.tz_convert("UTC")
        nonex_u[~has_off] = ok & as_dst.isna().to_numpy()
        amb_u[~has_off] = ok & nat_amb.isna().to_numpy() & as_dst.notna().to_numpy()
    valid = codes >= 0
    safe = np.where(valid, codes, 0)
    dst_ns = utc_dst.dt.tz_localize(None).to_numpy(dtype="datetime64[ns]")
    std_ns = utc_std.dt.tz_localize(None).to_numpy(dtype="datetime64[ns]")
    out = np.where(valid, dst_ns[safe], np.datetime64("NaT", "ns"))
    amb_rows = valid & amb_u[safe]
    if amb_rows.any():
        # repeated autumn hour: first occurrence per meter = summer time, later ones = standard time (file order)
        occ = pd.DataFrame({"m": np.asarray(meter)[amb_rows], "t": codes[amb_rows]}).groupby(["m", "t"]).cumcount()
        later = np.flatnonzero(amb_rows)[occ.to_numpy() > 0]
        out[later] = std_ns[codes[later]]
    res = pd.Series(pd.DatetimeIndex(out).tz_localize("UTC"), index=ts.index)
    nonexistent = pd.Series(valid & nonex_u[safe], index=ts.index)
    assert len(res) == n
    return res, nonexistent


def _fill(values: np.ndarray, max_interp: int) -> tuple[np.ndarray, np.ndarray]:
    """Fill NaNs in a 1-D kWh array; return (filled, quality codes)."""
    v = values.astype(float).copy()
    q = np.zeros(len(v), dtype=np.int8)
    miss = np.isnan(v)
    if not miss.any():
        return v, q
    orig = v.copy()
    # runs of missing values
    idx = np.flatnonzero(miss)
    breaks = np.flatnonzero(np.diff(idx) > 1)
    starts = np.r_[idx[0], idx[breaks + 1]]
    ends = np.r_[idx[breaks], idx[-1]]  # inclusive
    for a, b in zip(starts, ends, strict=True):
        length = b - a + 1
        if length <= max_interp and a > 0 and b < len(v) - 1:
            x0, x1 = a - 1, b + 1
            t = (np.arange(a, b + 1) - x0) / (x1 - x0)
            v[a : b + 1] = orig[x0] + t * (orig[x1] - orig[x0])
            q[a : b + 1] = INTERPOLATED
    rest = np.flatnonzero(np.isnan(v))
    for i in rest:  # forward order, so a long gap can chain on values filled one week earlier
        if i - WEEK >= 0 and not np.isnan(v[i - WEEK]):
            v[i] = v[i - WEEK]
            q[i] = SUBSTITUTED
        elif i + WEEK < len(v) and not np.isnan(orig[i + WEEK]):
            v[i] = orig[i + WEEK]
            q[i] = SUBSTITUTED
        else:
            v[i] = 0.0
            q[i] = ZERO_FILLED
    return v, q


def clean(
    readings: pd.DataFrame,
    meters: dict[str, float],
    grid: pd.DatetimeIndex,
    tz: str = "Europe/Zurich",
    max_interp: int = 8,
) -> CleanResult:
    """Clean long-format readings into a gap-free Wh matrix on ``grid``.

    ``meters`` maps meter id -> max plausible power in kW.
    """
    df = readings.copy()
    mcodes, muniq = pd.factorize(df["meter_id"])
    df["meter_id"] = pd.Series(muniq, dtype=object).astype(str).str.strip().to_numpy()[mcodes]
    reports = {m: MeterReport(meter=m) for m in meters}
    unknown = sorted(set(df["meter_id"]) - set(meters))
    df = df[df["meter_id"].isin(meters)].reset_index(drop=True)
    for m, n in df["meter_id"].value_counts().items():
        reports[m].rows = int(n)

    utc, nonexistent = parse_timestamps(df["meter_id"], df["timestamp"], tz)
    if pd.api.types.is_numeric_dtype(df["kwh"]):
        kwh = df["kwh"].astype(float)
    else:
        kwh = pd.to_numeric(df["kwh"], errors="coerce")
        bad = kwh.isna()
        if bad.any():  # retry only the failures with a decimal comma / spaces
            kwh[bad] = pd.to_numeric(df.loc[bad, "kwh"].astype(str).str.strip().str.replace(",", ".", regex=False), errors="coerce")
    df = pd.DataFrame({"meter": df["meter_id"].values, "utc": utc.array, "kwh": kwh.values})
    df["utc"] = pd.to_datetime(df["utc"], utc=True)

    def count(mask: pd.Series, attr: str) -> None:
        for m, n in df.loc[mask, "meter"].value_counts().items():
            setattr(reports[m], attr, getattr(reports[m], attr) + int(n))

    nonex = pd.Series(nonexistent.values, index=df.index)
    invalid = (df["utc"].isna() | df["kwh"].isna() | ~np.isfinite(df["kwh"].fillna(0))) & ~nonex
    count(nonex, "nonexistent_local")
    count(invalid, "invalid_rows")
    df = df[~(invalid | nonex)]
    off = df["utc"].dt.floor(INTERVAL) != df["utc"]
    count(off, "off_grid")
    df = df[~off]
    outp = (df["utc"] < grid[0]) | (df["utc"] > grid[-1])
    count(outp, "out_of_period")
    df = df[~outp]

    dup = df.duplicated(["meter", "utc"], keep="last")
    if dup.any():
        nuniq = df.groupby(["meter", "utc"])["kwh"].transform("nunique")
        count(dup & (nuniq == 1), "dup_identical")
        count(dup & (nuniq > 1), "dup_conflict")
    df = df[~dup]

    cols = list(meters)
    mat = df.pivot(index="utc", columns="meter", values="kwh").reindex(index=grid, columns=cols)
    vals = mat.to_numpy(dtype=float, copy=True)
    neg = vals < 0
    lim = np.array([meters[m] * 0.25 for m in cols])
    impl = vals > lim[None, :]
    for j, m in enumerate(cols):
        reports[m].negative = int(neg[:, j].sum())
        reports[m].implausible = int(impl[:, j].sum())
    vals[neg | impl] = np.nan

    wh = np.zeros(vals.shape, dtype=np.int64)
    qual = np.zeros(vals.shape, dtype=np.int8)
    for j, m in enumerate(cols):
        col = vals[:, j]
        r = reports[m]
        r.missing = int(np.isnan(col).sum())
        filled, q = _fill(col, max_interp)
        wh[:, j] = np.rint(filled * 1000.0).astype(np.int64)
        qual[:, j] = q
        r.interpolated = int((q == INTERPOLATED).sum())
        r.substituted = int((q == SUBSTITUTED).sum())
        r.zero_filled = int((q == ZERO_FILLED).sum())
    return CleanResult(
        wh=pd.DataFrame(wh, index=grid, columns=cols),
        quality=pd.DataFrame(qual, index=grid, columns=cols),
        reports=reports,
        unknown_meters=unknown,
    )
