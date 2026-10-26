"""D-14 gate tests T3b-T6b (approved 2026-10-23): direct service-principal grants + identity-keyed column masks.

Synthetic data only; nothing production is granted, masked or written. All objects live in a TEMPORARY CATALOG created
for the run (so no grant touches the production `workspace` catalog), and two temporary non-admin service principals:
  reader        : granted SELECT and on the mask allow-list      -> must see raw synthetic text
  granted_only  : granted SELECT but NOT on the allow-list       -> must see the placeholder
  you (owner)   : owner of everything, not on the allow-list     -> must see the placeholder
Synthetic text carries a random sentinel; outputs report booleans and counts only (never the sentinel, never secrets).
Every created object is written to a ledger immediately; cleanup runs in `finally`; state is compared with a baseline.

Usage (from procurement-intelligence/):  python -m silver.notices.d14_gate_tests_b
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets as pysecrets
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
from silver.notices.d14_gate_tests import JSON_TEXT_RE, JSON_TEXT_REPL, PH, sql_lit

RUNS = Path(__file__).resolve().parents[1] / "runs"
COHORT = Path(__file__).resolve().parents[2] / "cohort" / "poc_cohort_2000_manifest.csv"
PATCH = [iam.PatchSchema.URN_IETF_PARAMS_SCIM_API_MESSAGES_2_0_PATCH_OP]
PROD_PROBES = ["workspace.wbg_bronze.bronze_procurement_notices_api", "workspace.wbg_silver.procurement_notice_details",
               "workspace.wbg_silver.contract_award_lines"]


class GateStop(Exception):
    """Deliberate stop (cleanup still runs)."""


def err(e: Exception) -> str:
    m = re.search(r"\[([A-Z_]+(?:\.[A-Z_]+)?)\]", str(e))
    return m.group(1) if m else f"{type(e).__name__}: {str(e).splitlines()[0][:90]}"


class GateB:
    def __init__(self) -> None:
        self.ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        low = self.ts.lower()
        self.run_dir = RUNS / f"d14_gate_tests_b_{self.ts}"
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.cat = f"zz_wbg_d14_gateb_{low}"
        sch = f"{self.cat}.s"
        self.n = {"schema": sch, "restricted": f"{sch}.restricted", "restricted_inline": f"{sch}.restricted_inline",
                  "ungranted": f"{sch}.ungranted", "copy_owner": f"{sch}.copy_owner", "bronze": f"{sch}.bronze_like",
                  "constrained": f"{sch}.constrained", "view": f"{sch}.v_restricted",
                  "gov": f"{sch}.is_restricted_reader", "mask_gov": f"{sch}.mask_gov", "mask_inline": f"{sch}.mask_inline",
                  "mask_json": f"{sch}.mask_json", "volume": f"{sch}.raw_files"}
        self.vol_path = f"/Volumes/{self.cat}/s/raw_files"
        self.sp_names = {"reader": f"wbg-d14-test-reader-{low}", "granted_only": f"wbg-d14-test-granted-only-{low}"}
        self.sentinel = "SYNTH" + pysecrets.token_hex(8)
        self.raw_text = f"Synthetic contact synth.person@example.org Tel +00 000 000 0000 {self.sentinel}"
        self.ledger: list[dict] = []
        self.results: dict = {"run": self.ts, "tests": {}}
        ensure_databricks_cli_on_path()
        self.w = WorkspaceClient(profile=DEFAULT_PROFILE)
        self.host = Config(profile=DEFAULT_PROFILE).host
        self.me = get_spark()
        self.me_user: str = self.me.sql("SELECT current_user()").collect()[0][0]
        self.sp: dict[str, dict[str, Any]] = {}

    # ---------- bookkeeping ----------
    def created(self, kind: str, name: str, **extra) -> None:
        self.ledger.append({"kind": kind, "name": name, **extra})
        (self.run_dir / "created_objects.json").write_text(json.dumps(self.ledger, indent=1))

    def rec(self, test: str, check: str, passed: bool | None, observed: Any = None) -> None:
        self.results["tests"].setdefault(test, []).append({"check": check, "passed": passed, "observed": observed})
        mark = "ok  " if passed else ("info" if passed is None else "FAIL")
        print(f"[{mark}] {test} {check}: {observed}", flush=True)

    def baseline(self) -> dict:
        b: dict = {"tables": {}}
        for sc in ["wbg_bronze", "wbg_ref_bronze", "wbg_silver"]:
            for t in [r.tableName for r in self.me.sql(f"SHOW TABLES IN workspace.{sc}").collect()]:
                b["tables"][f"{sc}.{t}"] = self.me.sql(f"DESCRIBE HISTORY workspace.{sc}.{t} LIMIT 1").collect()[0].version
        b["column_masks"] = sorted(tuple(r) for r in self.me.sql(
            "SELECT table_schema, table_name, column_name, mask_name FROM workspace.information_schema.column_masks").collect())
        b["catalogs"] = sorted(r[0] for r in self.me.sql("SHOW CATALOGS").collect())
        b["workspace_schemas"] = sorted(r[0] for r in self.me.sql("SHOW SCHEMAS IN workspace").collect())
        b["workspace_catalog_grants"] = sorted(str(tuple(r)[:3]) for r in self.me.sql("SHOW GRANTS ON CATALOG workspace").collect())
        b["service_principals"] = sorted(sp.display_name or "" for sp in self.w.service_principals.list())
        b["groups"] = sorted(g.display_name or "" for g in self.w.groups.list())
        b["cohort_sha256"] = hashlib.sha256(COHORT.read_bytes()).hexdigest()
        return b

    # ---------- helpers ----------
    def session(self, who: str):
        sp = self.sp[who]
        empty = self.run_dir / "empty.databrickscfg"
        empty.write_text("")
        cfg = Config(host=self.host, client_id=sp["app_id"], client_secret=sp["secret"], auth_type="oauth-m2m",
                     config_file=str(empty), serverless_compute_id="auto")
        return DatabricksSession.Builder().sdkConfig(cfg).create()

    def try_q(self, sess, q: str):
        try:
            return True, sess.sql(q).collect()
        except Exception as e:  # noqa: BLE001
            return False, err(e)

    def read_col(self, sess, table: str, col: str = "txt") -> Any:
        for _ in range(5):
            ok, out = self.try_q(sess, f"SELECT id, {col} FROM {table} ORDER BY id")
            if ok:
                return {r[0]: r[1] for r in out}
            if "INSUFFICIENT_PERMISSIONS" in str(out) or "PERMISSION" in str(out):
                return f"denied ({out})"
            time.sleep(6)
        return f"error ({out})"

    def classify(self, got: Any) -> str:
        if isinstance(got, str):
            return got
        vals = [v for k, v in sorted(got.items()) if v is not None]
        if vals and all(v == self.raw_text for v in vals):
            return "RAW"
        if vals and all(v == PH for v in vals):
            return "PLACEHOLDER"
        return "MIXED/OTHER"

    # ---------- P0 / S1: identities ----------
    def precheck(self) -> None:
        groups = sorted(str(g.display) for g in (self.w.current_user.me().groups or []))
        self.rec("P0", "you are workspace admin", "admins" in groups, groups)
        self.base = self.baseline()
        (self.run_dir / "baseline_before.json").write_text(json.dumps(self.base, indent=1, default=str))
        clash = [x for x in self.sp_names.values() if x in self.base["service_principals"]] + ([self.cat] if self.cat in self.base["catalogs"] else [])
        self.rec("P0", "no name collisions", not clash, {"existing_service_principals": len(self.base["service_principals"]), "catalogs": self.base["catalogs"]})
        if clash:
            raise GateStop("name collision")

    def s1_identities(self) -> None:
        T = "T1b identity"
        for who, name in self.sp_names.items():
            sp = self.w.service_principals.create(display_name=name, active=True)
            self.sp[who] = {"id": str(sp.id), "app_id": str(sp.application_id)}
            self.created("service_principal", name, id=str(sp.id))
            got = self.w.service_principals.get(str(sp.id))
            self.rec(T, f"{who}: created non-admin", not (got.groups or got.roles),
                     {"default_entitlements": sorted(str(e.value) for e in (got.entitlements or [])),
                      "groups": [str(g.display) for g in (got.groups or [])], "roles": [str(r.value) for r in (got.roles or [])]})
            s = self.w.service_principal_secrets_proxy.create(service_principal_id=str(sp.id))
            self.sp[who]["secret_id"] = s.id
            self.sp[who]["secret"] = str(s.secret or "")
            self.created("sp_secret", f"secret of {name}", id=s.id)
        # identity format returned by current_user()/session_user()
        for who in self.sp:
            sess = self.session(who)
            r = sess.sql("SELECT current_user() cu, session_user() su, is_member('admins') adm").collect()[0]
            self.sp[who]["sess"] = sess
            fmt = "uuid-lowercase" if re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", r.cu or "") else "other"
            self.rec(T, f"{who}: Databricks Connect Serverless as SP; current_user() format", r.cu == self.sp[who]["app_id"] and not r.adm,
                     {"current_user_equals_application_id_exactly": r.cu == self.sp[who]["app_id"], "session_user_equals": r.su == self.sp[who]["app_id"],
                      "format": fmt, "in_admins": r.adm})
        me = self.me.sql("SELECT current_user() cu").collect()[0].cu
        self.rec(T, "owner: current_user() format", None, {"format": "email address" if "@" in me else "other"})
        self.minimal_entitlements()

    def minimal_entitlements(self) -> None:
        """Find the smallest entitlement set with which Databricks Connect Serverless still works (on both SPs)."""
        T = "T1b identity"
        kept = []
        for ent in ["databricks-sql-access", "workspace-access"]:
            for who in self.sp:
                self.w.service_principals.patch(self.sp[who]["id"], schemas=PATCH,
                                                operations=[iam.Patch(op=iam.PatchOp.REMOVE, path=f'entitlements[value eq "{ent}"]')])
            time.sleep(15)
            works = True
            for who in self.sp:
                try:
                    self.sp[who]["sess"].stop()
                except Exception:  # noqa: BLE001
                    pass
                try:
                    s = self.session(who)
                    s.sql("SELECT 1").collect()
                    self.sp[who]["sess"] = s
                except Exception as e:  # noqa: BLE001
                    works = False
                    self.rec(T, f"without {ent}: {who} cannot connect", None, err(e))
            if not works:
                for who in self.sp:
                    self.w.service_principals.patch(self.sp[who]["id"], schemas=PATCH,
                                                    operations=[iam.Patch(op=iam.PatchOp.ADD, path="entitlements", value=[{"value": ent}])])
                kept.append(ent)
                time.sleep(15)
                for who in self.sp:
                    self.sp[who]["sess"] = self.session(who)
                    self.sp[who]["sess"].sql("SELECT 1").collect()
        final = {who: sorted(str(e.value) for e in (self.w.service_principals.get(self.sp[who]["id"]).entitlements or [])) for who in self.sp}
        self.rec(T, "minimum entitlements for Databricks Connect Serverless", None, {"required": kept, "final": final})
        for who in self.sp:
            for t in PROD_PROBES:
                ok, out = self.try_q(self.sp[who]["sess"], f"SELECT 1 FROM {t} LIMIT 0")
                self.rec(T, f"{who}: production table {t.split('.')[-1]} denied", not ok, out if not ok else "ACCESSIBLE")
                if ok:
                    raise GateStop("service principal reached a production table")

    # ---------- S2/S3: temp catalog, direct grants, identity masks ----------
    def s2_grants_and_masks(self) -> None:
        T, n = "T3b direct grants", self.n
        try:
            self.me.sql(f"CREATE CATALOG {self.cat} COMMENT 'D-14 gate test T3b-T6b (temporary, synthetic)'")
        except Exception as e:  # noqa: BLE001
            self.rec(T, "create temporary catalog", False, err(e))
            raise GateStop("cannot create a temporary catalog; continuing would need grants on the production catalog")
        self.created("catalog", self.cat)
        self.me.sql(f"CREATE SCHEMA {n['schema']}")
        allow = self.sp["reader"]["app_id"]
        self.me.sql(f"CREATE FUNCTION {n['gov']}() RETURNS BOOLEAN RETURN current_user() IN ('{allow}')")
        self.me.sql(f"CREATE FUNCTION {n['mask_gov']}(t STRING) RETURNS STRING RETURN CASE WHEN t IS NULL THEN NULL WHEN {n['gov']}() THEN t ELSE '{PH}' END")
        self.me.sql(f"CREATE FUNCTION {n['mask_inline']}(t STRING) RETURNS STRING RETURN CASE WHEN t IS NULL THEN NULL "
                    f"WHEN current_user() IN ('{allow}') THEN t ELSE '{PH}' END")
        rows = f"(1, {sql_lit(self.raw_text)}), (2, NULL), (3, {sql_lit(self.raw_text)})"
        for tbl, fn in ((n["restricted"], n["mask_gov"]), (n["restricted_inline"], n["mask_inline"])):
            self.me.sql(f"CREATE TABLE {tbl} (id INT, txt STRING MASK {fn}) USING DELTA")
            self.me.sql(f"INSERT INTO {tbl} VALUES {rows}")
        self.me.sql(f"CREATE TABLE {n['ungranted']} (id INT, txt STRING) USING DELTA")
        self.me.sql(f"INSERT INTO {n['ungranted']} VALUES (1, 'synthetic ungranted')")
        for who in self.sp:
            a = self.sp[who]["app_id"]
            self.me.sql(f"GRANT USE CATALOG ON CATALOG {self.cat} TO `{a}`")
            self.me.sql(f"GRANT USE SCHEMA ON SCHEMA {n['schema']} TO `{a}`")
            self.me.sql(f"GRANT SELECT ON TABLE {n['restricted']} TO `{a}`")
            self.me.sql(f"GRANT SELECT ON TABLE {n['restricted_inline']} TO `{a}`")
        self.rec(T, "direct GRANT to service principals (by application id) accepted", True,
                 {"grants_on_restricted": sorted(str(tuple(r)[:2]).replace(self.sp['reader']['app_id'], '<reader>').replace(self.sp['granted_only']['app_id'], '<granted_only>')
                                                 for r in self.me.sql(f"SHOW GRANTS ON TABLE {n['restricted']}").collect())})
        for who in self.sp:
            got = self.read_col(self.sp[who]["sess"], n["ungranted"])
            self.rec(T, f"{who}: table without grant denied", isinstance(got, str) and got.startswith("denied"), got if isinstance(got, str) else "READABLE")
            if not isinstance(got, str):
                raise GateStop("service principal read an ungranted table")

        T = "T4b identity masks"
        expect = {"reader": "RAW", "granted_only": "PLACEHOLDER"}
        for tbl, label in ((n["restricted"], "governance-function mask"), (n["restricted_inline"], "inline allow-list mask")):
            for who in self.sp:
                c = self.classify(self.read_col(self.sp[who]["sess"], tbl))
                self.rec(T, f"{label}: {who} sees", c == expect[who], c)
                if who == "granted_only" and c == "RAW":
                    raise GateStop("non-allow-listed identity read raw text")
            c = self.classify(self.read_col(self.me, tbl))
            self.rec(T, f"{label}: you (owner) see", c == "PLACEHOLDER", c)
            if c == "RAW":
                raise GateStop("owner read raw text through an identity mask")
        nulls = self.read_col(self.sp["reader"]["sess"], n["restricted"])
        self.rec(T, "NULL preserved for reader", isinstance(nulls, dict) and nulls.get(2) is None, "id 2 is NULL" if isinstance(nulls, dict) else nulls)
        upper = self.sp["reader"]["sess"].sql(f"SELECT current_user() IN ('{allow.upper()}') u, current_user() IN (' {allow}') s").collect()[0]
        self.rec(T, "allow-list is exact (upper-case or padded id does not match)", not upper.u and not upper.s, {"upper_case_matches": upper.u, "padded_matches": upper.s})
        g = self.me.sql(f"SELECT {n['gov']}() r").collect()[0].r
        self.rec(T, "governance function is false for you", g is False, g)

    # ---------- S4: read/filter/copy/export paths ----------
    def s4_paths(self) -> None:
        T, n = "T4b read/copy/export paths", self.n
        like = f"SELECT count(*) c FROM {n['restricted']} WHERE txt LIKE '%{self.sentinel}%'"
        sha = f"SELECT count_if(sha2(txt, 256) = sha2({sql_lit(self.raw_text)}, 256)) c FROM {n['restricted']}"
        for who, sess in (("you", self.me), ("granted_only", self.sp["granted_only"]["sess"]), ("reader", self.sp["reader"]["sess"])):
            exp_hits = 2 if who == "reader" else 0
            ok, out = self.try_q(sess, like)
            self.rec(T, f"{who}: filter on sentinel matches", ok and out[0].c == exp_hits, out[0].c if ok else out)
            ok, out = self.try_q(sess, sha)
            self.rec(T, f"{who}: sha2 equals raw", ok and out[0].c == exp_hits, out[0].c if ok else out)
            df = sess.table(n["restricted"]).filter("txt IS NOT NULL").select("txt").collect()
            c = "RAW" if all(r[0] == self.raw_text for r in df) else ("PLACEHOLDER" if all(r[0] == PH for r in df) else "OTHER")
            self.rec(T, f"{who}: DataFrame API read", c == ("RAW" if who == "reader" else "PLACEHOLDER"), c)
        pdf = self.me.table(n["restricted"]).toPandas()
        self.rec(T, "you: toPandas", self.sentinel not in " ".join(pdf.txt.fillna("")), "no sentinel" if self.sentinel not in " ".join(pdf.txt.fillna("")) else "SENTINEL PRESENT")
        self.me.sql(f"CREATE TABLE {n['copy_owner']} AS SELECT * FROM {n['restricted']}")
        self.me.sql(f"INSERT INTO {n['copy_owner']} SELECT * FROM {n['restricted']}")
        c = self.classify({i: r[1] for i, r in enumerate(self.me.table(n["copy_owner"]).collect())})
        self.rec(T, "you: CTAS + INSERT…SELECT copy contains", c == "PLACEHOLDER", c)
        ok, out = self.try_q(self.sp["granted_only"]["sess"], f"CREATE TABLE {n['schema']}.sp_copy AS SELECT * FROM {n['restricted']}")
        self.rec(T, "granted_only: CTAS without CREATE TABLE privilege", not ok, out if not ok else "CREATED")
        self.me.sql(f"CREATE VIEW {n['view']} AS SELECT * FROM {n['restricted']}")
        for who in self.sp:
            self.me.sql(f"GRANT SELECT ON TABLE {n['view']} TO `{self.sp[who]['app_id']}`")
        for who, exp in (("reader", "RAW"), ("granted_only", "PLACEHOLDER")):
            c = self.classify(self.read_col(self.sp[who]["sess"], n["view"]))
            self.rec(T, f"{who}: read through a view owned by you", c == exp, c)
        ok, out = self.try_q(self.me, f"SELECT {n['mask_gov']}('x') r")
        self.rec(T, "you: calling the mask function directly returns placeholder", ok and out[0].r == PH, out[0].r == PH if ok else out)

    # ---------- S5: Bronze-style JSON redaction ----------
    def s5_bronze(self) -> None:
        T, n = "T5b Bronze-style JSON redaction", self.n
        recs: list[dict[str, Any]] = [{"id": "OP99000001", "notice_type": "Contract Award", "project_id": "P000001", "notice_text": self.raw_text},
                {"id": "OP99000002", "notice_type": "Invitation for Bids", "project_id": "P000001",
                 "notice_text": f'Quote "inside" back\\slash é  º № {self.sentinel}'},
                {"id": "OP99000003", "notice_type": "General Procurement Notice", "project_id": "P000002", "notice_text": None},
                {"id": "OP99000004", "notice_type": "Contract Award", "project_id": "P000002", "notice_text": "",
                 "bid_description": 'decoy "notice_text": "x" inside another field'}]
        lines = [json.dumps(r, ensure_ascii=False) for r in recs]
        self.me.sql(f"CREATE TABLE {n['bronze']} (raw_record STRING) USING DELTA")
        self.me.createDataFrame([(x,) for x in lines], "raw_record string").write.mode("append").saveAsTable(n["bronze"])
        v0 = self.me.sql(f"DESCRIBE HISTORY {n['bronze']} LIMIT 1").collect()[0].version
        self.me.sql(f"CREATE FUNCTION {n['mask_json']}(raw STRING) RETURNS STRING RETURN CASE WHEN {n['gov']}() THEN raw "
                    f"ELSE regexp_replace(raw, {sql_lit(JSON_TEXT_RE)}, {sql_lit(JSON_TEXT_REPL)}) END")
        self.me.sql(f"ALTER TABLE {n['bronze']} ALTER COLUMN raw_record SET MASK {n['mask_json']}")
        v1 = self.me.sql(f"DESCRIBE HISTORY {n['bronze']} LIMIT 1").collect()[0].version
        self.rec(T, "SET MASK does not change the Delta version", v0 == v1, {"before": v0, "after": v1})
        for who in self.sp:
            self.me.sql(f"GRANT SELECT ON TABLE {n['bronze']} TO `{self.sp[who]['app_id']}`")
        src = {r["id"]: r for r in recs}
        py = sorted(re.sub(JSON_TEXT_RE, JSON_TEXT_REPL, x) for x in lines)
        for who, sess in (("you", self.me), ("granted_only", self.sp["granted_only"]["sess"])):
            view = [r[0] for r in sess.table(n["bronze"]).collect()]
            parsed = [json.loads(x) for x in view]
            others = all({k: v for k, v in p.items() if k != "notice_text"} == {k: v for k, v in src[p["id"]].items() if k != "notice_text"} for p in parsed)
            states = sorted((p["id"], "placeholder" if p.get("notice_text") == PH else ("null" if p.get("notice_text") is None else "OTHER")) for p in parsed)
            self.rec(T, f"{who}: valid JSON, non-sensitive fields (incl. decoy) unchanged", len(parsed) == 4 and others, {"rows": len(parsed), "other_fields_equal": others})
            self.rec(T, f"{who}: notice_text redacted (NULL kept)", states == [("OP99000001", "placeholder"), ("OP99000002", "placeholder"),
                                                                             ("OP99000003", "null"), ("OP99000004", "placeholder")], states)
            self.rec(T, f"{who}: Python redaction parity (for verifiers)", sorted(view) == py, sorted(view) == py)
            self.rec(T, f"{who}: sentinel absent", all(self.sentinel not in x for x in view), all(self.sentinel not in x for x in view))
        rv = [r[0] for r in self.sp["reader"]["sess"].table(n["bronze"]).collect()]
        self.rec(T, "reader: exact raw records (SHA-256 multiset)", sorted(hashlib.sha256(x.encode()).hexdigest() for x in rv)
                 == sorted(hashlib.sha256(x.encode()).hexdigest() for x in lines), "equal")
        ok, out = self.try_q(self.me, f"SELECT count(*) FROM {n['bronze']} VERSION AS OF {v0}")
        self.rec(T, "time travel on masked table", None, "allowed" if ok else out)

    # ---------- S6: CHECK constraints, history ----------
    def s6_constraints_history(self) -> None:
        T, n = "T9 mask side effects", self.n
        ok, out = self.try_q(self.me, f"ALTER TABLE {n['restricted']} ADD CONSTRAINT c_id CHECK (id > 0)")
        self.rec(T, "ADD CHECK constraint to a masked table", None, "allowed" if ok else out)
        self.me.sql(f"CREATE TABLE {n['constrained']} (id INT, txt STRING) USING DELTA")
        self.me.sql(f"INSERT INTO {n['constrained']} VALUES (1, 'synthetic')")
        self.me.sql(f"ALTER TABLE {n['constrained']} ADD CONSTRAINT c_id CHECK (id > 0)")
        ok, out = self.try_q(self.me, f"ALTER TABLE {n['constrained']} ALTER COLUMN txt SET MASK {n['mask_gov']}")
        self.rec(T, "SET MASK on a table that already has a CHECK constraint", None, "allowed" if ok else out)
        ok, out = self.try_q(self.me, f"DESCRIBE HISTORY {n['restricted']}")
        self.rec(T, "DESCRIBE HISTORY on a masked table", ok, [r.operation for r in out] if ok else out)
        ok, out = self.try_q(self.me, f"DESCRIBE DETAIL {n['restricted']}")
        self.rec(T, "DESCRIBE DETAIL / row count on a masked table", ok and self.me.table(n["restricted"]).count() == 3, "works" if ok else out)

    # ---------- S7: volume restrictions ----------
    def s7_volume(self) -> None:
        T, n = "T6b volume", self.n
        self.me.sql(f"CREATE VOLUME {n['volume']}")
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
            f.write(json.dumps({"id": "OP99000001", "notice_text": self.raw_text}) + "\n")
            local = f.name
        subprocess.run(["databricks", "fs", "cp", local, f"dbfs:{self.vol_path}/synthetic.jsonl", "-p", DEFAULT_PROFILE], check=True, capture_output=True)
        Path(local).unlink()
        fpath = f"{self.vol_path}/synthetic.jsonl"

        def readable(sess) -> str:
            try:
                c = bytes(sess.read.format("binaryFile").load(fpath).select("content").collect()[0][0])
                return "read ok (raw)" if self.sentinel.encode() in c else "read ok (no sentinel)"
            except Exception as e:  # noqa: BLE001
                return f"denied ({err(e)})"
        self.me.sql(f"GRANT READ VOLUME ON VOLUME {n['volume']} TO `{self.sp['reader']['app_id']}`")
        r = ""
        for _ in range(5):
            r = readable(self.sp["reader"]["sess"])
            if "read ok" in r:
                break
            time.sleep(6)
        self.rec(T, "reader with READ VOLUME grant", "read ok" in r, r)
        r = readable(self.sp["granted_only"]["sess"])
        self.rec(T, "granted_only without READ VOLUME", r.startswith("denied"), r)
        self.rec(T, "you (owner of catalog/schema/volume)", None, readable(self.me))
        # export of masked data by the owner into a volume file
        self.me.table(n["restricted"]).write.mode("overwrite").json(f"{self.vol_path}/export_owner")
        exp = " ".join(r[0] for r in self.me.read.text(f"{self.vol_path}/export_owner").collect())
        self.rec("T4b read/copy/export paths", "you: export of masked column to a volume file", self.sentinel not in exp, "no sentinel" if self.sentinel not in exp else "SENTINEL PRESENT")
        # can ownership/grants restrict the owner-admin?
        rd = self.sp["reader"]["app_id"]
        steps = [("volume", f"ALTER VOLUME {n['volume']} OWNER TO `{rd}`"), ("schema", f"ALTER SCHEMA {n['schema']} OWNER TO `{rd}`"),
                 ("catalog", f"ALTER CATALOG {self.cat} OWNER TO `{rd}`")]
        for level, stmt in steps:
            ok, out = self.try_q(self.me, stmt)
            if not ok:
                self.rec(T, f"transfer {level} ownership to reader", False, out)
                break
            self.created("ownership", f"{level} owned by reader")
            time.sleep(5)
            self.rec(T, f"you after {level} ownership moved to reader", None, readable(self.me))
        ok, out = self.try_q(self.me, f"GRANT READ VOLUME ON VOLUME {n['volume']} TO `{self.me_user}`")
        self.rec(T, "you: self-grant READ VOLUME after transfers", None, "granted" if ok else out)
        ok, out = self.try_q(self.me, f"ALTER CATALOG {self.cat} OWNER TO `{self.me_user}`")
        self.rec(T, "you: re-take catalog ownership without the reader", None, "succeeded" if ok else out)
        self.results["owner_can_retake_catalog"] = ok
        if ok:
            self.ledger.append({"kind": "note", "name": "catalog ownership back with you"})
        self.rec(T, "you after attempted re-take", None, readable(self.me))

    # ---------- cleanup ----------
    def cleanup(self) -> None:
        steps: list = []

        def attempt(label, fn) -> bool:
            try:
                fn()
                steps.append((label, "ok"))
                return True
            except Exception as e:  # noqa: BLE001
                steps.append((label, err(e)))
                return False
        # Ownership first: an object owned by an SP becomes invisible to you (SHOW CATALOGS hides it), so never rely on
        # visibility, and never delete an SP before the objects it owns are confirmed gone (that orphans them).
        rs = self.sp.get("reader", {}).get("sess")
        if any(o["kind"] == "ownership" for o in self.ledger) and rs is not None:
            attempt("reader returns catalog ownership", lambda: rs.sql(f"ALTER CATALOG {self.cat} OWNER TO `{self.me_user}`").collect())
            attempt("reader returns schema ownership", lambda: rs.sql(f"ALTER SCHEMA {self.n['schema']} OWNER TO `{self.me_user}`").collect())
            attempt("reader returns volume ownership", lambda: rs.sql(f"ALTER VOLUME {self.n['volume']} OWNER TO `{self.me_user}`").collect())
        if any(o["kind"] == "catalog" for o in self.ledger):
            attempt("drop temp catalog (you)", lambda: self.me.sql(f"DROP CATALOG IF EXISTS {self.cat} CASCADE").collect())
        catalog_gone = self.catalog_absent()
        steps.append(("temp catalog confirmed absent via API", str(catalog_gone)))
        for who, sp in self.sp.items():
            if sp.get("sess") is not None:
                attempt(f"stop {who} session", lambda sp=sp: sp["sess"].stop())
            if sp.get("secret_id"):
                attempt(f"delete {who} secret", lambda sp=sp: self.w.service_principal_secrets_proxy.delete(service_principal_id=sp["id"], secret_id=sp["secret_id"]))
            sp["secret"] = None
            if who == "reader" and not catalog_gone:
                steps.append(("KEEP reader service principal", "it may own the temp catalog; needed for recovery"))
                continue
            attempt(f"delete {who} service principal", lambda sp=sp: self.w.service_principals.delete(sp["id"]))
        self.results["cleanup_steps"] = steps
        for s in steps:
            print(f"[cleanup] {s[0]}: {s[1]}", flush=True)
        after = self.baseline()
        (self.run_dir / "baseline_after.json").write_text(json.dumps(after, indent=1, default=str))
        diff = [k for k in self.base if self.base[k] != after[k]]
        self.rec("T10 cleanup", "production + workspace state identical to baseline (30 tables, masks, catalogs, schemas, grants, identities, cohort)", not diff, diff)
        left = {"catalog_exists_via_api": not catalog_gone, "service_principals": [s for s in after["service_principals"] if "d14-test" in s]}
        self.rec("T10 cleanup", "no temporary objects remain (catalog checked via API, not SHOW CATALOGS)",
                 catalog_gone and not left["service_principals"], left)

    def catalog_absent(self) -> bool:
        """True only if the API reports the temp catalog as not found (PermissionDenied means it still exists)."""
        try:
            self.w.catalogs.get(self.cat)
            return False
        except Exception as e:  # noqa: BLE001
            return "NotFound" in type(e).__name__ or "does not exist" in str(e).lower() or "not found" in str(e).lower()

    def run(self) -> int:
        try:
            self.precheck()
            for step in (self.s1_identities, self.s2_grants_and_masks, self.s4_paths, self.s5_bronze, self.s6_constraints_history, self.s7_volume):
                print(f"--- {step.__name__}", flush=True)
                step()
        except Exception as e:  # noqa: BLE001
            self.results["error"] = f"{type(e).__name__}: {err(e) if not isinstance(e, GateStop) else e}"
            self.results["traceback_tail"] = [ln.replace(self.sentinel, "<sentinel>") for ln in traceback.format_exc().splitlines()[-3:]]
            print("ERROR:", self.results["error"], flush=True)
        finally:
            print("--- cleanup", flush=True)
            if hasattr(self, "base"):
                self.cleanup()
            out = json.dumps(self.results, indent=1, default=str).replace(self.sentinel, "<sentinel>")
            for who in self.sp:
                out = out.replace(self.sp[who]["app_id"], f"<{who}_app_id>")
            out = out.replace(self.me_user, "<you>")
            (self.run_dir / "gate_results.json").write_text(out)
            print(f"results: {self.run_dir / 'gate_results.json'}", flush=True)
        return 0


if __name__ == "__main__":
    sys.exit(GateB().run())
