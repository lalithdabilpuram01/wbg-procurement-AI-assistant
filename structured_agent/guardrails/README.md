# Guardrails for the NL2SQL workflow (NVIDIA NeMo Guardrails 0.24.1)

An independent security layer around the Claude-only NL2SQL workflow. It adds earlier, separate checks. The existing MCP
authorization and the SQLGlot validator remain the authoritative controls, and nothing here relaxes them.

## What enforces each control

| Stage | Control | Enforced by |
|---|---|---|
| Input | Credential and secret requests | **NeMo built-in regex rail** (`regex check input`, patterns in `nemo_config/config.yml`) |
| Input | Size and format; instruction override; restricted-data requests (supplier identities, contact/personal data, notice text, raw records, re-identification) | **NeMo custom action rail** (`wbg input policy` → `policies.evaluate_user_input`) |
| Retrieved context | Unapproved tables dropped; instruction-like text removed from descriptions, limitations, metrics and examples; metadata framed as untrusted in the prompt | Application policy (`policies.sanitize_retrieved_context`) |
| Conversation memory | Instruction-like text replaced in memory items (conversation service); a memory block that still contains it is dropped before the Claude call (`app.conversation_memory`) | Application policy (`policies.contains_instruction_like_text`) |
| Model output | Secret-shaped values | **NeMo built-in regex rail** (`regex check output`) |
| Model output | Draft schema; SELECT/WITH shape; no execution or result claims; `tables_used` limited to the context | **NeMo custom action rail** (`wbg output policy` → `policies.evaluate_model_output`) |
| Tool authorization | Each stage may call only its approved MCP tools (retrieval / validation / execution) | Application policy (`ToolGate`), on top of the MCP server's own tool allow-list and principal authorization |
| Execution | Trusted identity unchanged; validator approval and validation id present; no earlier block | Application policy (`policies.evaluate_execution`) |
| Final response | Secret-shaped values | **NeMo built-in regex rail** |
| Final response | Consistency of status, rows and execution claims; executed SQL equals validated SQL | **NeMo custom action rail** (`policies.evaluate_final_response`) |

"Custom action rail" means NeMo runs the rail flow, and the check itself is our deterministic Python. Every decision records
`enforced_by` (`nemo_builtin_rail`, `nemo_custom_action`, `application_policy`, or `nemo_rails` for an allow after all
NeMo rails of that stage passed).

## Offline guarantees

- **No LLM:** `nemo_config/config.yml` defines no `models`, and the code refuses a configuration that does. NeMo is
  created without an LLM (`rails.llm is None`) and only input-only or output-only rails are run, so no LLM is called and
  no embedding model is loaded.
- **No NeMo telemetry:** NeMo's own usage telemetry (`events.telemetry.data.nvidia.com`) is disabled in-process with
  `NEMO_GUARDRAILS_NO_USAGE_STATS=1` before NeMo is imported.
- **Fail closed:** any NeMo error blocks the request (`guardrail_error`), and the CLI refuses to run without guardrails.

## Logging

Each decision is written to the JSONL audit trail and as a Logfire event (export stays off), with only these fields:
- request id;
- `guard_stage`, `policy_id`, `decision`, `reason_category`, `enforced_by`;
- duration.

Questions, retrieved text, SQL, rows and credentials are never logged.

## Limitations

These are deterministic checks. They reduce risk but do **not** solve prompt injection, hallucination or semantic content
safety:
- **Paraphrases:** the patterns catch explicit, common phrasings; reworded or indirect attacks can pass them. The
  validator and MCP authorization still bound what any SQL can do.
- **No semantic understanding:** there is no LLM-based or classifier-based rail (NeMo self-check, content safety, jailbreak
  heuristics). Those need a model and were not configured.
- **Retrieved text:** retrieved-context sanitising removes instruction-like text, but cannot judge whether a benign-looking
  description is misleading.
- **Value checks are shape-based:** secret detection matches known value shapes, not every possible credential format.
