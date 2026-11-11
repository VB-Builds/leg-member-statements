"""README figure (1600x900) and dark LinkedIn figure (960x700) from demo results."""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

DARK = {"bg": "#16213A", "grid": "#2A3750", "text": "#F8FAFC", "muted": "#A9B6CB", "accent": "#38BDF8",
        "green": "#34D399", "amber": "#FBBF24", "red": "#F87171", "grey": "#64748B", "violet": "#A78BFA"}
LIGHT = {"bg": "#FFFFFF", "grid": "#E5E7EB", "text": "#111827", "muted": "#6B7280", "accent": "#0284C7",
         "green": "#059669", "amber": "#D97706", "red": "#DC2626", "grey": "#64748B", "violet": "#7C3AED"}
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
KEY_STYLE = [("proportional", "accent", "o"), ("static, shares by yearly use", "green", "s"),
             ("static, equal shares", "amber", "D"), ("hybrid 50/50, equal shares", "violet", "^")]


def _font():
    from matplotlib import font_manager

    names = {f.name for f in font_manager.fontManager.ttflist}
    return "Segoe UI" if "Segoe UI" in names else "DejaVu Sans"


def _style(ax, pal):
    ax.set_facecolor(pal["bg"])
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(pal["grid"])
    ax.tick_params(colors=pal["muted"])
    ax.yaxis.label.set_color(pal["muted"])
    ax.grid(axis="y", color=pal["grid"], lw=0.8)
    ax.set_axisbelow(True)


def _energy_panel(ax, monthly, pal, fs):
    x = np.arange(12)
    comm = np.array(monthly["community_kwh"]) / 1000
    exp = np.array(monthly["export_kwh"]) / 1000
    cons = np.array(monthly["consumption_kwh"]) / 1000
    ax.bar(x, comm, color=pal["green"], width=0.7, label="PV used in the community")
    ax.bar(x, exp, bottom=comm, color=pal["amber"], width=0.7, label="PV exported to the grid")
    ax.plot(x, cons, color=pal["accent"], lw=3, marker="o", ms=5, label="members' consumption")
    ax.set_xticks(x, MONTHS, fontsize=fs)
    ax.set_ylabel("MWh per month", fontsize=fs)
    ax.tick_params(axis="y", labelsize=fs)
    ax.legend(frameon=False, fontsize=fs, labelcolor=pal["text"], loc="upper left", ncol=1)
    ax.set_ylim(0, max((comm + exp).max(), cons.max()) * 1.45)


def _key_panel(ax, results, pal, fs, ms=8):
    vs = results["vs_proportional_chf"]
    members = sorted(next(iter(vs.values())).keys())
    y = np.arange(len(members))
    lo = np.array([min(vs[k][m] for k, _, _ in KEY_STYLE) for m in members])
    hi = np.array([max(vs[k][m] for k, _, _ in KEY_STYLE) for m in members])
    ax.hlines(y, lo, hi, color=pal["grey"], lw=3, zorder=1)
    for k, c, mk in KEY_STYLE:
        ax.scatter([vs[k][m] for m in members], y, color=pal[c], marker=mk, s=ms**2, zorder=2, label=k)
    ax.set_yticks(y, members, fontsize=fs)
    ax.invert_yaxis()
    ax.axvline(0, color=pal["muted"], lw=1)
    ax.set_xlabel("yearly bill vs proportional key (CHF)", fontsize=fs, color=pal["muted"])
    ax.tick_params(axis="x", labelsize=fs)
    ax.grid(axis="x", color=pal["grid"], lw=0.8)
    ax.grid(axis="y", visible=False)


def make_figures(results: dict, statement_pdf: Path, docs: Path) -> None:
    import pandas as pd

    docs.mkdir(parents=True, exist_ok=True)
    out = Path(statement_pdf).parents[3]
    sm = pd.read_csv(out / "statements" / "community_summary.csv")
    results = dict(results)
    results["_monthly"] = {"community_kwh": sm["community_kwh"].tolist(), "export_kwh": sm["export_kwh"].tolist(),
                           "consumption_kwh": sm["consumption_kwh"].tolist()}
    plt.rcParams["font.family"] = _font()
    readme_figure(results, statement_pdf, docs / "figure.png")
    post_figure(results, docs / "post-figure.png")


def _render_pdf(path: Path, scale: float = 2.0):
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(str(path))
    img = doc[0].render(scale=scale).to_pil()
    doc.close()
    return np.asarray(img)


def readme_figure(results: dict, statement_pdf: Path, path: Path) -> None:
    pal = LIGHT
    fig = plt.figure(figsize=(16, 9), dpi=100, facecolor=pal["bg"])
    gs = fig.add_gridspec(2, 2, width_ratios=[1.35, 1], left=0.06, right=0.98, top=0.86, bottom=0.08, hspace=0.42, wspace=0.12)
    ck = results["checks"]
    sp = results["key_spread_chf"]
    fig.text(0.06, 0.95, "Monthly LEG member statements: every kWh and every Rappen reconciles", fontsize=21,
             fontweight="bold", color=pal["text"])
    fig.text(0.06, 0.915, f"Data: {results['data']}. Tariffs: {results['tariffs']} (Source: ElCom).",
             fontsize=11.5, color=pal["muted"])
    ax1 = fig.add_subplot(gs[0, 0])
    _style(ax1, pal)
    _energy_panel(ax1, results["_monthly"], pal, 11.5)
    ax1.set_title(f"Energy balance: {ck['max_abs_kwh_error_wh']} Wh error in {ck['intervals']:,} quarter-hours, "
                  f"{ck['max_abs_chf_error_cents']} cents between members and producers", fontsize=13, color=pal["text"], loc="left")
    ax2 = fig.add_subplot(gs[1, 0])
    _style(ax2, pal)
    _key_panel(ax2, results, pal, 11, ms=8)
    ax2.legend(frameon=False, fontsize=10.5, labelcolor=pal["text"], loc="lower right", ncol=2)
    ax2.set_title(f"Allocation key moves a household's yearly bill by at most CHF {sp['max']:.2f} (median {sp['median']:.2f})",
                  fontsize=13, color=pal["text"], loc="left")
    ax3 = fig.add_subplot(gs[:, 1])
    ax3.imshow(_render_pdf(statement_pdf, 2.0))
    ax3.set_axis_off()
    ax3.set_title("One-page statement, member who joined on 16 March", fontsize=13, color=pal["text"], loc="left")
    fig.savefig(path, dpi=100, facecolor=pal["bg"])
    plt.close(fig)


def post_figure(results: dict, path: Path) -> None:
    pal = DARK
    fig = plt.figure(figsize=(9.6, 7.0), dpi=100, facecolor=pal["bg"])
    ck = results["checks"]
    sp = results["key_spread_chf"]
    fig.text(0.04, 0.945, "LEG billing: every kWh and every Rappen reconciles", fontsize=14,
             fontweight="bold", color=pal["text"])
    fig.text(0.04, 0.905, "Simulated 12 households + 2 PV, 2026 | tariffs: ElCom open data 2026", fontsize=12, color=pal["muted"])
    ax1 = fig.add_axes([0.10, 0.50, 0.86, 0.33])
    _style(ax1, pal)
    _energy_panel(ax1, results["_monthly"], pal, 12)
    ax1.set_title(f"{ck['max_abs_kwh_error_wh']} Wh error in {ck['intervals']:,} quarter-hours", fontsize=13.5,
                  color=pal["text"], loc="left")
    ax2 = fig.add_axes([0.10, 0.08, 0.86, 0.32])
    _style(ax2, pal)
    per = results["key_spread_chf"]["per_member"]
    members = sorted(per)
    vals = [per[m] for m in members]
    ax2.bar(range(len(members)), vals, color=pal["violet"], width=0.7)
    ax2.set_xticks(range(len(members)), members, fontsize=11.5)
    ax2.tick_params(axis="y", labelsize=12)
    ax2.set_ylabel("CHF per year", fontsize=12)
    ax2.set_ylim(0, max(vals) * 1.3)
    ax2.set_title(f"Spread of each yearly bill across 4 allocation keys: max CHF {sp['max']:.2f}", fontsize=13.5,
                  color=pal["text"], loc="left")
    fig.savefig(path, dpi=100, facecolor=pal["bg"])
    plt.close(fig)
