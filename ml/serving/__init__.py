"""Serving the frozen Module 2 models for one procurement package (Milestone E, mode B).

    features.py   package request + project context -> the exact feature rows each model was trained on
    gold.py       project context from the Gold tables (fixed read-only SQL through an injected `run_sql`)
    models.py     hash-verified frozen models -> predictions with explanations and disclaimers
    service.py    predict_package(request, run_sql): lookup, chaining (Model 1 date -> Model 2), all three models

The models are loaded read-only and never refit. scikit-learn must be 1.5.1 (the version they were saved with).
"""
