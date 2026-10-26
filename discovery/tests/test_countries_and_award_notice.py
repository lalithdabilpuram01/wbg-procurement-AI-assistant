import pytest

from wbdisc.award_notice import parse_award_notice
from wbdisc.countries import is_regional, to_iso2


@pytest.mark.parametrize("wb,iso", [("ZR", "CD"), ("GZ", "PS"), ("YF", "RS"), ("TP", "TL"), ("RY", "YE"),
                                    ("KG", "KG"), ("XK", "XK"), (" ng ", "NG")])
def test_to_iso2_aliases_and_passthrough(wb, iso):
    assert to_iso2(wb) == iso


@pytest.mark.parametrize("code", ["3W", "3E", "6O", "7C", "E5", "P2", "99", "1W", "ZZ", "AC", "CB", "", None, "null",
                                  "YU", "CS", "SX"])
def test_to_iso2_refuses_regional_and_unmappable(code):
    assert to_iso2(code) is None


def test_is_regional():
    assert is_regional("3W") and is_regional("E4") and not is_regional("ET") and not is_regional("PE")


STEP_NOTICE = """<p>Contract Award</p><p>Project: P151416-Urban Development Project</p>
<p>Loan/Credit/TF Info: IDA-D1010; IDA-57620</p><p>Bid/Contract Reference No: IDA-UDP-SSS-21-1</p>
<p>Procurement Method: CDS-Direct Selection</p><p>Notice Version No: 2</p>
<p>Date Notification of Award Issued <br>(YYYY/MM/DD)<br>2021/05/31</p>
<p>Duration of Contract <br>4 Month(s)</p>
<p>Awarded Bidder(s): HYDROPHIL GMBH (567769) <br>Country: Austria</p>
<p>Bid Price at Opening<br>KGS 789360.00<br>Evaluated Bid Price<br>KGS 789360.00 Signed Contract price<br>KGS 789,360.00</p>
<p>Evaluated Bidder(s): ACME LLC (111222) <br>Country: Kyrgyz Republic</p>
<p>Rejected Bidder(s): BETA SA (333444) Non Responsive</p>"""


def test_parse_step_award_notice():
    r = parse_award_notice(STEP_NOTICE)
    assert r["is_step_template"]
    assert r["loan_ids"] == ["IDA-D1010", "IDA-57620"]
    assert r["reference_in_text"] == "IDA-UDP-SSS-21-1"
    assert r["notice_version_no"] == 2
    assert r["award_notification_date"] == "2021-05-31"
    assert (r["duration_value"], r["duration_unit"]) == (4, "month")
    assert r["awarded_supplier_ids"] == ["567769"]
    assert r["evaluated_supplier_ids"] == ["111222"] and r["rejected_supplier_ids"] == ["333444"]
    assert r["bidder_count_by_id"] == 3
    assert (r["signed_price_currency"], r["signed_price_amount"]) == ("KGS", 789360.0)


def test_parse_non_step_text_returns_no_fields():
    r = parse_award_notice("<p>Avis d'attribution de marché – lot 3</p>")
    assert r == {"is_step_template": False}
