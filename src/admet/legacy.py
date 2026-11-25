"""Explicit compatibility calculations for callers requesting historical ADME rules."""

import logging
import re
from importlib import metadata as importlib_metadata
from typing import Any
from urllib.parse import urlsplit

from src.system.redaction import contains_secret_material, sanitize_bounded

logger = logging.getLogger(__name__)

ADME = None
ADME_PY_VERSION = None
try:
    import adme_py as _adme_py_module
    from adme_py import ADME

    ADME_PY_AVAILABLE = True
    for _distribution_name in ("adme-py", "adme_py"):
        try:
            ADME_PY_VERSION = importlib_metadata.version(_distribution_name)
            break
        except importlib_metadata.PackageNotFoundError:
            continue
    if ADME_PY_VERSION is None:
        ADME_PY_VERSION = str(
            getattr(_adme_py_module, "__version__", "unknown")
        )
except ImportError:
    ADME_PY_AVAILABLE = False

try:
    from rdkit import Chem, rdBase
    from rdkit.Chem import Crippen, Descriptors, Lipinski, rdMolDescriptors
    RDKIT_AVAILABLE = True
except ImportError:
    RDKIT_AVAILABLE = False

# Only producer-supplied diagnostics, not scientific rows or inferred evidence.
_DIAGNOSTICS = ('prediction_method', 'backend_version', 'source', 'provenance',
                'warnings', 'error', 'reason', 'failure_reason')


def _public_source_url(value):
    """Narrow source exception, not a URL fetch or evidence endorsement.

    Only plain HTTP(S) DNS references: no userinfo, port, query, fragment,
    escapes, local host, or credential-shaped path. Other text stays redacted.
    """
    if (not isinstance(value, str) or len(value) > 2048
            or not re.fullmatch(r'[A-Za-z0-9:/._~-]+', value)):
        return False
    try:
        parsed = urlsplit(value)
        host = parsed.hostname or ''
        if (parsed.scheme not in ('http', 'https') or parsed.netloc.casefold() != host
                or len(host) > 253 or not re.fullmatch(
                    r'(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}', host)
                or host.rsplit('.', 1)[-1] in ('localhost', 'local', 'internal')
                or parsed.query or parsed.fragment):
            return False
    except ValueError:
        return False
    # Colons/repeated slashes belong only to the outer URL scheme here, not
    # embedded drive paths, file URIs or slash-normalized UNC paths.
    if ':' in parsed.path or '//' in parsed.path:
        return False
    parts = parsed.path.split('/')
    return (not any(part in ('.', '..') for part in parts)
            and not contains_secret_material(value)
            and not any(contains_secret_material({part: 'source'}) for part in parts))


def _restore_public_sources(original, sanitized):
    # Traverse only the sanitizer's retained structure: never resurrect a
    # redacted secret container or anything beyond its depth/item limits.
    if isinstance(sanitized, dict) and isinstance(original, dict):
        for key in sanitized:
            supplied = original[key]
            if key == 'source' and _public_source_url(supplied):
                sanitized[key] = supplied
            else:
                _restore_public_sources(supplied, sanitized[key])
    elif isinstance(sanitized, list) and isinstance(original, (list, tuple)):
        for supplied, retained in zip(original, sanitized):
            _restore_public_sources(supplied, retained)


def _diagnostic_metadata(props):
    """Keep supplied failure context, not private payloads or fabricated rows."""
    metadata = {key: props[key] for key in _DIAGNOSTICS if key in props}
    sanitized = sanitize_bounded(metadata)[0]
    _restore_public_sources(metadata, sanitized)
    return sanitized


def predict_admet_with_adme_py(smiles: str) -> dict[str, Any] | None:
    """Return historical adme_py calculations or explicitly labelled RDKit rules."""
    if not ADME_PY_AVAILABLE:
        return _predict_admet_with_rdkit(smiles)

    try:
        # 使用adme_py进行预测
        adme = ADME(smiles)
        adme_results = adme.calculate()

        # 转换为我们的格式
        admet_props = {
            # 理化性质
            'prediction_method': 'adme_py',
            'backend_version': str(ADME_PY_VERSION or 'unknown'),
            'physicochemical': adme_results.get('physiochemical', {}),

            # 溶解性
            'solubility': adme_results.get('solubility', {}),

            # 脂溶性
            'lipophilicity': adme_results.get('lipophilicity', {}),

            # 药代动力学
            'pharmacokinetics': adme_results.get('pharmacokinetics', {}),

            # 药物相似性
            'druglikeness': adme_results.get('druglikeness', {}),

            # 药物化学属性
            'medicinal': adme_results.get('medicinal', {})
        }
        for key, value in _diagnostic_metadata(adme_results).items():
            if key not in ('prediction_method', 'backend_version'):
                admet_props[key] = value

        return admet_props

    except Exception:
        logger.error("Legacy adme_py calculation failed; exception details omitted")
        return None


def _predict_admet_with_rdkit(smiles: str) -> dict[str, Any] | None:
    """Provide deterministic, clearly labelled ADME estimates using RDKit."""
    if not RDKIT_AVAILABLE:
        return None

    try:
        mol = Chem.MolFromSmiles(smiles)
        if mol is None:
            return None

        molecular_weight = float(Descriptors.MolWt(mol))
        logp = float(Crippen.MolLogP(mol))
        tpsa = float(Descriptors.TPSA(mol))
        rotatable_bonds = int(Lipinski.NumRotatableBonds(mol))
        h_donors = int(Lipinski.NumHDonors(mol))
        h_acceptors = int(Lipinski.NumHAcceptors(mol))
        heavy_atoms = int(mol.GetNumHeavyAtoms())
        aromatic_atoms = sum(1 for atom in mol.GetAtoms() if atom.GetIsAromatic())
        aromatic_proportion = aromatic_atoms / heavy_atoms if heavy_atoms else 0.0

        log_s = (
            0.16
            - (1.5 * logp)
            - (0.01 * (molecular_weight - 40.0))
            + (0.066 * rotatable_bonds)
            + (0.066 * aromatic_proportion)
        )
        solubility_mg_ml = max(0.0, (10 ** log_s) * molecular_weight)
        if log_s >= -1:
            solubility_class = "Very Soluble"
        elif log_s >= -2:
            solubility_class = "Soluble"
        elif log_s >= -3:
            solubility_class = "Moderately Soluble"
        elif log_s >= -4:
            solubility_class = "Poorly Soluble"
        else:
            solubility_class = "Insoluble"

        lipinski_violations = sum(
            (
                molecular_weight > 500,
                logp > 5,
                h_donors > 5,
                h_acceptors > 10,
            )
        )
        lipinski_result = "Pass" if lipinski_violations <= 1 else "Fail"
        veber_result = "Pass" if rotatable_bonds <= 10 and tpsa <= 140 else "Fail"
        gi_absorption = "High" if molecular_weight <= 500 and tpsa <= 140 else "Low"
        bbb_permeant = 0.0 <= logp <= 5.0 and tpsa < 90.0
        skin_logkp = -2.72 + (0.71 * logp) - (0.0061 * molecular_weight)
        synthetic_accessibility = min(
            10.0,
            max(
                1.0,
                1.0
                + (heavy_atoms / 25.0)
                + (rotatable_bonds / 5.0)
                + (mol.GetRingInfo().NumRings() / 4.0),
            ),
        )

        return {
            "prediction_method": "rdkit_rules",
            "backend_version": str(rdBase.rdkitVersion),
            "physicochemical": {
                "formula": rdMolDescriptors.CalcMolFormula(mol),
                "molecular_weight": molecular_weight,
                "num_heavy_atoms": heavy_atoms,
                "num_aromatic_atoms": aromatic_atoms,
                "sp3_carbon_ratio": float(rdMolDescriptors.CalcFractionCSP3(mol)),
                "num_rotatable_bonds": rotatable_bonds,
                "num_h_donors": h_donors,
                "num_h_acceptors": h_acceptors,
                "molar_refractivity": float(Crippen.MolMR(mol)),
                "tpsa": tpsa,
            },
            "solubility": {
                "log_s_esol": log_s,
                "solubility_esol": solubility_mg_ml,
                "class_esol": solubility_class,
            },
            "lipophilicity": {"wlogp": logp},
            "pharmacokinetics": {
                "gastrointestinal_absorption": gi_absorption,
                "blood_brain_barrier_permeant": bbb_permeant,
                "skin_permeability_logkp": skin_logkp,
            },
            "druglikeness": {
                "lipinski": lipinski_result,
                "veber": veber_result,
                "ghose": {},
            },
            "medicinal": {
                "pains": None,
                "brenk": None,
                "zinc": None,
                "synthetic_accessibility": synthetic_accessibility,
                "synthetic_accessibility_method": "rdkit_complexity_heuristic",
                "leadlikeness": {},
            },
        }
    except Exception:
        logger.error("Legacy RDKit ADME calculation failed; exception details omitted")
        return None
