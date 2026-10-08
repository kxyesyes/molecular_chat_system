"""Real basic descriptors, unavailable endpoint ADMET; no providers or assets."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from rdkit import Chem
from rdkit.Chem import Crippen, Descriptors, Lipinski, QED

from src.web.routes.molecule_properties_routes import setup_molecule_properties_routes


def client_for_properties():
    app = FastAPI()
    setup_molecule_properties_routes(app, logger=Mock())
    return TestClient(app)


def test_legacy_support_argument_remains_compatible():
    app = FastAPI()
    setup_molecule_properties_routes(app, _support=SimpleNamespace(logger=Mock()))
    with TestClient(app) as client:
        response = client.post("/api/molecule/properties", json={"smiles": "CCO"})
    assert response.status_code == 200 and response.json()["success"] is True


def test_route_reuses_molecular_design_core_for_descriptor_calculation(monkeypatch):
    from src.molecular_design import chemistry

    calls = []

    def fake_calculate_properties(smiles):
        calls.append(smiles)
        return {
            "success": True,
            "properties": {
                "molecular_formula": "C2H6O",
                "mw": 46.069,
                "logp": -0.001,
                "tpsa": 20.23,
                "hbd": 1,
                "hba": 1,
                "rotbonds": 0,
                "qed": 0.4062,
                "fsp3": 1.0,
                "sa_score": None,
            },
            "goals": {"items": []},
            "property_status": {"sa_score": "unavailable"},
        }

    monkeypatch.setattr(chemistry, "calculate_properties", fake_calculate_properties)
    with client_for_properties() as client:
        response = client.post("/api/molecule/properties", json={"smiles": "CCO"})

    assert response.status_code == 200
    assert calls == ["CCO"]
    assert response.json()["properties"]["basic"] == {
        "molecular_weight": 46.07,
        "logp": -0.0,
        "hbd": 1,
        "hba": 1,
        "tpsa": 20.23,
        "rotatable_bonds": 0,
        "qed": 0.406,
    }


@pytest.mark.parametrize("smiles", ["CCO", "CC(=O)Oc1ccccc1C(=O)O"])
def test_basic_values_are_actual_rdkit(smiles):
    mol = Chem.MolFromSmiles(smiles)
    with client_for_properties() as client:
        response = client.post("/api/molecule/properties", json={"smiles": smiles})
    body = response.json()
    assert response.status_code == 200 and body["success"] is True
    assert body["smiles"] == smiles
    assert body["properties"]["basic"] == {
        "molecular_weight": round(Descriptors.MolWt(mol), 2),
        "logp": round(Crippen.MolLogP(mol), 2),
        "hbd": Lipinski.NumHDonors(mol), "hba": Lipinski.NumHAcceptors(mol),
        "tpsa": round(Descriptors.TPSA(mol), 2),
        "rotatable_bonds": Lipinski.NumRotatableBonds(mol),
        "qed": round(QED.qed(mol), 3),
    }


@pytest.mark.parametrize("payload", [{}, {"smiles": ""}, {"smiles": "C1CC"}, {"smiles": 7}])
def test_legacy_invalid_input_envelope(payload):
    with client_for_properties() as client:
        response = client.post("/api/molecule/properties", json=payload)
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"success", "error", "properties"}
    assert body["success"] is False and body["properties"] is None
    assert isinstance(body["error"], str)
    if not payload.get("smiles"):
        # Preserve the caught exception string in this framework profile:
        # Starlette 0.27.0 uses '', whereas host 1.0.0 includes status/detail.
        assert body["error"] == str(HTTPException(status_code=400, detail="缺少SMILES参数"))
    elif payload["smiles"] == "C1CC":
        assert body["error"] == "无法识别的分子结构: C1CC"


@pytest.mark.parametrize("kwargs", [{}, {"content": "{", "headers": {"Content-Type": "application/json"}}])
def test_body_validation_stays_422(kwargs):
    with client_for_properties() as client:
        response = client.post("/api/molecule/properties", **kwargs)
    assert response.status_code == 422
    assert "detail" in response.json()


@pytest.mark.parametrize("backend", ["absent_method", "obsolete_method", "raises"])
def test_endpoint_admet_unknown_and_never_constructs_predictor(monkeypatch, backend):
    import src.agent.tools as tools
    calls = []
    def constructor():
        calls.append(backend)
        if backend == "raises":
            raise RuntimeError("controlled backend failure")
        if backend == "obsolete_method":
            return SimpleNamespace(_predict_admet_properties=lambda mol: {"hepatotoxicity": "Low"})
        return object()
    monkeypatch.setattr(tools, "ADMETPredictor", constructor)
    with client_for_properties() as client:
        response = client.post("/api/molecule/properties", json={"smiles": "CCO"})
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert calls == []
    assert body["properties"]["admet"] == dict.fromkeys((
        "bbb_penetration", "cyp_inhibition", "hepatotoxicity", "solubility", "bioavailability"), "Unknown")
    assert body["properties"]["admet_metadata"] == {
        "availability": "unavailable", "method": "not_calculated",
        "warning": "ADMET未计算；本接口仅计算基础理化性质，不能据此判断毒性、CNS安全性或体内表现。",
    }
