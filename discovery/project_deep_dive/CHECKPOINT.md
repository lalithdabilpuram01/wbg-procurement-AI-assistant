# Checkpoint: Two-Project Deep Dive (paused 2026-10-23)

**Question being answered:** does the full project data behind projects.worldbank.org contain enough evidence for the
contract-amendment and re-bidding ML labels? The pass covers two cohort projects; follow-up to
`reports/ML_LABEL_FEASIBILITY_AUDIT.md`.

## Done
- **The portal can't be fetched directly.** It renders its pages with JavaScript, so the same content was pulled from
  the public APIs behind it (`d01_fetch_two_projects.py`).
- **306 files (174 MB) downloaded** into `raw/`, with `raw/MANIFEST.json` (URL, SHA-256, timestamp) and
  `evidence/access_log.jsonl`:

  | Project | Notices | Document records | Text files | Procurement-plan PDF versions |
  |---|---|---|---|---|
  | **P178432** (Sri Lanka PFM, active) | 68 | 56 | 40 | 32 |
  | **P163870** (Madagascar LEAD, closed) | 216 | 164 | 126 | 102 |

## Findings so far
1. **P178432 amendments**
   - **Evidence:** 11 award notices "Addendum to Contract Agreement – \<original ref\>" each **quote the original
     contract reference**.
   - **Linked:** 10 of the 11 originals are found in DS00005 (signed Aug–Oct 2024). The addenda were signed exactly one
     year later: one-year extensions of individual consultant contracts, at roughly the same amount.
   - **Phase 1 linking missed these:** the original and the amendment carry different Supplier ID formats (hex versus
     numeric), so the supplier-ID link fails while the reference quoted in the text works.
   - **ISR 2026-07-28:** "terminate the existing e-GP contract and rebid for a new e-GP system". This is narrative only,
     with no reference.
2. **P163870 re-bidding**
   - **Evidence:** 4 "RELANCE" (re-launch) notices: D0622C and D0522C (IFB, 2022-09-02), 496002 (REOI 2025), and 130003
     (REOI 2020).
   - **First round missing:** no first-round notice exists under the same reference.
   - **Supervision notes (Aide Memoires):** an extension amendment ("avenant de prolongation") to the fund-manager
     contract was signed; a solar-kit supply contract was annulled for suspected fraud; a recruitment was annulled. All
     without reference numbers.
   - **Amendment record:** "AVENANT N°4 AU CONTRAT N°06/…/2015"; the original predates DS00005.

## In progress at pause
- **`d02_parse_plan_versions.py`** was parsing all 134 plan PDFs in the background into
  `derived/plan_activities_by_version.csv`. This is CPU-heavy: more than 12 minutes in, P163870's plans are large.

## Next steps when resumed
1. **Check that the plan output exists.** If it doesn't, re-run `python3 d02_parse_plan_versions.py`. It is offline,
   and `derived/` is output only.
2. **Trace activities across plan versions:**
   - follow D0622C, D0522C and the other "Relance" references, looking for a cancelled first round and its successor
     with dates;
   - count status transitions to `cancelled`;
   - check whether any `Contract Amendments` step values exist.
3. **Write `reports/TWO_PROJECT_DEEP_DIVE.md`:** what the full project data adds compared with the bulk sources, and
   whether it changes the feasibility verdicts. Interim view:
   - **Amendments:** reference-quoting award notices give verifiable, linked positives, but only where STEP publishes
     them. There are still no negatives.
   - **Re-bidding:** plan-version history is the deciding test.
4. **Raw files:** the deep-dive raw files are new and separate from Phase 1, the cohort and Bronze, and none of those
   was modified.
