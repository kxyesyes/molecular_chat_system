"""Shared target identity projection; complete structure evidence stays in results."""


def target_planning_data(data):
    """Keep target identity for Web planning, not its presentation payload."""
    if not isinstance(data, list):
        return data
    fields = (
        "target_id", "gene_symbol", "target_gene", "target_identifier",
        "target_chembl_id", "uniprot_id", "target_name", "protein_name",
        "organism", "target_type", "match_reason", "source_record_id",
        "source", "source_query", "structure_count",
        "downloaded_structure_count", "experimental_structure_count",
        "alphafold_structure_count", "has_experimental_structure",
        "has_alphafold_structure", "structure_evidence_status",
    )
    return [{key: row[key] for key in fields if key in row}
            if isinstance(row, dict) else row for row in data]
