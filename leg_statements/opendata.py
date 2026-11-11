"""ElCom electricity tariff open data (LINDAS SPARQL endpoint), reduced to numbers.

Source: ElCom, dataset "electricity prices per grid operator" on LINDAS / opendata.swiss ("open use" terms).
Only numeric components and public numeric identifiers are kept (no operator or product names).
"""

from __future__ import annotations

import io
import urllib.parse
import urllib.request
from pathlib import Path

import pandas as pd

DATA = Path(__file__).parent / "data"
ELCOM_FILE = DATA / "elcom_2026_h4.csv"
LINDAS_URL = "https://ld.admin.ch/query"
REF_KWH = {"H4": 4500}  # ElCom reference consumption of the category, kWh per year
_NS = "https://energy.ld.admin.ch/elcom/electricityprice"
_FIELDS = [
    "gridusage", "gridworkingprice", "energy", "energyworkingprice", "energyfixcost",
    "annualmeteringcost", "charge", "aidfee", "total",
]
_SPARQL = """PREFIX e: <{ns}/dimension/>
SELECT ?muni {vars} WHERE {{ GRAPH <https://lindas.admin.ch/elcom/electricityprice> {{
 ?o a <https://cube.link/Observation> ; e:period "{year}"^^<http://www.w3.org/2001/XMLSchema#gYear> ;
   e:operator <{ns}/operator/{op}> ; e:category <{ns}/category/{cat}> ;
   e:product <{ns}/product/standard> ; e:municipality ?muni ; {triples} . }} }}"""


def fetch_elcom(operator_id: int = 486, municipality: int = 101, category: str = "H4", year: int = 2026,
                out: Path = ELCOM_FILE) -> Path:
    """Download one ElCom tariff row (standard product) and store its numeric components."""
    q = _SPARQL.format(
        ns=_NS, year=year, op=operator_id, cat=category,
        vars=" ".join(f"?{f}" for f in _FIELDS), triples=" ; ".join(f"e:{f} ?{f}" for f in _FIELDS),
    )
    req = urllib.request.Request(
        LINDAS_URL, data=urllib.parse.urlencode({"query": q}).encode(),
        headers={"Accept": "text/csv", "User-Agent": "leg-member-statements"},
    )
    with urllib.request.urlopen(req, timeout=120) as r:  # noqa: S310 - fixed https URL
        raw = pd.read_csv(io.StringIO(r.read().decode("utf-8")))
    raw["bfs_municipality"] = raw["muni"].str.rsplit("/", n=1).str[-1].astype(int)
    row = raw[raw["bfs_municipality"] == municipality]
    if row.empty:
        raise ValueError(f"no ElCom row for operator {operator_id}, municipality {municipality}")
    row = row.iloc[[0]][_FIELDS].copy()
    row.insert(0, "category", category)
    row.insert(0, "bfs_municipality", municipality)
    row.insert(0, "operator_id", operator_id)
    row.insert(0, "year", year)
    row.to_csv(out, index=False)
    return out


def tariff_from_elcom(path: Path = ELCOM_FILE) -> dict:
    """Map the stored ElCom row to the tariff fields of the configuration."""
    r = pd.read_csv(path).iloc[0]
    ref = REF_KWH[r["category"]]
    grid_fixed = (r["gridusage"] - r["gridworkingprice"]) * ref / 100.0  # CHF/yr hidden in the per-kWh average
    return {
        "source": (f"ElCom open data {int(r['year'])}, operator id {int(r['operator_id'])}, "
                   f"municipality {int(r['bfs_municipality'])}, category {r['category']}, standard product"),
        "year": int(r["year"]),
        "energy_rp": float(r["energyworkingprice"]),
        "grid_working_rp": float(r["gridworkingprice"]),
        "levies_rp": float(r["charge"]),
        "grid_surcharge_rp": float(r["aidfee"]),
        "fixed_chf_per_year": round(float(r["energyfixcost"]) + float(grid_fixed) + float(r["annualmeteringcost"]), 2),
    }
