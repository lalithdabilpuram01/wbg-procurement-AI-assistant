"""Procurement notices: Bronze -> Silver (four tables; nothing merged, deleted or inferred beyond the stated evidence).

Sources:
  workspace.wbg_bronze.bronze_procurement_notices_ds00979  DS00979 bulk CSV (metadata only; no reference no., no text), 243,533 rows
  workspace.wbg_bronze.bronze_procurement_notices_api      procnotices v2 API records (raw JSON, full notice text + bid reference),
                                                           20,305 rows for the 184 cohort projects of the Phase 1 cluster sample

Grains established from the data (see the dry-run report):
  * DS00979 row = one published notice. ID is numeric and unique; URL = .../procurement-detail/OP + ID zero-padded to 8.
    Rows identical in every published field except ID/URL exist (62,193 rows); they are separate notices (different IDs,
    and the API shows different references or text for all but 21 groups) and are kept, with a group-size column.
  * API row = one notice (id OP########, unique). For the 184 projects it covers, it holds every DS00979 notice (20,296)
    plus 9 notices DS00979 does not list; shared fields agree with DS00979.
  * Award notices quote the contract reference in their text; for lots it can differ from bid_reference_no (3,620 notices),
    so both references are used for contract matching.

Tables:
  procurement_notices                  (DS00979)  key notice_id
  procurement_notice_details           (API)      key notice_op_id
  notice_contract_link_candidates      (API x contract_awards) key (notice_op_id, wb_contract_number): same project and same
                                       normalised reference; not a confirmed link
  notice_contract_links                evidence-supported subset: Contract Award notice whose awarded-bidder STEP IDs include a
                                       supplier ID of the contract. Strong evidence, not proof of a unique relationship:
                                       every pair carries link_cardinality and ambiguity_status; ambiguity is never resolved.

D-14 (approved 2026-10-23): notice_text is kept exactly as published but is restricted data. The runner creates the
details table with a Unity Catalog column mask on notice_text (declared in CREATE TABLE, before any row is stored);
readers outside the account group wbg_restricted_notice_text see a placeholder. notice_text_sha256 and the contact-data
flags are computed from the unmasked Bronze value at write time, so fidelity is verifiable without reading the text.

Python vs Spark: normalize_ref, strip_html and the award-notice fields mirror discovery/wbdisc (normalize.py,
award_notice.py). Differences, all checked for parity on every record by checks.py:
  * Spark has no NFKC. normalize_ref applies a per-character NFKC table generated from Python's unicodedata for the
    Latin-1, General Punctuation, super/subscript, Letterlike, Number Forms, ligature and fullwidth-ASCII blocks
    (observed: º ª № and no-break space). Characters outside these blocks and NFKC composition of combining
    sequences are not handled; the parity check blocks a write if that ever changes a value.
  * html.unescape decodes every entity; Spark decodes &nbsp; &lt; &gt; &quot; &#39; &apos; &amp; (in that order) only.
    This is used only to locate award-notice fields (parity checked); no plain-text column is published, because
    the source uses 95 distinct entities and contact data must be masked before any indexing (pending D-14).
  * Java \\s is ASCII-only; an explicit class WS equal to Python's str.isspace() set replaces \\s and str.strip().
  * Java \\d is ASCII-only (Python \\d matches any Unicode digit).
"""
from __future__ import annotations

from app_config.paths import BRONZE_INPUTS_MANIFEST, SOURCE_PULLS_CACHE_MANIFEST, SOURCE_PULLS_SAMPLES_MANIFEST
import json
import unicodedata
from pathlib import Path

from pyspark.sql import DataFrame, Window
from pyspark.sql import functions as F

from silver.common.pipeline import CATALOG, manifest
from silver.common.sourcefiles import row_hash_expr

TRANSFORM_VERSION = "notices_silver_v1.0.0"
BRONZE_CSV = f"{CATALOG}.wbg_bronze.bronze_procurement_notices_ds00979"
BRONZE_API = f"{CATALOG}.wbg_bronze.bronze_procurement_notices_api"
REPO = Path(__file__).resolve().parents[2]
BRONZE_INPUTS_MANIFEST = manifest("WBG_BRONZE_INPUTS_MANIFEST", BRONZE_INPUTS_MANIFEST)
PHASE1_MANIFESTS = [manifest("WBG_SOURCE_PULLS_MANIFEST", SOURCE_PULLS_CACHE_MANIFEST), manifest("WBG_SOURCE_PULLS_MANIFEST", SOURCE_PULLS_SAMPLES_MANIFEST)]
META = ("source_file", "cohort_id", "ingested_at")
URL_PREFIX = "https://projects.worldbank.org/en/projects-operations/procurement-detail/"
NOTICE_TYPES = ["Contract Award", "Request for Expression of Interest", "Invitation for Bids", "General Procurement Notice",
                "Invitation for Prequalification", "Small Contracts Award"]
TENDER_TYPES = ["Invitation for Bids", "Request for Expression of Interest", "Invitation for Prequalification"]
API_STRING_FIELDS = ["id", "notice_type", "noticedate", "notice_lang_name", "notice_status", "project_ctry_name", "project_id", "project_name",
                     "bid_reference_no", "bid_description", "procurement_group", "procurement_method_code", "procurement_method_name",
                     "submission_date", "notice_text", "contact_organization", "contact_ctry_name", "submission_deadline_date",
                     "submission_deadline_time"]
API_KEYS = sorted(API_STRING_FIELDS + ["_extract"])
API_SCHEMA = ", ".join(f"{k} string" for k in API_STRING_FIELDS) + ", _extract struct<filter_project_id: string>"
TEXT_MASK_GROUP = "wbg_restricted_notice_text"
TEXT_MASK_FUNCTION = f"{CATALOG}.wbg_silver.mask_restricted_notice_text"
TEXT_PLACEHOLDER = f"[RESTRICTED: requires membership of {TEXT_MASK_GROUP}]"
# contact-data detection (flags only; the whole column is restricted). Python uses the same strings with re.I.
EMAIL_RE = r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
PHONE_RE = r"\b(?:tel|t[\u00e9e]l[\u00e9e]phone|phone|fax|mobile|cell)\b[^0-9+]{0,12}\+?[0-9][0-9 ()./-]{6,}"
MATCH_METHOD = "same project_id; normalize_ref(borrower_contract_reference) = normalize_ref(bid_reference_no or award_reference_in_text)"
WS = r"[\t\n\x0b\x0c\r\x1c-\x1f \x85\xa0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]"  # Python str.isspace() set
SEC = "\u0001"  # section marker used while splitting award-notice text (absent from the source; checked)
_NFKC_BLOCKS = [(0x00A0, 0x00FF), (0x2000, 0x206F), (0x2070, 0x209F), (0x2100, 0x218F), (0xFB00, 0xFB06), (0xFF01, 0xFF5E)]
_NFKC = {chr(cp): unicodedata.normalize("NFKC", chr(cp)) for a, b in _NFKC_BLOCKS for cp in range(a, b + 1)
         if unicodedata.normalize("NFKC", chr(cp)) != chr(cp)}
_NFKC_1 = {k: v for k, v in _NFKC.items() if len(v) == 1}
_NFKC_N = {k: v for k, v in _NFKC.items() if len(v) != 1}
_STRIP = "^" + WS + "+|" + WS + "+$"
_AWARDED = r"(Awarded Bidder\(s\)|Awarded Firm\(s\)|Awarded Firm/Individual)" + WS + "*:"
_OTHER = r"(Evaluated Bidder\(s\)|Evaluated Firm\(s\)|Rejected Bidder\(s\)|Rejected Firm\(s\))" + WS + "*:"


def source_columns(df: DataFrame) -> list[str]:
    return [c for c in df.columns if c not in META]


def load_provenance(bronze_table_short: str) -> dict:
    """Provenance of one notices Bronze input from the manifests: SHA-256s, record count, retrieval time, coverage."""
    m = json.loads(BRONZE_INPUTS_MANIFEST.read_text())
    e = next(f for f in m["files"] if f["bronze_table"] == bronze_table_short)
    src_name = Path(e["source_file"]).name
    pull = next(json.loads(ln) for p in PHASE1_MANIFESTS for ln in p.read_text().splitlines() if src_name in ln)
    return {"bronze_input_file_sha256": e["output_sha256"], "records": int(e["records_selected"]), "source_population_sha256": e["source_sha256"],
            "source_retrieved_at": pull["extracted_at_utc"], "source_url_note": pull["source"], "coverage": e["coverage"]}


def _c(name: str) -> F.Column:
    return F.col(f"`{name}`")


def _ws(c: F.Column) -> F.Column:
    """Whitespace-normalised copy (published value kept separately)."""
    return F.trim(F.regexp_replace(c, WS + "+", " "))


def normalize_ref(c: F.Column) -> F.Column:
    """Spark mirror of wbdisc.normalize.normalize_ref (no NFKC; parity checked on every observed value)."""
    s = F.translate(c, "".join(_NFKC_1), "".join(_NFKC_1.values()))
    for k, v in _NFKC_N.items():
        s = F.replace(s, F.lit(k), F.lit(v))
    s = F.regexp_replace(F.upper(s), _STRIP, "")
    s = F.when(s.isin("", "NAN", "N/A", "NA", "NONE", "-"), F.lit(None)).otherwise(s)
    s = F.regexp_replace(s, r"[\u2010-\u2015_/\\.]", "-")
    s = F.regexp_replace(s, WS + "+", "")
    s = F.regexp_replace(F.regexp_replace(s, "-{2,}", "-"), "^-+|-+$", "")
    return F.when(s != "", s)


def strip_html(c: F.Column) -> F.Column:
    """Spark mirror of wbdisc.normalize.strip_html (fixed entity list instead of html.unescape)."""
    s = F.regexp_replace(c, r"(?i)<br\s*/?>|</p>|</tr>|</li>|</h\d>", "\n")
    s = F.regexp_replace(s, r"(?i)</t[dh]>", " | ")
    s = F.regexp_replace(s, r"<[^>]+>", " ")
    for ent, ch in (("&nbsp;", "\u00a0"), ("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'), ("&#39;", "'"), ("&apos;", "'"), ("&amp;", "&")):
        s = F.replace(s, F.lit(ent), F.lit(ch))
    s = F.replace(s, F.lit("\u00a0"), F.lit(" "))
    s = F.regexp_replace(s, r"[ \t]+", " ")
    s = F.regexp_replace(s, r"\n" + WS + r"*\n+", "\n")
    return F.regexp_replace(s, _STRIP, "")


def _award_fields(plain: F.Column) -> list[F.Column]:
    """Spark mirror of wbdisc.award_notice.parse_award_notice for the fields used in linkage."""
    ver = F.regexp_extract(plain, r"Notice Version No:" + WS + r"*(\d+)", 1)
    ref = F.regexp_replace(F.regexp_extract(plain, r"Bid/Contract Reference No:" + WS + r"*([^\n]+)", 1), _STRIP, "")
    parts = F.split(F.regexp_replace(F.regexp_replace(plain, _AWARDED, SEC + "A"), _OTHER, SEC + "X"), SEC)
    awarded = F.filter(F.slice(parts, 2, 1_000_000), lambda p: p.startswith("A"))
    ids = F.flatten(F.transform(awarded, lambda p: F.regexp_extract_all(p, F.lit(r"\((\d{3,9})\)"), F.lit(1))))
    return [F.coalesce(plain.contains("Notice Version No") & plain.contains("Bid/Contract Reference No"), F.lit(False)).alias("is_step_award_template"),
            F.when(ver != "", ver.cast("int")).alias("award_notice_version_no"),
            F.when(ref != "", ref).alias("award_reference_in_text"),
            F.when(F.size(awarded) > 0, ids).alias("awarded_supplier_ids")]


def _lineage(prov: dict, bronze_table: str, dataset: str, run_id: str, row_hash: F.Column) -> list[F.Column]:
    """Lineage columns shared by the notice tables: source row hash, Bronze file, manifest SHA-256s and run id."""
    return [F.lit(dataset).alias("source_dataset"), row_hash.alias("source_row_sha256"),
            F.lit(bronze_table).alias("bronze_table"), F.col("source_file").alias("bronze_source_file"),
            F.col("cohort_id").alias("bronze_cohort_id"), F.col("ingested_at").alias("bronze_ingested_at"),
            F.lit(prov["bronze_input_file_sha256"]).alias("bronze_input_file_sha256"),
            F.lit(prov["source_population_sha256"]).alias("source_population_sha256"),
            F.to_timestamp(F.lit(prov["source_retrieved_at"])).alias("source_retrieved_at"),
            F.lit(run_id).alias("silver_run_id"), F.current_timestamp().alias("silver_processed_at"), F.lit(TRANSFORM_VERSION).alias("transform_version")]


def build_details(api: DataFrame, prov: dict, run_id: str) -> DataFrame:
    """One typed row per API notice, with normalised references, award-text fields, contact flags and lineage."""
    j = api.select(F.from_json("raw_record", API_SCHEMA).alias("r"), "raw_record", *META)
    r = lambda k: F.col(f"r.{k}")  # noqa: E731
    df = j.select(
        r("id").alias("notice_op_id"),
        # The API id is "OP" plus the zero-padded 8-digit CSV notice ID; the CSV form is derived to join both sources.
        F.when(r("id").rlike(r"^OP\d{8}$"), F.substring(r("id"), 3, 8).cast("bigint").cast("string")).alias("notice_id"),
        r("notice_type").alias("notice_type"), r("notice_status").alias("notice_status"),
        F.to_date(r("noticedate"), "dd-MMM-yyyy").alias("notice_date"),
        r("notice_lang_name").alias("notice_language"),
        r("project_id").alias("project_id"), r("project_name").alias("project_name_source"), r("project_ctry_name").alias("project_country_name"),
        r("bid_reference_no").alias("bid_reference_no"), normalize_ref(r("bid_reference_no")).alias("bid_reference_normalized"),
        r("bid_description").alias("bid_description"), _ws(r("bid_description")).alias("bid_description_normalized"),
        r("procurement_group").alias("procurement_group"), r("procurement_method_code").alias("procurement_method_code"),
        r("procurement_method_name").alias("procurement_method_name"),
        F.to_date(F.substring(r("submission_date"), 1, 10), "yyyy-MM-dd").alias("submission_date"),
        F.to_date(F.substring(r("submission_deadline_date"), 1, 10), "yyyy-MM-dd").alias("submission_deadline_date"),
        r("submission_deadline_time").alias("submission_deadline_time"),
        r("contact_organization").alias("contact_organization"), r("contact_ctry_name").alias("contact_country_name"),
        r("notice_text").alias("notice_text"), F.sha2(r("notice_text"), 256).alias("notice_text_sha256"),
        F.when(r("notice_text").isNotNull(), r("notice_text").rlike(EMAIL_RE)).alias("notice_text_contains_email"),
        F.when(r("notice_text").isNotNull(), r("notice_text").rlike("(?iu)" + PHONE_RE)).alias("notice_text_contains_phone"),
        F.when(r("notice_text").isNotNull(), F.lit("restricted")).alias("notice_text_access_class"),
        strip_html(r("notice_text")).alias("_notice_text_plain"),
        r("_extract.filter_project_id").alias("extract_filter_project_id"),
        F.sha2("raw_record", 256).alias("raw_record_sha256"),
        *_lineage(prov, BRONZE_API, "procnotices_api_v2", run_id, F.sha2("raw_record", 256)),
        # validation-only
        r("noticedate").alias("_raw_noticedate"), r("submission_date").alias("_raw_submission_date"),
        r("submission_deadline_date").alias("_raw_deadline"), F.json_object_keys("raw_record").alias("_json_keys"),
        F.coalesce(r("notice_text").contains(SEC), F.lit(False)).alias("_has_section_marker"),
    )
    # Award fields are parsed from the plain text, and kept only for Contract Award notices.
    af = _award_fields(F.col("_notice_text_plain"))
    names = ["is_step_award_template", "award_notice_version_no", "award_reference_in_text", "awarded_supplier_ids"]
    df = df.select("*", *[F.when(F.col("notice_type") == "Contract Award", c).alias(n) for c, n in zip(af, names)])
    df = df.withColumn("award_reference_in_text_normalized", normalize_ref(F.col("award_reference_in_text")))
    tw = Window.partitionBy("project_id", "notice_type", "notice_text")
    df = df.withColumn("identical_text_group_size", F.when(F.col("notice_text").isNotNull(), F.count(F.lit(1)).over(tw)))
    lead = ["notice_op_id", "notice_id", "notice_type", "notice_status", "notice_date", "notice_language", "project_id", "project_name_source",
            "project_country_name", "bid_reference_no", "bid_reference_normalized", "award_reference_in_text", "award_reference_in_text_normalized",
            "bid_description", "bid_description_normalized", "procurement_group", "procurement_method_code", "procurement_method_name",
            "submission_date", "submission_deadline_date", "submission_deadline_time", "contact_organization", "contact_country_name",
            "notice_text", "notice_text_sha256", "notice_text_access_class", "notice_text_contains_email", "notice_text_contains_phone",
            "is_step_award_template", "award_notice_version_no", "awarded_supplier_ids",
            "identical_text_group_size", "extract_filter_project_id", "raw_record_sha256"]
    return df.select(*lead, *[c for c in df.columns if c not in lead])


def build_notices(csv: DataFrame, details: DataFrame, prov: dict, run_id: str) -> DataFrame:
    """One typed row per DS00979 notice, flagged when the API also has its detail record."""
    cols = source_columns(csv)
    content = [c for c in cols if c not in ("ID", "URL")]
    # Rows equal in every published column except ID and URL form a group; its size is kept, no row is dropped.
    gw = Window.partitionBy(*[F.coalesce(_c(c), F.lit("\u0000NULL")) for c in content])
    api_ids = details.select(F.col("notice_id").alias("_api_notice_id")).distinct()
    df = csv.select(
        _c("ID").alias("notice_id"), F.concat(F.lit("OP"), F.lpad(_c("ID"), 8, "0")).alias("notice_op_id"), _c("URL").alias("notice_url"),
        _c("Notice Type").alias("notice_type"),
        F.to_date(_c("Publication Date"), "MM/dd/yyyy").alias("publication_date"), F.to_date(_c("Deadline Date"), "MM/dd/yyyy").alias("deadline_date"),
        _c("Project ID").alias("project_id"),
        _c("Bid Description").alias("bid_description"), _ws(_c("Bid Description")).alias("bid_description_normalized"),
        _c("Procurement Category").alias("procurement_category"), _c("Procurement Method").alias("procurement_method"),
        _c("Country Code").alias("country_code"), _c("Country Name").alias("country_name"), _c("Region").alias("region_name_source"),
        _c("Sector").alias("sectors_published"),
        F.when(_c("Sector").isNotNull(), F.transform(F.split(_c("Sector"), ";"), lambda x: F.trim(x))).alias("sectors"),
        _c("Publication - Fiscal year").cast("int").alias("publication_fiscal_year"),
        _c("Publication - Calendar year").cast("int").alias("publication_calendar_year"),
        F.count(F.lit(1)).over(gw).alias("identical_metadata_group_size"),
        *_lineage(prov, BRONZE_CSV, "DS00979", run_id, row_hash_expr(cols)),
        _c("Publication Date").alias("_raw_publication_date"), _c("Deadline Date").alias("_raw_deadline_date"),
        _c("Publication - Fiscal year").alias("_raw_fy"), _c("Publication - Calendar year").alias("_raw_cy"),
    )
    df = df.join(api_ids, df.notice_id == api_ids._api_notice_id, "left").withColumn("has_api_detail", F.col("_api_notice_id").isNotNull()).drop("_api_notice_id")
    lead = ["notice_id", "notice_op_id", "notice_url", "notice_type", "publication_date", "deadline_date", "project_id", "bid_description",
            "bid_description_normalized", "procurement_category", "procurement_method", "country_code", "country_name", "region_name_source",
            "sectors_published", "sectors", "publication_fiscal_year", "publication_calendar_year", "has_api_detail", "identical_metadata_group_size"]
    return df.select(*lead, *[c for c in df.columns if c not in lead])


def build_link_candidates(details: DataFrame, contracts: DataFrame, run_id: str) -> DataFrame:
    """Every (notice, contract) pair in the same project whose normalised references are equal (field or award-text reference)."""
    keys = details.select("notice_op_id", "project_id", "notice_type", "notice_status", "notice_date", "award_notice_version_no",
                          "awarded_supplier_ids", "bid_reference_no", "award_reference_in_text",
                          F.explode(F.array(F.struct(F.col("bid_reference_normalized").alias("k"), F.lit("bid_reference_no").alias("basis")),
                                            F.struct(F.col("award_reference_in_text_normalized").alias("k"), F.lit("award_reference_in_text").alias("basis")))).alias("e")
                          ).filter("e.k IS NOT NULL")
    con = contracts.select("wb_contract_number", F.col("project_id").alias("c_project_id"), "borrower_contract_reference", "contract_signing_date",
                           F.filter("supplier_ids", lambda x: x.isNotNull()).alias("contract_supplier_ids"),
                           normalize_ref(F.col("borrower_contract_reference")).alias("contract_reference_normalized"))
    pairs = keys.join(con, (keys.project_id == con.c_project_id) & (F.col("e.k") == con.contract_reference_normalized))
    g = pairs.groupBy("notice_op_id", "wb_contract_number").agg(
        F.first("project_id").alias("project_id"), F.first("notice_type").alias("notice_type"), F.first("notice_status").alias("notice_status"),
        F.first("notice_date").alias("notice_date"), F.first("award_notice_version_no").alias("award_notice_version_no"),
        F.first("bid_reference_no").alias("bid_reference_no"), F.first("award_reference_in_text").alias("award_reference_in_text"),
        F.first("borrower_contract_reference").alias("borrower_contract_reference"), F.first("contract_reference_normalized").alias("matched_reference_normalized"),
        F.first("contract_signing_date").alias("contract_signing_date"),
        F.max((F.col("e.basis") == "bid_reference_no").cast("int")).cast("boolean").alias("matched_on_bid_reference_no"),
        F.max((F.col("e.basis") == "award_reference_in_text").cast("int")).cast("boolean").alias("matched_on_award_reference_in_text"),
        F.first("awarded_supplier_ids").alias("_awarded"), F.first("contract_supplier_ids").alias("_csids"),
    )
    is_award = F.col("notice_type") == "Contract Award"
    g = g.select("*", F.when(is_award & F.col("_awarded").isNotNull(), F.array_sort(F.array_intersect(F.col("_csids"), F.col("_awarded")))).alias("matched_supplier_ids"))
    g = g.withColumn("supplier_id_confirmed", F.when(is_award, F.coalesce(F.size("matched_supplier_ids") > 0, F.lit(False))))
    g = g.withColumn("link_evidence", F.when(F.col("supplier_id_confirmed"), "reference_and_awarded_supplier_id").otherwise("reference_only"))
    wn, wc = Window.partitionBy("notice_op_id"), Window.partitionBy("wb_contract_number")
    g = g.select("*", F.count(F.lit(1)).over(wn).alias("candidate_contracts_for_notice"), F.count(F.lit(1)).over(wc).alias("candidate_notices_for_contract"))
    return g.select(F.concat_ws(":", "notice_op_id", "wb_contract_number").alias("candidate_key"), "notice_op_id", "wb_contract_number", "project_id",
                    "notice_type", "notice_status", "notice_date", "award_notice_version_no", "bid_reference_no", "award_reference_in_text",
                    "borrower_contract_reference", "matched_reference_normalized", F.lit(MATCH_METHOD).alias("match_method"),
                    "matched_on_bid_reference_no", "matched_on_award_reference_in_text",
                    "contract_signing_date", "matched_supplier_ids", "supplier_id_confirmed", "link_evidence",
                    "candidate_contracts_for_notice", "candidate_notices_for_contract",
                    F.lit(run_id).alias("silver_run_id"), F.current_timestamp().alias("silver_processed_at"), F.lit(TRANSFORM_VERSION).alias("transform_version"))


def build_links(candidates: DataFrame, run_id: str) -> DataFrame:
    """Evidence-supported pairs (reference + awarded supplier ID). Cardinality and ambiguity are explicit, never resolved."""
    v = candidates.filter("supplier_id_confirmed").drop("candidate_contracts_for_notice", "candidate_notices_for_contract",
                                                        "silver_run_id", "silver_processed_at", "transform_version", "supplier_id_confirmed")
    wn, wc = Window.partitionBy("notice_op_id"), Window.partitionBy("wb_contract_number")
    v = v.select("*", F.count(F.lit(1)).over(wn).alias("supported_contracts_for_notice"), F.count(F.lit(1)).over(wc).alias("supported_notices_for_contract"))
    n1, c1 = F.col("supported_contracts_for_notice") == 1, F.col("supported_notices_for_contract") == 1
    card = (F.when(n1 & c1, "one_to_one").when(n1, "many_notices_one_contract").when(c1, "one_notice_many_contracts").otherwise("many_to_many"))
    return v.select(F.col("candidate_key").alias("link_key"), *[c for c in v.columns if c not in ("candidate_key", "link_evidence")],
                    card.alias("link_cardinality"), F.when(n1 & c1, "unambiguous").otherwise("ambiguous").alias("ambiguity_status"),
                    F.when(n1 & c1, "evidence_supported_one_to_one").otherwise("evidence_supported_ambiguous").alias("verification_status"),
                    "link_evidence",
                    F.lit(run_id).alias("silver_run_id"), F.current_timestamp().alias("silver_processed_at"), F.lit(TRANSFORM_VERSION).alias("transform_version"))


def drop_validation(df: DataFrame) -> DataFrame:
    return df.drop(*[c for c in df.columns if c.startswith("_")])
