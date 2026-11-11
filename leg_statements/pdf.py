"""One-page PDF statements (German labels) and a monthly community summary, with ReportLab."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .config import Community

KEY_DE = {"proportional": "proportional zum Verbrauch (15 min)", "static": "statische Anteile", "hybrid": "hybrid"}
INK = colors.HexColor("#1F2937")
MUTED = colors.HexColor("#6B7280")
LINE = colors.HexColor("#D1D5DB")
BAND = colors.HexColor("#F3F4F6")

_H1 = ParagraphStyle("h1", fontName="Helvetica-Bold", fontSize=15, leading=19, textColor=INK)
_H2 = ParagraphStyle("h2", fontName="Helvetica-Bold", fontSize=10.5, leading=14, textColor=INK, spaceBefore=6)
_TX = ParagraphStyle("tx", fontName="Helvetica", fontSize=9, leading=12, textColor=INK)
_SM = ParagraphStyle("sm", fontName="Helvetica", fontSize=7.2, leading=9.2, textColor=MUTED)


def chf(cents: int) -> str:
    """Swiss style amount: 1'234.50 (minus sign kept)."""
    s = "-" if cents < 0 else ""
    c = abs(int(cents))
    return f"{s}{c // 100:,}.{c % 100:02d}".replace(",", "'")


def kwh(wh: int) -> str:
    s = "-" if wh < 0 else ""
    w = abs(int(wh))
    return f"{s}{w // 1000:,}.{w % 1000:03d}".replace(",", "'")


def dmy(iso: str) -> str:
    return date.fromisoformat(iso).strftime("%d.%m.%Y")


def _table(rows, widths, money_cols=(), bold_last=False, header=True):
    t = Table(rows, colWidths=widths, hAlign="LEFT")
    st = [
        ("FONT", (0, 0), (-1, -1), "Helvetica", 8.8),
        ("TEXTCOLOR", (0, 0), (-1, -1), INK),
        ("LINEBELOW", (0, 0), (-1, -1), 0.3, LINE),
        ("TOPPADDING", (0, 0), (-1, -1), 2.2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.2),
    ]
    if header:
        st += [("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 8.4), ("BACKGROUND", (0, 0), (-1, 0), BAND)]
    for c in money_cols:
        st.append(("ALIGN", (c, 0), (c, -1), "RIGHT"))
    if bold_last:
        st += [("FONT", (0, -1), (-1, -1), "Helvetica-Bold", 9), ("LINEABOVE", (0, -1), (-1, -1), 0.8, INK)]
    t.setStyle(TableStyle(st))
    return t


def _price(rp: float) -> str:
    return f"{rp:.3f} Rp/kWh"


def _footer(community: Community) -> list:
    t = community.tariffs
    red = community.reduction
    basis = ("Art. 19g Abs. 4 StromVV, proportional zum Verbrauch" if community.reduction_basis == "regulatory"
             else "internem Verteilschlüssel")
    leg_txt = (f"LEG-Abschlag {red:.0%} auf den Netznutzungs-Arbeitspreis (Art. 19h StromVV; Menge nach {basis}). "
               if community.mode == "LEG" else "vZEV: Gemeinschaftsstrom ohne Netznutzung, Abgaben und Netzzuschlag (Vereinfachung). ")
    return [
        Spacer(1, 5 * mm),
        Paragraph(
            "Simulierte Messdaten (keine echten Zähler). Tarife: " + t.source + ". Interner Preis, Einspeisevergütung und "
            "Gemeinschaftsgebühr sind Annahmen. " + leg_txt + "Teil B ist eine Schätzung; massgebend ist die Rechnung des "
            "Netzbetreibers. Jede Zeile ist auf den Rappen gerundet (kaufmännisch). Keine Rechts- oder Steuerberatung.",
            _SM,
        ),
    ]


def _head(community: Community, title: str, r: dict, key: str) -> list:
    period = f"{dmy(r['period_from'])} - {dmy(r['period_to'])} ({r['active_days']} von {r['days_in_month']} Tagen)"
    meta = [
        ["Abrechnungs-Nr.", r["statement_no"], "Mitglied", f"{r['member_id']} - {r['name']}"],
        ["Zeitraum", period, "Verteilschlüssel", KEY_DE.get(key, key)],
    ]
    t = Table(meta, colWidths=[28 * mm, 62 * mm, 28 * mm, 56 * mm], hAlign="LEFT")
    t.setStyle(TableStyle([
        ("FONT", (0, 0), (-1, -1), "Helvetica", 8.6),
        ("FONT", (0, 0), (0, -1), "Helvetica-Bold", 8.6),
        ("FONT", (2, 0), (2, -1), "Helvetica-Bold", 8.6),
        ("TEXTCOLOR", (0, 0), (-1, -1), INK),
        ("TOPPADDING", (0, 0), (-1, -1), 1.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5),
    ]))
    return [Paragraph(community.name, _H2), Paragraph(title, _H1), Spacer(1, 2 * mm), t]


def consumer_story(community: Community, r: dict, key: str) -> list:
    t = community.tariffs
    leg = community.mode == "LEG"
    reducible = t.grid_working_rp - t.grid_non_reducible_rp
    usage_wh = r["consumption_wh"] if leg else r["grid_wh"]
    story = _head(community, "Mitgliederabrechnung", r, key)
    est = r["estimated_intervals"]
    story += [
        Paragraph("Energiemengen", _H2),
        _table([
            ["Position", "kWh"],
            ["Verbrauch total", kwh(r["consumption_wh"])],
            ["davon Solarstrom aus der Gemeinschaft", kwh(r["community_wh"])],
            ["davon Strom aus dem Netz", kwh(r["grid_wh"])],
            ["Menge für LEG-Abschlag" if leg else "Menge für LEG-Abschlag (vZEV: keine)", kwh(r["reduction_basis_wh"] if leg else 0)],
        ], [130 * mm, 44 * mm], money_cols=(1,)),
        Paragraph(f"Messwerte: {r['intervals'] - est} von {r['intervals']} Viertelstunden gemessen, {est} ersetzt "
                  "(Interpolation oder Wert der Vorwoche).", _SM),
        Paragraph("A. Gemeinschaft (zahlbar an die Gemeinschaft)", _H2),
        _table([
            ["Position", "Menge", "Preis", "CHF"],
            ["Solarstrom aus der Gemeinschaft", f"{kwh(r['community_wh'])} kWh", _price(t.internal_price_rp), chf(r["a_community_energy"])],
            ["Gemeinschaftsgebühr", f"{r['active_days']}/{r['days_in_month']} Monat",
             f"{t.admin_fee_chf_per_month:.2f} CHF/Monat", chf(r["a_admin_fee"])],
            ["MWST", "", f"{t.vat_rate_internal:.1%}", chf(r["a_vat"])],
            ["Total A", "", "", chf(r["a_total"])],
        ], [74 * mm, 36 * mm, 36 * mm, 28 * mm], money_cols=(1, 2, 3), bold_last=True),
        Paragraph("B. Netzbetreiber (Schätzung)", _H2),
        _table([
            ["Position", "Menge", "Preis", "CHF"],
            ["Energie aus dem Netz", f"{kwh(r['grid_wh'])} kWh", _price(t.energy_rp), chf(r["b_energy"])],
            ["Netznutzung", f"{kwh(usage_wh)} kWh", _price(t.grid_working_rp), chf(r["b_grid_usage"])],
            [f"LEG-Abschlag Netznutzung ({community.reduction:.0%})", f"{kwh(r['reduction_basis_wh'] if leg else 0)} kWh",
             _price(-reducible * community.reduction), chf(r["b_reduction"])],
            ["Abgaben an Gemeinwesen", f"{kwh(usage_wh)} kWh", _price(t.levies_rp), chf(r["b_levies"])],
            ["Netzzuschlag", f"{kwh(usage_wh)} kWh", _price(t.grid_surcharge_rp), chf(r["b_surcharge"])],
            ["Grundpreise (Energie, Netz, Messung)", f"{r['active_days']}/{r['days_in_month']} Monat",
             f"{t.fixed_chf_per_year:.2f} CHF/Jahr", chf(r["b_fixed"])],
            ["MWST", "", f"{t.vat_rate_grid:.1%}", chf(r["b_vat"])],
            ["Total B", "", "", chf(r["b_total"])],
        ], [74 * mm, 36 * mm, 36 * mm, 28 * mm], money_cols=(1, 2, 3), bold_last=True),
        Paragraph("Vergleich", _H2),
        _table([
            ["Total A + B", chf(r["total"])],
            ["Gleicher Verbrauch ohne Gemeinschaft (inkl. MWST)", chf(r["without_community_total"])],
            ["Ersparnis durch die Gemeinschaft", chf(r["saving"])],
        ], [146 * mm, 28 * mm], money_cols=(1,), bold_last=True, header=False),
    ]
    return story + _footer(community)


def producer_story(community: Community, r: dict, key: str) -> list:
    t = community.tariffs
    est = r["estimated_intervals"]
    story = _head(community, "Gutschrift Produzent", r, key)
    story += [
        Paragraph("Energiemengen", _H2),
        _table([
            ["Position", "kWh"],
            ["Produktion (Einspeisung)", kwh(r["production_wh"])],
            ["davon in der Gemeinschaft abgesetzt", kwh(r["sold_wh"])],
            ["davon ins Netz zurückgeliefert", kwh(r["export_wh"])],
        ], [130 * mm, 44 * mm], money_cols=(1,)),
        Paragraph(f"Messwerte: {r['intervals'] - est} von {r['intervals']} Viertelstunden gemessen, {est} ersetzt.", _SM),
        Paragraph("Gutschrift", _H2),
        _table([
            ["Position", "Menge", "Preis", "CHF"],
            ["Verkauf an Mitglieder (von der Gemeinschaft)", f"{kwh(r['sold_wh'])} kWh", _price(t.internal_price_rp),
             chf(r["credit_community"])],
            ["Einspeisevergütung (Netzbetreiber, Schätzung)", f"{kwh(r['export_wh'])} kWh", _price(t.feed_in_rp), chf(r["credit_feed_in"])],
            ["Total Gutschrift", "", "", chf(r["credit_total"])],
        ], [74 * mm, 36 * mm, 36 * mm, 28 * mm], money_cols=(1, 2, 3), bold_last=True),
        Paragraph("Der Verkauf an Mitglieder entspricht auf den Rappen genau der Summe, die die Mitglieder in Teil A für "
                  "Solarstrom bezahlen; sie wird nach abgesetzten kWh verteilt (grösste Reste).", _SM),
    ]
    return story + _footer(community)


def _doc(path: Path) -> SimpleDocTemplate:
    path.parent.mkdir(parents=True, exist_ok=True)
    return SimpleDocTemplate(str(path), pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm,
                             bottomMargin=14 * mm, title="Abrechnung", author="leg-member-statements")


def write_statement(community: Community, r: dict, key: str, path: Path) -> Path:
    story = consumer_story(community, r, key) if r["role"] == "consumer" else producer_story(community, r, key)
    _doc(path).build(story)
    return path


def write_summary(community: Community, month: str, rows: list[dict], s: dict, key: str, path: Path) -> Path:
    cons = [r for r in rows if r["role"] == "consumer"]
    prods = [r for r in rows if r["role"] == "producer"]
    body = [["Mitglied", "Tage", "Verbrauch kWh", "Gemeinschaft kWh", "A CHF", "B CHF", "Ersparnis CHF"]]
    for r in cons:
        body.append([r["member_id"], str(r["active_days"]), kwh(r["consumption_wh"]), kwh(r["community_wh"]),
                     chf(r["a_total"]), chf(r["b_total"]), chf(r["saving"])])
    body.append(["Total", "", kwh(s["consumption_wh"]), kwh(sum(r["community_wh"] for r in cons)),
                 chf(s["a_total"]), chf(s["b_total"]), chf(s["saving"])])
    pbody = [["Produzent", "Produktion kWh", "Gemeinschaft kWh", "Netz kWh", "Gutschrift CHF"]]
    for r in prods:
        pbody.append([r["member_id"], kwh(r["production_wh"]), kwh(r["sold_wh"]), kwh(r["export_wh"]), chf(r["credit_community"])])
    pbody.append(["Total", kwh(s["production_wh"]), kwh(s["community_wh"]), kwh(s["export_wh"]), chf(s["producer_credits"])])
    checks = [
        ["Kontrolle", "Differenz"],
        ["Summe Zuteilung Mitglieder - Gemeinschaftsstrom", f"{s['kwh_error_alloc_wh']} Wh"],
        ["Abgesetzt + zurückgeliefert - Produktion", f"{s['kwh_error_production_wh']} Wh"],
        ["Gemeinschaft + Netz - Verbrauch", f"{s['kwh_error_consumption_wh']} Wh"],
        ["Bezahlt für Solarstrom (A) - Gutschriften Produzenten", f"{chf(s['chf_error_producers_cents'])} CHF"],
    ]
    story = [
        Paragraph(community.name, _H2),
        Paragraph(f"Gemeinschaftsübersicht {month}", _H1),
        Paragraph(f"Verteilschlüssel: {KEY_DE.get(key, key)}. Mitglieder: {s['consumers']}, Produzenten: {s['producers']}.", _TX),
        Paragraph("Mitglieder", _H2),
        _table(body, [22 * mm, 12 * mm, 28 * mm, 30 * mm, 26 * mm, 26 * mm, 30 * mm], money_cols=(1, 2, 3, 4, 5, 6), bold_last=True),
        Paragraph("Produzenten", _H2),
        _table(pbody, [30 * mm, 34 * mm, 36 * mm, 34 * mm, 40 * mm], money_cols=(1, 2, 3, 4), bold_last=True),
        Paragraph("Abstimmung", _H2),
        _table(checks, [134 * mm, 40 * mm], money_cols=(1,)),
    ]
    _doc(path).build(story + _footer(community))
    return path
