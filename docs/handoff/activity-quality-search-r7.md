# RG-MPNN quality search r7 (2026-10-11)

## Scope and execution

The local MedChat Conda environment ran the real PDE/BuChE family training
search with feature-aware scaffold splits and two seeds per selected
hyperparameter group. The search identifier is
`pde-buche-quality-search-20261011-r7`; the complete machine-readable report
is `outputs/activity_training/pde-buche-quality-search-20261011-r7/search_report.json`.

The search selected by validation metrics only. Each selected group was then
evaluated once on the fixed scaffold test split, and the paired
classification/regression predictor was exercised against independent smoke
inputs. The random-split runs in the report are interpolation diagnostics and
are explicitly excluded from the deployment gate. No model was activated and
the root family registry was not changed.

## Result

All four selected groups completed two-seed scaffold evaluation and passed the
current stability threshold (`std <= 0.05` for every reported primary test
metric). They also improved the constant baseline on every test seed. That is
useful model-development evidence, but it is not sufficient for deployment:

| family/task | selected search test result | comparison with r2 | gate |
|---|---|---|---|
| PDE classification | ROC-AUC 0.9076; PR-AUC 0.9757; balanced accuracy 0.7103 | not no-worse on all required metrics | ineligible |
| PDE regression | RMSE 0.9737; MAE 0.7402; R² 0.5510 | RMSE is slightly worse than r2 although MAE/R² are similar or better | ineligible |
| BuChE classification | ROC-AUC 0.7627; PR-AUC 0.8228; balanced accuracy 0.6686 | below r2 on the required comparison | ineligible |
| BuChE regression | RMSE 1.1801; MAE 0.9309; R² 0.2606 | below r2 on the required comparison | ineligible |

The report therefore records `deployment_gate.eligible=false` and
`activated=false` for every family/task. The checkpoints may be used for
offline error analysis, ranking experiments, and further hyperparameter/data
development only. They must not be copied into the active production family
registry or presented as validated pIC50 predictions.

## Decision and remaining evidence

Keep the existing production activation unchanged (currently no active
PDE/BuChE bundle in the root registry). A separate reviewed activation is
required even if a future search passes the model gate. Before production use,
the next candidate must pass the same scaffold protocol, beat or match the
current r2 candidate on every required metric for every evaluated seed, and
then pass the real deployment/endpoint acceptance on the target server.

This evidence does not establish clinical, experimental, prospective, or
target-server validity. It also does not close the independent gates for the
external model endpoint, Temporal/PostgreSQL restart behavior, HTTPS/WSS
reverse proxy, real ProLIF/MDAnalysis interaction geometry, or reference-pose
redocking RMSD.
