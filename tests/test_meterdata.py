import numpy as np
import pandas as pd
import pytest

from leg_statements import meterdata as md


def day_grid(day: str):
    d = pd.Timestamp(day)
    return md.period_grid(d.strftime("%Y-%m-%d"), (d + pd.Timedelta(days=1)).strftime("%Y-%m-%d"))


def rows(meter, stamps, values):
    return pd.DataFrame({"meter_id": meter, "timestamp": stamps, "kwh": [str(v) for v in values]})


def test_period_grid_lengths_follow_dst():
    assert len(md.period_grid("2026-01-01", "2027-01-01")) == 35040
    assert len(md.period_grid("2026-03-01", "2026-04-01")) == 2972  # 23-hour day on 29 March
    assert len(md.period_grid("2026-10-01", "2026-11-01")) == 2980  # 25-hour day on 25 October
    assert len(day_grid("2026-10-25")) == 100


def test_autumn_repeated_hour_in_naive_local_time():
    grid = day_grid("2026-10-25")
    local = grid.tz_convert("Europe/Zurich").strftime("%Y-%m-%d %H:%M")
    assert list(local).count("2026-10-25 02:00") == 2  # the file really contains the stamp twice
    vals = np.arange(100) / 1000 + 0.1
    r = md.clean(rows("M1", list(local), [f"{v:.3f}" for v in vals]), {"M1": 30}, grid)
    assert r.reports["M1"].dup_identical == 0 and r.reports["M1"].missing == 0
    assert r.wh["M1"].tolist() == [int(round(v * 1000)) for v in vals]


def test_spring_gap_hour_does_not_exist():
    grid = day_grid("2026-03-29")
    assert len(grid) == 92
    local = list(grid.tz_convert("Europe/Zurich").strftime("%Y-%m-%d %H:%M"))
    df = rows("M1", local + ["2026-03-29 02:15"], [0.1] * 92 + [0.5])
    r = md.clean(df, {"M1": 30}, grid)
    assert r.reports["M1"].nonexistent_local == 1
    assert r.wh["M1"].sum() == 92 * 100


def test_offset_and_z_timestamps_parse_to_same_instant():
    grid = day_grid("2026-07-01")
    df = rows("M1", ["2026-07-01T00:00:00+02:00", "2026-06-30T22:15:00Z", "2026-07-01 00:30"], [0.1, 0.2, 0.3])
    r = md.clean(df, {"M1": 30}, grid)
    assert r.wh["M1"].iloc[:3].tolist() == [100, 200, 300]


def test_duplicates_identical_and_conflicting_keep_last():
    grid = day_grid("2026-07-01")
    local = list(grid.tz_convert("Europe/Zurich").strftime("%Y-%m-%d %H:%M"))
    df = rows("M1", local + [local[5], local[6]], [0.1] * 96 + [0.1, 0.4])
    r = md.clean(df, {"M1": 30}, grid)
    assert r.reports["M1"].dup_identical == 1 and r.reports["M1"].dup_conflict == 1
    assert r.wh["M1"].iloc[5] == 100 and r.wh["M1"].iloc[6] == 400


def test_negative_and_implausible_values_are_replaced_by_interpolation():
    grid = day_grid("2026-07-01")
    local = list(grid.tz_convert("Europe/Zurich").strftime("%Y-%m-%d %H:%M"))
    vals = [0.2] * 96
    vals[10], vals[20] = -0.3, 99.999
    r = md.clean(rows("M1", local, vals), {"M1": 20}, grid)  # 20 kW -> max 5 kWh per 15 min
    rep = r.reports["M1"]
    assert rep.negative == 1 and rep.implausible == 1 and rep.interpolated == 2
    assert r.wh["M1"].iloc[10] == 200 and r.wh["M1"].iloc[20] == 200
    assert r.quality["M1"].iloc[10] == md.INTERPOLATED


def test_short_gap_is_linear():
    grid = day_grid("2026-07-01")
    local = list(grid.tz_convert("Europe/Zurich").strftime("%Y-%m-%d %H:%M"))
    vals = [0.1] * 96
    vals[40] = 0.5
    keep = [i for i in range(96) if i not in (41, 42, 43)]
    vals[44] = 0.1
    r = md.clean(rows("M1", [local[i] for i in keep], [vals[i] for i in keep]), {"M1": 30}, grid)
    assert r.wh["M1"].iloc[40:45].tolist() == [500, 400, 300, 200, 100]


def test_long_gap_takes_value_of_previous_week():
    grid = md.period_grid("2026-07-01", "2026-07-15")
    local = list(grid.tz_convert("Europe/Zurich").strftime("%Y-%m-%d %H:%M"))
    vals = [round(0.05 + (i % 96) / 1000, 3) for i in range(len(grid))]
    gap = range(800, 900)  # 100 intervals > max_interp
    keep = [i for i in range(len(grid)) if i not in gap]
    r = md.clean(rows("M1", [local[i] for i in keep], [vals[i] for i in keep]), {"M1": 30}, grid)
    assert r.reports["M1"].substituted == 100
    assert r.wh["M1"].iloc[800:900].tolist() == [int(round(vals[i - 672] * 1000)) for i in gap]


def test_gap_without_any_reference_is_zero_and_flagged():
    grid = day_grid("2026-07-01")
    local = list(grid.tz_convert("Europe/Zurich").strftime("%Y-%m-%d %H:%M"))
    r = md.clean(rows("M1", local[20:], [0.1] * 76), {"M1": 30}, grid)
    assert r.reports["M1"].zero_filled == 20
    assert r.wh["M1"].iloc[:20].sum() == 0


def test_decimal_comma_garbage_offgrid_and_unknown_meter():
    grid = day_grid("2026-07-01")
    df = pd.DataFrame({
        "meter_id": ["M1", "M1", "M1", "M9", " M1 "],
        "timestamp": ["2026-07-01 00:00", "not a date", "2026-07-01 00:07", "2026-07-01 00:00", "2026-07-01 00:15"],
        "kwh": ["0,25", "0.1", "0.1", "0.1", "abc"],
    })
    r = md.clean(df, {"M1": 30}, grid)
    rep = r.reports["M1"]
    assert r.wh["M1"].iloc[0] == 250
    assert rep.invalid_rows == 2 and rep.off_grid == 1
    assert r.unknown_meters == ["M9"]


def test_read_csv_requires_columns(tmp_path):
    p = tmp_path / "x.csv"
    p.write_text("meter,time,value\nM1,2026-01-01 00:00,0.1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="lacks columns"):
        md.read_csv(p)


def test_out_of_period_rows_are_counted_not_used():
    grid = day_grid("2026-07-01")
    local = list(grid.tz_convert("Europe/Zurich").strftime("%Y-%m-%d %H:%M"))
    r = md.clean(rows("M1", local + ["2026-07-02 00:00"], [0.1] * 97), {"M1": 30}, grid)
    assert r.reports["M1"].out_of_period == 1 and r.wh["M1"].sum() == 9600
