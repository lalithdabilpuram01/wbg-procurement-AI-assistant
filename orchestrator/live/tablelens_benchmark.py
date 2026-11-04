"""TableLens benchmark on the 10 pilot PDFs: 22 hand-checked table questions (+2 that no table should answer).

    find                       free, read-only: does the right table come back (hit@1, hit@2)? Negatives: is nothing found?
    answer --confirm-live-call Claude writes the SQL (own ledger: 50 attempts / $0.40). Switch ON: is the expected value in
                               the rows TableLens returns? Switch OFF: is it anywhere in the top-10 passages the normal
                               search returns (an upper bound for the old path: the value still has to be read correctly)?

    cd orchestrator
    env -u VIRTUAL_ENV PYTHONPATH=..:. uv run --locked python -B -m live.tablelens_benchmark find
    WBG_ALLOW_LIVE_LLM_CALLS=true env -u VIRTUAL_ENV PYTHONPATH=..:. \\
        uv run --locked python -B -m live.tablelens_benchmark answer --confirm-live-call
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from decimal import Decimal
from typing import Any

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

from live.c4_end_to_end import RESULTS  # noqa: E402
from rag.retrieval.versions import build_retriever, build_table_lens  # noqa: E402
from rag.tablelens.query import labelled_rows  # noqa: E402
from wbg_orchestrator.claude import PortkeyJsonClient  # noqa: E402
from wbg_orchestrator.live_llm import SpendLedger  # noqa: E402
from wbg_orchestrator.observability import OrchestratorTracer  # noqa: E402
from wbg_orchestrator.runtime import RuntimeConfig, _claude_client  # noqa: E402
from wbg_orchestrator.table_sql import TableSqlWriter  # noqa: E402

ET, LK, UIIDP, SN = "Ethiopia Expressway Development Support Project (P148850)", "Sri Lanka project P132833", \
    "Ethiopia urban program P163452", "Senegal project P175325"
QUESTIONS = [  # (question, accepted table ids, expected values: all must appear)
    (f"How much IDA financing goes to institutional development and the regulatory framework in the {ET}?", ["24440102:t02201"], ["18"]),
    (f"What is the cost of the construction of the high capacity highway component in the {ET}?", ["24440102:t02201", "24440102:t06501"], ["340"]),
    (f"What EIRR and NPV were estimated for the new highway option (option 2) in the {ET}?", ["24440102:t02501", "24440102:t11001"], ["28.2", "1233"]),
    (f"What is the estimated cost of monitoring and supervision, sub-component 1(b), in the {ET}?", ["24440102:t06501"], ["17"]),
    (f"How much does China EXIM finance for the Arsi Negele-Hawassa road section in the {ET}?", ["24440102:t10401"], ["315"]),
    (f"What is the total financing of all road sections from Modjo to Hawassa across all financiers in the {ET}?", ["24440102:t10401"], ["1077.1"]),
    (f"How much of the credit is allocated to category 1 goods, works, non-consulting services and consultants in the {ET}?", ["24440102:t08301"], ["358"]),
    (f"What is the prior review threshold for ICB/NCB works in the procurement plan of the {ET}?", ["40025990:t00101"], ["10"]),
    (f"What are the estimated and actual amounts of the Modjo-Hawassa works contract in the procurement plan of the {ET}?", ["40025990:t00401"], ["290000000", "140348308.59"]),
    (f"How many goods packages are signed in the procurement plan of the {ET}?", ["40025990:t00402"], ["4"]),
    (f"What is the cost of the Piloting the Implementation of Road Asset Management component in the {LK}?", ["26256705:t02301"], ["102.1"]),
    (f"How much is budgeted for capacity building and training in the {LK}?", ["26256705:t04601"], ["2"]),
    (f"How much is allocated to land acquisition and resettlement in the {LK}?", ["26256705:t04601", "26256705:t05302"], ["2.1"]),
    (f"Which procurement method applies to works contracts above US$10,000,000 in the {LK}?", ["26256705:t05901", "33761744:t00101"], ["ICB"]),
    (f"What is the length of road A3-8 in the economic analysis of the {LK}?", ["26256705:t06801"], ["26"]),
    (f"What is the average annual OPRC cost for alternative 4 in the {LK}?", ["26256705:t06902"], ["9.5"]),
    (f"What was the actual cost at closing of the provincial roads improvements component in the {LK}?", ["34345370:t05001"], ["61.74"]),
    (f"How much of the IDA grant was actually disbursed in the {UIIDP}?", ["34455066:t04202"], ["270281509"]),
    (f"How much co-financing did the French Agency for Development provide in the {UIIDP}?", ["34455066:t04202"], ["10800000"]),
    (f"What was the average growth rate of own-source revenue collected by cities in the {UIIDP}?", ["34455066:t05101"], ["24.89"]),
    (f"Which works activities were cancelled in the procurement plan of the {SN}, and what were their estimated amounts?", ["40009757:t00301"], ["6076795"]),
    (f"What is the total estimated amount of all works activities in the procurement plan of the {SN}?", ["40009757:t00301"], ["9251967"]),
]
NEGATIVES = [  # no table answers these (the second also probes the staff-names front-matter table)
    f"What lessons were learned about contractor mobilisation in the {LK}?",
    f"Who are the procurement specialists on the team of the {ET}?",
]
CONFIG = RuntimeConfig(ledger_path=RESULTS / "tablelens_llm_ledger_2.json", max_attempts=50, max_usd=Decimal("0.40"))
NUMBER = re.compile(r"-?\d[\d,]*(?:\.\d+)?")


def numbers(text: str) -> set[float]:
    return {round(float(n.replace(",", "")), 4) for n in NUMBER.findall(text)}


def contains(text: str, value: str) -> bool:
    if NUMBER.fullmatch(value):
        return round(float(value), 4) in numbers(text)
    return value.lower() in text.lower()


def find(retriever, lens) -> dict[str, Any]:
    """Free, read-only: counts questions whose accepted table comes first (hit@1) or among those found (hit@2);
    also lists what the negative questions find."""
    rows, hit1, hit2 = [], 0, 0
    for question, accepted, _ in QUESTIONS:
        found = lens.find_tables(question, retriever.guard(question)["scope_projects"])
        ids = [t.table_id for t, _ in found]
        hit1 += bool(ids[:1] and ids[0] in accepted)
        hit2 += any(i in accepted for i in ids)
        rows.append({"question": question, "expected": accepted, "found": [(t.table_id, round(s, 3)) for t, s in found]})
    negatives = [{"question": q, "found": [(t.table_id, round(s, 3)) for t, s in lens.find_tables(q, retriever.guard(q)["scope_projects"])]}
                 for q in NEGATIVES]
    return {"questions": len(QUESTIONS), "hit@1": hit1, "hit@2": hit2, "rows": rows, "negatives": negatives}


def answer(retriever, lens, confirmed: bool) -> dict[str, Any]:
    """Paid: Claude writes the SQL; counts expected values found in the TableLens rows (switch ON) and in the top-10
    search passages (switch OFF)."""
    writer = TableSqlWriter(PortkeyJsonClient(_claude_client(CONFIG, confirmed, OrchestratorTracer.disabled()))).writer
    rows, on, off = [], 0, 0
    # The negatives run too, with no expected values, so they are recorded but can never count as hits.
    for n, (question, _, values) in enumerate(QUESTIONS + [(q, [], []) for q in NEGATIVES], start=1):
        answer_ = lens.ask(question, writer(f"tablelens-bench-{n:02d}"), retriever.guard(question)["scope_projects"])
        results, usage = answer_.results, answer_.usage
        table_text = "\n".join(line for r in results for line in labelled_rows(r))
        search = retriever.search(question, method="hybrid", k=10, guarded=True)
        passages = "\n".join(retriever.c.by_id[r["unit_id"]].text for r in search["results"])
        hit_on = bool(values) and all(contains(table_text, v) for v in values)
        hit_off = bool(values) and all(contains(passages, v) for v in values)
        on, off = on + hit_on, off + hit_off
        rows.append({"question": question, "expected": values, "on": hit_on, "off_in_top10": hit_off, "usage": usage,
                     "notes": answer_.notes,
                     "tables": [{"table": r.table.table_id, "sql": r.sql, "rows": labelled_rows(r)[:8], "flagged": r.flagged,
                                 "broadened": r.broadened}
                                for r in results]})
    return {"questions": len(QUESTIONS), "switch_on_value_returned": on, "switch_off_value_in_top10": off, "rows": rows}


def main(argv: list[str]) -> int:
    """Command line: runs `find` or `answer`, saves it to live_results/tablelens_<step>.json, prints a summary."""
    parser = argparse.ArgumentParser(description="TableLens benchmark (10 pilot PDFs)")
    parser.add_argument("step", choices=["find", "answer"])
    parser.add_argument("--confirm-live-call", action="store_true")
    args = parser.parse_args(argv)
    retriever, lens = build_retriever("pilot_v3"), build_table_lens("pilot_v3_tablelens")
    RESULTS.mkdir(exist_ok=True)
    result = find(retriever, lens) if args.step == "find" else answer(retriever, lens, args.confirm_live_call)
    if args.step == "answer":
        result["ledger"] = SpendLedger(CONFIG.ledger_path).summary()
    (RESULTS / f"tablelens_{args.step}.json").write_text(json.dumps(result, indent=1, default=str))
    print(json.dumps({k: v for k, v in result.items() if k not in ("rows",)}, indent=1, default=str)[:4000])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
