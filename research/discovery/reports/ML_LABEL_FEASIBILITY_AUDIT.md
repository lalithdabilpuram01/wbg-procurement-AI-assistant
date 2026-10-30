# ML Label Feasibility Audit: Contract Amendment and Re-bidding Risk

Run date **2026-10-23**. Scope: the approved 2,000-project PoC cohort (D-26). **Audit only:** no labels were created, no
models trained, and no data downloaded. The cohort, raw data and Bronze inputs are unchanged.

| Item | Location |
|---|---|
| Script | [`scripts/p09_ml_label_feasibility.py`](../scripts/p09_ml_label_feasibility.py) (offline, deterministic: re-running gives identical output) |
| Measured counts | [`profiling/ml_label_feasibility.json`](../profiling/ml_label_feasibility.json) |
| Coverage metrics, with definitions | [`ML_LABEL_COVERAGE_SUMMARY.csv`](ML_LABEL_COVERAGE_SUMMARY.csv) |
| Every candidate event, with its supporting passage | [`profiling/ml_label_evidence_candidates.csv`](../profiling/ml_label_evidence_candidates.csv) (476 rows) |

**How evidence is classified**
- **Candidate:** a pattern match with a quoted passage. It is not a label until a person verifies it.
- **Confirmed:** human-verified and linked to a specific contract or activity. **The confirmed count is 0 for both use
  cases, because no human verification has been done.**
- **Unknown:** everything else.
- **Never negative by default:** absence of evidence is never counted as a negative.

Where this report gives a share of candidates that "look genuine", it is the auditor's own reading of the passages, an
LLM's reading, and is **not** verification.

## 1. Executive summary

| | Contract amendment risk | Re-bidding risk |
|---|---|---|
| Structured field recording the event | **None** in any source | None directly. Indirect: plan Process Status, notice status and text, reference suffixes |
| Explicit evidence found (candidates) | 61 events: 56 contract records and 5 award notices whose text names an amendment, avenant, aditivo, addendum, extension or variation order. 33 projects | 221 tender notices (DS00979, 98 projects) and 100 tender notices with text (API, 37 projects) with explicit re-bid wording. 89 contracts with a re-bid reference. 269 cancelled plan activities (25 projects) |
| Linked to the unit we would predict for | 13 of 61 have an earlier same-supplier contract (8 with exactly one) | 123 DS00979 re-bid notices have an earlier similar tender (similarity ≥ 90); 2 API re-bid notices match an earlier tender by reference; 38 cancelled plan activities have a successor |
| Confirmed positives / confirmed negatives | **0 / 0** | **0 / 0** |
| Candidate negatives | **None defensible:** no public source records that a contract was *not* amended | 1,855 reference groups (184 projects) with one tender notice, one award notice and no cancellation or re-bid signal. Still candidates |
| Verdict with current data | **Not feasible.** Positives are rare and inconsistently recorded; negatives can't be established from public data | **Not yet feasible; conditionally feasible** after the targeted acquisition and human verification in §11. Evidence exists but is small, partly unlinked and unverified |

## 2. Existing structured data inventory (cohort rows)

The audit checked, in each source, every field relevant to the evidence categories in the brief. Populated percentages
are measured on cohort rows. "None" means the column was checked for and does not exist.

| Evidence category | DS00005 contracts (2,000 projects; 186,907 rows; 178,921 contracts) | DS00979 notices (2,000 projects; 243,533) | procnotices API, full text (184 projects; 20,305) | STEP procurement plans, parsed (25 projects; 843 activities) |
|---|---|---|---|---|
| Project ID | `Project ID` 100% | `Project ID` 100% | `project_id` 100% | `_project_id` (from WDS) |
| Tender or activity reference | `Borrower Contract Reference Number` 100% | **None** | `bid_reference_no` 99.2% | `Activity Reference No. / Description` 99.9% |
| Contract ID | `WB Contract Number` 100% | None | Award text `Bid/Contract Reference No` | None (matches DS00005 references for about 33% of activities, Phase 1) |
| Award or signing date | `Contract Signing Date` 100% | `Publication Date` (notice) | Award text `award_notification_date` 51.3% | `Signed Contract \| Actual` 45.3% |
| Contract value | `Supplier Contract Amount (USD)` 100% | None | Award text `signed_price_amount` 85.5% | `Estimated Amount` 89.3%, `Actual Amount` 99.9% |
| Procurement status | None | None | `notice_status`: Published / Revised / Draft / Cancelled | `Process Status` 100% |
| Tender publication date | None | `Publication Date`, `Deadline Date` 100% | `noticedate` 100%; `submission_deadline_date` 18.3% | `Specific Procurement Notice / Invitation \| Actual` 22.4% |
| Cancellation information | None | None | Status `Cancelled`: 2 tender notices. Cancellation wording: 5 | Process Status `cancelled`: 269 (31.9%) |
| Revised procurement notices | None | None | Status `Revised`: 43 tender, 28 award. Award `notice_version_no` > 0: 2,564 | Plan versions exist in WDS; 1 per project parsed |
| Contract amendment / extension / value revision | **None** (no amendment, original-contract or revised-amount field) | None | Award-notice text only (5 explicit) | Columns `Contract Amendments` and `Amendments to Bidding Documents` appear in headers with **0 values** |
| Re-advertisement or repeated bidding | Reference suffixes (89 explicit markers; 140 ambiguous `-R`/`R2`) | `Bid Description` wording (654 notices) | Text, description, reference markers (100 tender notices); repeated references 8 | Cancelled activity plus successor: 38 |

Other sources are project-level only and carry no contract or tender records:
- v3 projects, including the 184 with all fields: ISR ratings, restructuring and milestones.
- DS00053 IEG ratings.
- DS01004 legacy contracts: prior review FY2001–16, 6,448 cohort contracts, no amendment fields.
- The country reference data under `discovery/country_indicators/`: country-year values only.

## 3. Unstructured document coverage

**The figures in the brief, checked against the repository**

| Figure in the brief | Verified |
|---|---|
| "≈ 23,500 document metadata records" | **23,537** WDS v3 records on disk: 22,337 for the Phase 1 sample of 276 projects, plus 1,200 in a by-type sample. Another 19,550 v2 records exist |
| "42 PDFs for 29 projects" | **42 PDFs for 29 cohort projects**: 30 procurement plans and 3 each of PAD, ISR, ICR and ICR Review. The repository holds 63 PDFs in total (51 projects); the other 21 are for non-cohort projects. *Correction:* the cohort methodology note listed 29 procurement-plan PDFs; the true count is 30, because one project has two |

**Cohort coverage**
- **Complete document lists:** 184 projects, 14,046 documents.
- **Partial lists:** 174 projects appear only as co-tagged projects (222 records).
- **No document metadata:** 1,642 projects.
- **Downloaded text:** 27 files for 4 cohort projects (24 of them ISRs for P163714).

**Documents relevant to the two use cases** (counts for the 184 projects with complete lists)

| Document type | Documents (projects) | Relevance |
|---|---|---|
| Procurement Plan | 6,896 (184), about 37 versions per project | **Highest for re-bidding.** Each version has activity-level Process Status; diffing versions shows cancellations and successors with dates. The amendment columns exist but were empty in all 25 parsed plans |
| Implementation Status and Results Report | 1,981 (182) | Narrative; occasionally mentions contract amendments (e.g. a non-cohort ISR: "reporting following amendment of contract for additional works"). Project-level; rarely names a contract |
| Aide Memoire | 108 (59) | Mission notes; often discuss package status. Contract references are not yet tested |
| Implementation Completion and Results Report | 78 (77) | Procurement narrative. Cohort example, P145502: *"The initial ITP was also cancelled and readvertised with a reduced scope of work."* |
| ICR Review | 58 (58) | Evaluative; procurement problems at project level |
| Project Paper (restructuring) | 418 (158) | Project-level changes; occasionally contract extensions |
| PAD | 155 (142) | Rules only, e.g. "Re-bidding shall not be carried out … without the prior concurrence of the Bank". **No outcomes; a false-positive source** |

**Keyword checks on local text**
- **Cohort, genuine evidence:** the ICR passage for P145502 above.
- **Non-cohort example:** the ISR amendment sentence for P158231 above.
- **False positives:**
  - PAD boilerplate on re-bidding
  - "relance commerciale" (commercial recovery)
  - "Desierto de Atacama"
  - "re-launch existing Doppler Radars"

Document text therefore needs passage-level verification, and it gives project-level narrative rather than
contract-level events.

## 4. Contract amendment evidence

1. **Structured fields:** none, in any source. DS00005 has one row per contract and supplier, with no amendment,
   revision, extension or original-contract fields. DS00005 is a single snapshot, so it can't show amount revisions
   over time (D-20).
2. **Contract records that are themselves amendments.** 56 cohort contracts (33 projects) have descriptions that
   explicitly name an amendment. Examples:
   - "Addendum to Contract Agreement – LK-EGP SECRETARIAT -429347-CS-INDV" (11 of these)
   - "AVENANT N°4 AU CONTRAT N°06/MEH/CGP/PAGOSE/2015"
   - "Variation Order No.1 to PREPTONGA/WORKS 18"
   - "Termo aditivo …"
   - "Extension of Contract for the … Data Specialist"

   On reading:

   | Reading | Records |
   |---|---|
   | Look genuine | 49 |
   | Ambiguous | 4 |
   | Not amendments (e.g. a new consultancy to prepare an addendum to a resettlement plan) | 3 |

   These records show that STEP sometimes registers an amendment as a new contract. The broad first-pass pattern also
   caught legislative reviews ("Review and Amendment of … Act"); the narrow pattern excludes them.
3. **Linking to the original contract is the main obstacle.** Only **13 of 56** have an earlier contract in the same
   project sharing a STEP Supplier ID, and **8** have exactly one. Often the original predates FY2020 (e.g. "…/2015")
   and so isn't in DS00005; or it is a pre-FY2017 post-review contract, which is not public at all.
4. **Award notices** (184 projects): **5** of 16,189 explicitly describe an amendment, e.g. "Avenant n°1",
   "Extension of the contract". **Not amendments:**
   - **2,564 award notices with `Notice Version No` > 0:** these are revisions of the *notice*.
   - **167 tender notices with addenda, corrigenda or deadline extensions:** these are changes to *bidding
     documents*.

   Both are reported separately.
5. **Procurement plans:** the `Contract Amendments` step appears only as a header, with 0 values in all 25 cohort
   plans.

**Assessment**
- **How much is captured:** 61 candidate amendment events out of 178,921 contracts (0.03%).
- **Why that is far too few:** public sources don't record amendments systematically. This rate is not an estimate of
  the true amendment rate.
- **Why no negatives exist:** no source lists all amendments of a contract over a defined period, so none can be
  established.

## 5. Re-bidding evidence

| Source | Evidence | Count | Note |
|---|---|---|---|
| DS00979 (all 2,000 projects) | Tender notices with explicit re-bid wording in `Bid Description` ("REBIDDING …", "(Re-tender)", "Relance …") | **221** (98 projects) of 55,223 tender notices | Also 433 award notices with such wording, i.e. contracts awarded after a re-bid |
| | … with an earlier unmarked tender in the same project, description similarity ≥ 90 | **123** | Candidate original tender. DS00979 has no reference number |
| procnotices API (184 projects) | Tender notices with explicit re-bid wording or reference marker ("Specific Procurement Notice (Re-bid)", "RE-ADVERTISED", "…/Re-Tender", `NHMP2/1/A.3.A./REBID`) | **100** (37 projects) of 4,002 | On reading a sample of 25: 21 look genuine, 4 false (project name "Sahel RELANCE", procedural boilerplate "soit relancer…", job descriptions) |
| | … whose reference without the re-bid suffix matches an earlier unmarked tender | **2** | Re-bids usually get new or changed references |
| | Status `Cancelled` or explicit cancellation wording ("The bids received … has been cancelled, and the Ministry now re-invites …") | 5 | A cancellation alone is not proof of re-bidding |
| | Same project and normalised reference, ≥ 2 tender notices ≥ 30 days apart | 8 of 3,983 reference groups | Repetition is not proof (revisions, lots, re-publication) |
| DS00005 | Contracts whose reference carries a re-bid marker (`REBID`, `RETENDER`, `RELANCE`, `2NDCALL`) | **89** (25 projects) | Shows a re-bid happened; the original tender must still be found. 140 more with an ambiguous `-R` suffix are not counted |
| STEP plans (25 projects) | Activities with Process Status `cancelled` | **269** of 843 (31.9%) | Cancelled activities include descoped or merged activities, not only failed tenders |
| | … with a non-cancelled successor (same reference: 3; description ≥ 90: 35) | **38** | Phase 1 validation decision D-22 is pending |

**What counts as supporting evidence of re-bidding**
- **(a) An explicit official statement** that a tender failed, was cancelled or was declared unsuccessful *and* was
  re-launched. This comes from notice text, a re-bid reference or plan status.
- **(b) A link between the re-bid and the original tender's own record:** a notice ID, reference or plan activity.

Only (a) plus (b) together gives a usable positive for a model that predicts at initial tender publication. Today that
holds for at most 123 + 2 + 38 candidate pairs, all unverified and overlapping across sources.

## 6. Label counts (measured)

Sources overlap, so counts must not be summed.

| Use case | Unit | Population (cohort) | Confirmed positive | Candidate positive (event observed) | Candidate positive linked to the prediction unit | Confirmed negative | Candidate negative | Unknown |
|---|---|---|---|---|---|---|---|---|
| Contract amendment | contract (predicted at award) | 178,921 contracts | **0** | 61 events | 13 (8 unique) | **0** | **0**: no defensible rule | 178,921 (all) |
| Re-bidding (all projects, DS00979) | tender notice (predicted at publication) | 55,223 tender notices (not de-duplicated to activities) | **0** | 221 | 123 | **0** | not derivable (no reference numbers) | ≈ 55,000 |
| Re-bidding (184 API projects) | tender reference group | 3,983 groups (4,002 notices) | **0** | 100 | 2 | **0** | 1,855 (one tender, one award, no signal) | 2,028, of which 1,794 have no matching award notice |
| Re-bidding (25 plan projects) | plan activity | 843 activities | **0** | 269 cancelled | 38 with successor | **0** | not computed (389 signed or completed; predecessors not screened) | — |

**Observation windows** (snapshot 2026-10-20)
- **Contracts:** 137,173 contracts (76.7%) have at least 24 months of observation after signing; later contracts are
  right-censored for amendments.
- **Tender notices:** 48,487 (87.8%) have at least 12 months.

## 7. Identifier matching challenges

1. **DS00979 has no reference number.** For 1,816 of 2,000 projects, tender-to-award and tender-to-re-bid links can
   only be made by description similarity, which is probabilistic.
2. **Re-bids change their reference** (`…/Re-Bid`, `-R2`, a new STEP ID) or keep it with an in-place revision. Only 2
   of 100 API re-bid notices matched their original by reference stem.
3. **Amendments become new contract numbers.** The original is often outside DS00005 (before FY2020), or has a
   different Supplier ID.
4. **Matching across sources is incomplete.** Plan activity references match DS00005 references for about 33% of
   activities. Award notices match contracts for about 70% (Phase 1, 276 projects).
5. **Notices are not versioned.** The API returns the current text of a notice; a revised notice overwrites the
   original. The version history isn't recoverable.
6. **Documents are project-level.** ISR, ICR and Aide Memoire narratives rarely cite contract references. WDS
   `projectid` can be multi-valued (151 multi-project documents in the sample metadata), and there are 1,783
   duplicate titles.
7. **Languages:** evidence wording is in English, French ("avenant", "relance", "infructueux"), Spanish ("desierto",
   "nueva convocatoria") and Portuguese ("termo aditivo").

## 8. Proposed LLM evidence-extraction workflow (not implemented)

1. **Candidate retrieval (deterministic).** The multilingual patterns in `p09` run over notice text, plan activity
   rows (all versions) and document text, producing source ID, URL, passage and offsets. Patterns are tuned for recall.
2. **LLM extraction against a strict schema**, one call per candidate passage. Fields:
   - `project_id`
   - `contract_or_activity_reference`
   - `event_type`: `contract_amendment`, `contract_extension`, `value_revision`, `tender_cancelled`,
     `tender_failed`, `rebid_launched`, or `not_an_event`
   - `event_date`
   - `source_id`, `source_url`
   - `evidence_quote`: an exact substring
   - `extraction_method` (pattern ID plus model and prompt version)
   - `model_rationale`

   The model never assigns a label.
3. **Deterministic validation:**
   - the quote must appear verbatim in the source (offsets checked)
   - references must exist in DS00005, notices or the plan
   - dates must parse and fall within the project's life
   - anything that fails is rejected
4. **Linking** to the prediction unit, using deterministic keys first (project plus normalised reference, Supplier ID,
   plan activity), then similarity only as a candidate.
5. **Human verification:**
   - every candidate positive in the pilot
   - a stratified sample thereafter
   - a gold set built with procurement SMEs (D-23) to measure extraction precision and recall
6. **Label states**

   | State | Requirements |
   |---|---|
   | `confirmed_positive` | Verified evidence and a resolved link |
   | `confirmed_negative` | Only under a pre-registered rule with positive evidence of a normal outcome over a full observation window. Re-bidding example: a plan activity with a single invitation, signed, no cancelled predecessor in any plan version, at least 12 months observed, verified. For amendments, no such rule is possible from public data |
   | `candidate` | Unverified evidence, or an unresolved link |
   | `unknown` | Everything else |

   LLM confidence scores are stored but never turned into labels.

## 9. Additional document acquisition (no downloads made)

| Source (official, public) | Project link | Volume for the cohort | Value | Challenges |
|---|---|---|---|---|
| procnotices API `search.worldbank.org/api/v2/procnotices?project_id=` | `project_id` | 223,237 notices for the 1,816 missing projects (exact, from DS00979); about 2,050 requests at 1,000 rows | **High:** text, references and status for all re-bid and cancellation markers; award notices for amendment notices; contract linking | Current versions only; offset cap of 100k (handled by per-project queries); contact data in text (D-14) |
| WDS v3 `search.worldbank.org/api/v3/wds?projectid=&docty_exact=Procurement Plan` | `projectid` (multi-valued) | About 82,600 plan versions (6,896 known for 184 projects; estimated for the rest); about 0.31 MB per PDF | **Highest for re-bidding:** version diffs show cancellation and successor with dates | Too large in full. Table parsing (p05 parser, header variants); multilingual statuses |
| WDS v3 Aide Memoire | `projectid` | About 1,100 (108 for 184 projects) | Medium: package-status narratives; contract references to test | Narrative; partial disclosure |
| WDS v3 ICR / ICR Review | `projectid` | About 930 ICRs (78 known; the rest estimated; closed projects only) | Medium: procurement narratives ("cancelled and readvertised") | Project-level; links to contracts are rare |
| WDS v3 ISR | `projectid` | About 21,900 | Low to medium: occasional amendment mentions | Volume; project-level; component ratings absent |
| WDS v3 Project Paper (restructuring) | `projectid` | About 4,500 | Low: project-level changes | — |
| **Not public** | — | — | STEP contract-amendment records, no-objection records, bid evaluation reports | Needed for complete amendment labels and any confirmed negatives |

**Rate limits**
- None are documented for the search APIs.
- In Phase 1, about 1,400 search requests at 0.5-second spacing saw no throttling. The Data Catalog API did return
  HTTP 429 twice.
- WDS offers a text (`txturl`) version for 99.5% of documents, so text can usually be fetched instead of the PDF.

**Recommended targeted strategy** (within the 2,000 projects only)
1. **Notices:** fetch the notice text for all 1,816 remaining cohort projects (metadata-scale, about 170 MB). This is
   the only way to get references and markers cohort-wide.
2. **Procurement plans, pilot:** every version for a stratified pilot of about 100 projects (about 4,000 PDFs, about
   1.2 GB). Prioritise projects that already have DS00979 re-bid markers (98) or cancellations. Measure how often a
   version diff gives a dated cancellation-then-successor sequence.
3. **Aide Memoires and ICRs** for the same pilot projects, as text, to test contract-level evidence density before
   scaling up.

## 10. ML feasibility assessment

| Criterion | Contract amendment risk (predict at award) | Re-bidding risk (predict at initial tender publication) |
|---|---|---|
| Labelled examples possible now | 0 confirmed. At most about 49 positive events on reading, of which 13 are linkable | 0 confirmed. Up to about 160 linked candidate pairs (123 + 2 + 38, overlapping) |
| Positive availability | Very low; publication is unsystematic | Low to moderate. Plans suggest failure is common (31.9% of activities cancelled), but explicit links are rare |
| Negative availability | **None** from public data | Possible only via a verified rule (plan versions); 1,855 candidate negatives in 184 projects |
| Identifier quality | Poor for amendments (new contract numbers; originals outside the window) | Good where references exist (184 projects); poor in DS00979 (no reference) |
| Event timestamps | Signing date of the amendment record; the original's date when linked | Notice dates; plan actual dates (22–45% populated) |
| Observation window | 24 months or more for 76.7% of contracts; amendments may take longer | 12 months or more for 87.8% of tender notices |
| Leakage risks | DS00005 is a current snapshot (amounts may reflect later changes; unverified). Award notices are published a median 127 days after the award event. Project ratings and IEG are after the event | Current notice text may include later revisions ("Revised" status). Plan versions after the tender reveal the outcome. Award-notice bidder counts come after the tender. Country indicators use a two-year lag (D-30) |
| Missing data | Supplier ID 100%; review type 99.9% | Deadline in 18% of API notices; plan dates 22–45% |
| Class imbalance | Unknown (true amendment rate not observable) | Unknown at notice level. 0.4% of tender notices are explicitly marked, but this is a floor, not a rate |
| Credible training set? | **No.** Not without non-public amendment records. Options: descope; reframe as "variation order or extension present in public records" (positive-only, weak); or obtain STEP amendment data | **Not yet.** A credible pilot needs the notices and plan versions in §9, plus verified labels. Re-assess once about 300 verified positives and a defined negative rule exist |

## 11. Recommended next steps (for review; nothing started)

1. **Human verification of the 476 candidates** in `ml_label_evidence_candidates.csv`: precision per evidence type
   and resolution of links. Start with the 61 amendment rows and the 100 API re-bid rows.
2. **Decision on amendment risk:** descope or reframe it, or seek non-public STEP amendment data. Public data can't
   support it.
3. **Approve the targeted acquisition for re-bidding** (§9):
   1. notices for the 1,816 projects;
   2. all plan versions for a stratified pilot of about 100 cohort projects;
   3. Aide Memoires and ICRs for the same pilot, as text.
4. **Define the re-bidding label rule and the negative rule** before extraction. This extends D-22: validate about 200
   cancelled → successor pairs. Build the gold set (D-23).
5. **LLM extraction pilot** (§8) on the pilot projects only, with verbatim-quote validation and human review. Report
   precision and recall against the gold set before any scale-up.
