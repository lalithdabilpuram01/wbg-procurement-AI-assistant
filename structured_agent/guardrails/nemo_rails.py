"""NVIDIA NeMo Guardrails runtime for the input, model-output and final-response rails.

What runs inside NeMo (LLMRails, Colang 1.0 flows in nemo_config/):
  * built-in regex rail  (`regex check input` / `regex check output`): credential requests, secret-shaped values
  * custom action rails  (`wbg input policy` / `wbg output policy`): deterministic Python policies from policies.py

Safety properties of this module:
  * NeMo's own usage telemetry (events.telemetry.data.nvidia.com) is disabled before NeMo is imported.
  * The configuration must not define models; NeMo is created without an LLM and only input-only or output-only
    rails are run, so no LLM is ever called and no embedding model is loaded.
  * Any unexpected NeMo error blocks the request (fail closed) with reason `guardrail_error`.
"""
from __future__ import annotations

import os

os.environ["NEMO_GUARDRAILS_NO_USAGE_STATS"] = "1"   # before importing nemoguardrails: never send usage telemetry

from pathlib import Path  # noqa: E402
from typing import Any, Iterable  # noqa: E402

from nemoguardrails import LLMRails, RailsConfig  # noqa: E402
from nemoguardrails.rails.llm.options import GenerationOptions  # noqa: E402

from structured_agent.guardrails import policies  # noqa: E402
from structured_agent.guardrails.decisions import GuardDecision, GuardStage, PolicyResult, ReasonCategory, timed  # noqa: E402
from structured_agent.nl2sql.settings import ConfigurationError  # noqa: E402

CONFIG_DIR = Path(__file__).resolve().parent / "nemo_config"
DECISION_VARIABLE = "wbg_decision"

# Built-in rail flow name -> (policy id, reason) when that rail stops the request.
BUILTIN_BLOCKS = {
    "regex check input": ("nemo.regex.input.credential_request", ReasonCategory.CREDENTIAL_EXFILTRATION),
    "regex check output": ("nemo.regex.output.secret_value", ReasonCategory.SENSITIVE_VALUE_IN_OUTPUT),
}
CUSTOM_FLOWS = {"wbg input policy": "nemo.custom.input_policy", "wbg topic policy": "nemo.custom.topic_policy",
                "wbg output policy": "nemo.custom.output_policy"}


def _as_dict(result: PolicyResult) -> dict[str, Any]:
    return {"allowed": result.allowed, "reason": result.reason.value if result.reason else None}


class NemoGuardrails:
    """Deterministic NeMo rails: input-only and output-only checks, no LLM."""

    def __init__(self, config_dir: Path = CONFIG_DIR):
        config = RailsConfig.from_path(str(config_dir))
        if config.models:
            raise ConfigurationError("The NeMo guardrail configuration must not define models (no LLM may be called)")
        self._rails = LLMRails(config)
        if self._rails.llm is not None:
            raise ConfigurationError("NeMo created an LLM; these rails must run without one")
        self._rails.register_action(self._input_policy, "wbg_input_policy")
        self._rails.register_action(self._topic_policy, "wbg_topic_policy")
        self._rails.register_action(self._output_policy, "wbg_output_policy")

    # ------------------------------------------------------------------------------------- custom actions (NeMo)
    @staticmethod
    async def _input_policy(text: str) -> dict[str, Any]:
        return _as_dict(policies.evaluate_user_input(text or ""))

    @staticmethod
    async def _topic_policy(text: str) -> dict[str, Any]:
        return _as_dict(policies.evaluate_topic(text or ""))

    @staticmethod
    async def _output_policy(text: str, stage: str | None, context: dict | None = None) -> dict[str, Any]:
        if stage == GuardStage.MODEL_OUTPUT.value:
            tables = (context or {}).get("context_tables") or []
            return _as_dict(policies.evaluate_model_output(text or "", tables))
        if stage == GuardStage.FINAL_RESPONSE.value:
            return _as_dict(policies.evaluate_final_response(text or ""))
        return _as_dict(PolicyResult.block(ReasonCategory.GUARDRAIL_ERROR))

    # ----------------------------------------------------------------------------------------------- public API
    def check_input(self, text: str) -> GuardDecision:
        """Run the input rails on the user's question."""
        messages = [{"role": "user", "content": text}]
        return self._run(GuardStage.INPUT, messages, rails=["input"])

    def check_output(self, stage: GuardStage, text: str, context_tables: Iterable[str] = ()) -> GuardDecision:
        """Run the output rails on the model's reply (MODEL_OUTPUT) or the final response (FINAL_RESPONSE)."""
        messages = [{"role": "context", "content": {"guard_stage": stage.value, "context_tables": list(context_tables)}},
                    {"role": "user", "content": ""},
                    {"role": "assistant", "content": text}]
        return self._run(stage, messages, rails=["output"])

    def _run(self, stage: GuardStage, messages: list[dict], rails: list[str]) -> GuardDecision:
        with timed() as timing:
            try:
                response = self._rails.generate(messages=messages, options=GenerationOptions(
                    rails=rails, output_vars=[DECISION_VARIABLE], log={"activated_rails": True}))
                decision = self._decision(stage, response)
            except Exception:   # fail closed; the exception text may contain the message, so it is not kept
                decision = GuardDecision(stage=stage, policy_id="nemo.runtime", allowed=False,
                                         reason_category=ReasonCategory.GUARDRAIL_ERROR, enforced_by="nemo_rails")
        return decision.model_copy(update={"duration_ms": timing["ms"]})

    @staticmethod
    def _decision(stage: GuardStage, response: Any) -> GuardDecision:
        for rail in response.log.activated_rails:
            if not rail.stop:
                continue
            if rail.name in BUILTIN_BLOCKS:
                policy_id, reason = BUILTIN_BLOCKS[rail.name]
                return GuardDecision(stage=stage, policy_id=policy_id, allowed=False, reason_category=reason,
                                     enforced_by="nemo_builtin_rail")
            if rail.name in CUSTOM_FLOWS:
                verdict = (response.output_data or {}).get(DECISION_VARIABLE) or {}
                reason = verdict.get("reason") or ReasonCategory.GUARDRAIL_ERROR.value
                return GuardDecision(stage=stage, policy_id=CUSTOM_FLOWS[rail.name], allowed=False,
                                     reason_category=ReasonCategory(reason), enforced_by="nemo_custom_action")
            return GuardDecision(stage=stage, policy_id="nemo.unknown_rail", allowed=False,
                                 reason_category=ReasonCategory.GUARDRAIL_ERROR, enforced_by="nemo_rails")
        verdict = (response.output_data or {}).get(DECISION_VARIABLE)
        if not verdict or not verdict.get("allowed"):
            # The custom rail must always run and allow; anything else is treated as a failure (fail closed).
            return GuardDecision(stage=stage, policy_id="nemo.custom_rail_missing", allowed=False,
                                 reason_category=ReasonCategory.GUARDRAIL_ERROR, enforced_by="nemo_rails")
        reason = verdict.get("reason")
        return GuardDecision(stage=stage, policy_id=f"nemo.rails.{stage.value}", allowed=True,
                             reason_category=ReasonCategory(reason) if reason else None, enforced_by="nemo_rails")
