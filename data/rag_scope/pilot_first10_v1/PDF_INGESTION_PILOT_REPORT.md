# Module 1: first PDF ingestion pilot (10 documents)

2026-10-25.

**What was done:**
- Downloaded only the 10 authorised PDFs in `FIRST_BATCH_10_v3.csv`, through the logged downloader.
- Inspected them with locally installed PyMuPDF and pdfplumber.
- Queried Silver `contract_awards` read-only.

**What was not done:** no other downloads, no Databricks or manifest changes, no tables, embeddings or pipeline.

**Scripts** (in `rag/module1_discovery/`):
- `m19_download_first_batch.py`: download;
- `m20_inspect_pilot_pdfs.py`: structure inspection;
- `m21_pdf_verify_plan_links.py`: plan-table validation against contracts.

## 1. Download results

| | |
|---|---|
| Authorised | 10 |
| Downloaded | **10** (0 failed). All start with `%PDF`. |
| Total size / pages | 7,471,241 bytes / 373 pages |
| Location | `procurement-intelligence/rag/corpus_v3/pdf/<country>/<project_id>/<TYPE>_<WDS date>_<document_id>.pdf` |
| Manifest | `procurement-intelligence/rag/corpus_v3/CORPUS_MANIFEST_FIRST10.csv`: project, country, document ID, title, type, role, WDS document and disclosure dates, language, version type, report number, GUID, source URL, URL after redirect (from the earlier HEAD), content type, local path, SHA-256, size, page count, download timestamp |

| # | Project | Type | WDS ID | WDS date | Pages | Size | SHA-256 (first 16) | Local path |
|---|---|---|---|---|---|---|---|---|
| 1 | P132833 (LK) | PAD | 26256705 | 2016-04-18 | 78 | 1,890,352 | cae07eff92cafa04 | `rag/corpus_v3/pdf/LK/P132833/PAD_2016-04-18_26256705.pdf` |
| 2 | P132833 (LK) | ICR | 34345370 | 2024-06-19 | 57 | 782,874 | fef821c7135c3e82 | `rag/corpus_v3/pdf/LK/P132833/ICR_2024-06-19_34345370.pdf` |
| 3 | P132833 (LK) | ICRR | 34407877 | 2024-10-18 | 25 | 176,848 | f1daf66f5a2bc4f4 | `rag/corpus_v3/pdf/LK/P132833/ICRR_2024-10-18_34407877.pdf` |
| 4 | P132833 (LK) | PP | 33761744 | 2022-03-17 | 8 | 210,952 | ca2103c2206ea6f6 | `rag/corpus_v3/pdf/LK/P132833/PP_2022-03-17_33761744.pdf` |
| 5 | P148850 (ET) | PAD | 24440102 | 2015-04-27 | 120 | 1,356,212 | b1276d43b5547894 | `rag/corpus_v3/pdf/ET/P148850/PAD_2015-04-27_24440102.pdf` |
| 6 | P148850 (ET) | ISR | 40089196 | 2026-02-28 | 8 | 164,080 | c32be7ca2d3d1ce2 | `rag/corpus_v3/pdf/ET/P148850/ISR_2026-02-28_40089196.pdf` |
| 7 | P148850 (ET) | PP | 40025990 | 2025-06-27 | 6 | 207,831 | 1fb4c3baeebe513c | `rag/corpus_v3/pdf/ET/P148850/PP_2025-06-27_40025990.pdf` |
| 8 | P163452 (ET) | ICR | 34455066 | 2025-01-24 | 55 | 2,179,010 | 17b5877172ed60cd | `rag/corpus_v3/pdf/ET/P163452/ICR_2025-01-24_34455066.pdf` |
| 9 | P175325 (SN) | ISR | 33851641 | 2022-06-24 | 8 | 161,128 | c69d202954f96cef | `rag/corpus_v3/pdf/SN/P175325/ISR_2022-06-24_33851641.pdf` |
| 10 | P175325 (SN) | PP | 40009757 | 2025-04-29 | 8 | 341,954 | aea08b27c1009ebf | `rag/corpus_v3/pdf/SN/P175325/PP_2025-04-29_40009757.pdf` |

The already-local P132833 latest plan (`discovery/samples/documents/pp_files/P132833_PP_34172790.pdf`, part of the approved manifest) was also used in the link validation. It was not re-downloaded.

## 2. Metadata completeness

- **Fields:** in the 10-row corpus manifest, every field is filled except one report number: plan 40025990 has none in WDS, so it is stored as NULL.
- **Version types:** are WDS cover labels ("Buff cover", "Gray Cover", "Final"), not version numbers.
- **Project identity:** the project ID is printed inside all 10 PDFs.
  - Plan 40025990 prints it as "( P148850)" in a free-text title rather than a "Project ID:" field.
  - The Ethiopia PAD also mentions P082998, a related earlier project.
- **Plan version dates differ from WDS dates:**

| Plan | WDS date | Version date inside the PDF (STEP "Revised Plan Date(s)") | Textual part date | Plan stream (executing agency) |
|---|---|---|---|---|
| 33761744 (P132833) | 2022-03-17 | 2022-03-18; original approval 2019-02-19 | January 09, 2018 ("Sample Procurement Plan") | Ministry of Internal and Home Affairs and Provincial Councils and Local Government (MIHAPCLG) |
| 34172790 (P132833) | 2023-10-02 | 2023-10-02; original approval 2018-01-09 | n/a | Road Development Authority |
| 40025990 (P148850) | 2025-06-27 | No STEP header. The textual part says "Revision V1 … April 21, 2017"; the STEP tables on pp. 4–6 hold activities signed up to 2024-11-04. | April 21, 2017 | Ethiopian Roads Authority (ERA) |
| 40009757 (P175325) | 2025-04-29 | 2025-04-29; original approval 2021-05-31 | April 18, 2021 | Project Implementation Unit (PPDC) |

The consequences for the metadata model:
- **(a)** A plan's version date must be read from the PDF, not taken from WDS.
- **(b)** A plan PDF can combine an older textual part with a newer STEP table export.
- **(c)** One project can have **parallel plan streams per executing agency.** P132833's two "versions" are different agencies' plans, not successive revisions.

## 3. PDF structures and parsing challenges

All files are born-digital, with a text layer on 372 of 373 pages. The only low-text page is the last page of the Ethiopia PAD (p.120), which is image-only, probably a map. No OCR is needed for this batch.

| Type | Documents (pages) | Structure observed | Tables (pdfplumber) | Page numbering | Challenges |
|---|---|---|---|---|---|
| **PAD** | 26256705 (78), 24440102 (120) | No PDF outline. Cover "FOR OFFICIAL USE ONLY"; Table of Contents; PAD Data Sheet (pp.6–8); Sections I–VII; Annexes (Results Framework, Detailed Description, Implementation Arrangements incl. **Procurement**, Risk, Implementation Support Plan, maps) | 31 and 59 tables on 25 and 47 pages; largest 35×17 (Data Sheet) and 43×12 (results framework) | Roman front matter, then arabic. Printed page = physical − 13 for 63 of 73 pages (LK) and 105 of 114 pages (ET). | Headings only by font size or numbering. Procurement text is in Annex 3 (LK pp.55, 58–59; ET pp.86–92). 6–10 landscape pages. |
| **ICR** | 34345370 (57), 34455066 (55) | Data Sheet; I Context & Objectives; II Outcome; III Key factors; IV Bank performance, compliance (fiduciary/procurement); V Lessons; Annexes (results framework, costs, team) | 46 and 74 tables; 12 and 18 landscape pages | Printed numbers mostly absent or unreliable. P163452 has a 23-entry PDF outline. | Large results tables span pages; repeated "The World Bank" running header; procurement findings at LK pp.31–33 and ET pp.21, 39–40. |
| **ICR Review** | 34407877 (25) | Numbered sections: 1 Project Data … Relevance, Efficacy, Efficiency, Outcome, Fiduciary Compliance (p.21), Ratings table, Lessons | 5 tables (largest 24×4: ratings) | Only the last page is numbered | Rating fields are label/value pairs; ICR-versus-IEG rating differences need structured extraction |
| **ISR** | 40089196 (8), 33851641 (8) | Fixed template: Key Dates, Objective, Components, Overall Ratings, Implementation Status and Key Decisions, Risks (SORT), Results (PDO and Intermediate indicators) | 5 and 16 tables (indicator tables 13×9) | None printed | Repeated "Public Disclosure Authorized" watermark text appears inside extracted text and must be stripped. The two templates differ (2022 versus 2026 layout). |
| **Procurement plan** | 33761744 (8), 40025990 (6), 40009757 (8), 34172790 (6) | Textual part (thresholds, methods) on pp.1–2, then STEP tables per section (WORKS, GOODS, NON CONSULTING SERVICES, CONSULTING FIRMS, INDIVIDUAL CONSULTANTS) on landscape pages | 10–13 tables per plan; widest 22–30 columns | STEP table pages restart at "1" (printed = physical − 2) | 3-pt fonts. Cell text wraps with injected spaces ("Quota tions", "Process St atus"). Header labels split across lines. French labels in Senegal ("Demande de prix", "A posteriori", "Annulé"). Legacy references (`MOT/RSDS/SR/02/09`) and STEP references can sit in one table. pdfplumber missed rows (see §5). |

**Procurement-reference formats:**
- *STEP:* `CC-AGENCY-NNNNNN-(CW|GO|CS|NC)-METHOD`, sometimes printed with spaces or wrapped. Some contracts add lot or sequence suffixes (`-1`, `-LTD`, `-DIR`).
- *Legacy:* agency/serial (`MOT/RSDS/SR/02/09`). Seen in the Ethiopia plan only.
- *PAD, ISR, ICR and ICRR contain no contract references* (0 STEP or legacy references in 351 pages). This confirms that narrative documents link to projects, not contracts.

**Sensitive information** (counts and page numbers only; no values were extracted into outputs):

| Indicator | Where |
|---|---|
| Staff names with roles: PAD team composition, ICR/ICRR "Prepared by / Reviewed by", task team leaders | PAD LK pp.3, 6, 11–12, 24, 51, 53; PAD ET pp.4, 7, 12, 22, 30 …; ICR LK pp.2, 9, 33, 47–49; ICR ET pp.2, 9, 41; ICRR p.1 |
| E-mail address | PAD LK p.6; PAD ET p.7 (task-team contact) |
| Phone-like number patterns | 51 matches across 9 documents. Mostly numeric tables (false positives); PAD ET p.7 is likely a contact block. |
| Supplier names | Not in plan tables (STEP plans show no awardees) |

**Recommendation:** drop or mask personal names in team, staff and contact blocks, plus e-mails and phone numbers, before chunking. Keep role titles.

## 4. Sample extracted sections and plan table rows

**Sections** (verbatim, shortened, no personal data):
- **PAD P132833, p.55, Annex 3, "Procurement":** "31. Procurement guideline. All works, goods, consulting services and non-consulting services under the project will be procured in accordance with the World Bank's 'Guidelines: Procurement of Goods, Works, and Non-consulting Services … dated January 2011 (revised July 2014)' …"
- **ICR P132833, p.33:** "… The Bank also supported the PMU in selecting appropriate contract packaging (province-wise vs country wide) based on factors such as contract management and time and cost efficiency. 80. Institutional interventions under Component 2 could have been better defined …"
- **ICR Review P132833, p.21, "Fiduciary Compliance – Procurement":** "… the ICR, para 72, points out that the project's procurement performance was moderately satisfactory because it faced contract management problems and did not regularly update the procurement information in STEP."
- **ISR P175325, p.1, "Overall Ratings":** "Progress towards achievement of PDO – Satisfactory; Overall Implementation Progress – Satisfactory; Overall Risk Rating – Moderate; Implementation Status and Key Decisions: The project was approved on 25 February, 2022 …"

**Plan table rows** (pdfplumber; `PDF_PLAN_ROWS.csv` has all 148):

| Plan, page, table, row | Section | Reference | Review | Method (printed) | Status | Est. US$ | Actual US$ | Signed planned / actual |
|---|---|---|---|---|---|---|---|---|
| 33761744, p.3, t1, r1 | WORKS | LK-MIHAPCLG-108999-CW-RFB | Post | Request for Bids | under/pending implementation | – | 0.00 | 2019-06-11 / – |
| 40009757, p.3, t1, r1 | WORKS | SN-PDEC-237672-CW-RFQ | A posteriori → post | Demande de prix | Annulé → cancelled | 140,000 | 0.00 | 2021-11-22 / – |
| 34172790, p.3, t2, r1 | GOODS | LK-RDA-41147-GO-RFQ | Post | Request for Bids | Signed | – | 0.00 | 2018-07-05 / 2020-02-18 |
| 40025990, p.4, t1, r2 | WORKS | ET-ERA-221389-CW-DIR | Post | Direct Selection | Signed | – | 9,890,912.07 | – / 2021-08-13 |

## 5. PDF-verified contract links

Four plans were checked (3 downloaded, 1 already local). For each PDF table row, the reference was matched to DS00005 contracts of the same project. The match is by normalised reference or by package reference plus suffix; a shared project ID alone never counts. Planned, plan-actual and recorded (DS00005) dates are kept in separate columns.

| Plan | PDF rows | Rows with STEP reference | Contracts linked | verified_amount | verified_exact_date | candidate | conflicting |
|---|---|---|---|---|---|---|---|
| 33761744 (P132833, MIHAPCLG) | 46 | 44 | 13 | 0 | 0 | 13 | 0 |
| 34172790 (P132833, Road Development Authority) | 44 | 44 | 31 | 4 | 6 | 19 | 2 |
| 40025990 (P148850, ERA) | 30 | 11 (+19 legacy) | 6 | 4 | 1 | 1 | 0 |
| 40009757 (P175325, PPDC, French labels) | 28 | 28 | 17 | 0 | 13 | 4 | 0 |
| **Total** | 148 | 127 | **67** | **8** | **20** | **37** | **2** |

**Agreement with the earlier text-only evidence** (`PDF_CONTRACT_LINKS.csv`):
- **Overlap:** 55 of the 67 PDF links also exist in the text analysis.
- **Same page:** 55 of 55.
- **Same tier:** 54 of 55. The one difference is ET-ERA-221389-CW-DIR, contract 1675726. The PDF table shows an actual amount of US$9,890,912.07, equal to DS00005, so it becomes amount-verified; the text parser had not assigned that amount.
- **Found only by the PDF tables:** 12 links.
- **Not in the PDF tables:** 85 text links.
  - 84 of those references are printed on the PDF page the text analysis predicted, which was checked with PyMuPDF.
  - The other one is in the Senegal plan: the reference is present in the PDF text, but not on the predicted page.

**Conclusion:** the evidence is real, but pdfplumber's table detection drops rows. Neither extractor alone is complete, so the pipeline should combine table extraction with layout-text extraction.

| Example | Evidence (PDF page / table / row) | Tier |
|---|---|---|
| One plan row, one contract | 34172790 p.3 t1 r2 (WORKS) `LK-RDA-204059-CW-RFQ` → contract 1674110. Plan actual US$17,652.71 = DS00005. Planned 2020-12-14, plan-actual 2020-12-29, recorded 2020-12-24: the 5-day actual-versus-recorded difference is kept, not reconciled. | verified_amount (PDF table, matching the text tier) |
| Lot sum | 34172790 p.3 t2 r5 `LK-RDA-106767-GO-RFQ`. Plan US$5,010.81 = contract 1698692 (US$159.46) + 1698690 (`…-RFQ-1`, US$4,851.35). | verified_amount (one row, two contracts) |
| Amount, Ethiopia | 40025990 p.5 t1 r6 `ET-ERA-147679-CS-QCBS` → contract 1672571. US$1,317,983.35 on both sides; plan-actual date = recorded date (2021-09-28). | verified_amount |
| Exact date, lots | 34172790 p.3 t2 r15 `LK-RDA-163114-GO-RFQ` → 4 contracts all signed 2021-02-08 = plan actual. Plan amount 0. | verified_exact_date (separate tier) |
| Exact date, French | 40009757 p.3 t2 r6 `SN-PDEC-301217-GO-RFQ` → contracts 1729238 and 1729239, signed 2022-11-11 = plan actual. Plan amount 0. | verified_exact_date |
| Candidate, lots | 34172790 p.3 t2 r1 `LK-RDA-41147-GO-RFQ` → 8 contracts signed 2019-11-01 to 2020-02-11. Plan actual 2020-02-18; amount 0. | candidate (no proof by nearby dates) |
| Candidate, large lots | 40009757 p.3 t1 r7 `SN-PDEC-463614-CW-RFB` → 4 contracts totalling US$2,410,827.55; plan amount 0, no actual date. | candidate |
| Conflicting | 34172790 p.3 t2 r14 `LK-RDA-161704-GO-RFQ` → contract 1674232: plan US$52,957.59 against DS00005 US$1,436.41. Also p.5 t2 r4 `LK-RDA-148668-NC-RFB`: US$951,489.29 against US$995,986.40. | conflicting (both values kept) |

**Dates:** across the 67 PDF links with both dates, the plan-actual signing date is never earlier than the recorded DS00005 date:
- 21 same day;
- 1–23 days later for one-to-one rows;
- up to 109 days for lot rows, where the plan's actual date is the last lot's signing.

This is consistent with the plan recording the date the STEP step was completed. It is a difference, not a conflict.

## 6. Proposed final metadata schema

Full field list: `METADATA_SCHEMA_v1.json`. The pilot confirms **Project → Document → DocumentVersion → Section → Chunk**, with three refinements and two linked entities.

1. **DocumentVersion** is keyed by document_id + SHA-256. For plans it carries:
   - `plan_stream` (executing agency);
   - `plan_revised_date`, read from the PDF (the version date);
   - `plan_original_approval_date`;
   - `textual_part_date`;
   - the WDS dates kept separately.

   Versions are ordered within (project, plan_stream). ISRs are ordered by `isr_sequence`.
2. **Section** records:
   - `section_type`: data sheet, narrative, annex, ratings, plan textual part, or plan table by category;
   - the physical page range and the printed page label (they differ, for example by −13 in PADs);
   - `contains_personal_data`.
3. **Chunk** carries the filter fields (country, project, doc_type, dates, section_type, language), physical and printed pages, sanitised text and a hash.
4. **PlanActivity:** one row per plan table row, with document, page, table and row, section, printed and normalised reference, package core, review, method, status, amounts, and planned/actual dates per stage.
5. **ContractEvidenceLink** (contract ↔ PlanActivity):
   - match class and cardinality;
   - evidence tier, with verified links stored apart from candidate, conflicting and ambiguous ones;
   - both sides' values and the date difference;
   - `pdf_confirmed`.
6. **NarrativeEvidence** is project-level only and never linked to a specific contract.

**What this enables:**
- **Filtering:** by country, project, doc type, date or plan version, section type and evidence tier.
- **Citations:** "PP 34172790, page 3 [printed 1], GOODS table 2 row 5, source URL, SHA-256".
- **Version comparison:** join PlanActivity rows by package core across versions of the same plan stream, comparing status, amounts and dates.

## 7. Recommendations for chunking and table extraction

1. **Plans:** do not chunk the STEP tables as prose. Store each table row as a PlanActivity record, plus a short generated row text for retrieval. Keep the textual part (thresholds, methods) as normal chunks.
2. **Combine two extractors for plan tables:**
   - pdfplumber tables (cell structure);
   - PyMuPDF layout-text rows (coverage).

   Reconcile them by reference and page, and flag rows found by only one extractor. In the pilot, the tables missed 85 of the 140 text-derived links; 84 of those references are printed on the predicted page. The text missed 12 links that the tables found.
3. **Undo cell wrapping:** remove injected spaces when comparing labels and statuses, and merge header variants (`wbdisc.procurement_plan`). Map French and Spanish labels for review type, status and method.
4. **PAD, ICR and ICRR:** split on numbered headings and font-size headings (no outlines except one ICR). Keep annexes as separate sections. Keep results-framework and ratings tables as table chunks with header rows repeated, and do not split mid-table.
5. **ISR:** use template sections (ratings, Implementation Status and Key Decisions, risks, indicators). Strip "Public Disclosure Authorized" and running headers.
6. **Sanitise before chunking:** staff-name blocks, e-mails and phone numbers.
7. **Record both page references:** physical page and printed label.

## 8. Quality checks before scaling to 237 documents

1. **Download integrity:** `%PDF` header, SHA-256 and page count recorded; a re-download must reproduce the same hash or be logged as a new version.
2. **Text layer:** flag pages under 100 characters and route image pages to OCR review. None needed it in this batch apart from one map page.
3. **Identity:** the project ID found in the PDF must equal the manifest project. Extract the plan version date, flag WDS-date mismatches, and assign the plan stream.
4. **Table extraction recall per plan:** STEP references in page text against references in table rows; target 100% after combining both extractors.
5. **Link validation:**
   - amount-verified only with actual amount > 0 and within tolerance;
   - exact date as a separate tier;
   - no 1–7-day acceptance;
   - lots handled at activity level;
   - conflicts kept.
6. **Regression against these 10 documents:** the 148 plan rows and 67 PDF links act as a fixed test set.
7. **Sanitisation:** zero e-mails or phone numbers, and no staff-name blocks, in chunk text. Check counts per document.
8. **Section coverage:** every PAD has its procurement annex section, every ICR its procurement/fiduciary section, every ISR its ratings section.

## 9. Paths

**PDFs** (`procurement-intelligence/rag/corpus_v3/pdf/`):

```
LK/P132833/PAD_2016-04-18_26256705.pdf
LK/P132833/ICR_2024-06-19_34345370.pdf
LK/P132833/ICRR_2024-10-18_34407877.pdf
LK/P132833/PP_2022-03-17_33761744.pdf
ET/P148850/PAD_2015-04-27_24440102.pdf
ET/P148850/ISR_2026-02-28_40089196.pdf
ET/P148850/PP_2025-06-27_40025990.pdf
ET/P163452/ICR_2025-01-24_34455066.pdf
SN/P175325/ISR_2022-06-24_33851641.pdf
SN/P175325/PP_2025-04-29_40009757.pdf
```

**Corpus manifest:** `procurement-intelligence/rag/corpus_v3/CORPUS_MANIFEST_FIRST10.csv`

**Pilot outputs** (`procurement-intelligence/rag/module1_discovery/pilot_first10_v1/`):
- `PDF_INGESTION_PILOT_REPORT.md`: this report
- `pdf_structure_inspection.json`
- `pdf_page_profile.csv`
- `PDF_PLAN_ROWS.csv`
- `PDF_CONTRACT_LINKS.csv`
- `pdf_link_validation.json`
- `METADATA_SCHEMA_v1.json`

**Pages for manual inspection:**

| Document | Pages | What to look at |
|---|---|---|
| PAD LK | 6–8 | Data Sheet |
| PAD LK | 55, 58–59 | Procurement annex |
| PAD LK | 34–36 | Results framework tables |
| PAD ET | 86–92 | Procurement |
| PAD ET | 120 | Image-only page |
| ICR LK | 31–33 | Procurement findings |
| ICR ET | 6–7, 26–29 | Large tables |
| ICRR | 21 | Fiduciary compliance |
| ICRR | 14 | Ratings table |
| ISR ET | 1–4 | Ratings, key decisions, watermark text |
| PP 33761744 | 3–4 | MIHAPCLG tables |
| PP 34172790 | 3 | Verified and conflicting rows |
| PP 40025990 | 1 | 2017 textual part |
| PP 40025990 | 4–5 | ERA tables, legacy and STEP references |
| PP 40009757 | 3–4 | French labels and lot rows |

Awaiting approval before any further download or pipeline work.
