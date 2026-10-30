"""D-14 gate tests T1-T7 (approved 2026-10-23): can Free Edition support a separate sanitisation identity?

Synthetic data only. Every identity and object is temporary, carries a `d14` test name with a timestamp, is recorded in
a ledger file the moment it is created, and is removed in T7 (also on failure). Production objects are never written:
  * the test group is NOT named wbg_restricted_notice_text (that name would unmask production Silver text);
  * grants go only to the test group, on synthetic objects;
  * the service principal gets no admin group, no account role, no grants on wbg_* schemas.
Secrets live only in memory: never printed, logged or written. Results: silver/runs/d14_gate_tests_<ts>/.

Tests:
  T1 create a service principal              T2 OAuth M2M secret + Databricks Connect Serverless as the SP
  T3 test group; membership functions        T4 column mask + grants on a synthetic table (SP raw, owner masked)
  T5 Bronze-style raw_record redaction mask   T6 restricted volume: group ownership/READ VOLUME vs admin/owner access
  T7 cleanup + unchanged-production checks

Usage (from procurement-intelligence/):  python -m silver.notices.d14_gate_tests
"""
from __future__ import annotations

from app_config.paths import COHORT_MANIFEST
import hashlib
import json
import re
import subprocess
import sys
import tempfile
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from databricks.connect import DatabricksSession
from databricks.sdk import WorkspaceClient
from databricks.sdk.core import Config
from databricks.sdk.service import iam

from silver.common.session import DEFAULT_PROFILE, ensure_databricks_cli_on_path, get_spark

RUNS = Path(__file__).resolve().parents[1] / "runs"
COHORT = COHORT_MANIFEST
PH = "[RESTRICTED-TEST]"
JSON_TEXT_RE = r'"notice_text": "(?:[^"\\]|\\.)*"'          # a JSON string value, escape-aware
JSON_TEXT_REPL = '"notice_text": "' + PH + '"'


def sql_lit(s: str) -> str:
    """Single-quoted Spark SQL literal (backslash escapes on by default)."""
    return "'" + s.replace("\\", "\\\\").replace("'", "\\'") + "'"


def synthetic_records() -> list[str]:
    recs = [
        {"id": "OP99000001", "notice_type": "Contract Award", "project_id": "P000001",
         "notice_text": "<p>Contact: test.person@example.org, Tel: +00 000 000 0000</p>"},
        {"id": "OP99000002", "notice_type": "Invitation for Bids", "project_id": "P000001",
         "notice_text": 'Quote "inside" and back\\slash, unicode é line º № end'},
        {"id": "OP99000003", "notice_type": "General Procurement Notice", "project_id": "P000002", "notice_text": None},
        {"id": "OP99000004", "notice_type": "Contract Award", "project_id": "P000002", "notice_text": "",
         "bid_description": 'has "notice_text": "decoy" inside another field'},
    ]
    return [json.dumps(r, ensure_ascii=False) for r in recs]


class GateStop(Exception):
    """Raised to stop the gate tests deliberately (cleanup still runs)."""


class Gate:
    def __init__(self) -> None:
        self.ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self.run_dir = RUNS / f"d14_gate_tests_{self.ts}"
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.ledger: list[dict] = []
        self.results: dict = {"run": self.ts, "tests": {}, "notes": []}
        low = self.ts.lower()
        self.names = {"sp": f"wbg-d14-test-sanitizer-{low}", "group": f"wbg_d14_test_restricted_{low}",
                      "tbl": f"workspace.default.zz_wbg_d14_gate_{low}_t", "tbl_bronze": f"workspace.default.zz_wbg_d14_gate_{low}_bronze",
                      "tbl_ungranted": f"workspace.default.zz_wbg_d14_gate_{low}_ungranted",
                      "fn_acct": f"workspace.default.zz_wbg_d14_gate_{low}_mask_acct", "fn_ws": f"workspace.default.zz_wbg_d14_gate_{low}_mask_ws",
                      "fn_json": f"workspace.default.zz_wbg_d14_gate_{low}_mask_json",
                      "schema": f"workspace.zz_wbg_d14_gate_{low}", "volume": f"workspace.zz_wbg_d14_gate_{low}.restricted_raw"}
        ensure_databricks_cli_on_path()
        self.w = WorkspaceClient(profile=DEFAULT_PROFILE)
        self.host = Config(profile=DEFAULT_PROFILE).host
        self.me = get_spark()
        self.sp_session: Any = None   # set in T2
        self.sp_id: Any = None        # set in T1 (SCIM id)
        self.sp_app_id: Any = None    # set in T1 (OAuth client id; not a secret)
        self.secret_id: Any = None    # set in T2 (id only; the secret value is never kept)
        self.group_id: Any = None     # set in T3
        self.mask_fn: Any = None      # set in T3

    # ---------- bookkeeping ----------
    def created(self, kind: str, name: str, **extra) -> None:
        self.ledger.append({"kind": kind, "name": name, **extra})
        (self.run_dir / "created_objects.json").write_text(json.dumps(self.ledger, indent=1))

    def rec(self, test: str, passed: bool | None, observed, note: str = "") -> None:
        self.results["tests"].setdefault(test, {"checks": []})["checks"].append({"passed": passed, "observed": observed, "note": note})
        mark = "ok  " if passed else ("info" if passed is None else "FAIL")
        print(f"[{mark}] {test}: {observed}" + (f"  ({note})" if note else ""), flush=True)

    def stop(self, why: str) -> None:
        self.results["stopped"] = why
        raise GateStop(why)

    def baseline(self) -> dict:
        b: dict = {"tables": {}}
        for sc in ["wbg_bronze", "wbg_ref_bronze", "wbg_silver"]:
            for t in [r.tableName for r in self.me.sql(f"SHOW TABLES IN workspace.{sc}").collect()]:
                b["tables"][f"{sc}.{t}"] = self.me.sql(f"DESCRIBE HISTORY workspace.{sc}.{t} LIMIT 1").collect()[0].version
        b["column_masks"] = sorted(tuple(r) for r in self.me.sql(
            "SELECT table_schema, table_name, column_name, mask_name FROM workspace.information_schema.column_masks").collect())
        b["schemas"] = sorted(r[0] for r in self.me.sql("SHOW SCHEMAS IN workspace").collect())
        b["default_tables"] = sorted(r.tableName for r in self.me.sql("SHOW TABLES IN workspace.default").collect())
        b["service_principals"] = sorted(sp.display_name or "" for sp in self.w.service_principals.list())
        b["groups"] = sorted(g.display_name or "" for g in self.w.groups.list())
        b["cohort_sha256"] = hashlib.sha256(COHORT.read_bytes()).hexdigest()
        return b

    # ---------- tests ----------
    def precheck(self) -> None:
        me = self.w.current_user.me()
        groups = sorted(str(g.display) for g in (me.groups or []))
        self.rec("P0", "admins" in groups, {"current_user_groups": groups}, "workspace admin can manage identities")
        self.base = self.baseline()
        (self.run_dir / "baseline_before.json").write_text(json.dumps(self.base, indent=1, default=str))
        clash = [n for n in (self.names["sp"],) if n in self.base["service_principals"]] + \
                [n for n in (self.names["group"], "wbg_restricted_notice_text") if n in self.base["groups"]]
        self.rec("P0", not clash, {"name_collisions": clash, "existing_service_principals": len(self.base["service_principals"]),
                                   "groups": self.base["groups"]})
        if clash:
            self.stop("name collision or production group already exists")

    def t1(self) -> None:
        sp = self.w.service_principals.create(display_name=self.names["sp"], active=True)
        self.sp_id, self.sp_app_id = sp.id, sp.application_id
        self.created("service_principal", self.names["sp"], id=sp.id)
        got = self.w.service_principals.get(str(sp.id))
        ents = sorted(str(e.value) for e in (got.entitlements or []))
        grps = sorted(str(g.display) for g in (got.groups or []))
        roles = sorted(str(r.value) for r in (got.roles or []))
        self.rec("T1", bool(sp.id and sp.application_id), {"created": True, "entitlements": ents, "groups": grps, "roles": roles})
        self.rec("T1", "admins" not in grps and not roles, {"admin_or_roles": "admins" in grps or bool(roles)}, "least privilege at creation")

    def _sp_session(self, secret: str):
        empty = self.run_dir / "empty.databrickscfg"  # isolate from ~/.databrickscfg profiles
        empty.write_text("")
        cfg = Config(host=self.host, client_id=self.sp_app_id, client_secret=secret, auth_type="oauth-m2m", config_file=str(empty),
                     serverless_compute_id="auto")  # serverless must be set here: .serverless() conflicts with sdkConfig
        return DatabricksSession.Builder().sdkConfig(cfg).create()  # fresh builder: the shared one carries the profile config

    def t2(self) -> None:
        try:
            s = self.w.service_principal_secrets_proxy.create(service_principal_id=self.sp_id)
        except Exception as e:  # noqa: BLE001
            self.rec("T2", False, f"secret creation refused: {type(e).__name__}: {str(e).splitlines()[0][:200]}")
            self.stop("cannot create an OAuth secret for the service principal")
        self.secret_id = s.id
        self.created("sp_secret", f"secret of {self.names['sp']}", id=s.id)
        secret = str(s.secret or "")
        self.rec("T2", bool(secret), {"secret_created": True, "secret_value": "<never printed or stored>"})
        last = None
        for attempt in range(6):
            try:
                self.sp_session = self._sp_session(secret)
                who = self.sp_session.sql("SELECT current_user() u, is_member('admins') a").collect()[0]
                self.rec("T2", who.u == self.sp_app_id, {"databricks_connect_serverless_as_sp": True, "current_user_is_sp_application_id": who.u == self.sp_app_id,
                                                         "sp_in_admins": who.a, "attempts": attempt + 1})
                break
            except Exception as e:  # noqa: BLE001
                last = f"{type(e).__name__}: {str(e).splitlines()[0][:220]}"
                time.sleep(10)
        else:
            self.rec("T2", False, {"databricks_connect_as_sp": False, "last_error": last})
            self.stop("Databricks Connect cannot authenticate as the service principal")
        del secret
        # least privilege: the SP must not reach production schemas (metadata-only probes, LIMIT 0)
        for t in ["workspace.wbg_bronze.bronze_procurement_notices_api", "workspace.wbg_silver.procurement_notice_details",
                  "workspace.wbg_silver.contract_award_lines"]:
            try:
                self.sp_session.sql(f"SELECT 1 FROM {t} LIMIT 0").collect()
                self.rec("T2", False, {t: "ACCESSIBLE"}, "unexpected: SP can query a production table")
                self.stop("service principal unexpectedly has access to production tables")
            except GateStop:
                raise
            except Exception as e:  # noqa: BLE001
                self.rec("T2", True, {t: "denied", "error_class": type(e).__name__, "reason": str(e).splitlines()[0][:120]})

    def t3(self) -> None:
        g = self.w.groups.create(display_name=self.names["group"])
        self.group_id = str(g.id)
        self.created("group", self.names["group"], id=self.group_id)
        self.w.groups.patch(self.group_id, operations=[iam.Patch(op=iam.PatchOp.ADD, path="members", value=[{"value": self.sp_id}])],
                            schemas=[iam.PatchSchema.URN_IETF_PARAMS_SCIM_API_MESSAGES_2_0_PATCH_OP])
        members = [m.value for m in (self.w.groups.get(self.group_id).members or [])]
        self.rec("T3", members == [self.sp_id], {"group_created": True, "members": "only the test SP" if members == [self.sp_id] else len(members)})
        q = f"SELECT is_account_group_member('{self.names['group']}') a, is_member('{self.names['group']}') w"
        seen: Any = None
        for _ in range(12):
            seen = self.sp_session.sql(q).collect()[0]
            if seen.a or seen.w:
                break
            time.sleep(10)
        mine = self.me.sql(q).collect()[0]
        self.rec("T3", bool(seen.a or seen.w), {"sp_is_account_group_member": seen.a, "sp_is_member": seen.w})
        self.rec("T3", not (mine.a or mine.w), {"owner_is_account_group_member": mine.a, "owner_is_member": mine.w})
        self.mask_fn = "is_account_group_member" if seen.a else "is_member"
        self.results["membership_function_that_works"] = self.mask_fn

    def t4(self) -> None:
        n, grp = self.names, self.names["group"]
        for fn, check in ((n["fn_acct"], "is_account_group_member"), (n["fn_ws"], "is_member")):
            self.me.sql(f"CREATE FUNCTION {fn}(t STRING) RETURNS STRING RETURN CASE WHEN t IS NULL THEN NULL "
                        f"WHEN {check}('{grp}') THEN t ELSE '{PH}' END")
            self.created("function", fn)
        fn = n["fn_acct"] if self.mask_fn == "is_account_group_member" else n["fn_ws"]
        self.me.sql(f"CREATE TABLE {n['tbl']} (id INT, notice_text STRING MASK {fn}) USING DELTA")
        self.created("table", n["tbl"])
        self.me.sql(f"INSERT INTO {n['tbl']} VALUES (1, 'SYNTHETIC test.person@example.org'), (2, NULL)")
        self.me.sql(f"CREATE TABLE {n['tbl_ungranted']} (id INT) USING DELTA")
        self.created("table", n["tbl_ungranted"])
        self.me.sql(f"GRANT SELECT ON TABLE {n['tbl']} TO `{grp}`")
        self.created("grant", f"SELECT ON {n['tbl']} TO {grp}")
        mine = {r.id: r.notice_text for r in self.me.table(n["tbl"]).collect()}
        self.rec("T4", mine == {1: PH, 2: None}, {"owner_reads": mine})
        sp: Any = None
        for _ in range(6):
            try:
                sp = {r.id: r.notice_text for r in self.sp_session.table(n["tbl"]).collect()}
                break
            except Exception as e:  # noqa: BLE001
                sp = f"{type(e).__name__}: {str(e).splitlines()[0][:160]}"
                time.sleep(10)
        self.rec("T4", sp == {1: "SYNTHETIC test.person@example.org", 2: None}, {"sp_reads_raw_synthetic": sp == {1: "SYNTHETIC test.person@example.org", 2: None},
                                                                                  "sp_result": sp if not isinstance(sp, dict) else "rows ok"})
        try:
            self.sp_session.sql(f"SELECT 1 FROM {n['tbl_ungranted']} LIMIT 0").collect()
            self.rec("T4", False, {"ungranted_table": "ACCESSIBLE"}, "grants are not least-privilege")
        except Exception as e:  # noqa: BLE001
            self.rec("T4", True, {"ungranted_table": "denied", "reason": str(e).splitlines()[0][:100]})
        sp_grants = [tuple(r)[:3] for r in self.me.sql(f"SHOW GRANTS ON TABLE {n['tbl']}").collect()]
        self.rec("T4", None, {"grants_on_test_table": sp_grants})

    def t5(self) -> None:
        n, grp = self.names, self.names["group"]
        lines = synthetic_records()
        self.me.sql(f"CREATE TABLE {n['tbl_bronze']} (raw_record STRING) USING DELTA")
        self.created("table", n["tbl_bronze"])
        self.me.createDataFrame([(x,) for x in lines], "raw_record string").write.mode("append").saveAsTable(n["tbl_bronze"])
        v_before = self.me.sql(f"DESCRIBE HISTORY {n['tbl_bronze']} LIMIT 1").collect()[0].version
        check = self.mask_fn
        self.me.sql(f"CREATE FUNCTION {n['fn_json']}(raw STRING) RETURNS STRING RETURN CASE WHEN {check}('{grp}') THEN raw "
                    f"ELSE regexp_replace(raw, {sql_lit(JSON_TEXT_RE)}, {sql_lit(JSON_TEXT_REPL)}) END")
        self.created("function", n["fn_json"])
        self.me.sql(f"ALTER TABLE {n['tbl_bronze']} ALTER COLUMN raw_record SET MASK {n['fn_json']}")
        self.created("mask", f"{n['tbl_bronze']}.raw_record")
        self.me.sql(f"GRANT SELECT ON TABLE {n['tbl_bronze']} TO `{grp}`")
        self.created("grant", f"SELECT ON {n['tbl_bronze']} TO {grp}")
        v_after = self.me.sql(f"DESCRIBE HISTORY {n['tbl_bronze']} LIMIT 1").collect()[0].version
        self.rec("T5", v_before == v_after, {"delta_version_before_set_mask": v_before, "after": v_after})
        owner_view = [r[0] for r in self.me.table(n["tbl_bronze"]).collect()]
        py_redacted = [re.sub(JSON_TEXT_RE, JSON_TEXT_REPL, x) for x in lines]
        parsed = [json.loads(x) for x in owner_view]
        src = {json.loads(x)["id"]: json.loads(x) for x in lines}
        other_fields_equal = all({k: v for k, v in p.items() if k != "notice_text"} == {k: v for k, v in src[p["id"]].items() if k != "notice_text"} for p in parsed)
        text_state = sorted((p["id"], "placeholder" if p.get("notice_text") == PH else ("null" if p.get("notice_text") is None else "OTHER")) for p in parsed)
        self.rec("T5", True, {"owner_view_valid_json": len(parsed) == len(lines)})
        self.rec("T5", other_fields_equal, {"other_fields_unchanged_incl_decoy_field": other_fields_equal})
        self.rec("T5", text_state == [("OP99000001", "placeholder"), ("OP99000002", "placeholder"), ("OP99000003", "null"), ("OP99000004", "placeholder")],
                 {"notice_text_for_owner": text_state})
        self.rec("T5", sorted(owner_view) == sorted(py_redacted), {"python_redaction_parity": sorted(owner_view) == sorted(py_redacted)},
                 "redacted comparison for verifiers is reproducible in Python")
        sp_view: Any = None
        for _ in range(6):
            try:
                sp_view = [r[0] for r in self.sp_session.table(n["tbl_bronze"]).collect()]
                break
            except Exception as e:  # noqa: BLE001
                sp_view = f"{type(e).__name__}: {str(e).splitlines()[0][:160]}"
                time.sleep(10)
        ok = isinstance(sp_view, list) and sorted(hashlib.sha256(x.encode()).hexdigest() for x in sp_view) == sorted(hashlib.sha256(x.encode()).hexdigest() for x in lines)
        self.rec("T5", ok, {"sp_reads_exact_raw_records": ok})
        try:
            self.me.sql(f"SELECT count(*) FROM {n['tbl_bronze']} VERSION AS OF {v_before}").collect()
            self.rec("T5", None, {"time_travel": "allowed"})
        except Exception as e:  # noqa: BLE001
            self.rec("T5", None, {"time_travel": "refused" if "COLUMN_MASKS_FEATURE_NOT_SUPPORTED" in str(e) else str(e)[:120]}, "expected with masks")

    def t6(self) -> None:
        n, grp = self.names, self.names["group"]
        self.me.sql(f"CREATE SCHEMA {n['schema']}")
        self.created("schema", n["schema"])
        self.me.sql(f"CREATE VOLUME {n['volume']}")
        self.created("volume", n["volume"])
        vol_path = "/Volumes/" + n["volume"].replace(".", "/")
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
            f.write("\n".join(synthetic_records()) + "\n")
            local = f.name
        subprocess.run(["databricks", "fs", "cp", local, f"dbfs:{vol_path}/synthetic.jsonl", "-p", DEFAULT_PROFILE], check=True, capture_output=True)
        Path(local).unlink()
        self.created("file", f"{vol_path}/synthetic.jsonl")
        self.me.sql(f"GRANT USE SCHEMA ON SCHEMA {n['schema']} TO `{grp}`")
        self.me.sql(f"GRANT READ VOLUME ON VOLUME {n['volume']} TO `{grp}`")
        self.created("grant", f"USE SCHEMA + READ VOLUME on {n['volume']} TO {grp}")

        def can_read(sess, label):
            try:
                c = sess.read.format("binaryFile").load(f"{vol_path}/synthetic.jsonl").select("content").collect()
                return f"{label}: read ok ({len(bytes(c[0][0]))} bytes)"
            except Exception as e:  # noqa: BLE001
                return f"{label}: denied ({str(e).splitlines()[0][:100]})"

        sp_before: Any = None
        for _ in range(6):
            sp_before = can_read(self.sp_session, "sp")
            if "read ok" in sp_before:
                break
            time.sleep(10)
        self.rec("T6", "read ok" in sp_before, {"sp_with_read_volume_grant": sp_before})
        self.me.sql(f"ALTER VOLUME {n['volume']} OWNER TO `{grp}`")
        self.created("ownership", f"{n['volume']} owned by {grp}")
        owner_after = self.me.sql(f"DESCRIBE VOLUME {n['volume']}").collect()
        self.rec("T6", None, {"volume_owner_now": [r for r in owner_after if "owner" in str(r).lower()][:1].__str__()[:160]})
        me_after = can_read(self.me, "you (admin, schema owner, catalog-owner group member; no longer volume owner)")
        self.rec("T6", None, {"owner_access_after_transfer": me_after},
                 "denied = real restriction; read ok = guardrail only (admin/parent-owner access remains)")
        self.results["t6_admin_can_still_read_volume"] = "read ok" in me_after

    def cleanup(self) -> None:
        n = self.names
        steps = []

        def attempt(label, fn):
            try:
                fn()
                steps.append((label, "ok"))
            except Exception as e:  # noqa: BLE001
                steps.append((label, f"{type(e).__name__}: {str(e).splitlines()[0][:140]}"))

        kinds = {o["kind"] for o in self.ledger}
        if "volume" in kinds:
            attempt("drop volume (you)", lambda: self.me.sql(f"DROP VOLUME IF EXISTS {n['volume']}").collect())
            if steps[-1][1] != "ok" and self.sp_session is not None:
                attempt("drop volume (SP via group ownership)", lambda: self.sp_session.sql(f"DROP VOLUME IF EXISTS {n['volume']}").collect())
        if "schema" in kinds:
            attempt("drop schema", lambda: self.me.sql(f"DROP SCHEMA IF EXISTS {n['schema']} CASCADE").collect())
        for t in (n["tbl"], n["tbl_bronze"], n["tbl_ungranted"]):
            if any(o["name"] == t for o in self.ledger):
                attempt(f"drop table {t.split('.')[-1]}", lambda t=t: self.me.sql(f"DROP TABLE IF EXISTS {t}").collect())
        for fn in (n["fn_acct"], n["fn_ws"], n["fn_json"]):
            if any(o["name"] == fn for o in self.ledger):
                attempt(f"drop function {fn.split('.')[-1]}", lambda fn=fn: self.me.sql(f"DROP FUNCTION IF EXISTS {fn}").collect())
        if self.sp_session is not None:
            attempt("stop SP session", lambda: self.sp_session.stop())
        if self.group_id:
            attempt("delete group", lambda: self.w.groups.delete(self.group_id))
        if self.secret_id and self.sp_id:
            attempt("delete SP secret", lambda: self.w.service_principal_secrets_proxy.delete(service_principal_id=self.sp_id, secret_id=self.secret_id))
        if self.sp_id:
            attempt("delete service principal", lambda: self.w.service_principals.delete(self.sp_id))
        self.results["cleanup_steps"] = steps
        for s in steps:
            print(f"[cleanup] {s[0]}: {s[1]}", flush=True)
        after = self.baseline()
        (self.run_dir / "baseline_after.json").write_text(json.dumps(after, indent=1, default=str))
        diff = {k: (self.base[k], after[k]) for k in self.base if self.base[k] != after[k]}
        self.rec("T7", not diff, {"production_and_workspace_state_identical_to_baseline": not diff, "differences": list(diff)})
        leftovers = {"tables": [t for t in after["default_tables"] if "d14_gate" in t], "schemas": [s for s in after["schemas"] if "d14_gate" in s],
                     "service_principals": [s for s in after["service_principals"] if "d14-test" in s], "groups": [g for g in after["groups"] if "d14_test" in g]}
        funcs = [r[0] for r in self.me.sql("SHOW USER FUNCTIONS IN workspace.default").collect() if "d14_gate" in r[0]]
        leftovers["functions"] = funcs
        self.rec("T7", not any(leftovers.values()), {"leftovers": leftovers})

    def run(self) -> int:
        try:
            self.precheck()
            for name in ("t1", "t2", "t3", "t4", "t5", "t6"):
                print(f"--- {name.upper()}", flush=True)
                getattr(self, name)()
        except Exception as e:  # noqa: BLE001
            self.results["error"] = f"{type(e).__name__}: {str(e).splitlines()[0][:300]}"
            self.results["traceback_tail"] = traceback.format_exc().splitlines()[-3:]
            print("ERROR:", self.results["error"], flush=True)
        finally:
            print("--- T7 cleanup", flush=True)
            if hasattr(self, "base"):
                self.cleanup()
            (self.run_dir / "gate_results.json").write_text(json.dumps(self.results, indent=1, default=str))
            print(f"results: {self.run_dir / 'gate_results.json'}", flush=True)
        return 0


if __name__ == "__main__":
    sys.exit(Gate().run())
