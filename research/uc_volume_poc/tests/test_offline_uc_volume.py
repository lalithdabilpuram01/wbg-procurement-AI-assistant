"""Offline tests of the UC Volume PDF POC (no Databricks call, no network)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import uc_volume_pdf_poc as poc  # noqa: E402


def test_selected_document_is_approved_public_and_intact():
    document = poc.select_document()
    assert (document.project_id, document.document_id, document.doc_type) == ("P148850", "40089196", "ISR")
    assert document.privacy_mask_events == 0 and document.size_bytes == 164_080
    assert document.volume_path == "/Volumes/workspace/default/wbg_rag_pdfs_poc/ET/P148850/" + document.filename


def test_documents_with_personal_data_events_are_refused():
    with pytest.raises(poc.PocError, match="masking events"):
        poc.select_document("26256705")           # P132833 PAD: names, an email and a phone were masked in the pilot


def test_unknown_or_unapproved_documents_are_refused():
    with pytest.raises(poc.PocError):
        poc.select_document("99999999")


def test_volume_is_new_and_isolated_from_bronze_volumes():
    assert poc.VOLUME_FULL_NAME == "workspace.default.wbg_rag_pdfs_poc"
    assert poc.VOLUME not in {"my_files", "raw_data", "wbg_procurement_raw", "wbg_procurement_test", "wbg_reference_raw"}
    source = Path(poc.__file__).read_text()
    assert "--overwrite" not in source and '"rm"' not in source and '"delete"' not in source


def test_live_run_requires_opt_in(monkeypatch):
    monkeypatch.delenv(poc.LIVE_OPT_IN, raising=False)
    with pytest.raises(SystemExit, match="not enabled"):
        poc.main("run")
