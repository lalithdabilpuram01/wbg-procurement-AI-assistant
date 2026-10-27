# Milestone E: ML Predictions in the Application (Package Assessment)

**Date:** 2026-10-27 · **Scope:** assessment Module 2 (predictive engines plus contextual retrieval when a user initiates a
new procurement package) · **Status:** built, tested offline, validated live (2 bounded runs, $0.024 in total).

## 1. What the user does

The user sends a **package** (mode B) with six fields:
- project ID;
- procurement method;
- procurement category;
- review type;
- bid window (days);
- planned tender date.

Endpoint: `POST /conversations/{id}/packages`. Everything else is looked up. The package arrives as structured fields,
never parsed from message text; free-text messages cannot start an assessment.

## 2. What runs

| Step | Component | Notes |
|---|---|---|
| Supervisor | `supervisor.py` (mode `package`) | Needs the `ml_prediction` route in the request's authorization |
| Fixed plan (no LLM planner) | `planner.package_plan` | Task 1: `predict_package` (required). Task 2: document lessons (optional) |
| Gold lookups | `ml/serving/gold.py` via the Structured Data Agent gateway | 3 fixed, parameterised queries, read-only principal; pass the SQL validator and column policy |
| Sector | `ml/serving/project_sector.csv` | Not in the Gold objects the principal can read; taken from the training snapshot (2,000 cohort projects) |
| Features | `ml/serving/features.py` | Same derivations as training (fiscal-year rule, income classification, bid-window status) |
| Models | `ml/serving/models.py` | Frozen artefacts, SHA-256 checked against `FROZEN.json`; scikit-learn pinned to 1.5.1 (the pickles do not load on 1.9) |
| Chaining | `ml/serving/service.py` | Model 1's estimated signing date feeds Model 2 (amendment risk is scored at signing) |
| Evidence | `workers/prediction.py` | 3 `model_prediction` records, `numeric_use = model_estimate`; Models 2–3 carry the `synthetic_training_data` warning |
| Answer | `response.py` | Claude states estimates as estimates; a fixed limitation always names the synthetic models |

## 3. Models

| Model | Output | Basis |
|---|---|---|
| 1. Tender duration | Days, P10–P90 range, P(> 365 days), long-tender alert, top 3 drivers | Ridge, **real data**, test MAE ≈ 77 days |
| 2. Amendment risk | Simulated probability, demonstration review-list flag | L2 logistic, **synthetic outcomes**, not validated |
| 3. Re-bidding risk | Simulated probability, Low / Medium / High band | L2 logistic, **synthetic outcomes**, not validated |

## 4. Verification

- **Exact reproduction:**
  - Model 1: 5 frozen test rows (point, range, probability and alert);
  - Models 2–3: all frozen example API outputs.
- **Refusals:**
  - a changed model file;
  - an invalid package (bad project ID, negative window, unknown field);
  - a project outside the portfolio;
  - a missing route authorization;
  - a package started from message text.
- **Tests:**
  - 16 serving tests;
  - 7 package-flow tests;
  - 6 API tests.
  - All suites pass: 182 orchestrator / ML / vector store, plus 102 RAG.
- **Live run** (P148850, Request for Bids, Works, prior review, 60-day window, tender on 2026-12-01):
  - Model 1: **186 days** (86–412), 17% chance of exceeding a year;
  - Model 2: 38.1% (on the review list);
  - Model 3: 4.7% (Low);
  - plus a relevant status-report passage on the works bidding.
  - Time and cost: 34 seconds, one Claude call.

**Fixes made after the first live run:**
- The lessons question was reworded: it now finds appraisal "Lessons Learned" and status-report passages instead of
  procurement-plan rows.
- A conflict flag now affects the status only when the answer cites that evidence.

## 5. Limitations

- Models 2 and 3 demonstrate the mechanism only: their outcomes are simulated.
- Sector comes from the training snapshot. Projects outside the 2,000-project cohort score with sector "Unknown".
- Some projects have no completion report yet (e.g. P148850), so the lessons come from appraisal and status reports.
