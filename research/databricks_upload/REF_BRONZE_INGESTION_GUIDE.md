# Reference Bronze Ingestion Guide: `workspace.wbg_ref_bronze`

Status (2026-10-22): **files staged and validated locally. Nothing has been uploaded, and no schema or table has been
created.** The nine procurement Bronze tables in `workspace.wbg_bronze` are not affected.

**Separation.** Country-level reference data lives in its own schema (`workspace.wbg_ref_bronze`) and its own staging
folder (`databricks_upload/ref_bronze_inputs/`). It is not filtered by the cohort (D-28). Joins to projects happen only
in Silver, using the source-specific crosswalks (D-31) and the regional rule (D-29).

**Provenance:**
- Raw inventory, validation and revision notes:
  [`discovery/country_indicators/RAW_DATA_INVENTORY.md`](../discovery/country_indicators/RAW_DATA_INVENTORY.md).
- Staged-file manifest (path, volume path, original file, URL, parameters, retrieval time, source vintage, records,
  bytes, SHA-256): `ref_bronze_inputs/_manifest/ref_bronze_inputs_manifest.csv` and `.json`.

## 1. Staged files (`databricks_upload/ref_bronze_inputs/`, 21 data files, 5.64 MB)

| Folder → table | Files | Format | Records | Bytes |
|---|---|---|---|---|
| `wdi_indicator_observations/` | 3 raw API responses (`observations_<code>_2000-2025.json`) | JSON `[header, [records]]` | 20,748 (6,916 × 3) | 4,657,056 |
| `wdi_indicator_metadata/` | 3 (`indicator_<code>.json`) | JSON | 3 | 2,585 |
| `wdi_source_metadata/` | `source_2_wdi.json` | JSON | 1 | 236 |
| `wdi_country_metadata/` | `countries.json` | JSON | 296 | 113,954 |
| `income_classification_history/` | `OGHIST_2026_10_08.xlsx` | XLSX (unchanged) | 5 sheets | 115,957 |
| `wgi_estimates/` | 6 Phase 1 files (`GOV_WGI_*.EST.jsonl.gz`) | JSONL, gzip | 34,992 (5,832 × 6) | 325,355 |
| `cpia_ratings/` | 6 Phase 1 files (`IQ.CPA.*.XQ.jsonl.gz`) | JSONL, gzip | 105,336 (17,556 × 6) | 427,069 |
| `_retrieval_metadata/` | 9 sidecars (`*.meta.json`) | JSON | 9 | — |
| `_manifest/` | `ref_bronze_inputs_manifest.{json,csv}` | — | 21 rows | — |

Volume target: `/Volumes/workspace/default/wbg_procurement_test/ref_bronze_inputs/<folder>/<file>`. This is a sibling of
the existing `bronze_inputs/`, which is not touched.

## 2. Recommended tables

**Two levels per JSON source**
- **Response level:** one row per downloaded file. `raw_response` holds the exact original text and is the
  byte-preservation layer.
- **Record level:** one row per API record. `record` is a VARIANT parsed from the response, plus string key columns for
  convenience.

There is no type casting (the `value` key stays a string), no deduplication, no filtering and no imputation.

| Table | Grain | Key columns (strings) | Built from |
|---|---|---|---|
| `wdi_api_responses` | API response file (7 rows: 3 observation, 3 indicator, 1 source) | `api_page_header` (VARIANT), `raw_response` | `wdi_indicator_observations/`, `wdi_indicator_metadata/`, `wdi_source_metadata/` |
| `wdi_indicator_observations` | indicator × country × year (20,748 rows) | `indicator_id`, `country_id`, `countryiso3code`, `date`, `value` (string), `record` (VARIANT) | the 3 observation responses |
| `wdi_country_metadata` | country or aggregate (296 rows) | `id`, `iso2Code`, `region_id`, `record` (VARIANT) | `countries.json`. **`incomeLevel` and `lendingType` are today's values only** (table comment) |
| `income_classification_files` | file (1 row) | `content` (BINARY, exact XLSX bytes), `length` | `binaryFile` reader |
| `income_classification_sheet_rows` | sheet × row (every row of all 5 sheets) | `sheet_name`, `row_number`, `cells` (ARRAY<STRING>, cell values as text, blanks kept as NULL) | XLSX read with openpyxl. No header interpretation: rows 5 and 6 of `Country Analytical History` are the fiscal-year and data-year headers, and Silver interprets them |
| `wgi_estimates` | record (34,992) | `raw_record` (exact line), `record` (VARIANT), `indicator_id`, `country_id`, `countryiso3code`, `date`, `value` (string) | `wgi_estimates/*.jsonl.gz` |
| `cpia_ratings` | record (105,336) | same as `wgi_estimates` | `cpia_ratings/*.jsonl.gz` |
| `ctl_ref_input_manifest` | staged file (21) | as in the manifest CSV | `_manifest/ref_bronze_inputs_manifest.csv` |
| `ctl_retrieval_metadata` | downloaded file (9) | `file`, `final_url`, `request_params`, `retrieval_finished_utc`, `bytes`, `sha256`, `response_headers` (VARIANT) | `_retrieval_metadata/*.meta.json` |

**Ingestion metadata columns (all data tables)**

| Column | Content |
|---|---|
| `_source_file_path`, `_source_file_size`, `_source_file_modified_at` | From `_metadata` |
| `_source_file_sha256` | From the staging manifest |
| `_acquisition` | `R1` / `R2` / `R3` |
| `_retrieved_at_utc` | From the sidecar, or the Phase 1 manifest for R3 |
| `_source_vintage` | `2026-10-08` for WDI and CPIA, `2026-09-25` for WGI, `OGHIST_2026_10_08` |
| `_ingested_at` | Ingestion timestamp |
| `_row_sha256` | Hash of the raw line or record JSON |

**Table properties:** `source.url`, `source.vintage`, `acquisition`, `decisions = D-28,D-29,D-30,D-31`. Every table is
created once with `CREATE TABLE … AS` or `writeTo(...).create()`; nothing is replaced. A future refresh appends a new
vintage, identified by `_source_vintage`.

## 3. Ingestion sketch (serverless; review before running)

```python
import json
from pyspark.sql import functions as F

BASE = "/Volumes/workspace/default/wbg_procurement_test/ref_bronze_inputs"
S = "workspace.wbg_ref_bronze"
spark.sql(f"CREATE SCHEMA IF NOT EXISTS {S}")
manifest = {r["staged_file"].split("/")[-1]: r
            for r in json.load(open(f"{BASE}/_manifest/ref_bronze_inputs_manifest.json"))["files"]}
file_sha = F.udf(lambda p: manifest[p.split("/")[-1]]["sha256"])
vintage = F.udf(lambda p: manifest[p.split("/")[-1]]["source_lastupdated"])

def meta_cols(df, acquisition):
    return (df.withColumn("_source_file_path", F.col("_metadata.file_path"))
              .withColumn("_source_file_size", F.col("_metadata.file_size"))
              .withColumn("_source_file_modified_at", F.col("_metadata.file_modification_time"))
              .withColumn("_source_file_sha256", file_sha("_source_file_path"))
              .withColumn("_source_vintage", vintage("_source_file_path"))
              .withColumn("_acquisition", F.lit(acquisition))
              .withColumn("_ingested_at", F.current_timestamp()))

# WDI responses: whole file as text (exact), then records exploded from the parsed VARIANT
resp = meta_cols(spark.read.option("wholetext", True).text(
    [f"{BASE}/{d}" for d in ("wdi_indicator_observations", "wdi_indicator_metadata", "wdi_source_metadata")]), "R1") \
    .withColumnRenamed("value", "raw_response").withColumn("v", F.expr("parse_json(raw_response)")) \
    .withColumn("api_page_header", F.expr("v:[0]"))
resp.drop("v").writeTo(f"{S}.wdi_api_responses").create()
obs = resp.filter(F.col("_source_file_path").contains("/wdi_indicator_observations/")) \
    .selectExpr("*", "variant_explode(v:[1]) AS (pos, key, record)")   # verify on first run (DBR 15.3+)
obs = obs.select(
    F.expr("record:indicator.id::string").alias("indicator_id"), F.expr("record:country.id::string").alias("country_id"),
    F.expr("record:countryiso3code::string").alias("countryiso3code"), F.expr("record:date::string").alias("date"),
    F.expr("record:value::string").alias("value"), "record", F.expr("v:[0].lastupdated::string").alias("_api_lastupdated"),
    *[c for c in resp.columns if c.startswith("_")]).withColumn("_row_sha256", F.sha2(F.expr("to_json(record)"), 256))
obs.writeTo(f"{S}.wdi_indicator_observations").create()

# WGI / CPIA: JSON Lines (gzip is decompressed by extension); raw line kept exactly
for folder in ("wgi_estimates", "cpia_ratings"):
    t = meta_cols(spark.read.text(f"{BASE}/{folder}"), "R3").withColumnRenamed("value", "raw_record") \
        .withColumn("record", F.expr("parse_json(raw_record)")) \
        .withColumn("indicator_id", F.expr("record:indicator.id::string")) \
        .withColumn("country_id", F.expr("record:country.id::string")) \
        .withColumn("countryiso3code", F.expr("record:countryiso3code::string")) \
        .withColumn("date", F.expr("record:date::string")).withColumn("value", F.expr("record:value::string")) \
        .withColumn("_row_sha256", F.sha2("raw_record", 256))
    t.writeTo(f"{S}.{folder}").create()

# Income classification: exact bytes + every sheet row as text cells (needs openpyxl; %pip install openpyxl if absent)
xl = meta_cols(spark.read.format("binaryFile").load(f"{BASE}/income_classification_history"), "R2")
xl.writeTo(f"{S}.income_classification_files").create()
import openpyxl
path = f"{BASE}/income_classification_history/OGHIST_2026_10_08.xlsx"
wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
rows = [(ws.title, i, [None if c is None else str(c) for c in r])
        for ws in wb.worksheets for i, r in enumerate(ws.iter_rows(values_only=True), start=1)]
spark.createDataFrame(rows, "sheet_name string, row_number int, cells array<string>") \
    .withColumn("_source_file_path", F.lit(path)).withColumn("_source_file_sha256", F.lit(manifest["OGHIST_2026_10_08.xlsx"]["sha256"])) \
    .withColumn("_source_vintage", F.lit("OGHIST_2026_10_08")).withColumn("_acquisition", F.lit("R2")) \
    .withColumn("_ingested_at", F.current_timestamp()).writeTo(f"{S}.income_classification_sheet_rows").create()
```

Notes:
- **Two lines to verify on first run:** the `v:[1]` path and `variant_explode` both need serverless DBR 15.3 or later.
  If either fails, parse the three small files (1.5 MB each) on the driver with `json.loads` and build the
  record-level rows from that.
- **The response-level table is the exact copy;** the record-level table is a convenience view of it.
- **Dates in OGHIST cells:** `str()` of a cell in the `Thresholds` sheet renders dates as `YYYY-MM-DD HH:MM:SS`. No
  other conversion is applied.

## 4. Recommended upload order

| # | Upload | Size | Why at this point |
|---|---|---|---|
| 1 | `_manifest/`, `_retrieval_metadata/` | 15 KB | Provenance first, so every later table can stamp its checksum and vintage |
| 2 | `wdi_source_metadata/`, `wdi_indicator_metadata/`, `wdi_country_metadata/` | 117 KB | Small; tests the response-level JSON path; provides the country list for checks |
| 3 | `wdi_indicator_observations/` | 4.66 MB | Main R1 data; tests the VARIANT explode |
| 4 | `income_classification_history/` | 116 KB | Binary plus sheet-row path |
| 5 | `wgi_estimates/`, `cpia_ratings/` | 0.75 MB | Reused R3 files; tests the gzip JSONL path |

After each upload, compare each file's SHA-256 (or `_source_file_sha256` against a hash of `content` for the binary
table) with the manifest.

## 5. Post-load checks

| Check | Expected |
|---|---|
| `wdi_api_responses` rows | 7 |
| `wdi_indicator_observations` rows | 20,748; 6,916 per `indicator_id`; 0 duplicate (indicator, country, date); `_api_lastupdated` = 2026-10-08 throughout |
| Distinct `country_id` per indicator | 266 (217 economies + 49 aggregates) |
| `wdi_country_metadata` rows | 296 |
| `wgi_estimates` / `cpia_ratings` rows | 34,992 / 105,336 |
| `income_classification_sheet_rows` | `Country Analytical History` 235 rows (223 economies); fiscal-year header on row 5, FY89–FY27 |
| NULL counts in the `value` key | WDI economies 2000–2025: GDP growth 5.10%, inflation 16.75%, GDP per capita 3.79% (all rows including aggregates: 4.54%, 14.05%, 3.47%) |
| No row joins the procurement tables in `wbg_bronze` | By design; joins happen in Silver |
