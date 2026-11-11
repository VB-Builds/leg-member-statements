from datetime import date

import numpy as np
import pandas as pd
import pytest

from leg_statements import billing, meterdata
from leg_statements.config import Community, Member, Tariffs

TARIFFS = dict(source="test", year=2026, internal_price_rp=10.0, energy_rp=12.0, grid_working_rp=8.0, levies_rp=0.5,
               grid_surcharge_rp=2.3, fixed_chf_per_year=120.0, feed_in_rp=6.0, admin_fee_chf_per_month=3.0,
               vat_rate_grid=0.081, vat_rate_internal=0.0)


def tiny(key="proportional", start="2026-01-01", end="2026-02-01", **kw):
    """Two consumers (100 / 300 Wh per quarter-hour), one producer with 10 x 200 Wh then 2 x 1000 Wh."""
    members = [Member("A", "Haushalt A", "consumer", "MA", share=1.0), Member("B", "Haushalt B", "consumer", "MB", share=1.0),
               Member("P", "PV", "producer", "MP")]
    for m in members:
        for k, v in kw.pop(m.id, {}).items():
            setattr(m, k, v)
    c = Community(name="Test", key=key, members=members, tariffs=Tariffs(**TARIFFS), **kw)
    grid = meterdata.period_grid(start, end)
    n = len(grid)
    p = np.zeros(n, dtype=np.int64)
    p[:10], p[10:12] = 200, 1000
    wh = pd.DataFrame({"MA": np.full(n, 100), "MB": np.full(n, 300), "MP": p}, index=grid)
    clean = meterdata.CleanResult(wh=wh, quality=pd.DataFrame(0, index=grid, columns=wh.columns, dtype=np.int8))
    return c, clean


def row(run, member, month="2026-01"):
    st = run.statements
    return st[(st.member_id == member) & (st.month == month)].iloc[0]


def test_cent_rounding_is_half_up_and_symmetric():
    assert billing.energy_cents(1, 5.0) == 0  # 0.005 cents
    assert billing.energy_cents(1000, 0.5) == 1  # exactly 0.5 cents -> 1
    assert billing.energy_cents(-1000, 0.5) == -1
    assert billing.energy_cents(63786, 9.5) == 606  # README worked example
    assert billing.rate_cents(3171, 0.081) == 257
    assert billing.prorated_cents(2.0, 16, 31) == 103


def test_largest_remainder_split_is_exact():
    assert billing.largest_remainder(100, [1, 1, 1]) == [34, 33, 33]
    assert sum(billing.largest_remainder(12345, [7, 0, 13, 5])) == 12345
    assert billing.largest_remainder(0, [0, 0]) == [0, 0]
    with pytest.raises(ValueError):
        billing.largest_remainder(5, [0, 0])


def test_worked_example_by_hand():
    c, clean = tiny()
    run = billing.bill(c, clean)
    a = row(run, "A")
    # energy: A gets 10 x 50 + 2 x 100 = 700 Wh, B 10 x 150 + 2 x 300 = 2100 Wh
    assert (a.community_wh, row(run, "B").community_wh) == (700, 2100)
    assert a.consumption_wh == 2976 * 100 and a.grid_wh == 297600 - 700
    assert a.a_community_energy == 7 and a.a_admin_fee == 300 and a.a_total == 307
    assert a.b_energy == 3563  # 296.9 kWh x 12 Rp
    assert a.b_grid_usage == 2381  # 297.6 kWh x 8 Rp
    assert a.b_reduction == -2  # 0.7 kWh x 8 Rp x 40 %
    assert (a.b_levies, a.b_surcharge, a.b_fixed) == (149, 684, 1000)
    assert a.b_vat == 630 and a.b_total == 8405 and a.total == 8712
    p = row(run, "P")
    assert p.sold_wh == 2800 and p.export_wh == 1200 and p.production_wh == 4000
    assert p.credit_community == 7 + 21 and p.credit_feed_in == 7


def test_static_equal_shares_hand_example():
    c, clean = tiny("static")
    run = billing.bill(c, clean)
    # 200 Wh split 100/100 (A capped at 100), then 400 Wh -> 100/300
    assert row(run, "A").community_wh == 10 * 100 + 2 * 100
    assert row(run, "B").community_wh == 10 * 100 + 2 * 300


def test_statement_lines_sum_to_totals_and_summary_matches():
    c, clean = tiny("hybrid", end="2026-04-01")
    run = billing.bill(c, clean)
    st = run.statements[run.statements.role == "consumer"]
    a_parts = st.a_community_energy + st.a_admin_fee + st.a_vat
    b_parts = st.b_energy + st.b_grid_usage + st.b_reduction + st.b_levies + st.b_surcharge + st.b_fixed + st.b_vat
    assert (a_parts == st.a_total).all() and (b_parts == st.b_total).all()
    assert (st.a_total + st.b_total == st.total).all()
    assert run.summary.members_total.sum() == st.total.sum()
    assert run.checks["max_abs_kwh_error_wh"] == 0 and run.checks["max_abs_chf_error_cents"] == 0


def test_member_joining_mid_month_is_billed_from_join_date():
    c, clean = tiny(B={"join": date(2026, 1, 16)})
    run = billing.bill(c, clean)
    b = row(run, "B")
    assert b.active_days == 16 and b.period_from == "2026-01-16"
    assert b.consumption_wh == 16 * 96 * 300
    assert b.community_wh == 0  # all PV fell on 1 January, before B joined
    assert row(run, "A").community_wh == 10 * 100 + 2 * 100  # A alone, capped by its own use
    assert b.b_fixed == billing.prorated_cents(10.0, 16, 31)


def test_member_who_left_has_no_statement_next_month():
    c, clean = tiny(end="2026-03-01", A={"leave": date(2026, 1, 31)})
    run = billing.bill(c, clean)
    assert set(run.statements[run.statements.month == "2026-02"].member_id) == {"B", "P"}


def test_transformation_halves_reduction_to_20_percent():
    c, clean = tiny(transformation_required=True)
    run = billing.bill(c, clean)
    assert row(run, "B").b_reduction == -billing.energy_cents(2100, 8.0 * 0.2)


def test_non_reducible_part_is_excluded_from_reduction():
    c, clean = tiny()
    c.tariffs.grid_non_reducible_rp = 3.0
    run = billing.bill(c, clean)
    assert row(run, "B").b_reduction == -billing.energy_cents(2100, 5.0 * 0.4)


def test_vzev_mode_has_no_grid_usage_on_community_energy():
    c, clean = tiny(mode="vZEV")
    run = billing.bill(c, clean)
    b = row(run, "B")
    assert b.b_reduction == 0
    assert b.b_grid_usage == billing.energy_cents(b.grid_wh, 8.0)


def test_reduction_basis_regulatory_vs_internal():
    c, clean = tiny("static")
    reg = row(billing.bill(c, clean), "A")
    c.reduction_basis = "internal"
    internal = row(billing.bill(c, clean), "A")
    assert reg.reduction_basis_wh == 700  # proportional (Art. 19g para. 4)
    assert internal.reduction_basis_wh == 1200  # what the static key gave A


def test_producer_credits_match_collected_cents_random():
    rng = np.random.default_rng(3)
    members = [Member(f"C{i}", f"C{i}", "consumer", f"M{i}", share=float(rng.uniform(0.1, 1))) for i in range(7)]
    members += [Member("P1", "P1", "producer", "MP1"), Member("P2", "P2", "producer", "MP2")]
    c = Community(name="R", key="hybrid", members=members, tariffs=Tariffs(**{**TARIFFS, "internal_price_rp": 9.137}))
    grid = meterdata.period_grid("2026-05-01", "2026-07-01")
    n = len(grid)
    data = {f"M{i}": rng.integers(0, 900, n) for i in range(7)}
    data["MP1"], data["MP2"] = rng.integers(0, 4000, n), rng.integers(0, 2500, n)
    wh = pd.DataFrame(data, index=grid)
    run = billing.bill(c, meterdata.CleanResult(wh=wh, quality=pd.DataFrame(0, index=grid, columns=wh.columns)))
    assert (run.summary.collected_community_energy == run.summary.producer_credits).all()
    assert run.checks["per_interval_cap_violations"] == 0
    assert abs(run.checks["rounding_drift_chf"]) < 0.005 * len(run.statements) * 6


def test_missing_meter_data_raises():
    c, clean = tiny()
    with pytest.raises(ValueError, match="no cleaned data"):
        billing.bill(c, meterdata.CleanResult(wh=clean.wh.drop(columns="MB"), quality=clean.quality.drop(columns="MB")))
