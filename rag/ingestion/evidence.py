"""Contract evidence links between plan activities and DS00005 contracts.

Link key: same project_id AND the contract's package reference (normalised; lot / sequence suffix removed) equals the
activity's package reference. Non-STEP references are compared as whole normalised strings. A shared project_id alone
never creates a link. Statuses (kept separate, never merged):
  amount_verified                plan actual amount > 0 equals the contract amount (one row : one contract) or the sum
                                 of the lot contracts (one row : n contracts), tolerance max(US$1, 0.5%)
  exact_actual_date_supported    plan 'Signed Contract | Actual' equals the contract signing date exactly, plan actual
                                 amount 0 or blank (dates differing by 1-7 days are NOT accepted)
  candidate                      reference match without independent corroboration (also: values only from the text
                                 fallback, or the reference on several rows of the plan)
  conflicting                    plan actual amount > 0 that differs, or a category / review-type conflict; both values kept
  unmatched                      plan activity with no contract carrying its reference (contract fields NULL)
Planned (plan), actual (plan) and recorded (DS00005) dates are separate fields.
"""
from __future__ import annotations

import collections
import hashlib
from datetime import date

from .references import category_family, normalize_ref, package_core, review_code, squash

STATUSES = ("amount_verified", "exact_actual_date_supported", "candidate", "conflicting", "unmatched")


def method_codes(contracts: list[dict]) -> set[str]:
    codes = {"RFQ", "RFB", "RFP", "DIR", "QCBS", "CQS", "LCS", "FBS", "QBS", "INDV", "CDS", "SSS"}
    for c in contracts:
        core = package_core(c["borrower_contract_reference"])
        if core:
            codes.add(core.rsplit("-", 1)[1])
    return codes


def index_contracts(contracts: list[dict], codes: set[str]) -> dict:
    idx: dict = collections.defaultdict(list)
    for c in contracts:
        n = normalize_ref(c["borrower_contract_reference"])
        core = package_core(c["borrower_contract_reference"], codes)
        c = dict(c, _nref=n, _core=core, _amount=float(c["contract_amount_usd"]) if c["contract_amount_usd"] not in (None, "") else None)
        if core:
            idx[(c["project_id"], "step", core)].append(c)
        elif n and len(n) >= 6:
            idx[(c["project_id"], "legacy", n)].append(c)
    return idx


def _tol(a: float, b: float) -> bool:
    return abs(a - b) <= max(1.0, 0.005 * b)


def link(project_id: str, activity: dict, idx: dict) -> list[dict]:
    core = activity["package_reference_core"]
    key = (project_id, "step", core) if core else (project_id, "legacy", activity["activity_reference_normalized"])
    group = idx.get(key, []) if key[2] else []
    base = {"project_id": project_id, "activity_id": activity["activity_id"], "plan_version_id": activity["plan_version_id"],
            "document_id": activity["document_id"], "pdf_page": activity["pdf_page"], "table_index": activity["table_index"],
            "row_index": activity["row_index"], "extraction_method": activity["extraction_method"],
            "activity_reference_printed": activity["activity_reference_printed"], "package_reference_core": core,
            "plan_process_status": activity["process_status"], "plan_review_type": activity["review_type"],
            "plan_estimated_amount_usd": activity["estimated_amount_usd"], "plan_actual_amount_usd": activity["actual_amount_usd"],
            "date_planned_signing": activity["signed_contract_planned"], "date_plan_actual_signing": activity["signed_contract_actual"]}
    if not group:
        reason = ("no reference in the row" if not key[2] else
                  "signed/completed in plan; no DS00005 contract with this reference" if activity["process_status"] in ("signed", "completed") else
                  f"no contract expected (plan status: {activity['process_status']})")
        rec = base | {"wb_contract_number": None, "borrower_contract_reference": None, "match_class": None, "cardinality": None,
                      "contract_amount_usd": None, "lot_contracts_amount_sum_usd": None, "date_recorded_signing": None,
                      "days_plan_actual_minus_recorded": None, "independent_fields_agreeing": [], "disagreeing_fields": [],
                      "flags": [reason], "evidence_status": "unmatched"}
        rec["link_id"] = _lid(rec)
        return [rec]
    lot_sum = round(sum(c["_amount"] or 0 for c in group), 2)
    out = []
    act_amt = activity["actual_amount_usd"]
    for c in group:
        flags, agree, disagree = [], [], []
        fam_p, fam_c = activity["category_family"], category_family(c["procurement_category"])
        if fam_p and fam_c and fam_p != fam_c:
            disagree.append("category")
        rv_c = review_code(c["review_type"])
        if activity["review_type"] and rv_c and activity["review_type"] != rv_c:
            disagree.append("review_type")
        if act_amt:
            target = c["_amount"] if len(group) == 1 else lot_sum
            if target is not None and _tol(act_amt, target):
                agree.append("actual_amount" if len(group) == 1 else "actual_amount_equals_lot_sum")
            else:
                disagree.append("actual_amount")
        sa, rec_d = activity["signed_contract_actual"], c["contract_signing_date"]
        days = (date.fromisoformat(sa) - date.fromisoformat(rec_d)).days if sa and rec_d else None
        if days == 0:
            agree.append("actual_signing_date_exact")
        if activity["process_status"] == "cancelled":
            flags.append("plan_row_cancelled_in_this_version")
        uncertain = activity["extraction_method"] != "pdf_table" or any(
            f in activity["uncertain_fields"] for f in ("actual_amount_usd", "actual_amount", "signed_actual", "signed_contract_actual"))
        if uncertain:
            flags.append("values_from_text_fallback_or_unparsed")
        if "reference_on_multiple_rows" in activity["value_flags"]:
            flags.append("reference_on_multiple_rows")
        if disagree:
            status = "conflicting"
        elif uncertain or "reference_on_multiple_rows" in flags:
            status = "candidate"
        elif any(a.startswith("actual_amount") for a in agree):
            status = "amount_verified"
        elif days == 0 and not act_amt:
            status = "exact_actual_date_supported"
        else:
            status = "candidate"
        rec = base | {
            "wb_contract_number": c["wb_contract_number"], "borrower_contract_reference": c["borrower_contract_reference"],
            "match_class": ("exact" if (c["borrower_contract_reference"] or "").strip() == (activity["activity_reference_printed"] or "").strip()
                            else "normalized" if c["_nref"] == (core or activity["activity_reference_normalized"]) else "lot_or_sequence_suffix"),
            "cardinality": "one_row_one_contract" if len(group) == 1 else f"one_row_{len(group)}_contracts",
            "contract_category": c["procurement_category"], "contract_review_type": c["review_type"], "contract_method": c["procurement_method"],
            "contract_amount_usd": c["_amount"], "lot_contracts_amount_sum_usd": lot_sum if len(group) > 1 else None,
            "date_recorded_signing": rec_d, "recorded_source_as_of_date": c["source_as_of_date"], "days_plan_actual_minus_recorded": days,
            "independent_fields_agreeing": agree, "disagreeing_fields": disagree, "flags": flags, "evidence_status": status}
        rec["link_id"] = _lid(rec)
        out.append(rec)
    return out


def _lid(rec: dict) -> str:
    return hashlib.sha256(f"{rec['activity_id']}|{rec['wb_contract_number']}".encode()).hexdigest()[:20]


__all__ = ["STATUSES", "method_codes", "index_contracts", "link", "squash"]
