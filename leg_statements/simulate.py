"""Seeded, SIMULATED community: household loads, PV feed-in, membership changes and meter-data defects.

Nothing here is measured data. Profiles are shaped by time of day, season, heat pumps and EV charging;
PV follows a clear-sky sun model with random daily cloudiness, scaled to about 1,000 kWh per kWp and year.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .config import Community, Member, Tariffs
from .meterdata import period_grid
from .opendata import tariff_from_elcom

LAT, LON = 47.4, 8.5  # Swiss Plateau, used only for the simulated sun position


@dataclass
class SimCommunity:
    community: Community
    readings: pd.DataFrame  # long format with defects, naive local timestamps (what a file would contain)
    truth_kwh: pd.DataFrame  # clean values per meter (index UTC grid), to measure gap-filling error
    defects: dict


def _local_parts(grid: pd.DatetimeIndex, tz: str):
    loc = grid.tz_convert(tz)
    hour = loc.hour.to_numpy() + loc.minute.to_numpy() / 60.0
    doy = loc.day_of_year.to_numpy()
    wd = loc.day_of_week.to_numpy()
    day = (loc.normalize().tz_localize(None) - pd.Timestamp(loc[0].date())).days.to_numpy()
    return hour, doy, wd, day


def household_kwh(grid, tz, annual_kwh, rng, heat_pump=False, ev=False) -> np.ndarray:
    hour, doy, wd, day = _local_parts(grid, tz)
    shape = 0.45 + 0.9 * np.exp(-((hour - 7.3) / 1.1) ** 2) + 1.4 * np.exp(-((hour - 19.0) / 2.0) ** 2)
    shape += 0.45 * np.exp(-((hour - 12.4) / 1.3) ** 2) * (1 + 0.6 * (wd >= 5))
    shape *= 1.0 + 0.22 * np.cos(2 * np.pi * (doy - 15) / 365)
    p = shape * rng.lognormal(0.0, 0.35, len(grid))
    events = rng.random(len(grid)) < 0.03 * (hour > 6) * (hour < 23)
    p += events * rng.uniform(1.0, 2.5, len(grid))  # kettle, oven, washing machine
    if heat_pump:
        ndays = day.max() + 1
        temp_day = 9.5 - 9.5 * np.cos(2 * np.pi * (np.arange(ndays) + 1 - 20) / 365) + rng.normal(0, 3, ndays)
        t = temp_day[day] - 3 * np.cos(2 * np.pi * (hour - 15) / 24)
        p += 2.2 * np.clip((16 - t) / 22, 0, None) * rng.uniform(0.7, 1.3, len(grid))
    if ev:
        ndays = day.max() + 1
        charge_day = rng.random(ndays) < 0.4
        start = rng.uniform(18, 21, ndays)
        dur = rng.uniform(1.0, 2.5, ndays)
        on = charge_day[day] & (hour >= start[day]) & (hour < start[day] + dur[day])
        p += on * 7.0
    kwh = p / 4.0
    return kwh * annual_kwh / kwh.sum()


def _sun(grid: pd.DatetimeIndex):
    doy = grid.day_of_year.to_numpy()
    utc_h = grid.hour.to_numpy() + grid.minute.to_numpy() / 60.0 + 7.5 / 60  # middle of the interval
    decl = np.radians(23.44) * np.sin(2 * np.pi * (284 + doy) / 365)
    ha = np.radians(15 * (utc_h + LON / 15 - 12))
    lat = np.radians(LAT)
    sin_el = np.sin(lat) * np.sin(decl) + np.cos(lat) * np.cos(decl) * np.cos(ha)
    el = np.arcsin(np.clip(sin_el, -1, 1))
    cos_az = (np.sin(decl) - np.sin(el) * np.sin(lat)) / (np.cos(el) * np.cos(lat) + 1e-12)
    az = np.arccos(np.clip(cos_az, -1, 1))
    az = np.where(ha > 0, 2 * np.pi - az, az)  # from north, clockwise
    return el, az


def pv_kwh(grid, tz, kwp, rng, planes=((30, 180, 1.0),)) -> np.ndarray:
    el, az = _sun(grid)
    _, _, _, day = _local_parts(grid, tz)
    up = el > np.radians(1)
    am = 1 / np.maximum(np.sin(el), 0.05)
    dni = np.where(up, 1000 * 0.7 ** (am**0.678), 0.0)
    poa = np.zeros(len(grid))
    for tilt, azimuth, frac in planes:
        b, ap = np.radians(tilt), np.radians(azimuth)
        cosi = np.sin(el) * np.cos(b) + np.cos(el) * np.sin(b) * np.cos(az - ap)
        poa += frac * (dni * np.clip(cosi, 0, None) + 0.12 * dni * up)
    ndays = day.max() + 1
    doy_d = np.arange(ndays) + 1
    clear = 0.45 + 0.2 * np.sin(2 * np.pi * (doy_d - 100) / 365)
    cloud = np.clip(rng.beta(2, 2, ndays) * 0.6 + clear * 0.6, 0.05, 1.0)
    p = poa * cloud[day] * rng.uniform(0.85, 1.0, len(grid))
    kwh = p / 4.0
    return kwh * (1000.0 * kwp) / kwh.sum()  # scale to 1,000 kWh/kWp and year


def demo_tariffs(**over) -> Tariffs:
    d = tariff_from_elcom()
    d.update({
        "internal_price_rp": 9.5,  # set by the community (assumption: between feed-in and supply energy price)
        "feed_in_rp": 8.0,  # assumption
        "admin_fee_chf_per_month": 2.0,  # assumption
        "vat_rate_grid": 0.081,  # Swiss standard VAT rate since 2024
        "vat_rate_internal": 0.0,
    })
    d.update(over)
    return Tariffs(**d)


HOUSEHOLDS = [  # id, annual kWh, heat pump, EV
    ("H01", 3200, False, False), ("H02", 4500, False, False), ("H03", 2400, False, False),
    ("H04", 7800, True, False), ("H05", 5200, False, True), ("H06", 3900, False, False),
    ("H07", 2100, False, False), ("H08", 6900, True, False), ("H09", 4100, False, False),
    ("H10", 5600, False, True), ("H11", 3500, False, False), ("H12", 2800, False, False),
]
PRODUCERS = [("P1", "PV Dach Sued", 30.0, ((30, 180, 1.0),)), ("P2", "PV Dach Ost-West", 20.0, ((15, 90, 0.5), (15, 270, 0.5)))]


def make_community(key: str = "proportional", static_fraction: float = 0.5, mode: str = "LEG") -> Community:
    total = sum(h[1] for h in HOUSEHOLDS)
    members = []
    for hid, kwh, _, ev in HOUSEHOLDS:
        members.append(Member(id=hid, name=f"Haushalt {hid[1:]}", role="consumer", meter=f"M-{hid}",
                              share=round(kwh / total, 4), max_kw=25.0 if not ev else 30.0))
    members[10].join = pd.Timestamp("2026-03-16").date()  # H11 joins mid-March
    members[11].leave = pd.Timestamp("2026-08-20").date()  # H12 leaves after 20 August
    for pid, name, kwp, _ in PRODUCERS:
        members.append(Member(id=pid, name=name, role="producer", meter=f"M-{pid}", max_kw=kwp * 1.1))
    c = Community(name="LEG Musterquartier (simuliert)", mode=mode, key=key, static_fraction=static_fraction,
                  members=members, tariffs=demo_tariffs())
    return c


def simulate(seed: int = 7, year: int = 2026, key: str = "proportional", defects: bool = True) -> SimCommunity:
    rng = np.random.default_rng(seed)
    comm = make_community(key)
    tz = comm.timezone
    grid = period_grid(f"{year}-01-01", f"{year + 1}-01-01", tz)
    truth = {}
    for hid, kwh, hp, ev in HOUSEHOLDS:
        truth[f"M-{hid}"] = household_kwh(grid, tz, kwh, rng, heat_pump=hp, ev=ev)
    for pid, _, kwp, planes in PRODUCERS:
        truth[f"M-{pid}"] = pv_kwh(grid, tz, kwp, rng, planes)
    truth_df = pd.DataFrame(truth, index=grid).round(3)
    readings, info = to_readings(truth_df, tz, rng if defects else None)
    return SimCommunity(community=comm, readings=readings, truth_kwh=truth_df, defects=info)


def to_readings(truth: pd.DataFrame, tz: str, rng: np.random.Generator | None) -> tuple[pd.DataFrame, dict]:
    """Long-format readings with naive local timestamps; optionally inject seeded defects."""
    local = truth.index.tz_convert(tz)
    stamps = np.asarray(local.strftime("%Y-%m-%d %H:%M"))
    dst_day = np.asarray(local.normalize().tz_localize(None).isin(_dst_days(local)))
    parts = []
    info = {"short_gaps": 0, "long_gaps": 0, "dup_identical": 0, "dup_conflict": 0, "negative": 0, "implausible": 0}
    for m in truth.columns:
        df = pd.DataFrame({"meter_id": m, "timestamp": stamps, "kwh": truth[m].to_numpy()})
        df["_order"] = np.arange(len(df), dtype=float)
        if rng is not None:
            n = len(df)
            ok = np.flatnonzero(~dst_day)
            drop = np.zeros(n, dtype=bool)
            for _ in range(3):
                a = int(rng.choice(ok[:-200]))
                drop[a : a + int(rng.integers(1, 7))] = True
                info["short_gaps"] += 1
            if rng.random() < 0.25:
                a = int(rng.choice(ok[:-300]))
                drop[a : a + int(rng.integers(96, 193))] = True
                info["long_gaps"] += 1
            idx = rng.choice(ok, size=6, replace=False)
            neg, spike, dup_i, dup_c = idx[:1], idx[1:2], idx[2:4], idx[4:6]
            df.loc[neg, "kwh"] = -df.loc[neg, "kwh"] - 0.05
            df.loc[spike, "kwh"] = 99.999
            info["negative"] += 1
            info["implausible"] += 1
            extra = []
            for i in dup_i:  # identical re-delivery
                r = df.loc[i].copy()
                r["_order"] += 0.5
                extra.append(r)
                info["dup_identical"] += 1
            for i in dup_c:  # wrong value first, corrected value later (last wins)
                r = df.loc[i].copy()
                r["_order"] -= 0.5
                r["kwh"] = round(float(r["kwh"]) * 1.7 + 0.2, 3)
                extra.append(r)
                info["dup_conflict"] += 1
            df = df[~(drop & ~np.isin(np.arange(n), idx))]
            if extra:
                df = pd.concat([df, pd.DataFrame(extra)])
        parts.append(df.sort_values("_order", kind="stable"))
    out = pd.concat(parts, ignore_index=True).drop(columns="_order")
    out["kwh"] = out["kwh"].map(lambda v: f"{v:.3f}")
    return out, info


def _dst_days(local: pd.DatetimeIndex) -> pd.DatetimeIndex:
    off = local.map(lambda t: t.utcoffset())
    ch = np.flatnonzero(np.asarray(off[1:] != off[:-1])) + 1
    return pd.DatetimeIndex([local[i].normalize().tz_localize(None) for i in ch])


def big_community(n_consumers: int, n_producers: int, seed: int = 11, year: int = 2026):
    """Large community for the timing benchmark (simulated, no defects): (Community, readings, grid)."""
    rng = np.random.default_rng(seed)
    tz = "Europe/Zurich"
    grid = period_grid(f"{year}-01-01", f"{year + 1}-01-01", tz)
    cols, members = {}, []
    for i in range(n_consumers):
        kwh = float(rng.uniform(1800, 8000))
        mid = f"C{i:03d}"
        cols[f"M-{mid}"] = household_kwh(grid, tz, kwh, rng, heat_pump=rng.random() < 0.2, ev=rng.random() < 0.15)
        members.append(Member(id=mid, name=f"Mitglied {i:03d}", role="consumer", meter=f"M-{mid}", share=kwh, max_kw=40))
    for k in range(n_producers):
        kwp = float(rng.uniform(20, 80))
        mid = f"P{k:02d}"
        cols[f"M-{mid}"] = pv_kwh(grid, tz, kwp, rng)
        members.append(Member(id=mid, name=f"PV {k:02d}", role="producer", meter=f"M-{mid}", max_kw=kwp * 1.1))
    comm = Community(name="Benchmark (simuliert)", members=members, tariffs=demo_tariffs())
    wide = pd.DataFrame(cols, index=grid).round(3)
    long = wide.stack().reset_index()
    long.columns = ["timestamp", "meter_id", "kwh"]
    return comm, long[["meter_id", "timestamp", "kwh"]], grid
