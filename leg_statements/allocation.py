"""Allocate community production to members, per 15-minute interval, in whole Wh.

In every interval the community energy is ``E = min(total production, total consumption of active members)``
(the rule of Art. 19g para. 4 let. a StromVV). All keys hand out exactly ``E``; they differ only in WHO gets it:

* ``proportional``: in proportion to each member's consumption in that interval (Art. 19g para. 4 let. b).
* ``static``: by fixed shares; a share a member cannot use (its consumption is lower) is re-offered to the others
  by their shares (water-filling), and anything still left goes proportionally to remaining demand.
* ``hybrid``: ``static_fraction`` of E by static shares (water-filling), the rest proportionally to the demand
  that is still uncovered.

Rounding uses largest remainders, so per interval ``sum(members) == E`` exactly and nobody gets more than it used.
Production not used inside the community is exported; it is attributed to producers in proportion to their feed-in.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


def integerize(y: np.ndarray, cap: np.ndarray, total: np.ndarray) -> np.ndarray:
    """Round a float allocation (rows = intervals) to int64 with exact row sums and ``0 <= x <= cap``.

    ``y`` must satisfy ``0 <= y <= cap`` and ``sum(y) ~= total`` per row; ``sum(cap) >= total`` must hold.
    """
    cap = np.asarray(cap, dtype=np.int64)
    total = np.asarray(total, dtype=np.int64)
    y = np.clip(np.asarray(y, dtype=float), 0, cap)
    x = np.minimum(np.floor(y + 1e-9).astype(np.int64), cap)
    k = total - x.sum(axis=1)
    if (k > 0).any():
        rows = np.flatnonzero(k > 0)
        frac = y[rows] - x[rows]
        score = np.where(x[rows] < cap[rows], frac, -np.inf)
        order = np.argsort(-score, axis=1, kind="stable")
        rank = np.empty_like(order)
        np.put_along_axis(rank, order, np.broadcast_to(np.arange(y.shape[1]), order.shape), axis=1)
        add = (rank < k[rows, None]) & np.isfinite(score)
        if (add.sum(axis=1) != k[rows]).any():
            raise ValueError("allocation cannot be rounded: total exceeds the caps")
        x[rows] += add
    if (k < 0).any():
        rows = np.flatnonzero(k < 0)
        over = x[rows] - y[rows]
        score = np.where(x[rows] > 0, over, -np.inf)
        order = np.argsort(-score, axis=1, kind="stable")
        rank = np.empty_like(order)
        np.put_along_axis(rank, order, np.broadcast_to(np.arange(y.shape[1]), order.shape), axis=1)
        sub = (rank < -k[rows, None]) & np.isfinite(score)
        x[rows] -= sub
    return x


def water_fill(e: np.ndarray, w: np.ndarray, cap: np.ndarray) -> np.ndarray:
    """Per row, give ``min(cap_i, lam * w_i)`` with ``lam`` chosen so the row sums to ``min(e, sum caps of w>0)``."""
    e = np.asarray(e, dtype=float)
    w = np.asarray(w, dtype=float)
    cap = np.asarray(cap, dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        r = np.where(w > 0, cap / w, np.inf)
    order = np.argsort(r, axis=1, kind="stable")
    rs = np.take_along_axis(r, order, axis=1)
    cs = np.take_along_axis(np.where(w > 0, cap, 0.0), order, axis=1)
    ws = np.take_along_axis(w, order, axis=1)
    cum_c = np.cumsum(cs, axis=1) - cs  # capacity of members saturated before position k
    suf_w = np.cumsum(ws[:, ::-1], axis=1)[:, ::-1]  # weight of members not yet saturated
    with np.errstate(invalid="ignore"):
        filled = np.where(np.isinf(rs), np.inf, cum_c + rs * suf_w)
    k = (filled < e[:, None] - 1e-9).sum(axis=1)
    n = w.shape[1]
    kk = np.minimum(k, n - 1)
    cum_k = np.take_along_axis(cum_c, kk[:, None], axis=1)[:, 0]
    suf_k = np.take_along_axis(suf_w, kk[:, None], axis=1)[:, 0]
    with np.errstate(divide="ignore", invalid="ignore"):
        lam = np.where(suf_k > 0, (e - cum_k) / suf_k, np.inf)  # no unsaturated weight left: everyone at cap
    lam = np.where(k >= n, np.inf, lam)
    with np.errstate(invalid="ignore"):
        out = np.where(w > 0, np.minimum(cap, lam[:, None] * w), 0.0)
    return np.nan_to_num(out, nan=0.0, posinf=0.0)


def _proportional(e: np.ndarray, demand: np.ndarray) -> np.ndarray:
    d = demand.sum(axis=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        y = np.where(d[:, None] > 0, e[:, None] * demand / d[:, None], 0.0)
    return np.minimum(y, demand)


@dataclass
class Allocation:
    community_wh: np.ndarray  # (T,) E per interval
    member_wh: np.ndarray  # (T, N) allocation by the configured key
    regulatory_wh: np.ndarray  # (T, N) allocation proportional to consumption (Art. 19g para. 4)
    sold_wh: np.ndarray  # (T, K) community energy per producer
    export_wh: np.ndarray  # (T, K) exported energy per producer


def allocate_members(
    consumption: np.ndarray, production_total: np.ndarray, key: str, shares: np.ndarray, static_fraction: float = 0.5
) -> np.ndarray:
    """Integer Wh allocation (T, N) of ``min(production, consumption)`` with the given key."""
    c = np.asarray(consumption, dtype=np.int64)
    e = np.minimum(np.asarray(production_total, dtype=np.int64), c.sum(axis=1))
    cf = c.astype(float)
    ef = e.astype(float)
    w = np.broadcast_to(np.asarray(shares, dtype=float), c.shape) * (c > 0)
    if key == "proportional":
        y = _proportional(ef, cf)
    elif key in ("static", "hybrid"):
        part = ef if key == "static" else ef * static_fraction
        y1 = water_fill(part, w, cf)
        left = np.maximum(ef - y1.sum(axis=1), 0.0)
        y = y1 + _proportional(left, np.maximum(cf - y1, 0.0))
    else:
        raise ValueError(f"unknown allocation key {key!r}")
    return integerize(y, c, e)


def allocate(
    consumption: np.ndarray, production: np.ndarray, key: str, shares: np.ndarray, static_fraction: float = 0.5
) -> Allocation:
    """Full allocation: members by ``key``, regulatory (proportional) quantities, producer sold/export split."""
    c = np.asarray(consumption, dtype=np.int64)
    p = np.asarray(production, dtype=np.int64)
    if c.ndim != 2 or p.ndim != 2 or len(c) != len(p):
        raise ValueError("consumption (T, N) and production (T, K) must share T")
    if (c < 0).any() or (p < 0).any():
        raise ValueError("meter values must be >= 0 after cleaning")
    ptot = p.sum(axis=1)
    e = np.minimum(ptot, c.sum(axis=1))
    member = allocate_members(c, ptot, key, shares, static_fraction)
    regulatory = member if key == "proportional" else allocate_members(c, ptot, "proportional", shares)
    if p.shape[1]:
        sold = integerize(_proportional(e.astype(float), p.astype(float)), p, e)
    else:
        sold = np.zeros_like(p)
    return Allocation(community_wh=e, member_wh=member, regulatory_wh=regulatory, sold_wh=sold, export_wh=p - sold)
