"""Stale target structures must not be advertised as docking inputs."""

from src.agent.tools.target_database_tool import TargetDatabaseTool


class _StaleTargetService:
    def search_targets(self, _query):
        return {
            "status": "resolved",
            "warnings": ["stale_authoritative_cache"],
            "lookup_path": ["local", "UniProt", "RCSB_PDB"],
            "results": [
                {
                    "target_id": None,
                    "gene_symbol": "EGFR",
                    "protein_name": "Epidermal growth factor receptor",
                    "uniprot_id": "P00533",
                    "organism": "Homo sapiens",
                    "source": "UniProt",
                    "source_record_id": "P00533",
                    "source_url": "https://www.uniprot.org/uniprotkb/P00533/entry",
                    "stale": True,
                    "recommended_structures": [
                        {
                            "structure_id": "1ABC",
                            "source": "RCSB_PDB",
                            "stale": True,
                            "docking_recommended": True,
                        }
                    ],
                }
            ],
            "evidence": [],
        }


def test_stale_structure_is_not_docking_recommended():
    tool = TargetDatabaseTool()
    tool._service = _StaleTargetService()

    result = tool.execute("EGFR")

    structure = result["data"][0]["recommended_structures"][0]
    assert structure["stale"] is True
    assert structure["docking_recommended"] is False
    assert "stale_structure_excluded_from_docking" in result["warnings"]
