# Frozen family baseline training plan

User approved the fixed baseline on 2026-09-08. Execute with TDD and independent review.

Four serial jobs: PDE classification/regression, then BuChE classification/regression.
Use verified family-v1 packages and unchanged scaffold splits / pIC50 threshold 5.
Frozen defaults: epochs=50, patience=12, batch_size=32, lr=0.001, num_layers=5,
hidden_size=256, dropout=0.2, weight_decay=0.0001, lr_scheduler=Cosine,
loss_metric=MSE (classification uses BCEWithLogitsLoss), random_seed=42.
No hyperparameter search, automatic retries, activation or restart.
Validation selects checkpoints; fit_prepared restores best weights and evaluates test once.

- [ ] Test frozen configuration, serial runner, failure reporting, baselines and no activation.
- [ ] Add scripts/train_family_activity_models.py reusing run_prepared_training and registry.
  Reports/progress go under ignored outputs; weights/cards/registry into a new ignored run directory.
  Exclusive run directory prevents overwrite/rerun. No raw CSV rows or credentials in reports.
- [ ] Run tests and independent review before real training.
- [ ] Verify both packages, run four real jobs serially, persist progress between jobs.
- [ ] Evaluate train-derived constant baselines on held-out labels after training; never rerun the trained
  network on test. Report ROC/PR AUC, balanced accuracy, confusion matrix, RMSE/MAE/R² honestly.
- [ ] Register complete pairs without activation; report incomplete pairs and poor metrics explicitly.
- [ ] Update handoff, commit only script/tests/docs, no private assets.

This is one frozen baseline, not exhaustive optimization or evidence of clinical utility.
The trainer does not retain per-molecule held-out outputs: do not invent joint classification/regression
consistency from marginal summaries or evaluate test twice. No source edits during training because
publication verifies source digests. Changing frozen training parameters requires a recorded decision.
