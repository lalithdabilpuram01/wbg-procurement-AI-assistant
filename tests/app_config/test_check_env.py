"""Tests for the environment check: correct names reported, values never printed."""
from __future__ import annotations

from app_config.check_env import check, merged_settings, print_report, read_env_file

SECRET = "pk-test-not-a-real-key-123"
COMPLETE = {"PORTKEY_API_KEY": SECRET, "PORTKEY_CLAUDE_MODEL": "@wbg-anthropic/example-model",
            "WBG_ALLOW_DEV_OWNER_CREDENTIALS": "false"}


def test_complete_configuration_is_ok():
    assert check(COMPLETE, file_values={}).ok


def test_missing_names_are_reported():
    report = check({**COMPLETE, "PORTKEY_API_KEY": "", "PORTKEY_CLAUDE_MODEL": "  "}, file_values={})
    assert report.missing == ["PORTKEY_API_KEY", "PORTKEY_CLAUDE_MODEL"] and not report.ok


def test_model_must_use_the_model_catalog_form():
    assert check({**COMPLETE, "PORTKEY_CLAUDE_MODEL": "claude-model-without-provider"}, file_values={}).invalid == ["PORTKEY_CLAUDE_MODEL"]


def test_dev_credential_flag_must_be_boolean():
    assert check({**COMPLETE, "WBG_ALLOW_DEV_OWNER_CREDENTIALS": "yes"}, file_values={}).invalid == ["WBG_ALLOW_DEV_OWNER_CREDENTIALS"]


def test_raw_provider_keys_and_fallback_config_are_not_allowed_in_the_file():
    file_values = {"ANTHROPIC_API_KEY": "x", "XAI_API_KEY": "y", "PORTKEY_CONFIG_ID": "z"}
    report = check(COMPLETE, file_values)
    assert report.not_allowed == ["ANTHROPIC_API_KEY", "XAI_API_KEY", "PORTKEY_CONFIG_ID"] and not report.ok


def test_report_never_prints_values(capsys):
    print_report(check({**COMPLETE, "PORTKEY_CLAUDE_MODEL": ""}, file_values={"ANTHROPIC_API_KEY": SECRET}), env_file_found=True)
    output = capsys.readouterr().out
    assert SECRET not in output and "PORTKEY_CLAUDE_MODEL" in output and "ANTHROPIC_API_KEY" in output


def test_process_environment_overrides_the_file(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("PORTKEY_CLAUDE_MODEL=\nWBG_ALLOW_DEV_OWNER_CREDENTIALS=false\n")
    monkeypatch.setenv("PORTKEY_CLAUDE_MODEL", "@wbg-anthropic/from-environment")
    settings = merged_settings(read_env_file(env_file))
    assert settings["PORTKEY_CLAUDE_MODEL"] == "@wbg-anthropic/from-environment"
    assert settings["WBG_ALLOW_DEV_OWNER_CREDENTIALS"] == "false"
