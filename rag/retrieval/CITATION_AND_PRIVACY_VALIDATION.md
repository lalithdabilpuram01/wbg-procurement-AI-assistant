# Citation Integrity and Privacy Validation: Retrieval MVP

**Run:** `rag/retrieval/runs/20261025T164420Z/` (`citation_check.json`, `privacy_check.json`).

## Citation integrity

**Method:** every unit returned in the top 5 by any configuration (447 unique units) was re-checked against the **text layer of the cited physical page(s) of the local PDF**, using PyMuPDF.

| Unit type | What is checked |
|---|---|
| Narrative | Up to 4 probes of 25–80 characters from the sanitised excerpt; masked tokens are skipped |
| Table windows | Up to 8 non-masked cell values |
| Plan rows | The printed activity reference |

A unit is verified if at least 75% of its probes appear on the cited pages (alphanumeric-normalised).

**Result:** **441 / 447 verified (98.7%).** The 6 non-verified units are explained artefacts, not wrong pages:

| Unit | Cause |
|---|---|
| `24440102:A:0000`, `24440102:C:0000` | Title-page hyphenation / layout (2 of 3 probes found) |
| `34455066:C:0023` | Reading-order artefact in an ICR child chunk |
| `24440102:t02201:w1-2` | Cleaned table cells (word repair) differ from raw text |
| `act:40025990:p4t2r5`, `act:40025990:p4t2r11` | The printed reference contains spill-over text from an adjacent cell ("structure ET-ERA-172731-GO-RFQ", "Roads Administration ET-ERA-391970-GO-RFB"). All reference parts are present on the page |

**Page labels and dates:**
- **Page labels:** printed labels are shown only when detected (328 of 373 pages). Otherwise the result says "not detected (cite the physical page)".
- **Dates:** document and disclosure dates come from WDS metadata. Plan rows carry their own plan version (revision date from the PDF header), and streams are never merged.

**Association levels:**
- **Narrative results** are labelled "project-level document context; not a contract-level match".
- **Plan rows** show contract links with their evidence status. `candidate` and `conflicting` links are displayed as such, with disagreeing fields listed, and are never resolved silently.

## Privacy

| Check | Strategy A + tables (2,288 units) | Strategy C + tables (2,396) | A, no tables (679) |
|---|---:|---:|---:|
| Units with an e-mail pattern | 0 | 0 | 0 |
| Units with a phone-like pattern | 21 | 23 | 15 |
| Units containing a mask token (e.g. `[PERSON]`) | 96 | – | – |
| Retrieval code references `restricted/` | No | No | No |

- **Phone-like matches:** all are year or page ranges (e.g. `2015-2020`, `12-15`). Their digit / letter shapes were inspected; no values were printed.
- **Mask tokens:** masked names remain masked. Some table windows (ICR team-composition tables) consist mostly of masks; they are harmless but add noise.
- **Contact-detail questions** are refused by the guard ("withheld"). The demo output for Q30 is `withheld_personal_data`.
- **No transmission:** no text was sent to any external service. Embeddings and reranking run locally from the HF cache, offline.
