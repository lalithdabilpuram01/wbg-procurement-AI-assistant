# Models 2 and 3: Final Selection and Integration Readiness

> **SYNTHETIC OUTCOMES — NOT VALIDATED WORLD BANK RISK ACCURACY**

## Decision (D1–D3 approved)

- **Models:** **L2 logistic regression** for both. No Random Forest or XGBoost.
- **Model 3 features:** cleaned. **Model 2 features:** unchanged.
- **Evaluation:** S2 primary, S0 negative control, S1 supporting validation only. Both sealed test sets were scored **once** (lock `ml/final23/frozen/TEST_SCORED.json`).

## Final results (test; simulated)

| | Model 2 (amendment, 24 m) | Model 3 (re-bid, 12 m) |
|---|---|---|
| Test rows | 28,949 contracts (FY2024) | 20,954 tenders (FY2023–25) |
| S2 PR-AUC (prevalence) | 0.232 (0.138) | 0.334 (0.082) |
| S2 ROC-AUC | 0.655 | 0.735 |
| S2 recall / precision / F1 | 0.51 / 0.22 / 0.31 at 31% alerts | 0.32 / 0.44 / 0.37 at 6% alerts |
| Calibration (ECE) | 0.014 | 0.012 |
| Unseen projects, PR-AUC | 0.247 | 0.341 |
| **S0 null control** | ROC-AUC 0.504 (CI 0.497–0.512): **pass** | ROC-AUC 0.501 (CI 0.487–0.514): **pass** |
| Risk bands | – | **Ordered:** 4.4% / 8.4% / 31.5%; flat under S0 |
| Validation → test stability | PR-AUC 0.252 → 0.232 | 0.339 → 0.334 |

## Assessment

1. **The null tests pass.** The pipeline does not manufacture signal: S0 discrimination is at chance on test, and S0 bands are flat.
2. **The models recover the simulation.** Performance sits close to the theoretical ceilings found in Stage 3A, is well calibrated and generalises to unseen projects.
3. **Explanation quality is acceptable for a demonstration, with caveats:**
   - Displayed factors are real model inputs.
   - Collinear bid-window inputs are merged; country and unstable levels are hidden.
   - Model 2 sometimes shows region or sector, which absorb simulated random effects rather than assumed drivers.
   - No generator internals, identifiers or personal data appear in outputs.
4. **Operating points drift:**
   - The Model 2 30% review cap became 31% on test.
   - Model 3 band shares stayed close to design (68.6 / 20.2 / 11.1% vs 70 / 20 / 10%).
   - Thresholds and bands must be re-derived at each refit.

## Integration readiness (FastAPI and dashboard)

**Ready as clearly labelled demonstrations.**

| Artefact | Path |
|---|---|
| Pipelines (load with joblib; call `pipeline.predict_proba(X)[:, 1]`) | `ml/final23/frozen/models/m{2,3}_S2_l2_final.joblib` |
| Input schemas | `ml/final23/frozen/m{2,3}_input_schema.json` |
| Example responses | `ml/final23/frozen/m{2,3}_example_api_outputs.json` |
| Thresholds, bands, explanation policy (in `refit_details.json`) | `ml/final23/frozen/refit_details.json`, `ml/final23/FINAL_EVAL_CONFIG_M23.json` |
| Explanation helpers | `ml/final23/common23.py` (`contributions`), `final_refit23.py` (`display_factors`) |
| Hashes | `ml/final23/frozen/FROZEN.json` |
| Tracking | `ml/experiments/runs.jsonl` (stage `final_test_m23`) |

**Every response and screen must carry:**
- the disclaimer "SYNTHETIC OUTCOMES — NOT VALIDATED WORLD BANK RISK ACCURACY";
- for Model 3, "Simulated — Demonstration Only".

**Not ready, and must not be presented as:**
- real amendment or re-bidding risk;
- a production alerting tool;
- a country or supplier risk rating.

## Remaining limitations

- Outcomes, prevalence and drivers are assumptions. Simulated precision is low by design.
- Unit dependence: Model 3 repeat notices outside the 184 reference projects; Model 2 lots spanning fiscal years.
- The evaluation is not fully blind: simulated test prevalence was printed beforehand.
- Prevalence and drift sensitivity sweeps were deferred.

**Module 2 is closed.** All three predictive capabilities are finalised:
- Model 1: real labels, Ridge + B1;
- Model 2: simulated, logistic regression;
- Model 3: simulated, logistic regression with risk bands.

Next: document retrieval, grounded RAG, citations, guardrails and the integrated application.
