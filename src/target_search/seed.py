"""Seed and rebuild the local SQLite target database."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Optional

from .database import get_target_db_dir, init_db, get_connection, resolve_project_root


DEFAULT_ALIASES = {
    "EGFR": ["ERBB1", "HER1"],
    "WDR5": ["SWD3", "BIG3"],
    "CYP3A4": ["CP34", "CYP3A"],
    "KRAS": ["KRAS2", "RASK2"],
    "BRAF": ["B-RAF", "BRAF1"],
    "JAK2": ["JTK10"],
    "PIK3CA": ["PI3KCA", "p110alpha"],
    "ALK": ["CD246"],
    "MET": ["HGFR"],
    "HDAC1": ["HD1"],
    "CDK2": ["p33(CDK2)"],
    "KDR": ["VEGFR2", "FLK1"],
    "MYC": ["c-Myc"],
    "TP53": ["p53"],
    "AURKA": ["Aurora-A", "STK15"],
}

TARGET_ENRICHMENT = {
    "EGFR": {
        "pathway": "EGFR/ERBB signaling; PI3K/AKT; RAS/MAPK",
        "known_drugs": "Gefitinib;Erlotinib;Osimertinib;Afatinib",
        "representative_ligands": "AQ4;ATP-site inhibitors",
    },
    "WDR5": {
        "pathway": "MLL/SET histone methyltransferase complex; MYC transcriptional regulation",
        "known_drugs": "OICR-9429;C16;WDR5 WIN-site inhibitors",
        "representative_ligands": "WDR;WIN-site peptidomimetics",
    },
    "CYP3A4": {
        "pathway": "Xenobiotic metabolism; drug-drug interaction liability",
        "known_drugs": "Ketoconazole;Ritonavir;Midazolam",
        "representative_ligands": "HEM;DTZ;azole inhibitors",
    },
    "KRAS": {
        "pathway": "RAS/MAPK signaling",
        "known_drugs": "Sotorasib;Adagrasib",
        "representative_ligands": "GDP;GTP;G12C covalent inhibitors",
    },
    "BRAF": {
        "pathway": "MAPK signaling",
        "known_drugs": "Vemurafenib;Dabrafenib;Encorafenib",
        "representative_ligands": "BAX;RAF kinase inhibitors",
    },
}

TARGET_HEADERS = [
    "gene_symbol", "protein_name", "uniprot_id", "organism", "target_type",
    "description", "function_summary", "disease_keywords", "chembl_target_id",
]

STRUCTURE_HEADERS = [
    "gene_symbol", "structure_id", "source", "structure_type", "method", "resolution",
    "chain_ids", "ligand_ids", "organism", "title", "file_format", "local_file_path",
    "download_url", "docking_recommended", "is_preferred",
]

TARGET_ROWS = [
    ["EGFR", "Epidermal growth factor receptor", "P00533", "Homo sapiens", "Kinase", "Receptor tyrosine kinase involved in cell proliferation", "cell signaling and proliferation", "lung cancer;breast cancer;glioblastoma", "CHEMBL203"],
    ["WDR5", "WD repeat-containing protein 5", "P61964", "Homo sapiens", "Epigenetic regulator", "Scaffold protein involved in chromatin regulation", "histone methyltransferase complex scaffold", "MYC;leukemia;cancer", "CHEMBL4523"],
    ["CYP3A4", "Cytochrome P450 3A4", "P08684", "Homo sapiens", "Enzyme", "Major drug-metabolizing enzyme", "xenobiotic and drug metabolism", "drug metabolism;DDI;toxicity", "CHEMBL340"],
    ["KRAS", "GTPase KRas", "P01116", "Homo sapiens", "GTPase", "Small GTPase frequently mutated in cancer", "RAS/MAPK pathway signaling", "pancreatic cancer;colorectal cancer;lung cancer", "CHEMBL2189121"],
    ["BRAF", "Serine/threonine-protein kinase B-raf", "P15056", "Homo sapiens", "Kinase", "MAP kinase pathway kinase and oncology target", "MAPK pathway kinase", "melanoma;colorectal cancer;thyroid cancer", "CHEMBL5145"],
    ["JAK2", "Tyrosine-protein kinase JAK2", "O60674", "Homo sapiens", "Kinase", "Non-receptor tyrosine kinase in cytokine signaling", "JAK/STAT signaling", "myelofibrosis;polycythemia vera;leukemia", "CHEMBL2971"],
    ["PIK3CA", "Phosphatidylinositol 4,5-bisphosphate 3-kinase catalytic subunit alpha", "P42336", "Homo sapiens", "Kinase", "PI3K catalytic subunit involved in growth signaling", "PI3K/AKT signaling", "breast cancer;solid tumor;overgrowth syndrome", "CHEMBL4005"],
    ["ALK", "ALK tyrosine kinase receptor", "Q9UM73", "Homo sapiens", "Kinase", "Receptor tyrosine kinase with oncogenic fusion variants", "RTK signaling", "lung cancer;lymphoma;neuroblastoma", "CHEMBL4247"],
    ["MET", "Hepatocyte growth factor receptor", "P08581", "Homo sapiens", "Kinase", "Receptor tyrosine kinase activated by HGF", "invasion and growth signaling", "lung cancer;gastric cancer;papillary renal carcinoma", "CHEMBL3717"],
    ["HDAC1", "Histone deacetylase 1", "Q13547", "Homo sapiens", "Epigenetic enzyme", "Histone deacetylase involved in transcriptional repression", "chromatin remodeling", "cancer;lymphoma;neurological disease", "CHEMBL325"],
    ["CDK2", "Cyclin-dependent kinase 2", "P24941", "Homo sapiens", "Kinase", "Cell-cycle kinase regulating G1/S transition", "cell cycle regulation", "cancer;cell cycle disorder", "CHEMBL301"],
    ["KDR", "Vascular endothelial growth factor receptor 2", "P35968", "Homo sapiens", "Kinase", "VEGF receptor driving angiogenesis", "angiogenesis signaling", "angiogenesis;cancer;macular degeneration", "CHEMBL279"],
    ["MYC", "Myc proto-oncogene protein", "P01106", "Homo sapiens", "Transcription factor", "Transcription factor controlling growth and proliferation", "transcriptional regulation", "MYC-driven cancer;lymphoma;leukemia", "CHEMBL1250375"],
    ["TP53", "Cellular tumor antigen p53", "P04637", "Homo sapiens", "Tumor suppressor", "Tumor suppressor transcription factor responding to DNA damage", "DNA damage response", "cancer;Li-Fraumeni syndrome", "CHEMBL4096"],
    ["AURKA", "Aurora kinase A", "O14965", "Homo sapiens", "Kinase", "Mitotic serine/threonine kinase", "mitosis and spindle assembly", "cancer;aneuploidy", "CHEMBL4722"],
]

STRUCTURE_ROWS = [
    ["EGFR", "1M17", "RCSB_PDB", "experimental", "X-ray", "2.6", "A", "AQ4", "Homo sapiens", "EGFR kinase domain with inhibitor", "cif", "data/target_db/cache/rcsb/EGFR/1M17.cif", "", "1", "1"],
    ["EGFR", "AF-P00533-F1", "AlphaFold", "predicted", "Predicted", "", "A", "", "Homo sapiens", "AlphaFold EGFR model", "cif", "data/target_db/cache/alphafold/P00533/AF-P00533-F1-model_v4.cif", "", "0", "0"],
    ["WDR5", "4QL1", "RCSB_PDB", "experimental", "X-ray", "1.7", "A", "WDR", "Homo sapiens", "WDR5 WIN-site complex", "cif", "data/target_db/cache/rcsb/WDR5/4QL1.cif", "", "1", "1"],
    ["WDR5", "AF-P61964-F1", "AlphaFold", "predicted", "Predicted", "", "A", "", "Homo sapiens", "AlphaFold WDR5 model", "cif", "data/target_db/cache/alphafold/P61964/AF-P61964-F1-model_v4.cif", "", "0", "0"],
    ["CYP3A4", "3NXU", "RCSB_PDB", "experimental", "X-ray", "2.05", "A", "HEM;DTZ", "Homo sapiens", "CYP3A4 ligand-bound structure", "cif", "data/target_db/cache/rcsb/CYP3A4/3NXU.cif", "", "1", "1"],
    ["CYP3A4", "AF-P08684-F1", "AlphaFold", "predicted", "Predicted", "", "A", "", "Homo sapiens", "AlphaFold CYP3A4 model", "cif", "data/target_db/cache/alphafold/P08684/AF-P08684-F1-model_v4.cif", "", "0", "0"],
    ["KRAS", "6OIM", "RCSB_PDB", "experimental", "X-ray", "1.9", "A", "GDP", "Homo sapiens", "KRAS G12C covalent inhibitor complex", "cif", "data/target_db/cache/rcsb/KRAS/6OIM.cif", "", "1", "1"],
    ["BRAF", "4RZV", "RCSB_PDB", "experimental", "X-ray", "2.55", "A", "BAX", "Homo sapiens", "BRAF kinase inhibitor complex", "cif", "data/target_db/cache/rcsb/BRAF/4RZV.cif", "", "1", "1"],
    ["JAK2", "2B7A", "RCSB_PDB", "experimental", "X-ray", "2.0", "A", "", "Homo sapiens", "JAK2 kinase domain", "cif", "data/target_db/cache/rcsb/JAK2/2B7A.cif", "", "1", "1"],
    ["PIK3CA", "4JPS", "RCSB_PDB", "experimental", "X-ray", "2.8", "A", "ATP", "Homo sapiens", "PI3K alpha complex", "cif", "data/target_db/cache/rcsb/PIK3CA/4JPS.cif", "", "1", "1"],
    ["ALK", "2XP2", "RCSB_PDB", "experimental", "X-ray", "1.8", "A", "IRE", "Homo sapiens", "ALK kinase domain inhibitor complex", "cif", "data/target_db/cache/rcsb/ALK/2XP2.cif", "", "1", "1"],
    ["MET", "3DKF", "RCSB_PDB", "experimental", "X-ray", "2.0", "A", "ANP", "Homo sapiens", "MET kinase domain", "cif", "data/target_db/cache/rcsb/MET/3DKF.cif", "", "1", "1"],
    ["HDAC1", "4BKX", "RCSB_PDB", "experimental", "X-ray", "3.0", "A", "", "Homo sapiens", "HDAC1 corepressor complex", "cif", "data/target_db/cache/rcsb/HDAC1/4BKX.cif", "", "1", "1"],
    ["CDK2", "1HCK", "RCSB_PDB", "experimental", "X-ray", "1.9", "A", "ATP", "Homo sapiens", "CDK2 ATP complex", "cif", "data/target_db/cache/rcsb/CDK2/1HCK.cif", "", "1", "1"],
    ["KDR", "3VHE", "RCSB_PDB", "experimental", "X-ray", "1.95", "A", "0LI", "Homo sapiens", "VEGFR2 kinase inhibitor complex", "cif", "data/target_db/cache/rcsb/KDR/3VHE.cif", "", "1", "1"],
    ["MYC", "1NKP", "RCSB_PDB", "experimental", "NMR", "", "A;B", "", "Homo sapiens", "MYC-MAX DNA binding domain", "cif", "data/target_db/cache/rcsb/MYC/1NKP.cif", "", "0", "1"],
    ["TP53", "2OCJ", "RCSB_PDB", "experimental", "X-ray", "1.85", "A", "", "Homo sapiens", "p53 DNA-binding domain", "cif", "data/target_db/cache/rcsb/TP53/2OCJ.cif", "", "0", "1"],
    ["AURKA", "3E5A", "RCSB_PDB", "experimental", "X-ray", "2.75", "A", "ADP", "Homo sapiens", "Aurora A kinase domain", "cif", "data/target_db/cache/rcsb/AURKA/3E5A.cif", "", "1", "1"],
]


def ensure_seed_csvs(project_root: Optional[Path | str] = None) -> None:
    root = resolve_project_root(project_root)
    db_dir = get_target_db_dir(root)
    db_dir.mkdir(parents=True, exist_ok=True)
    _write_csv_if_missing(db_dir / "seed_targets.csv", TARGET_HEADERS, TARGET_ROWS)
    _write_csv_if_missing(db_dir / "seed_structures.csv", STRUCTURE_HEADERS, STRUCTURE_ROWS)


def seed_database(project_root: Optional[Path | str] = None) -> dict:
    root = resolve_project_root(project_root)
    init_db(root)
    ensure_seed_csvs(root)

    target_count = 0
    structure_count = 0
    conn = get_connection(root)
    try:
        for target_csv in _target_csv_files(root):
            with target_csv.open("r", newline="", encoding="utf-8") as handle:
                target_count += _seed_targets_from_reader(conn, csv.DictReader(handle))
        conn.commit()

        for structure_csv in _structure_csv_files(root):
            with structure_csv.open("r", newline="", encoding="utf-8") as handle:
                structure_count += _seed_structures_from_reader(conn, csv.DictReader(handle))
        conn.commit()
    finally:
        conn.close()

    _ensure_cache_dirs(root)
    return {"success": True, "targets": target_count, "structures": structure_count}


def rebuild_database(project_root: Optional[Path | str] = None) -> dict:
    root = resolve_project_root(project_root)
    db_path = get_target_db_dir(root) / "target_database.sqlite"
    if db_path.exists():
        db_path.unlink()
    return seed_database(root)


def split_seed_list(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in str(value).replace("|", ";").split(";") if item.strip()]


def _write_csv_if_missing(path: Path, headers: list[str], rows: list[list[str]]) -> None:
    if path.exists():
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(headers)
        writer.writerows(rows)


def _target_csv_files(root: Path) -> list[Path]:
    db_dir = get_target_db_dir(root)
    return [
        path
        for path in (
            db_dir / "seed_targets.csv",
            db_dir / "common_targets.csv",
            db_dir / "pde_targets.csv",
        )
        if path.exists()
    ]


def _structure_csv_files(root: Path) -> list[Path]:
    db_dir = get_target_db_dir(root)
    return [
        path
        for path in (
            db_dir / "seed_structures.csv",
            db_dir / "common_structures.csv",
            db_dir / "pde_structures.csv",
        )
        if path.exists()
    ]


def _seed_targets_from_reader(conn, reader: csv.DictReader) -> int:
    count = 0
    for row in reader:
        if not row.get("gene_symbol") or not row.get("uniprot_id"):
            continue
        conn.execute(
            """
            INSERT INTO targets (
                gene_symbol, protein_name, uniprot_id, organism, target_type,
                description, function_summary, pathway, known_drugs, representative_ligands,
                external_links, disease_keywords, chembl_target_id,
                created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
            ON CONFLICT(uniprot_id) DO UPDATE SET
                gene_symbol = excluded.gene_symbol,
                protein_name = excluded.protein_name,
                organism = excluded.organism,
                target_type = excluded.target_type,
                description = excluded.description,
                function_summary = excluded.function_summary,
                pathway = excluded.pathway,
                known_drugs = excluded.known_drugs,
                representative_ligands = excluded.representative_ligands,
                external_links = excluded.external_links,
                disease_keywords = excluded.disease_keywords,
                chembl_target_id = excluded.chembl_target_id,
                updated_at = datetime('now')
            """,
            (
                row["gene_symbol"],
                row.get("protein_name", ""),
                row["uniprot_id"],
                row.get("organism", "Homo sapiens"),
                row.get("target_type", ""),
                row.get("description", ""),
                row.get("function_summary", ""),
                _enrichment_value(row, "pathway"),
                _enrichment_value(row, "known_drugs"),
                _enrichment_value(row, "representative_ligands"),
                row.get("external_links") or _external_links(row),
                row.get("disease_keywords", ""),
                row.get("chembl_target_id", ""),
            ),
        )
        target = conn.execute(
            "SELECT id FROM targets WHERE uniprot_id = ?",
            (row["uniprot_id"],),
        ).fetchone()
        aliases = [
            row["gene_symbol"],
            *split_seed_list(row.get("aliases", "")),
            *DEFAULT_ALIASES.get(row["gene_symbol"], []),
        ]
        for alias in dict.fromkeys(aliases):
            conn.execute(
                """
                INSERT OR IGNORE INTO target_aliases (target_id, alias, alias_type, source)
                VALUES (?, ?, ?, ?)
                """,
                (target["id"], alias, "symbol", "seed"),
            )
        count += 1
    return count


def _seed_structures_from_reader(conn, reader: csv.DictReader) -> int:
    count = 0
    for row in reader:
        target = conn.execute(
            "SELECT id FROM targets WHERE gene_symbol = ?",
            (row.get("gene_symbol", ""),),
        ).fetchone()
        if not target:
            continue
        resolution = float(row["resolution"]) if row.get("resolution") else None
        download_url = row.get("download_url") or _default_download_url(row)
        local_file_path = row.get("local_file_path", "")
        is_downloaded = int(row.get("is_downloaded") or 0)
        conn.execute(
            """
            INSERT INTO target_structures (
                target_id, structure_id, source, structure_type, method, resolution,
                chain_ids, ligand_ids, organism, title, file_format, local_file_path,
                download_url, is_downloaded, is_preferred, docking_recommended,
                quality_note, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
            ON CONFLICT(target_id, structure_id, source, file_format) DO UPDATE SET
                method = excluded.method,
                resolution = excluded.resolution,
                chain_ids = excluded.chain_ids,
                ligand_ids = excluded.ligand_ids,
                organism = excluded.organism,
                title = excluded.title,
                local_file_path = excluded.local_file_path,
                download_url = excluded.download_url,
                is_downloaded = excluded.is_downloaded,
                is_preferred = excluded.is_preferred,
                docking_recommended = excluded.docking_recommended,
                quality_note = excluded.quality_note,
                updated_at = datetime('now')
            """,
            (
                target["id"],
                row["structure_id"],
                row.get("source", ""),
                row.get("structure_type", ""),
                row.get("method", ""),
                resolution,
                row.get("chain_ids", ""),
                row.get("ligand_ids", ""),
                row.get("organism", "Homo sapiens"),
                row.get("title", ""),
                row.get("file_format", "cif"),
                local_file_path,
                download_url,
                is_downloaded,
                int(row.get("is_preferred") or 0),
                int(row.get("docking_recommended") or 0),
                row.get("quality_note") or _quality_note(row),
            ),
        )
        count += 1
    return count


def _default_download_url(row: dict) -> str:
    if row.get("source") != "RCSB_PDB":
        return row.get("download_url", "")
    file_format = row.get("file_format") or "cif"
    return f"https://files.rcsb.org/download/{row['structure_id'].upper()}.{file_format}"


def _enrichment_value(row: dict, key: str) -> str:
    if row.get(key):
        return row[key]
    return TARGET_ENRICHMENT.get(row["gene_symbol"], {}).get(key, "")


def _external_links(row: dict) -> str:
    uniprot_id = row.get("uniprot_id", "")
    gene = row.get("gene_symbol", "")
    links = [f"UniProt|https://www.uniprot.org/uniprotkb/{uniprot_id}/entry"] if uniprot_id else []
    if row.get("chembl_target_id"):
        links.append(f"ChEMBL|https://www.ebi.ac.uk/chembl/target_report_card/{row['chembl_target_id']}/")
    if gene:
        links.append(f"RCSB Search|https://www.rcsb.org/search?request={gene}")
    return ";".join(links)


def _quality_note(row: dict) -> str:
    if row.get("source") == "AlphaFold":
        return "AlphaFold predicted model; demo stage checks local cache first."
    notes = ["Experimental RCSB structure"]
    if row.get("resolution"):
        notes.append(f"resolution {row['resolution']} A")
    if row.get("ligand_ids"):
        notes.append(f"ligand(s): {row['ligand_ids']}")
    if str(row.get("docking_recommended", "")) == "1":
        notes.append("recommended for docking demo")
    return "; ".join(notes) + "."


def _ensure_cache_dirs(project_root: Path) -> None:
    for structure_csv in _structure_csv_files(project_root):
        with structure_csv.open("r", newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                local_path = row.get("local_file_path")
                if local_path:
                    (project_root / local_path).parent.mkdir(parents=True, exist_ok=True)


def main() -> None:
    result = seed_database()
    print(f"Seeded target database: {result['targets']} targets, {result['structures']} structures")


if __name__ == "__main__":
    main()
