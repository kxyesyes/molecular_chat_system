"""Bulk-sync curated target sets and local structure caches.

This module intentionally avoids mirroring complete public databases. Instead it
builds a local, deployable target/structure subset for common drug-discovery
targets, with the PDE family prioritized.
"""

from __future__ import annotations

import argparse
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

import requests

from .database import get_connection, init_db, resolve_project_root
from .downloader import StructureDownloader, StructureDownloadError


logger = logging.getLogger(__name__)

RCSB_SEARCH_URL = "https://search.rcsb.org/rcsbsearch/v2/query"
RCSB_GRAPHQL_URL = "https://data.rcsb.org/graphql"
UNIPROT_URL = "https://rest.uniprot.org/uniprotkb/{accession}.json"


@dataclass(frozen=True)
class TargetSpec:
    gene_symbol: str
    uniprot_id: str
    target_type: str
    disease_keywords: str
    aliases: tuple[str, ...] = ()
    priority: str = "common"


PDE_TARGETS: tuple[TargetSpec, ...] = (
    TargetSpec("PDE1A", "P54750", "Phosphodiesterase", "asthma;neurological disease;cardiovascular disease", ("PDE1",), "pde"),
    TargetSpec("PDE1B", "Q01064", "Phosphodiesterase", "neurological disease;dopamine signaling", ("PDE1B1",), "pde"),
    TargetSpec("PDE1C", "Q14123", "Phosphodiesterase", "cardiovascular disease;asthma", ("PDE1C1",), "pde"),
    TargetSpec("PDE2A", "O00408", "Phosphodiesterase", "cardiovascular disease;neurological disease", ("PDE2",), "pde"),
    TargetSpec("PDE3A", "Q14432", "Phosphodiesterase", "heart failure;platelet aggregation;cardiovascular disease", ("CGI-PDE",), "pde"),
    TargetSpec("PDE3B", "Q13370", "Phosphodiesterase", "diabetes;obesity;metabolic disease", ("HcGIP1",), "pde"),
    TargetSpec("PDE4A", "P27815", "Phosphodiesterase", "COPD;asthma;inflammation", ("DPDE2",), "pde"),
    TargetSpec("PDE4B", "Q07343", "Phosphodiesterase", "COPD;inflammation;depression", ("DPDE4",), "pde"),
    TargetSpec("PDE4C", "Q08493", "Phosphodiesterase", "inflammation;respiratory disease", ("DPDE1",), "pde"),
    TargetSpec("PDE4D", "Q08499", "Phosphodiesterase", "COPD;asthma;memory;stroke", ("DPDE3",), "pde"),
    TargetSpec("PDE5A", "O76074", "Phosphodiesterase", "pulmonary hypertension;erectile dysfunction;cardiovascular disease", ("PDE5",), "pde"),
    TargetSpec("PDE6A", "P16499", "Phosphodiesterase", "retinal degeneration;vision disorder", ("PDEA",), "pde"),
    TargetSpec("PDE6B", "P35913", "Phosphodiesterase", "retinitis pigmentosa;vision disorder", ("PDEB",), "pde"),
    TargetSpec("PDE6C", "P51160", "Phosphodiesterase", "achromatopsia;vision disorder", ("PDEC",), "pde"),
    TargetSpec("PDE6D", "O43924", "Phosphodiesterase regulator", "retinal disease;prenylated protein trafficking", ("PDED",), "pde"),
    TargetSpec("PDE6G", "P18545", "Phosphodiesterase regulator", "retinal disease;phototransduction", ("PDEG",), "pde"),
    TargetSpec("PDE6H", "Q13956", "Phosphodiesterase regulator", "retinal disease;phototransduction", ("PDE6H",), "pde"),
    TargetSpec("PDE7A", "Q13946", "Phosphodiesterase", "inflammation;immune disease", ("HCP1",), "pde"),
    TargetSpec("PDE7B", "Q9NP56", "Phosphodiesterase", "inflammation;neurological disease", ("PDE7B",), "pde"),
    TargetSpec("PDE8A", "O60658", "Phosphodiesterase", "endocrine disease;steroidogenesis", ("PDE8",), "pde"),
    TargetSpec("PDE8B", "O95263", "Phosphodiesterase", "thyroid disease;adrenal disease", ("PDE8B",), "pde"),
    TargetSpec("PDE9A", "O76083", "Phosphodiesterase", "heart failure;neurodegeneration;Alzheimer disease", ("PDE9",), "pde"),
    TargetSpec("PDE10A", "Q9Y233", "Phosphodiesterase", "schizophrenia;Huntington disease;neurological disease", ("PDE10",), "pde"),
    TargetSpec("PDE11A", "Q9HCR9", "Phosphodiesterase", "adrenal disease;endocrine tumor;fertility", ("PDE11",), "pde"),
)

COMMON_TARGETS: tuple[TargetSpec, ...] = (
    TargetSpec("EGFR", "P00533", "Kinase", "lung cancer;breast cancer;glioblastoma", ("ERBB1", "HER1")),
    TargetSpec("WDR5", "P61964", "Epigenetic regulator", "MYC;leukemia;cancer", ("SWD3", "BIG3")),
    TargetSpec("CYP3A4", "P08684", "Enzyme", "drug metabolism;DDI;toxicity", ("CP34", "CYP3A")),
    TargetSpec("KRAS", "P01116", "GTPase", "pancreatic cancer;colorectal cancer;lung cancer", ("KRAS2",)),
    TargetSpec("BRAF", "P15056", "Kinase", "melanoma;colorectal cancer;thyroid cancer", ("B-RAF",)),
    TargetSpec("JAK2", "O60674", "Kinase", "myelofibrosis;polycythemia vera;leukemia", ("JTK10",)),
    TargetSpec("PIK3CA", "P42336", "Kinase", "breast cancer;solid tumor;overgrowth syndrome", ("PI3KCA",)),
    TargetSpec("ALK", "Q9UM73", "Kinase", "lung cancer;lymphoma;neuroblastoma", ("CD246",)),
    TargetSpec("MET", "P08581", "Kinase", "lung cancer;gastric cancer;papillary renal carcinoma", ("HGFR",)),
    TargetSpec("HDAC1", "Q13547", "Epigenetic enzyme", "cancer;lymphoma;neurological disease", ("HD1",)),
    TargetSpec("CDK2", "P24941", "Kinase", "cancer;cell cycle disorder", ("p33(CDK2)",)),
    TargetSpec("KDR", "P35968", "Kinase", "angiogenesis;cancer;macular degeneration", ("VEGFR2", "FLK1")),
    TargetSpec("MYC", "P01106", "Transcription factor", "MYC-driven cancer;lymphoma;leukemia", ("c-Myc",)),
    TargetSpec("TP53", "P04637", "Tumor suppressor", "cancer;Li-Fraumeni syndrome", ("p53",)),
    TargetSpec("AURKA", "O14965", "Kinase", "cancer;aneuploidy", ("Aurora-A", "STK15")),
    TargetSpec("AKT1", "P31749", "Kinase", "breast cancer;solid tumor;PI3K AKT signaling", ("PKB", "RAC-alpha")),
    TargetSpec("SRC", "P12931", "Kinase", "cancer;metastasis;bone disease", ("SRC1", "c-Src")),
    TargetSpec("LCK", "P06239", "Kinase", "T-cell signaling;autoimmune disease;leukemia", ("p56-LCK",)),
    TargetSpec("BTK", "Q06187", "Kinase", "B-cell malignancy;leukemia;lymphoma", ("AGMX1", "ATK")),
    TargetSpec("ABL1", "P00519", "Kinase", "chronic myeloid leukemia;acute lymphoblastic leukemia", ("ABL", "BCR-ABL")),
    TargetSpec("FGFR1", "P11362", "Kinase", "cancer;angiogenesis;skeletal disorder", ("FLT2", "CEK")),
    TargetSpec("FGFR2", "P21802", "Kinase", "cancer;cholangiocarcinoma;gastric cancer", ("BEK", "KGFR")),
    TargetSpec("FGFR3", "P22607", "Kinase", "bladder cancer;multiple myeloma;skeletal dysplasia", ("ACH", "CEK2")),
    TargetSpec("FGFR4", "P22455", "Kinase", "liver cancer;FGF signaling", ("JTK2", "TKF")),
    TargetSpec("ERBB2", "P04626", "Kinase", "breast cancer;gastric cancer;ERBB signaling", ("HER2", "NEU")),
    TargetSpec("RET", "P07949", "Kinase", "thyroid cancer;lung cancer;RET fusion", ("CDHF12",)),
    TargetSpec("ROS1", "P08922", "Kinase", "lung cancer;ROS1 fusion;glioblastoma", ("MCF3",)),
    TargetSpec("BRD4", "O60885", "Epigenetic reader", "cancer;inflammation;transcriptional regulation", ("HUNK1",)),
    TargetSpec("EZH2", "Q15910", "Epigenetic enzyme", "lymphoma;prostate cancer;epigenetic regulation", ("KMT6",)),
    TargetSpec("DNMT1", "P26358", "Epigenetic enzyme", "cancer;DNA methylation;epigenetic regulation", ("MCMT",)),
    TargetSpec("KDM1A", "O60341", "Epigenetic enzyme", "acute myeloid leukemia;neuroblastoma;epigenetic regulation", ("LSD1", "AOF2")),
    TargetSpec("HDAC2", "Q92769", "Epigenetic enzyme", "cancer;neurological disease;chromatin remodeling", ("HD2",)),
    TargetSpec("HDAC3", "O15379", "Epigenetic enzyme", "cancer;inflammation;chromatin remodeling", ("HD3",)),
    TargetSpec("HDAC6", "Q9UBN7", "Epigenetic enzyme", "cancer;neurodegeneration;protein deacetylation", ("HD6",)),
    TargetSpec("PARP1", "P09874", "DNA repair enzyme", "ovarian cancer;breast cancer;DNA damage response", ("ADPRT", "PPOL")),
    TargetSpec("MDM2", "Q00987", "E3 ligase", "cancer;p53 pathway;protein-protein interaction", ("HDM2",)),
    TargetSpec("MTOR", "P42345", "Kinase", "cancer;metabolic disease;immune modulation", ("FRAP1", "RAFT1")),
    TargetSpec("AR", "P10275", "Nuclear receptor", "prostate cancer;androgen signaling", ("NR3C4",)),
    TargetSpec("ESR1", "P03372", "Nuclear receptor", "breast cancer;endocrine therapy;estrogen signaling", ("ER-alpha", "NR3A1")),
    TargetSpec("PPARG", "P37231", "Nuclear receptor", "diabetes;metabolic disease;inflammation", ("NR1C3", "PPAR-gamma")),
    TargetSpec("DPP4", "P27487", "Protease", "diabetes;metabolic disease;inflammation", ("CD26",)),
    TargetSpec("BACE1", "P56817", "Protease", "Alzheimer disease;amyloid processing", ("ASP2", "Memapsin-2")),
    TargetSpec("MAOA", "P21397", "Enzyme", "depression;neurological disease;monoamine metabolism", ("MAO-A",)),
    TargetSpec("MAOB", "P27338", "Enzyme", "Parkinson disease;neurological disease;monoamine metabolism", ("MAO-B",)),
    TargetSpec("PTGS1", "P23219", "Enzyme", "inflammation;pain;prostaglandin biosynthesis", ("COX1",)),
    TargetSpec("PTGS2", "P35354", "Enzyme", "inflammation;pain;cancer;prostaglandin biosynthesis", ("COX2",)),
    TargetSpec("IDO1", "P14902", "Enzyme", "cancer immunotherapy;tryptophan metabolism;immune suppression", ("INDO",)),
    TargetSpec("ADRB2", "P07550", "GPCR", "asthma;COPD;cardiovascular disease", ("B2AR", "BAR")),
    TargetSpec("DRD2", "P14416", "GPCR", "schizophrenia;Parkinson disease;dopamine signaling", ("D2DR",)),
    TargetSpec("HTR2A", "P28223", "GPCR", "psychiatric disease;serotonin signaling", ("5-HT2A",)),
    TargetSpec("OPRM1", "P35372", "GPCR", "pain;opioid signaling;addiction", ("MOR1",)),
    TargetSpec("KCNH2", "Q12809", "Ion channel", "cardiotoxicity;long QT syndrome;hERG liability", ("HERG", "Kv11.1")),
    TargetSpec("MAPK1", "P28482", "Kinase", "cancer;inflammation;MAPK signaling", ("ERK2", "p42-MAPK")),
    TargetSpec("MAPK14", "Q16539", "Kinase", "inflammation;pain;autoimmune disease", ("p38-alpha", "CSBP")),
    TargetSpec("CHEK1", "O14757", "Kinase", "DNA damage response;cancer;cell cycle checkpoint", ("CHK1",)),
    TargetSpec("CDK4", "P11802", "Kinase", "breast cancer;melanoma;cell cycle disorder", ("PSK-J3",)),
    TargetSpec("CDK6", "Q00534", "Kinase", "breast cancer;leukemia;cell cycle disorder", ("PLSTIRE",)),
    TargetSpec("GSK3B", "P49841", "Kinase", "neurodegeneration;diabetes;cancer signaling", ("GSK3-beta",)),
    TargetSpec("FLT3", "P36888", "Kinase", "acute myeloid leukemia;hematologic malignancy", ("CD135",)),
    TargetSpec("KIT", "P10721", "Kinase", "gastrointestinal stromal tumor;mastocytosis;melanoma", ("CD117", "SCFR")),
    TargetSpec("SYK", "P43405", "Kinase", "B-cell malignancy;autoimmune disease;inflammation", ("p72-Syk",)),
    TargetSpec("CSF1R", "P07333", "Kinase", "cancer;macrophage biology;inflammation", ("FMS", "CD115")),
    TargetSpec("NTRK1", "P04629", "Kinase", "NTRK fusion cancer;pain;neurotrophic signaling", ("TRKA",)),
    TargetSpec("NTRK2", "Q16620", "Kinase", "NTRK fusion cancer;neurological disease", ("TRKB",)),
    TargetSpec("NTRK3", "Q16288", "Kinase", "NTRK fusion cancer;secretory carcinoma", ("TRKC",)),
    TargetSpec("PLK1", "P53350", "Kinase", "cancer;mitosis;cell cycle disorder", ("STPK13",)),
    TargetSpec("RIPK1", "Q13546", "Kinase", "inflammation;necroptosis;autoimmune disease", ("RIP1",)),
    TargetSpec("ROCK1", "Q13464", "Kinase", "fibrosis;cardiovascular disease;cancer migration", ("ROK-beta",)),
    TargetSpec("ROCK2", "O75116", "Kinase", "fibrosis;autoimmune disease;cardiovascular disease", ("ROK-alpha",)),
    TargetSpec("ACE", "P12821", "Metalloprotease", "hypertension;cardiovascular disease;renin angiotensin signaling", ("DCP1", "ACE1")),
    TargetSpec("ACE2", "Q9BYF1", "Metalloprotease", "cardiovascular disease;viral entry;renin angiotensin signaling", ("ACEH",)),
    TargetSpec("HMGCR", "P04035", "Enzyme", "hypercholesterolemia;cardiovascular disease;lipid metabolism", ("HMG-CoA reductase",)),
    TargetSpec("FASN", "P49327", "Enzyme", "cancer;metabolic disease;fatty acid biosynthesis", ("FAS",)),
    TargetSpec("NAMPT", "P43490", "Enzyme", "cancer;inflammation;NAD metabolism", ("PBEF", "visfatin")),
    TargetSpec("FAAH", "O00519", "Enzyme", "pain;inflammation;endocannabinoid metabolism", ("FAAH1",)),
    TargetSpec("DHFR", "P00374", "Enzyme", "cancer;infection;folate metabolism", ("DHFRP1",)),
    TargetSpec("TYMS", "P04818", "Enzyme", "cancer;folate metabolism;DNA synthesis", ("TS",)),
    TargetSpec("CA2", "P00918", "Enzyme", "glaucoma;epilepsy;carbonic anhydrase inhibition", ("CA-II",)),
    TargetSpec("CA9", "Q16790", "Enzyme", "solid tumor;hypoxia;cancer metabolism", ("CAIX",)),
    TargetSpec("LDHA", "P00338", "Enzyme", "cancer metabolism;glycolysis;Warburg effect", ("LDH-A",)),
    TargetSpec("SLC6A4", "P31645", "Transporter", "depression;anxiety;serotonin reuptake", ("SERT", "5-HTT")),
    TargetSpec("SLC6A3", "Q01959", "Transporter", "Parkinson disease;ADHD;dopamine reuptake", ("DAT", "DAT1")),
    TargetSpec("ADORA2A", "P29274", "GPCR", "Parkinson disease;inflammation;adenosine signaling", ("A2A", "ADORA2")),
    TargetSpec("HRH1", "P35367", "GPCR", "allergy;inflammation;histamine signaling", ("H1R",)),
    TargetSpec("CHRM3", "P20309", "GPCR", "COPD;overactive bladder;cholinergic signaling", ("M3R",)),
    TargetSpec("S1PR1", "P21453", "GPCR", "multiple sclerosis;immune trafficking;sphingosine signaling", ("S1P1", "EDG1")),
    TargetSpec("CCR5", "P51681", "GPCR", "HIV;inflammation;immune cell trafficking", ("CD195",)),
    TargetSpec("CXCR4", "P61073", "GPCR", "cancer metastasis;HIV;immune trafficking", ("CD184",)),
    TargetSpec("CCR2", "P41597", "GPCR", "inflammation;monocyte trafficking;metabolic disease", ("CD192",)),
    TargetSpec("VDR", "P11473", "Nuclear receptor", "bone disease;immune modulation;vitamin D signaling", ("NR1I1",)),
    TargetSpec("NR3C1", "P04150", "Nuclear receptor", "inflammation;autoimmune disease;glucocorticoid signaling", ("GR", "GCR")),
    TargetSpec("RARA", "P10276", "Nuclear receptor", "acute promyelocytic leukemia;retinoid signaling", ("RAR-alpha", "NR1B1")),
    TargetSpec("BCL2", "P10415", "Apoptosis regulator", "lymphoma;leukemia;apoptosis evasion", ("Bcl-2",)),
    TargetSpec("MCL1", "Q07820", "Apoptosis regulator", "cancer;apoptosis evasion;hematologic malignancy", ("BCL2L3",)),
    TargetSpec("XIAP", "P98170", "Apoptosis regulator", "cancer;apoptosis evasion;IAP family", ("BIRC4",)),
    TargetSpec("HSP90AA1", "P07900", "Chaperone", "cancer;protein folding;stress response", ("HSP90A", "HSPC1")),
    TargetSpec("PSMB5", "P28074", "Proteasome subunit", "multiple myeloma;proteasome inhibition;protein degradation", ("MB1",)),
    TargetSpec("MMP2", "P08253", "Protease", "cancer invasion;fibrosis;extracellular matrix remodeling", ("MMP-2",)),
    TargetSpec("MMP9", "P14780", "Protease", "cancer invasion;inflammation;extracellular matrix remodeling", ("MMP-9",)),
    TargetSpec("ADAM17", "P78536", "Protease", "inflammation;TNF shedding;EGFR ligand shedding", ("TACE",)),
    TargetSpec("PRMT5", "O14744", "Epigenetic enzyme", "cancer;splicing regulation;arginine methylation", ("HRMT1L5",)),
    TargetSpec("DOT1L", "Q8TEK3", "Epigenetic enzyme", "MLL-rearranged leukemia;histone methylation", ("KMT4",)),
    TargetSpec("EP300", "Q09472", "Epigenetic enzyme", "cancer;transcriptional regulation;histone acetylation", ("p300",)),
    TargetSpec("CREBBP", "Q92793", "Epigenetic coactivator", "cancer;Rubinstein-Taybi syndrome;histone acetylation", ("CBP",)),
    TargetSpec("PCSK9", "Q8NBP7", "Protease", "hypercholesterolemia;cardiovascular disease;LDL receptor regulation", ("NARC1",)),
    TargetSpec("TSPO", "P30536", "Mitochondrial receptor", "neuroinflammation;PET imaging;steroidogenesis", ("PBR",)),
    TargetSpec("TTR", "P02766", "Transport protein", "amyloidosis;transthyretin stabilization", ("ATTR",)),
)


def target_specs(name: str) -> tuple[TargetSpec, ...]:
    if name == "pde":
        return PDE_TARGETS
    if name == "common":
        return COMMON_TARGETS
    if name == "all":
        seen = {}
        for spec in (*COMMON_TARGETS, *PDE_TARGETS):
            seen[spec.uniprot_id] = spec
        return tuple(seen.values())
    raise ValueError(f"Unknown target set: {name}")


class TargetBulkSync:
    def __init__(self, project_root: Optional[Path | str] = None, delay_seconds: float = 0.15):
        self.project_root = resolve_project_root(project_root)
        init_db(self.project_root)
        self.session = requests.Session()
        self.delay_seconds = delay_seconds
        self.downloader = StructureDownloader(self.project_root)

    def sync(
        self,
        specs: Iterable[TargetSpec],
        max_rcsb_per_target: Optional[int] = 20,
        index_rcsb: bool = True,
        include_alphafold: bool = True,
        download_rcsb: bool = False,
        download_alphafold: bool = True,
        file_format: str = "cif",
    ) -> dict:
        init_db(self.project_root)
        summary = {
            "targets": 0,
            "rcsb_structures": 0,
            "alphafold_structures": 0,
            "downloaded": 0,
            "download_failed": 0,
            "errors": [],
        }

        for spec in specs:
            try:
                uniprot = self.fetch_uniprot(spec)
                target_id = self.upsert_target(spec, uniprot)
                summary["targets"] += 1

                if index_rcsb:
                    pdb_ids = self.search_rcsb_entries(spec.uniprot_id, max_rcsb_per_target)
                    entries = self.fetch_rcsb_entries(pdb_ids) if pdb_ids else []
                    for rank, entry in enumerate(entries):
                        structure_id = self.upsert_rcsb_structure(target_id, spec, entry, rank, file_format)
                        summary["rcsb_structures"] += 1
                        if download_rcsb:
                            if self.download_structure(structure_id, file_format):
                                summary["downloaded"] += 1
                            else:
                                summary["download_failed"] += 1

                if include_alphafold:
                    structure_id = self.upsert_alphafold_structure(target_id, spec, file_format)
                    summary["alphafold_structures"] += 1
                    if download_alphafold:
                        if self.download_structure(structure_id, file_format):
                            summary["downloaded"] += 1
                        else:
                            summary["download_failed"] += 1
            except Exception as exc:
                logger.warning("Target sync failed for %s: %s", spec.gene_symbol, exc)
                summary["errors"].append(f"{spec.gene_symbol}: {exc}")

        return summary

    def fetch_uniprot(self, spec: TargetSpec) -> dict:
        url = UNIPROT_URL.format(accession=spec.uniprot_id)
        response = self._request("GET", url)
        if not response:
            return {}
        return response.json()

    def search_rcsb_entries(self, uniprot_id: str, max_entries: Optional[int]) -> list[str]:
        rows = 1000 if not max_entries or max_entries <= 0 else max_entries
        payload = {
            "query": {
                "type": "terminal",
                "service": "text",
                "parameters": {
                    "attribute": "rcsb_polymer_entity_container_identifiers.reference_sequence_identifiers.database_accession",
                    "operator": "exact_match",
                    "value": uniprot_id,
                },
            },
            "return_type": "entry",
            "request_options": {
                "paginate": {"start": 0, "rows": rows},
                "sort": [{"sort_by": "score", "direction": "desc"}],
            },
        }
        response = self._request("POST", RCSB_SEARCH_URL, json=payload)
        if not response:
            return []
        return [item["identifier"] for item in response.json().get("result_set", [])]

    def fetch_rcsb_entries(self, pdb_ids: list[str]) -> list[dict]:
        if not pdb_ids:
            return []
        query = """
        query structure($ids:[String!]!){
          entries(entry_ids:$ids){
            rcsb_id
            struct { title }
            exptl { method }
            rcsb_entry_info { resolution_combined nonpolymer_entity_count }
            polymer_entities {
              rcsb_polymer_entity_container_identifiers {
                auth_asym_ids
                asym_ids
                reference_sequence_identifiers { database_accession database_name }
              }
              rcsb_entity_source_organism { ncbi_scientific_name }
            }
            nonpolymer_entities { pdbx_entity_nonpoly { comp_id name } }
          }
        }
        """
        response = self._request("POST", RCSB_GRAPHQL_URL, json={"query": query, "variables": {"ids": pdb_ids}})
        if not response:
            return []
        entries = response.json().get("data", {}).get("entries", [])
        return [entry for entry in entries if entry]

    def upsert_target(self, spec: TargetSpec, uniprot: dict) -> int:
        protein_name = _protein_name(uniprot) or spec.gene_symbol
        description = _function_text(uniprot) or f"{spec.gene_symbol} target imported from UniProt/RCSB sync."
        aliases = (spec.gene_symbol, *spec.aliases)
        conn = get_connection(self.project_root)
        try:
            conn.execute(
                """
                INSERT INTO targets (
                    gene_symbol, protein_name, uniprot_id, organism, target_type,
                    description, function_summary, pathway, known_drugs,
                    representative_ligands, external_links, disease_keywords,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
                ON CONFLICT(uniprot_id) DO UPDATE SET
                    gene_symbol = excluded.gene_symbol,
                    protein_name = excluded.protein_name,
                    organism = excluded.organism,
                    target_type = excluded.target_type,
                    description = excluded.description,
                    function_summary = excluded.function_summary,
                    pathway = excluded.pathway,
                    external_links = excluded.external_links,
                    disease_keywords = excluded.disease_keywords,
                    updated_at = datetime('now')
                """,
                (
                    spec.gene_symbol,
                    protein_name,
                    spec.uniprot_id,
                    "Homo sapiens",
                    spec.target_type,
                    description,
                    description,
                    _pathway_text(spec),
                    "",
                    "",
                    f"UniProt|https://www.uniprot.org/uniprotkb/{spec.uniprot_id}/entry;RCSB Search|https://www.rcsb.org/search?request={spec.uniprot_id}",
                    spec.disease_keywords,
                ),
            )
            row = conn.execute("SELECT id FROM targets WHERE uniprot_id = ?", (spec.uniprot_id,)).fetchone()
            target_id = int(row["id"])
            for alias in aliases:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO target_aliases (target_id, alias, alias_type, source)
                    VALUES (?, ?, ?, ?)
                    """,
                    (target_id, alias, "symbol", "bulk_sync"),
                )
            conn.commit()
            return target_id
        finally:
            conn.close()

    def upsert_rcsb_structure(
        self,
        target_id: int,
        spec: TargetSpec,
        entry: dict,
        rank: int,
        file_format: str,
    ) -> int:
        pdb_id = entry["rcsb_id"].upper()
        chain_ids = _chain_ids_for_uniprot(entry, spec.uniprot_id)
        ligand_ids = _ligand_ids(entry)
        resolution = _resolution(entry)
        method = _method(entry)
        organism = _organism(entry)
        local_path = f"data/target_db/cache/rcsb/{spec.gene_symbol}/{pdb_id}.{file_format}"
        docking_recommended = int(method in {"X-ray", "X-RAY DIFFRACTION", "Cryo-EM", "ELECTRON MICROSCOPY"} and bool(ligand_ids))
        is_preferred = int(rank == 0)
        quality_note = _rcsb_quality_note(resolution, ligand_ids, docking_recommended)
        return self._upsert_structure(
            target_id=target_id,
            structure_id=pdb_id,
            source="RCSB_PDB",
            structure_type="experimental",
            method=_normalize_method(method),
            resolution=resolution,
            chain_ids=";".join(chain_ids),
            ligand_ids=";".join(ligand_ids),
            organism=organism,
            title=entry.get("struct", {}).get("title") or f"{spec.gene_symbol} RCSB structure {pdb_id}",
            file_format=file_format,
            local_file_path=local_path,
            download_url=f"https://files.rcsb.org/download/{pdb_id}.{file_format}",
            is_preferred=is_preferred,
            docking_recommended=docking_recommended,
            quality_note=quality_note,
        )

    def upsert_alphafold_structure(self, target_id: int, spec: TargetSpec, file_format: str) -> int:
        ext = "cif" if file_format not in {"cif", "pdb"} else file_format
        model_id = f"AF-{spec.uniprot_id}-F1"
        local_path = f"data/target_db/cache/alphafold/{spec.uniprot_id}/{model_id}-model_v6.{ext}"
        return self._upsert_structure(
            target_id=target_id,
            structure_id=model_id,
            source="AlphaFold",
            structure_type="predicted",
            method="Predicted",
            resolution=None,
            chain_ids="A",
            ligand_ids="",
            organism="Homo sapiens",
            title=f"AlphaFold predicted model for {spec.gene_symbol}",
            file_format=ext,
            local_file_path=local_path,
            download_url=f"https://alphafold.ebi.ac.uk/files/{model_id}-model_v6.{ext}",
            is_preferred=0,
            docking_recommended=0,
            quality_note="AlphaFold predicted model; useful as structural reference when experimental structures are unavailable.",
        )

    def download_structure(self, structure_id: int, file_format: str) -> bool:
        conn = get_connection(self.project_root)
        try:
            structure = conn.execute("SELECT * FROM target_structures WHERE id = ?", (structure_id,)).fetchone()
        finally:
            conn.close()
        if not structure:
            return False
        try:
            self.downloader.prepare_structure_file(structure, requested_format=file_format)
            return True
        except StructureDownloadError as exc:
            logger.warning("Structure download failed for %s: %s", structure.get("structure_id"), exc)
            return False

    def _upsert_structure(self, **values) -> int:
        conn = get_connection(self.project_root)
        try:
            conn.execute(
                """
                INSERT INTO target_structures (
                    target_id, structure_id, source, structure_type, method, resolution,
                    chain_ids, ligand_ids, organism, title, file_format, local_file_path,
                    download_url, is_downloaded, is_preferred, docking_recommended,
                    quality_note, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, datetime('now'), datetime('now'))
                ON CONFLICT(target_id, structure_id, source, file_format) DO UPDATE SET
                    structure_type = excluded.structure_type,
                    method = excluded.method,
                    resolution = excluded.resolution,
                    chain_ids = excluded.chain_ids,
                    ligand_ids = excluded.ligand_ids,
                    organism = excluded.organism,
                    title = excluded.title,
                    local_file_path = excluded.local_file_path,
                    download_url = excluded.download_url,
                    is_preferred = excluded.is_preferred,
                    docking_recommended = excluded.docking_recommended,
                    quality_note = excluded.quality_note,
                    updated_at = datetime('now')
                """,
                (
                    values["target_id"],
                    values["structure_id"],
                    values["source"],
                    values["structure_type"],
                    values["method"],
                    values["resolution"],
                    values["chain_ids"],
                    values["ligand_ids"],
                    values["organism"],
                    values["title"],
                    values["file_format"],
                    values["local_file_path"],
                    values["download_url"],
                    values["is_preferred"],
                    values["docking_recommended"],
                    values["quality_note"],
                ),
            )
            row = conn.execute(
                """
                SELECT id FROM target_structures
                WHERE target_id = ? AND structure_id = ? AND source = ? AND file_format = ?
                """,
                (values["target_id"], values["structure_id"], values["source"], values["file_format"]),
            ).fetchone()
            conn.commit()
            return int(row["id"])
        finally:
            conn.close()

    def _request(self, method: str, url: str, **kwargs) -> Optional[requests.Response]:
        for attempt in range(3):
            try:
                response = self.session.request(method, url, timeout=35, **kwargs)
                if response.status_code == 204:
                    return None
                response.raise_for_status()
                if self.delay_seconds:
                    time.sleep(self.delay_seconds)
                return response
            except requests.RequestException as exc:
                if attempt == 2:
                    logger.warning("Request failed: %s %s (%s)", method, url, exc)
                    return None
                time.sleep(0.6 * (attempt + 1))
        return None


def _protein_name(uniprot: dict) -> str:
    description = uniprot.get("proteinDescription") or {}
    recommended = description.get("recommendedName") or {}
    full_name = recommended.get("fullName") or {}
    return full_name.get("value", "")


def _function_text(uniprot: dict) -> str:
    for comment in uniprot.get("comments", []):
        if comment.get("commentType") == "FUNCTION":
            texts = comment.get("texts") or []
            if texts:
                return texts[0].get("value", "")
    return ""


def _pathway_text(spec: TargetSpec) -> str:
    if spec.priority == "pde":
        return "Cyclic nucleotide signaling; cAMP/cGMP phosphodiesterase regulation"
    return "Imported common target set"


def _chain_ids_for_uniprot(entry: dict, uniprot_id: str) -> list[str]:
    chains = []
    for entity in entry.get("polymer_entities") or []:
        identifiers = entity.get("rcsb_polymer_entity_container_identifiers") or {}
        refs = identifiers.get("reference_sequence_identifiers") or []
        if any(ref.get("database_accession") == uniprot_id for ref in refs):
            chains.extend(identifiers.get("auth_asym_ids") or identifiers.get("asym_ids") or [])
    return sorted(set(chains)) or ["A"]


def _ligand_ids(entry: dict) -> list[str]:
    excluded = {"HOH", "WAT", "DOD"}
    ligands = []
    for entity in entry.get("nonpolymer_entities") or []:
        comp = (entity.get("pdbx_entity_nonpoly") or {}).get("comp_id")
        if comp and comp not in excluded:
            ligands.append(comp)
    return sorted(set(ligands))


def _resolution(entry: dict) -> Optional[float]:
    values = (entry.get("rcsb_entry_info") or {}).get("resolution_combined") or []
    if not values:
        return None
    return float(values[0])


def _method(entry: dict) -> str:
    methods = entry.get("exptl") or []
    return methods[0].get("method", "") if methods else ""


def _normalize_method(method: str) -> str:
    upper = (method or "").upper()
    if "X-RAY" in upper:
        return "X-ray"
    if "ELECTRON" in upper:
        return "Cryo-EM"
    if "NMR" in upper:
        return "NMR"
    return method or "-"


def _organism(entry: dict) -> str:
    for entity in entry.get("polymer_entities") or []:
        organisms = entity.get("rcsb_entity_source_organism") or []
        if organisms:
            return organisms[0].get("ncbi_scientific_name") or "Homo sapiens"
    return "Homo sapiens"


def _rcsb_quality_note(resolution: Optional[float], ligand_ids: list[str], docking_recommended: int) -> str:
    notes = ["Experimental RCSB structure imported by bulk sync"]
    if resolution:
        notes.append(f"resolution {resolution} A")
    if ligand_ids:
        notes.append(f"ligand(s): {';'.join(ligand_ids[:8])}")
    if docking_recommended:
        notes.append("candidate for docking review")
    return "; ".join(notes) + "."


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync curated target sets and structure caches.")
    parser.add_argument("--set", choices=["pde", "common", "all"], default="pde")
    parser.add_argument("--max-rcsb-per-target", type=int, default=20, help="0 means index all returned RCSB entries.")
    parser.add_argument("--download-rcsb", action="store_true", help="Download RCSB coordinate files after indexing.")
    parser.add_argument("--no-download-af", action="store_true", help="Do not download AlphaFold models.")
    parser.add_argument("--format", choices=["cif", "pdb"], default="cif")
    parser.add_argument("--project-root", default=None)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    syncer = TargetBulkSync(args.project_root)
    summary = syncer.sync(
        target_specs(args.set),
        max_rcsb_per_target=args.max_rcsb_per_target,
        download_rcsb=args.download_rcsb,
        download_alphafold=not args.no_download_af,
        file_format=args.format,
    )
    print(summary)


if __name__ == "__main__":
    main()
