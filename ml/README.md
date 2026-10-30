# ml/ (prediction models the app serves)

```text
ml/
  serving/            loads the frozen models and answers one procurement package (used by the prediction worker)
    features.py       package request -> model features (from the Gold tables)
    gold.py           the Gold lookups the features need (read-only SQL through the structured-data tools)
    models.py         loads and checksum-verifies the frozen models
    service.py        the three predictions with their explanations
  final/frozen/       Model 1 (tender duration): FROZEN.json, models/*.joblib, test results
  final23/frozen/     Models 2 and 3 (amendment and re-bid risk, trained on SYNTHETIC labels): same layout
  final23/common23.py feature code the Model 2/3 model files need when they are loaded
```

The models are frozen: their files are verified against the checksums in FROZEN.json at load time and are never
retrained or re-scored by the app. How they were built (data extraction, EDA, training stages, final refit and the
one-time test scoring) is recorded in research/ml/ (see research/ml/README.md). Tests: tests/ml_serving/.
