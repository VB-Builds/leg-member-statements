"""Community, member, allocation and tariff configuration (plain JSON, validated on load)."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path

KEYS = ("static", "proportional", "hybrid")
MODES = ("LEG", "vZEV")


class ConfigError(ValueError):
    """Raised for an invalid community configuration."""


@dataclass
class Member:
    id: str
    name: str
    role: str  # "consumer" or "producer"
    meter: str
    share: float = 0.0  # static allocation share (consumers only, relative weight)
    join: date | None = None  # first day of membership (local date, inclusive)
    leave: date | None = None  # last day of membership (local date, inclusive)
    max_kw: float = 30.0  # plausibility limit: a 15-minute value above max_kw * 0.25 kWh is rejected

    def active_on(self, d: date) -> bool:
        return (self.join is None or d >= self.join) and (self.leave is None or d <= self.leave)


@dataclass
class Tariffs:
    """All prices excl. VAT. Rp/kWh for energy-based items, CHF for fixed items."""

    source: str
    year: int
    internal_price_rp: float  # price members pay for community energy (set by the community)
    energy_rp: float  # supply energy working price from the grid operator
    grid_working_rp: float  # grid-usage working price (Netznutzung, per kWh)
    grid_non_reducible_rp: float = 0.0  # part of grid_working_rp that the reduction must not touch (Art. 19h para. 5)
    levies_rp: float = 0.0  # levies to public authorities (Abgaben an Gemeinwesen)
    grid_surcharge_rp: float = 0.0  # federal grid surcharge (Netzzuschlag)
    fixed_chf_per_year: float = 0.0  # base fees: energy + grid + metering, per consumer and year
    feed_in_rp: float = 0.0  # remuneration for energy exported to the grid
    admin_fee_chf_per_month: float = 0.0  # community administration fee per consumer and month
    vat_rate_grid: float = 0.081  # VAT on the grid-operator part
    vat_rate_internal: float = 0.0  # VAT on the internal community part (0 if the community is not VAT-registered)
    reduction_single_level: float = 0.40  # Art. 19h para. 1 StromVV
    reduction_with_transformation: float = 0.20  # Art. 19h para. 3 StromVV


@dataclass
class Community:
    name: str
    mode: str = "LEG"
    timezone: str = "Europe/Zurich"
    transformation_required: bool = False  # True -> 20 % reduction instead of 40 %
    key: str = "proportional"
    static_fraction: float = 0.5  # hybrid key: part of community energy split by static shares
    reduction_basis: str = "regulatory"  # "regulatory" (Art. 19g para. 4: proportional) or "internal" (configured key)
    members: list[Member] = field(default_factory=list)
    tariffs: Tariffs | None = None

    @property
    def consumers(self) -> list[Member]:
        return [m for m in self.members if m.role == "consumer"]

    @property
    def producers(self) -> list[Member]:
        return [m for m in self.members if m.role == "producer"]

    @property
    def reduction(self) -> float:
        if self.mode != "LEG":
            return 0.0
        t = self.tariffs
        return t.reduction_with_transformation if self.transformation_required else t.reduction_single_level

    def to_dict(self) -> dict:
        d = asdict(self)
        for m in d["members"]:
            for k in ("join", "leave"):
                m[k] = m[k].isoformat() if m[k] else None
        return d


def _date(v) -> date | None:
    if v in (None, ""):
        return None
    try:
        return date.fromisoformat(str(v))
    except ValueError as e:
        raise ConfigError(f"invalid date {v!r}") from e


def from_dict(d: dict) -> Community:
    try:
        members = [
            Member(
                id=str(m["id"]),
                name=str(m.get("name", m["id"])),
                role=str(m["role"]),
                meter=str(m["meter"]),
                share=float(m.get("share", 0.0)),
                join=_date(m.get("join")),
                leave=_date(m.get("leave")),
                max_kw=float(m.get("max_kw", 30.0)),
            )
            for m in d["members"]
        ]
        tariffs = Tariffs(**d["tariffs"])
        kw = {k: d[k] for k in ("mode", "timezone", "transformation_required", "key", "static_fraction", "reduction_basis") if k in d}
        c = Community(name=str(d["name"]), members=members, tariffs=tariffs, **kw)
    except (KeyError, TypeError) as e:
        raise ConfigError(f"missing or unexpected field: {e}") from e
    validate(c)
    return c


def validate(c: Community) -> None:
    if c.mode not in MODES:
        raise ConfigError(f"mode must be one of {MODES}, got {c.mode!r}")
    if c.key not in KEYS:
        raise ConfigError(f"key must be one of {KEYS}, got {c.key!r}")
    if not 0.0 <= c.static_fraction <= 1.0:
        raise ConfigError("static_fraction must be within 0..1")
    if c.reduction_basis not in ("regulatory", "internal"):
        raise ConfigError("reduction_basis must be 'regulatory' or 'internal'")
    ids = [m.id for m in c.members]
    if len(set(ids)) != len(ids):
        raise ConfigError("member ids must be unique")
    meters = [m.meter for m in c.members]
    if len(set(meters)) != len(meters):
        raise ConfigError("each meter can belong to one member only")
    for m in c.members:
        if m.role not in ("consumer", "producer"):
            raise ConfigError(f"{m.id}: role must be consumer or producer")
        if m.share < 0:
            raise ConfigError(f"{m.id}: share must be >= 0")
        if m.join and m.leave and m.leave < m.join:
            raise ConfigError(f"{m.id}: leave date before join date")
        if m.max_kw <= 0:
            raise ConfigError(f"{m.id}: max_kw must be > 0")
    if not c.consumers:
        raise ConfigError("at least one consumer is required")
    if c.key in ("static", "hybrid") and sum(m.share for m in c.consumers) <= 0:
        raise ConfigError("static and hybrid keys need positive shares")
    t = c.tariffs
    for name in ("internal_price_rp", "energy_rp", "grid_working_rp", "levies_rp", "grid_surcharge_rp", "feed_in_rp"):
        if getattr(t, name) < 0:
            raise ConfigError(f"tariff {name} must be >= 0")
    if not 0 <= t.grid_non_reducible_rp <= t.grid_working_rp:
        raise ConfigError("grid_non_reducible_rp must be within 0..grid_working_rp")
    for name in ("reduction_single_level", "reduction_with_transformation", "vat_rate_grid", "vat_rate_internal"):
        if not 0 <= getattr(t, name) < 1:
            raise ConfigError(f"{name} must be within 0..1")


def load(path: str | Path) -> Community:
    with open(path, encoding="utf-8") as f:
        return from_dict(json.load(f))


def save(c: Community, path: str | Path) -> None:
    Path(path).write_text(json.dumps(c.to_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
