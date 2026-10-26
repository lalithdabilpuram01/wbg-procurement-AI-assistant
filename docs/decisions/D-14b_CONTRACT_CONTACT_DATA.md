# D-14b: Contact Data in Contract-Award Datasets

| | |
|---|---|
| Status | **Option A approved for future implementation (2026-10-23).** Redact e-mails, phone numbers and other unnecessary contact details in Gold/display datasets, API responses, sanitised document text and RAG inputs. Existing Bronze/Silver contract tables stay unchanged; no new masks yet. Supplier identity policy approved (below) |
| Parent decision | [D-14](D-14_NOTICE_TEXT_ACCESS_CONTROL.md) (personal contact data in notice text) |
| Method | Read-only Spark counts with the shared detectors `EMAIL_RE` / `PHONE_RE` (`silver/notices/transform.py`) over every string and `array<string>` column; local file scan. No value was printed |

## Findings

**Where.** DS00005 (World Bank contract awards, FY2020+) publishes contact details inside two text fields.

| Table / column | Rows with an e-mail or labelled phone number |
|---|---|
| Bronze `wbg_bronze.bronze_contract_awards_ds00005.Supplier` | e-mail 35, phone 39 (58 rows with either) |
| Bronze `…ds00005.Contract Description` | 8 rows (e-mail 6) |
| Silver `wbg_silver.contract_award_lines.supplier_name` / `contract_description` | 58 / 8 (same rows) |
| Silver `wbg_silver.contract_awards.supplier_names` (array) / `contract_description` | 58 / 8 |
| Bronze DS01004 legacy; Silver `contract_awards_legacy` | none |
| Notice link tables | none: 0 of the affected contracts appear in `notice_contract_links` or `…_candidates` |

**Scale and shape**
- **Contracts and projects:** 66 lines in 66 contracts across 25 projects, signed FY2020–FY2026; US$6.76 M of supplier amounts.
- **Supplier field:** 58 lines, from 42 numeric STEP supplier IDs. 19 have only an e-mail address, 23 only a phone number,
  and 16 both.
- **Consistency per supplier:** each of the 42 supplier IDs is published with contact data on all of its lines (0 lines
  without it). The contact details appear to come from the supplier's STEP name record, not from individual contracts.
- **Possible natural persons:** only 22 of the 58 supplier lines look like firms (legal-form or business words). The rest
  may be sole proprietors or individuals, so the name itself may be personal data, not just the contact details.
- **Descriptions:** 8 contract descriptions carry contact details, 3 of them individual-consultant contracts.
  Anonymised individual consultants (16-hex IDs, "INDIVIDUAL CONSULTANT") are not affected.

**Local copies.**

| File | E-mail matches |
|---|---|
| `discovery/data_cache/ds00005_contract_awards.csv` (full population, git-ignored) | 66 |
| `databricks_upload/bronze_inputs/contract_awards_ds00005/…cohort2000.csv` (git-ignored) | 41 |
| Same file in the Volume `wbg_procurement_raw/bronze_inputs/contract_awards_ds00005/` | (same contents) |

## Downstream exposure

| Consumer | Status | Risk |
|---|---|---|
| Silver contract tables | Exist; readable by the owner (only identity) | Contact substrings visible in `supplier_name`, `supplier_names`, `contract_description` |
| Gold / supplier analytics | Not built | Supplier names would be shown or aggregated as published |
| RAG / embeddings / vector index | Not built | Contract records or descriptions embedded with contact data |
| Application APIs and AI answers | Not built | Supplier names returned verbatim |
| ML features | Not built (ML paused) | Name-derived features could carry contact data; not a label issue |
| Logs and reports | Current jobs print counts only; `contracts` run reports list the one missing-supplier name (no contact data) | Low |

## Options

| Option | What | Pros | Cons |
|---|---|---|---|
| **A. Redact in Gold/serving (recommended first step)** | Gold display fields `supplier_name_display`, `contract_description_display`, with contact substrings replaced by a placeholder. The same redaction runs in the RAG sanitiser and in the API output filter. Silver keeps published values | No change to approved Silver; deterministic and testable; protects every consumer-facing path | Silver/Bronze still hold raw values (owner-readable, like today) |
| **B. Bronze redaction mask** | Column masks on Bronze `Supplier` and `Contract Description` that replace e-mail/phone substrings for non-members of `wbg_restricted_notice_text`. Bronze DS00005 has no CHECK constraints | Restricts the raw layer; can share the D-14 identity work (steps 1–2) | Bronze time travel then refused; the contracts Silver rebuild and verification must run as a member or compare redacted forms (same code pattern as D-14 step 4) |
| **C. Silver masks** | Masks on Silver `supplier_name`, `supplier_names`, `contract_description` | Strongest at the analytics layer | **Not possible as-is:** the contract tables carry CHECK constraints, which cannot coexist with masks. Needs a new version of the approved tables (constraints converted to validated rules), i.e. changes to accepted Silver |
| **D. Restricted side table** | A new Silver table of contact substrings keyed by `line_key` (masked); redacted copies in the main tables | Clean separation | Rebuilds approved Silver tables |

**Recommendation:** A now, at design time of the Gold/RAG layers. Add B together with D-14 step 2 once the identity tests
pass. Defer C/D unless Silver itself must be exposed to non-owner identities.

## Redaction requirements (shared with D-14 sanitisation)

- **Detect:**
  - e-mail addresses, including obfuscated forms
  - labelled phone, fax and mobile numbers in EN/FR/ES/PT, plus bare international formats inside supplier names
    (common in this field, e.g. a trailing "Tel …")
- **Replace:** a typed placeholder (`[EMAIL]`, `[PHONE]`). Never truncate or alter the remaining name text, so
  supplier-name grouping (D-06 candidate layer) still works.
- **Tests** (synthetic values only):
  - the field shapes seen here: name + e-mail, name + phone, name + both, description with contact
  - false-positive guards: product codes, amounts, reference numbers, "mobile station", "Intel"
  - idempotence
- **Blocking checks:** after redaction, 0 detector hits in display fields; row counts and keys unchanged.
- **Open question for the owner:** should supplier names that look like natural persons (36 of the 58 lines) be
  displayed at all in the application, or only as supplier IDs? WB publishes them, but D-06/D-14 policy should decide
  display.

---

# Supplier Identity Policy (approved 2026-10-23)

## Principles

1. **Governed source and analytical tables** (Bronze, Silver, internal Gold facts) keep the original supplier IDs and
   names as published. Stable internal identifiers (`supplier_id`, `line_key`, `wb_contract_number`) are never altered,
   so joins and analytics stay reliable.
2. **Public-facing application views:**
   - Organisations: shown by name, with contact substrings redacted (Option A).
   - Individuals: masked or pseudonymised by default.
   - Direct personal contact details: never exposed, in any classification.
3. **Uncertain classifications** are flagged for review; the pipeline does not guess.

## Evidence (Silver `contract_award_lines`, distinct supplier IDs; counts only)

| Group | Supplier IDs | Treatment |
|---|---|---|
| `anonymized_individual_consultant` (16-hex ID, name "INDIVIDUAL CONSULTANT"), already anonymised by the World Bank | 37,369 | Individual; displayed as an individual consultant with a pseudonym |
| Numeric ID with an Individual Consultant Selection contract | **0** | All individual-consultant contracts are already anonymised at source |
| Numeric ID, name matches an organisation lexicon (legal forms, consult/engineering/services/construction, JV/consortium, university/ministry/UN agency…) | 35,731 | Organisation (rule-based) |
| Numeric ID, no organisation word | **49,341** | **Uncertain:** many are firms without a legal suffix or with non-English names; some may be sole proprietors or individuals |
| Missing supplier ID | 1 | Uncertain (kept as published in Silver) |

## Classification design (Gold `dim_supplier`; nothing built yet)

- **Output columns:**
  - `supplier_party_type` ∈ {`organization`, `individual`, `uncertain`}
  - `classification_rule` (which rule fired)
  - `classification_confidence` ∈ {`source_asserted`, `rule_high`, `rule_low`, `none`}
  - `review_status` ∈ {`not_required`, `pending`, `confirmed_org`, `confirmed_individual`}
  - `contact_data_detected` (D-14b detectors)
- **Rules, in order; the first decisive rule wins:**
  1. **Source-asserted individual:** `supplier_id_type = anonymized_individual_consultant` → `individual` /
     `source_asserted`.
  2. **Manual override table** (`supplier_classification_overrides`: versioned, reviewer, date, reason) → as reviewed.
  3. **Organisation lexicon** (multilingual EN/FR/ES/PT legal forms and organisation nouns; JV/consortium separators;
     known UN/IFI/government bodies) → `organization` / `rule_high`.
  4. **Person-name signals:** titles (Mr, Mrs, Dr, M., Mme, Sr, Sra, Eng.), 2–4 alphabetic tokens with no
     organisation word or digit, or contact data embedded in the name → `uncertain` / `rule_low`, with
     **review priority high**. Never auto-labelled `individual`, which would be a guess.
  5. **Otherwise** → `uncertain` / `none`, review priority by exposure (contract count, appearance in app or RAG
     outputs).
- **Review queue:** `supplier_classification_review` (supplier ID, signals fired, priority; **no contact data**).
  Decisions go to the override table. The queue is re-generated on each run, and confirmed decisions persist.
- **Pseudonyms:** `supplier_public_id` = `"S-" + first 10 hex of HMAC-SHA256(secret key, supplier_id)`.
  - The key is held in a Databricks secret scope, or locally for the PoC, and is never in code.
  - The same supplier always gets the same pseudonym. It cannot be reversed without the key.
  - Joins inside governed tables keep using `supplier_id`.

## Display rules (application views and APIs)

| `supplier_party_type` | Shown as |
|---|---|
| `organization` | Published name with contact substrings redacted (`[EMAIL]`, `[PHONE]`), plus country |
| `individual` | "Individual consultant" + `supplier_public_id` + country; no name |
| `uncertain` (default, pending review) | **Pseudonymised** (decision 2026-10-23, option a): "Supplier" + `supplier_public_id` + country, until the supplier is confidently verified as an organisation (rule `rule_high` or reviewer `confirmed_org`) |
| any | Never: e-mail addresses, phone/fax numbers, contact-person blocks, addresses beyond country |

**Tests required:**
- the rule order, and that the override table wins;
- lexicon false positives and negatives on synthetic names;
- pseudonym stability and key isolation;
- display-rule snapshot tests showing no contact data or individual names in API fixtures;
- a review queue that contains no contact data.

## Decision 2026-10-23: default display for uncertain suppliers (approved, option a)

- **Default:** the 49,341 numeric supplier IDs classified `uncertain` are shown in future public-facing application
  outputs **only by a stable pseudonym** (`supplier_public_id`, plus country) until confidently verified as an
  organisation. "Confidently verified" means either:
  - the organisation-lexicon rule (`rule_high`), or
  - a reviewer decision `confirmed_org` in the override table.
- **Unchanged in governed data:** original identifiers and names stay as published in Bronze, Silver and internal Gold
  facts, and joins use `supplier_id`.
- **Anonymised consultants stay anonymised.** The 37,369 already-anonymised individual-consultant IDs are never
  re-identified: no lookups, no linkage to names from other sources.
- **Pseudonymisation method (future implementation):**
  - `supplier_public_id = "S-" + hex(HMAC-SHA256(key, supplier_id))[:10]`
  - deterministic: the same supplier always gets the same pseudonym
  - secret-keyed: not reversible or recomputable without the key
  - the key lives in a secret store, never in code, logs or tables
  - **No production key is generated or stored until the Gold implementation is approved**, and the D-14 gate tests
    did not create one.
- **Promotion:** when a supplier is promoted from `uncertain` to `organization`, its public view switches from pseudonym
  to redacted name. The pseudonym stays resolvable internally, so links in earlier outputs keep working.
- **Not implemented yet:** no Gold transformation has been started.

---

## Addendum 2026-10-24: additional redaction requirement (documentation only)

The consolidated Silver audit (`silver/runs/silver_audit_20261024T043611Z/`, case-insensitive detectors, counts only) found
phone-number patterns in **4 rows of `wbg_silver.procurement_notices.bid_description`** (and the same 4 rows of
`bid_description_normalized`), from DS00979. These rows were outside the original D-14b scan scope. No e-mail
addresses were found in these columns.

**Future requirement:**
- Add `procurement_notices.bid_description` / `bid_description_normalized` to the Option A redaction scope.
- Apply it to Gold display fields, API responses, sanitised text and RAG inputs.
- Use the same detectors, placeholders and blocking checks as for supplier names and contract descriptions.
- Add tests for this field's shapes.

**Unchanged:**
- Bronze and Silver keep the published values.
- No mask, permission or data change was made.
- D-14/D-14b production implementation stays paused.
- `gold_project_portfolio` does not include any description column.

**Detector note:** in Spark, `rlike` with the shared `PHONE_RE` (`silver/notices/transform.py`) is case-sensitive.
Implementations must prefix it with `(?i)`, as the audit does, or labelled numbers such as "Tel …" are missed.

## Addendum 2026-10-24: supplier identifiers in Gold (assessment for `gold_contract_analytics`; documentation only)

**Question.** Can supplier identifiers be used internally in Gold without exposing identities?

**Findings (counts only):**

| Identifier | Distinct values | Re-identification risk |
|---|---|---|
| Numeric STEP `supplier_id` | 85,072, of which 49,341 are classified uncertain (possible individuals or sole proprietors) | **Directly re-identifiable.** The same ID is published next to the supplier name in DS00005, which is public, and in Silver |
| `anonymized_individual_consultant` ID (16-hex, anonymised by the World Bank) | 37,369 | No name is attached. But a stable ID lets anyone link one consultant's contracts together (profiling) |
| `contract_award_lines.line_key` | — | Embeds the supplier ID (`contract_number:supplier_id`), so it carries the same risk as the ID |
| `borrower_contract_reference` | — | Free text of up to 40 characters, with 0 e-mail or phone hits. 85 individual-consultant references contain no digits and 1 looks like a name. Not safe for unsanitised display |

**Decision applied in `gold_contract_analytics` v1:**
- No supplier names, supplier IDs, line keys or borrower references.
- Only non-identifying per-contract aggregates:
  - supplier-line count;
  - ID-type flags (anonymised consultant / numeric / missing);
  - distinct supplier country codes;
  - a same-country / other-country relation;
  - a SHA-256 over the Silver line *row hashes*, for lineage. It is one-way and is not a supplier key.

**Internal supplier analytics** (concentration, repeat winners) still needs the approved D-14b design. Until that is implemented:
- Raw supplier IDs stay only in Silver, for governed joins.
- No Gold table carries raw supplier IDs or line keys.

The approved D-14b design comprises:
- `dim_supplier` with a `supplier_party_type` classification;
- `supplier_public_id` = HMAC-SHA256 with a secret-store key, never in code or tables;
- the anonymised-consultant rule: never re-identified, never linked to names.

The key does not exist yet: D-14/D-14b production implementation remains paused.
