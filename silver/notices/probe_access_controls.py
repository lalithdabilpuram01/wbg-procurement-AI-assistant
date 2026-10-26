"""D-14 access-control probe: what can this Databricks Free Edition workspace actually enforce? (synthetic data only)

Creates temporary objects in workspace.default (prefix zz_wbg_d14_probe_), holding two synthetic rows, tests a Unity
Catalog column mask through every read path the Silver jobs use, records the results, and drops the objects.
No Bronze or Silver object is touched.

Tests:
  * column mask declared in CREATE TABLE (so no row is ever stored without the mask)
  * masked for the current user (also the owner) via SQL, DataFrame API, toPandas, sha2(), CTAS copy; time travel masked or refused
  * NULL stays NULL (missing text is distinguishable from restricted text)
  * the managed-table storage location is not exposed (so no path-based read is possible), or a path read is refused
  * mask visible in information_schema.column_masks
  * group membership functions (is_account_group_member / is_member) for the restricted group
  * the stored value is intact under the mask (pass-through mask), and whether the owner can drop the mask

Usage (from procurement-intelligence/):
  python -m silver.notices.probe_access_controls
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from silver.common.session import DEFAULT_PROFILE, get_spark

RUNS = Path(__file__).resolve().parents[1] / "runs"
GROUP = "wbg_restricted_notice_text"
PLACEHOLDER = f"[RESTRICTED: requires membership of {GROUP}]"
SYNTHETIC = "Synthetic contact: probe.person@example.org, Tel: +00 000 000 0000"


def main() -> int:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = RUNS / f"d14_access_probe_{ts}"
    run_dir.mkdir(parents=True, exist_ok=True)
    spark = get_spark(DEFAULT_PROFILE)
    p = f"workspace.default.zz_wbg_d14_probe_{ts.lower()}"
    fn, fn_pass, tbl, cp = f"{p}_mask", f"{p}_passthrough", f"{p}_t", f"{p}_copy"
    res: dict = {"probe_id": ts, "objects": [fn, fn_pass, tbl, cp], "group": GROUP, "tests": {}}
    t = res["tests"]

    def rec(name: str, ok: bool, observed) -> None:
        t[name] = {"passed": bool(ok), "observed": observed}
        print(f"[{'ok  ' if ok else 'FAIL'}] {name}: {observed}")

    def attempt(name: str, f, expect_error: bool = False) -> None:
        try:
            out = f()
            rec(name, not expect_error, out if not expect_error else f"no error: {str(out)[:200]}")
        except Exception as e:  # noqa: BLE001
            rec(name, expect_error, f"{type(e).__name__}: {str(e).splitlines()[0][:200]}")

    try:
        g = spark.sql(f"SELECT is_account_group_member('{GROUP}') AS acct, is_member('{GROUP}') AS ws").collect()[0]
        rec("current_user_not_in_restricted_group", not g.acct and not g.ws, {"is_account_group_member": g.acct, "is_member": g.ws})
        attempt("show_groups_available", lambda: [r[0] for r in spark.sql("SHOW GROUPS").collect()][:10])
        spark.sql(f"CREATE FUNCTION {fn}(t STRING) RETURNS STRING RETURN CASE WHEN t IS NULL THEN NULL "
                  f"WHEN is_account_group_member('{GROUP}') THEN t ELSE '{PLACEHOLDER}' END")
        spark.sql(f"CREATE TABLE {tbl} (id INT, notice_text STRING MASK {fn}) USING DELTA")
        rec("mask_declared_in_create_table", True, "CREATE TABLE ... MASK accepted")
        spark.sql(f"INSERT INTO {tbl} VALUES (1, '{SYNTHETIC}'), (2, NULL)")
        sql_rows = {r.id: r.notice_text for r in spark.sql(f"SELECT * FROM {tbl}").collect()}
        rec("sql_read_masked", sql_rows == {1: PLACEHOLDER, 2: None}, sql_rows)
        df_rows = {r.id: r.notice_text for r in spark.table(tbl).collect()}
        rec("dataframe_read_masked", df_rows == {1: PLACEHOLDER, 2: None}, df_rows)
        pdf = spark.table(tbl).toPandas()
        rec("topandas_masked", SYNTHETIC not in pdf.notice_text.fillna("").tolist(), pdf.notice_text.fillna("<NULL>").tolist())
        h = spark.sql(f"SELECT sha2(notice_text, 256) = sha2('{PLACEHOLDER}', 256) AS m FROM {tbl} WHERE id = 1").collect()[0].m
        rec("derived_values_use_masked_text (sha2)", bool(h), h)
        like = spark.sql(f"SELECT count(*) FROM {tbl} WHERE notice_text LIKE '%example.org%'").collect()[0][0]
        rec("filter_on_raw_text_not_possible", like == 0, like)
        try:
            tt = {r.id: r.notice_text for r in spark.sql(f"SELECT * FROM {tbl} VERSION AS OF 1").collect()}
            rec("time_travel_masked_or_refused", tt == {1: PLACEHOLDER, 2: None}, tt)
        except Exception as e:  # noqa: BLE001  (refusal is also safe: no unmasked historical read)
            rec("time_travel_masked_or_refused", "COLUMN_MASKS_FEATURE_NOT_SUPPORTED" in str(e), str(e).splitlines()[0][:200])
        spark.sql(f"CREATE TABLE {cp} AS SELECT * FROM {tbl}")
        cpr = {r.id: r.notice_text for r in spark.table(cp).collect()}
        rec("ctas_copy_contains_only_masked_text", cpr == {1: PLACEHOLDER, 2: None}, cpr)
        loc = spark.sql(f"DESCRIBE DETAIL {tbl}").collect()[0].location
        rec("storage_location_not_exposed", not loc, loc or "<empty>")
        if loc:
            attempt("path_based_read_refused", lambda: spark.read.format("delta").load(loc).collect(), expect_error=True)
        else:
            rec("path_based_read_refused", True, "not applicable: managed-table location is not exposed to this identity")
        cm = spark.sql("SELECT table_name, column_name, mask_name FROM workspace.information_schema.column_masks "
                       f"WHERE table_schema = 'default' AND table_name = '{tbl.split('.')[-1]}'").collect()
        rec("mask_listed_in_information_schema", len(cm) == 1, [tuple(r) for r in cm])
        # positive control and limitation: pass-through mask shows the stored value is intact; the owner can change masks
        spark.sql(f"CREATE FUNCTION {fn_pass}(t STRING) RETURNS STRING RETURN t")
        spark.sql(f"ALTER TABLE {tbl} ALTER COLUMN notice_text SET MASK {fn_pass}")
        raw = spark.sql(f"SELECT notice_text FROM {tbl} WHERE id = 1").collect()[0][0]
        rec("stored_value_intact_under_mask (positive control)", raw == SYNTHETIC, "synthetic value returned" if raw == SYNTHETIC else raw)
        attempt("owner_can_drop_mask (limitation)", lambda: spark.sql(f"ALTER TABLE {tbl} ALTER COLUMN notice_text DROP MASK").collect() or "dropped")
        hist = [r.operation for r in spark.sql(f"DESCRIBE HISTORY {tbl}").collect()]
        t["limitation:mask_changes_not_in_delta_history"] = {"passed": True, "observed": hist,
                                                              "note": "SET/DROP MASK do not appear in DESCRIBE HISTORY; rely on the table definition checks"}
        print(f"[info] mask changes visible in Delta history: {any('MASK' in o or 'ALTER' in o for o in hist)} ({hist})")
    except Exception as e:  # noqa: BLE001
        rec("probe_aborted", False, f"{type(e).__name__}: {str(e).splitlines()[0][:300]}")
    finally:
        for stmt in (f"DROP TABLE IF EXISTS {cp}", f"DROP TABLE IF EXISTS {tbl}", f"DROP FUNCTION IF EXISTS {fn}", f"DROP FUNCTION IF EXISTS {fn_pass}"):
            try:
                spark.sql(stmt)
            except Exception as e:  # noqa: BLE001
                print(f"cleanup failed: {stmt}: {e}")
        left = [r.tableName for r in spark.sql("SHOW TABLES IN workspace.default").collect() if r.tableName.startswith("zz_wbg_d14_probe_")]
        res["cleanup_remaining_tables"] = left
    enforce = ["current_user_not_in_restricted_group", "mask_declared_in_create_table", "sql_read_masked", "dataframe_read_masked", "topandas_masked",
               "derived_values_use_masked_text (sha2)", "filter_on_raw_text_not_possible", "time_travel_masked_or_refused", "ctas_copy_contains_only_masked_text",
               "storage_location_not_exposed", "path_based_read_refused", "mask_listed_in_information_schema", "stored_value_intact_under_mask (positive control)"]
    res["column_mask_enforced"] = all(t.get(k, {}).get("passed") for k in enforce) and not res["cleanup_remaining_tables"]
    (run_dir / "access_control_probe.json").write_text(json.dumps(res, indent=1, default=str))
    print(f"\ncolumn_mask_enforced={res['column_mask_enforced']}  report: {run_dir / 'access_control_probe.json'}")
    return 0 if res["column_mask_enforced"] else 1


if __name__ == "__main__":
    sys.exit(main())
