# Model source verification plan

Scope: fix the observed hERG model-source followup; keep prediction values and existing model selection unchanged.
1. Retain installed endpoint dataset URL and model version in accepted session records.
2. Recognize the complete provenance question, bind only owned hERG model records and honor tool enablement.
3. Fetch fixed official TDC hERG section with time/size bounds; identify binary labels and referenced DOI without using neighboring datasets. Retrieve that DOI through existing Europe PMC client. Report raw-paper assay cutoff unverified.
4. Preserve provenance separately from scientific prediction evidence; use deterministic source-bound presentation.
5. Focused regression tests then actual frontend prediction + source followup, screenshot and temporary server shutdown.

Alternatives considered: generic literature search may mix datasets; model-only explanation cannot verify sources. Use package-to-official-dataset link plus DOI metadata lookup. No new dependency or generic crawler.
