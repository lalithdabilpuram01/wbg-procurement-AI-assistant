from wbdisc.isr import parse_isr_text

G = ""  # private-use rating icon observed in WB ISR text renditions
ISR = f"""The World Bank
IBRD/IDA | Specific Investment Loan | FY 2012 | Seq No: 10 | ARCHIVED on 23-Dec-2016 | ISR25589 |

Name                                         Previous Rating              Current Rating

Progress towards achievement of PDO          {G} Satisfactory              {G} Moderately Satisfactory
Overall Implementation Progress (IP)         {G} Satisfactory              {G} Satisfactory
Procurement                                  {G} Moderately Satisfactory   {G} Moderately Unsatisfactory


Implementation Status and Key Decisions
 The project faced procurement-related delays.   Some narrative   More narrative

 Risk Category                          Rating at Approval      Previous Rating      Current Rating

 Political and Governance               --                      Low                  Low
 Institutional Capacity for Implementation and
 Sustainability
                                        --                      Low                  Substantial
 Fiduciary                              Moderate                Moderate             High
 Other                                  --                      --                   --


Results
"""


def test_header_fields():
    r = parse_isr_text(ISR)
    assert r["seq_no"] == 10 and r["archived_on"] == "2016-12-23" and r["rating_tables_found"] == 2


def test_performance_ratings_strip_glyphs_and_take_current_column():
    r = parse_isr_text(ISR)["ratings"]
    assert r == {"Progress towards achievement of PDO": "Moderately Satisfactory",
                 "Overall Implementation Progress (IP)": "Satisfactory",
                 "Procurement": "Moderately Unsatisfactory"}


def test_risk_table_wrapped_names_and_placeholders():
    r = parse_isr_text(ISR)["risk_ratings"]
    assert r["Institutional Capacity for Implementation and Sustainability"] == "Substantial"
    assert r["Fiduciary"] == "High"
    assert r["Other"] is None
    assert "The project faced procurement-related delays." not in r


def test_no_tables():
    r = parse_isr_text("plain text without ratings")
    assert r == {"ratings": {}, "risk_ratings": {}, "rating_tables_found": 0, "seq_no": None, "archived_on": None,
                 "template": None}


ISR_B = f"""SOUTH ASIA—Sri Lanka—IBRD/IDA—Investment Project Financing (IPF) | FY 2018—Seq No: 17—Archived on 15-Oct-2026—ISR09224
3. OVERALL RATINGS
Name Previous Current
Progress towards achievement of PDO {G}Highly Satisfactory {G}Highly Satisfactory
Overall Implementation Progress (IP) {G}Highly Satisfactory {G}Satisfactory
4. KEY ISSUES & STATUS
The project faced delays. Satisfactory progress overall.
5. SYSTEMATIC OPERATIONS RISK-RATING TOOL
Risk Category
Rating at Approval
Approval Package -
26 Apr 2018
Last Approved Rating
Proposed Rating
Institutional Capacity for Implementation and
Sustainability
{G}Moderate {G}Low {G}Low
Fiduciary {G}Moderate {G}Moderate {G}High
6. RESULTS
"""


def test_template_b():
    r = parse_isr_text(ISR_B)
    assert r["template"] == "B" and r["seq_no"] == 17 and r["archived_on"] == "2026-10-15"
    assert r["ratings"] == {"Progress towards achievement of PDO": "Highly Satisfactory",
                            "Overall Implementation Progress (IP)": "Satisfactory"}
    assert r["risk_ratings"] == {"Institutional Capacity for Implementation and Sustainability": "Low", "Fiduciary": "High"}


def test_name_continuation_after_values():
    txt = f"""| Seq No: 10 | ARCHIVED on 24-May-2024 |
 Risk Category        Rating at Approval    Previous Rating    Current Rating
 Institutional Capacity for Implementation and
                      {G}Substantial        {G}Substantial      {G}High
 Sustainability
 Fiduciary            {G}Substantial        {G}Substantial      {G}Substantial
"""
    r = parse_isr_text(txt)["risk_ratings"]
    assert r == {"Institutional Capacity for Implementation and Sustainability": "High", "Fiduciary": "Substantial"}
