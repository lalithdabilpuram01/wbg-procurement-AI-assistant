# Grounded answering MVP (Module 1)

question → guarded retrieval (`rag/retrieval/core.py`) → evidence selection / verification → generation (typed-record route or
local LLM) → deterministic validation → answer or abstention. Citations are always built from index metadata.

```bash
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
/opt/anaconda3/bin/python3 rag/answering/pipeline.py "What procurement method is planned for activity LK-MIHAPCLG-109016-CW-RFB?"
/opt/anaconda3/bin/python3 rag/answering/evaluate_answers.py            # evidence_only, llm_only, hybrid (or pass e.g. "hybrid")
/opt/anaconda3/bin/python3 rag/answering/run_demos.py                    # 12 demonstration cases -> demo_outputs/
/opt/anaconda3/bin/python3 rag/answering/schemas.py                      # export JSON schemas -> schemas/
```
Requires the local Ollama endpoint (localhost:11434) for LLM generation; all other stages run without it.
Reports: `RAG_ANSWER_GENERATION_REPORT.md`, `RAG_GROUNDING_AND_GUARDRAIL_REPORT.md`, `RAG_END_TO_END_EVALUATION.md`.
