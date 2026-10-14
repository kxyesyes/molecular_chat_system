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

| family/task | selected seed test result | worst-seed comparison with r2 | gate |
|---|---|---|---|
| PDE classification | ROC-AUC 0.9076; PR-AUC 0.9757; balanced accuracy 0.7103 | worst ROC-AUC 0.8952 < 0.9114; PR-AUC 0.9703 < 0.9776 | ineligible |
| PDE regression | RMSE 0.9737; MAE 0.7402; R² 0.5510 | worst RMSE 0.9834 > 0.9774; R² 0.5420 < 0.5475 | ineligible |
| BuChE classification | ROC-AUC 0.7627; PR-AUC 0.8228; balanced accuracy 0.6686 | worst ROC-AUC 0.7627 < 0.7689; PR-AUC 0.8117 < 0.8184; balanced accuracy 0.6686 < 0.6857 | ineligible |
| BuChE regression | RMSE 1.1801; MAE 0.9309; R² 0.2606 | worst RMSE 1.2274 > 1.0374; MAE 0.9705 > 0.8140; R² 0.2001 < 0.4286 | ineligible |

The gate uses the worst value of each metric across both evaluated seeds,
not just the validation-selected representative. In particular, the selected
PDE regression seed improves all three r2 test metrics, but the other seed
does not; the paired group therefore fails the current no-regression gate.
Two seeds passing this fixed-split threshold do not establish robustness to
new dataset splits or prospective molecules.

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

The same fixed scaffold test split was already exposed in earlier searches.
The once-per-selected-checkpoint evaluation protocol is local to each search;
it does not make r7 a new untouched confirmatory test. Any later changes
motivated by these results must be treated as further model development.
Before a confirmatory performance claim, freeze the candidate using only
training/validation evidence and evaluate on a genuinely unused external or
prospective set with a documented protocol. Do not keep tuning until this
already-observed test set turns the gate green.

This evidence does not establish clinical, experimental, prospective, or
target-server validity. It also does not close the independent gates for the
external model endpoint, Temporal/PostgreSQL restart behavior, HTTPS/WSS
reverse proxy, real ProLIF/MDAnalysis interaction geometry, or reference-pose
redocking RMSD.
