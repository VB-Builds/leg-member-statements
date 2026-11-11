import json
from pathlib import Path

import pytest
from pypdf import PdfReader

from leg_statements import cli, config, pdf
from leg_statements.config import ConfigError

from .test_billing import tiny

BASE = {
    "name": "X",
    "members": [{"id": "A", "role": "consumer", "meter": "MA", "share": 1}, {"id": "P", "role": "producer", "meter": "MP"}],
    "tariffs": {"source": "t", "year": 2026, "internal_price_rp": 9, "energy_rp": 11, "grid_working_rp": 7.5},
}


def with_(path, value):
    d = json.loads(json.dumps(BASE))
    obj = d
    for k in path[:-1]:
        obj = obj[k]
    obj[path[-1]] = value
    return d


def test_valid_config_round_trip(tmp_path):
    c = config.from_dict(BASE)
    config.save(c, tmp_path / "c.json")
    c2 = config.load(tmp_path / "c.json")
    assert c2.to_dict() == c.to_dict()
    assert c2.reduction == 0.40


@pytest.mark.parametrize("d", [
    with_(["key"], "lottery"),
    with_(["mode"], "ZEVX"),
    with_(["members", 1, "meter"], "MA"),
    with_(["members", 0, "leave"], "2025-12-31") | {"members": [
        {"id": "A", "role": "consumer", "meter": "MA", "join": "2026-02-01", "leave": "2026-01-01"}]},
    with_(["tariffs", "energy_rp"], -1),
    with_(["members", 0, "role"], "owner"),
    with_(["static_fraction"], 1.5),
    {"name": "no members or tariffs"},
])
def test_invalid_configs_are_rejected(d):
    with pytest.raises(ConfigError):
        config.from_dict(d)


def test_swiss_number_formatting():
    assert pdf.chf(123456) == "1'234.56"
    assert pdf.chf(-191) == "-1.91"
    assert pdf.kwh(1234567) == "1'234.567"


def _text(p: Path) -> str:
    r = PdfReader(str(p))
    assert len(r.pages) == 1
    return r.pages[0].extract_text()


def test_pdf_statements_are_one_page_and_show_the_amounts(tmp_path):
    from leg_statements import billing

    c, clean = tiny()
    run = billing.bill(c, clean)
    recs = run.statements.to_dict("records")
    a = next(r for r in recs if r["member_id"] == "A")
    p = next(r for r in recs if r["member_id"] == "P")
    a = {k: v for k, v in a.items() if v == v}
    p = {k: v for k, v in p.items() if v == v}
    t = _text(pdf.write_statement(c, a, c.key, tmp_path / "A.pdf"))
    assert "Mitgliederabrechnung" in t and "Total B" in t and "87.12" in t and "LEG-Abschlag" in t
    t = _text(pdf.write_statement(c, p, c.key, tmp_path / "P.pdf"))
    assert "Gutschrift Produzent" in t and "0.28" in t
    s = run.summary.to_dict("records")[0]
    t = _text(pdf.write_summary(c, "2026-01", recs and [a, p], s, c.key, tmp_path / "S.pdf"))
    assert "Abstimmung" in t and "0 Wh" in t


def test_cli_simulate_then_bill_one_month(tmp_path):
    assert cli.main(["simulate", "--out", str(tmp_path / "sim"), "--seed", "3"]) == 0
    rc = cli.main(["bill", "--config", str(tmp_path / "sim" / "community.json"), "--readings",
                   str(tmp_path / "sim" / "readings.csv"), "--start", "2026-10", "--out", str(tmp_path / "bill"), "--key", "static"])
    assert rc == 0
    out = tmp_path / "bill"
    lines = (out / "member_statements.csv").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1 + 13  # 11 households (H12 left in August) + 2 producers in October
    assert (out / "pdf" / "2026-10" / "H01.pdf").exists() and (out / "pdf" / "2026-10" / "_community_summary.pdf").exists()
    checks = json.loads((out / "checks.json").read_text())
    assert checks["intervals"] == 2980 and checks["max_abs_chf_error_cents"] == 0


def test_cli_reports_bad_config(tmp_path, capsys):
    (tmp_path / "c.json").write_text(json.dumps(with_(["key"], "x")), encoding="utf-8")
    (tmp_path / "r.csv").write_text("meter_id,timestamp,kwh\n", encoding="utf-8")
    assert cli.main(["bill", "--config", str(tmp_path / "c.json"), "--readings", str(tmp_path / "r.csv"), "--start", "2026-01"]) == 1
    assert "key must be one of" in capsys.readouterr().err
