# Reverse-target result presentation

The real frontend exposed two distinct issues: a fully validated source could
complete while the concise answer omitted its ranked rows, and an unavailable
source displayed both a failure diagnostic and an unsupported success claim.

Publish the already validated, session-bound ranked rows through the negotiated
summary format; keep full unmodified observations in existing details. Show only
the requested bounded count, retain concrete similarity limitations, and keep
technical identifiers out of the main answer. An additive display-kind metadata
value identifies the exact structured rank table for safe single-escape display;
ordinary Markdown tables retain their previous semantics. Unknown display kinds
receive ordinary rendering.

Explicit instructions not to display a rank range must never be treated as a
request to show the first ten. Preserve the owned evidence and ask for the desired
range; indicate that details contain the full ranking only when publication
actually succeeded. No filtering, reordering or inference of scientific data is
performed. Explicit affirmative replacement counts remain supported.

For partial runs without a concise result, show a neutral details affordance and
the specific failure reason rather than claim a scientific result exists. Keep
internal warning tokens in the structured details, not the main answer. A failed
details publication likewise uses a neutral notice.

This is a presentation change, not a new predictor, provenance upgrade, model
change or persistent dataset configuration. Test negative/mixed count scopes,
cross-session and stale evidence, details publication failure, escaped target
names, normal Markdown literal entities, optional metadata and real browser flows.
