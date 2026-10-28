# Targeted candidate generation with property-based ranking

## Problem and scope

A real Web request to generate EGFR candidates, calculate their properties and rank by those properties is intercepted by the property-only compiler. The existing target-design workflow also attempts optional ADMET and activity and its default ranker incorporates those when present. Neither silently rejecting the request nor substituting a mixed-evidence ranking fulfills the specified criterion.

## Design

Recognize only a bounded, affirmative target-design request that explicitly asks for generation, property calculation and ranking by general molecular properties. Keep unsupported metric, other requested actions, contradictory exclusions and unspecified ranking criteria on existing fail-closed paths. The original request and requested count remain sealed and are checked by the existing full-Web coverage and generation validators.

For this reviewed pattern, use the existing target search, generator, property calculator and candidate ranker with only the candidate and property outputs as ranking inputs. Do not run ADMET or activity unless separately authorized; mixed requests remain outside this narrow route. The existing ranker orders by its documented property component (QED and a LogP window), scaled by its fixed property weight. Describe that precise heuristic and its limitations in the result; never call it binding affinity, activity or experimental validation. Missing or invalid properties remain unrankable, and no missing data are filled in.

The ordering, count, scores and evidence must be derived from authenticated tool results, preserving the original input and producer/consumer binding. Recompute the deterministic ranking from those bound inputs at acceptance; do not publish unverified ranking data in either the modern detail view or legacy result frames, including when acceptance was interrupted. Preserve failure diagnostics and unrelated evidence. Other ranking criteria, endpoints, constraints or negations must not be ignored or interpreted as this heuristic. Keep the existing behavior for all nonmatching requests.

## Verification

First add failing tests for admission, exact step/inputs, explicit criterion preservation, unsafe near-matches and deterministic property-only ranking. Include tampered evidence, partial properties and actual browser reference/selection regression. Then run focused and related regression tests, Python compilation, and launch the app for a real frontend run using the frontend-configured Agent and generation-only model. Report the actual tool results and any unsupported or incomplete steps without presenting fixture values as scientific evidence.
