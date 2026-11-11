"""Monthly member statements and community summary, in whole Wh and whole Rappen (cents).

Every money line is rounded half-up to the cent ONCE, from integer Wh and a price in 1/1000 Rp, so totals are sums
of printed lines and reconcile exactly. Producers receive exactly what consumers paid for community energy: the
month's collected cents are split by sold kWh with largest remainders.

Statement sections (all prices excl. VAT):

* A. community (paid to the community): community energy x internal price, administration fee, VAT.
* B. grid operator (estimate of what the grid operator bills): supply energy for the grid share, grid usage on all
  consumption minus the LEG reduction (Art. 19h StromVV) on the community share, levies, grid surcharge,
  base fees, VAT. In ``vZEV`` mode the community energy carries no grid usage, levies or surcharge and there is no
  reduction (simplification, see README).
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

import numpy as np
import pandas as pd

from .allocation import Allocation, allocate
from .config import Community, Member
from .meterdata import CleanResult


def milli(rp: float) -> int:
    """Price in Rp/kWh -> integer 1/1000 Rp per kWh."""
    return int(Decimal(str(rp)).scaleb(3).to_integral_value(ROUND_HALF_UP))


def energy_cents(wh: int, price_rp: float) -> int:
    """Cents for ``wh`` Wh at ``price_rp`` Rp/kWh, rounded half-up (sign-symmetric)."""
    num = abs(int(wh)) * milli(price_rp)  # Wh * mRp/kWh = 1e-6 cents
    c = (num + 500_000) // 1_000_000
    return -c if wh < 0 else c


def rate_cents(base_cents: int, rate: float) -> int:
    ppm = int(Decimal(str(rate)).scaleb(6).to_integral_value(ROUND_HALF_UP))
    c = (abs(base_cents) * ppm + 500_000) // 1_000_000
    return -c if base_cents < 0 else c


def prorated_cents(chf: float, num: int, den: int) -> int:
    """``chf * num / den`` in cents, rounded half-up."""
    v = Decimal(str(chf)) * 100 * num / den
    return int(v.to_integral_value(ROUND_HALF_UP))


def largest_remainder(total: int, weights: list[int]) -> list[int]:
    """Split integer ``total`` by integer ``weights``; parts sum exactly to ``total``."""
    s = sum(weights)
    if total == 0 or s == 0:
        if total != 0:
            raise ValueError("cannot split a non-zero amount with zero weights")
        return [0] * len(weights)
    floors = [total * w // s for w in weights]
    rems = [total * w % s for w in weights]
    left = total - sum(floors)
    for i in sorted(range(len(weights)), key=lambda i: (-rems[i], i))[:left]:
        floors[i] += 1
    return floors


@dataclass
class BillingRun:
    community: Community
    allocation: Allocation
    statements: pd.DataFrame  # one row per member and month
    summary: pd.DataFrame  # one row per month
    checks: dict = field(default_factory=dict)


def _active_matrix(members: list[Member], days: np.ndarray) -> np.ndarray:
    out = np.ones((len(days), len(members)), dtype=bool)
    for j, m in enumerate(members):
        if m.join:
            out[:, j] &= days >= np.datetime64(m.join)
        if m.leave:
            out[:, j] &= days <= np.datetime64(m.leave)
    return out


def _month_active_days(m: Member, y: int, mo: int) -> tuple[int, date, date]:
    first, last = date(y, mo, 1), date(y, mo, calendar.monthrange(y, mo)[1])
    a = max(first, m.join) if m.join else first
    b = min(last, m.leave) if m.leave else last
    return max((b - a).days + 1, 0), a, b


def bill(community: Community, clean: CleanResult, key: str | None = None) -> BillingRun:
    """Allocate and bill every month covered by ``clean`` (whole local months)."""
    t = community.tariffs
    key = key or community.key
    cons, prods = community.consumers, community.producers
    missing = [m.meter for m in cons + prods if m.meter not in clean.wh.columns]
    if missing:
        raise ValueError(f"no cleaned data for meters {missing}")
    local = clean.wh.index.tz_convert(community.timezone)
    days = local.tz_localize(None).to_numpy().astype("datetime64[D]")
    months = local.strftime("%Y-%m").to_numpy()

    act_c = _active_matrix(cons, days)
    act_p = _active_matrix(prods, days)
    c = clean.wh[[m.meter for m in cons]].to_numpy() * act_c
    p = clean.wh[[m.meter for m in prods]].to_numpy() * act_p
    est_c = (clean.quality[[m.meter for m in cons]].to_numpy() != 0) & act_c
    est_p = (clean.quality[[m.meter for m in prods]].to_numpy() != 0) & act_p
    shares = np.array([m.share for m in cons], dtype=float)
    al = allocate(c, p, key, shares, community.static_fraction)
    basis = al.regulatory_wh if community.reduction_basis == "regulatory" else al.member_wh
    red = community.reduction
    leg = community.mode == "LEG"
    reducible_rp = t.grid_working_rp - t.grid_non_reducible_rp

    rows, summ = [], []
    exact_total = 0.0
    printed_total = 0
    for mon in pd.unique(months):
        sel = months == mon
        y, mo = int(mon[:4]), int(mon[5:7])
        dim = calendar.monthrange(y, mo)[1]
        cs, ms, bs = c[sel].sum(0), al.member_wh[sel].sum(0), basis[sel].sum(0)
        es, ac = est_c[sel].sum(0), act_c[sel].sum(0)
        month_rows = []
        for j, m in enumerate(cons):
            n_days, d_from, d_to = _month_active_days(m, y, mo)
            if n_days == 0 or ac[j] == 0:
                continue
            cw, mw, bw = int(cs[j]), int(ms[j]), int(bs[j])
            gw = cw - mw
            r = {"month": mon, "member_id": m.id, "name": m.name, "role": "consumer", "statement_no": f"{mon}-{m.id}",
                 "period_from": d_from.isoformat(), "period_to": d_to.isoformat(), "active_days": n_days,
                 "days_in_month": dim, "intervals": int(ac[j]), "estimated_intervals": int(es[j]),
                 "consumption_wh": cw, "community_wh": mw, "grid_wh": gw, "reduction_basis_wh": bw}
            r["a_community_energy"] = energy_cents(mw, t.internal_price_rp)
            r["a_admin_fee"] = prorated_cents(t.admin_fee_chf_per_month, n_days, dim)
            r["a_vat"] = rate_cents(r["a_community_energy"] + r["a_admin_fee"], t.vat_rate_internal)
            r["a_total"] = r["a_community_energy"] + r["a_admin_fee"] + r["a_vat"]
            usage_wh = cw if leg else gw  # vZEV: internal energy carries no grid usage / levies / surcharge
            r["b_energy"] = energy_cents(gw, t.energy_rp)
            r["b_grid_usage"] = energy_cents(usage_wh, t.grid_working_rp)
            r["b_reduction"] = -energy_cents(bw, reducible_rp * red) if leg else 0
            r["b_levies"] = energy_cents(usage_wh, t.levies_rp)
            r["b_surcharge"] = energy_cents(usage_wh, t.grid_surcharge_rp)
            r["b_fixed"] = prorated_cents(t.fixed_chf_per_year / 12, n_days, dim)
            b_net = r["b_energy"] + r["b_grid_usage"] + r["b_reduction"] + r["b_levies"] + r["b_surcharge"] + r["b_fixed"]
            r["b_vat"] = rate_cents(b_net, t.vat_rate_grid)
            r["b_total"] = b_net + r["b_vat"]
            r["total"] = r["a_total"] + r["b_total"]
            alt = sum(energy_cents(cw, x) for x in (t.energy_rp, t.grid_working_rp, t.levies_rp, t.grid_surcharge_rp)) + r["b_fixed"]
            r["without_community_total"] = alt + rate_cents(alt, t.vat_rate_grid)
            r["saving"] = r["without_community_total"] - r["total"]
            # unrounded value of the same lines, for the rounding-drift measurement
            ex = mw * t.internal_price_rp / 1000 + gw * t.energy_rp / 1000 + usage_wh * t.grid_working_rp / 1000
            ex += -(bw * reducible_rp * red / 1000 if leg else 0) + usage_wh * (t.levies_rp + t.grid_surcharge_rp) / 1000
            exact_total += ex
            printed_total += (r["a_community_energy"] + r["b_energy"] + r["b_grid_usage"] + r["b_reduction"]
                              + r["b_levies"] + r["b_surcharge"])
            month_rows.append(r)
        collected = sum(r["a_community_energy"] for r in month_rows)
        sold_m = al.sold_wh[sel].sum(0)
        exp_m = al.export_wh[sel].sum(0)
        prod_m = p[sel].sum(0)
        credits = largest_remainder(collected, [int(x) for x in sold_m]) if prods else []
        for k, m in enumerate(prods):
            n_days, d_from, d_to = _month_active_days(m, y, mo)
            if n_days == 0:
                continue
            r = {"month": mon, "member_id": m.id, "name": m.name, "role": "producer", "statement_no": f"{mon}-{m.id}",
                 "period_from": d_from.isoformat(), "period_to": d_to.isoformat(), "active_days": n_days,
                 "days_in_month": dim, "intervals": int(act_p[sel][:, k].sum()), "estimated_intervals": int(est_p[sel][:, k].sum()),
                 "production_wh": int(prod_m[k]), "sold_wh": int(sold_m[k]), "export_wh": int(exp_m[k]),
                 "credit_community": int(credits[k]), "credit_feed_in": energy_cents(int(exp_m[k]), t.feed_in_rp)}
            r["credit_total"] = r["credit_community"] + r["credit_feed_in"]
            month_rows.append(r)
        rows.extend(month_rows)
        crow = [r for r in month_rows if r["role"] == "consumer"]
        prow = [r for r in month_rows if r["role"] == "producer"]
        e_m = int(al.community_wh[sel].sum())
        s = {"month": mon, "consumers": len(crow), "producers": len(prow),
             "production_wh": int(prod_m.sum()), "consumption_wh": int(cs.sum()), "community_wh": e_m,
             "export_wh": int(exp_m.sum()), "grid_wh": int(sum(r["grid_wh"] for r in crow)),
             "collected_community_energy": collected, "admin_fees": sum(r["a_admin_fee"] for r in crow),
             "producer_credits": sum(r["credit_community"] for r in prow),
             "feed_in_credits": sum(r["credit_feed_in"] for r in prow),
             "a_total": sum(r["a_total"] for r in crow), "b_total": sum(r["b_total"] for r in crow),
             "members_total": sum(r["total"] for r in crow), "saving": sum(r["saving"] for r in crow),
             "estimated_intervals": sum(r["estimated_intervals"] for r in month_rows)}
        s["kwh_error_alloc_wh"] = int(sum(r["community_wh"] for r in crow)) - e_m
        s["kwh_error_production_wh"] = int(sold_m.sum() + exp_m.sum() - prod_m.sum())
        s["kwh_error_consumption_wh"] = int(sum(r["community_wh"] + r["grid_wh"] - r["consumption_wh"] for r in crow))
        s["chf_error_producers_cents"] = collected - s["producer_credits"]
        summ.append(s)

    st = pd.DataFrame(rows)
    sm = pd.DataFrame(summ)
    checks = {
        "intervals": int(len(clean.wh)),
        "community_energy_wh": int(al.community_wh.sum()),
        "max_abs_kwh_error_wh": int(sm[["kwh_error_alloc_wh", "kwh_error_production_wh", "kwh_error_consumption_wh"]]
                                    .abs().to_numpy().max()),
        "per_interval_alloc_error_wh": int(np.abs(al.member_wh.sum(1) - al.community_wh).max()),
        "per_interval_cap_violations": int((al.member_wh > c).sum()),
        "max_abs_chf_error_cents": int(sm["chf_error_producers_cents"].abs().max()),
        "rounding_drift_chf": round(printed_total / 100 - exact_total / 100, 4),
        "statement_lines": int(len(st)),
    }
    return BillingRun(community=community, allocation=al, statements=st, summary=sm, checks=checks)
