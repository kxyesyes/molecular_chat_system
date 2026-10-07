from __future__ import annotations

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient


def test_smiles_to_3d_rejects_missing_force_field_parameters(monkeypatch):
    from rdkit.Chem import AllChem
    from src.web.routes import molecule_utility_routes

    monkeypatch.setattr(AllChem, "MMFFHasAllMoleculeParams", lambda _mol: False)
    monkeypatch.setattr(AllChem, "UFFHasAllMoleculeParams", lambda _mol: False)

    with pytest.raises(HTTPException) as error:
        molecule_utility_routes._smiles_to_3d_sync("CCO")
    assert "力场参数" in error.value.detail


def test_smiles_to_3d_rejects_non_converged_force_field(monkeypatch):
    from rdkit.Chem import AllChem
    from src.web.routes import molecule_utility_routes

    monkeypatch.setattr(AllChem, "MMFFHasAllMoleculeParams", lambda _mol: True)
    monkeypatch.setattr(AllChem, "MMFFOptimizeMolecule", lambda _mol: 1)

    with pytest.raises(HTTPException) as error:
        molecule_utility_routes._smiles_to_3d_sync("CCO")
    assert "未收敛" in error.value.detail


def test_smiles_to_3d_uses_uff_when_mmff_is_unavailable(monkeypatch):
    from rdkit.Chem import AllChem
    from src.web.routes import molecule_utility_routes

    calls = []
    monkeypatch.setattr(AllChem, "MMFFHasAllMoleculeParams", lambda _mol: False)
    monkeypatch.setattr(AllChem, "UFFHasAllMoleculeParams", lambda _mol: True)
    monkeypatch.setattr(AllChem, "UFFOptimizeMolecule", lambda _mol: calls.append("UFF") or 0)

    result = molecule_utility_routes._smiles_to_3d_sync("CCO")

    assert result["success"] is True
    assert calls == ["UFF"]


@pytest.mark.parametrize(
    ("path", "method", "kwargs", "function_name"),
    [
        ("/api/docking/smiles_to_3d", "post", {"json": {"smiles": "CCO"}}, "_smiles_to_3d_sync"),
        ("/api/utils/smiles_to_image", "get", {"params": {"smiles": "CCO"}}, "_smiles_to_image_sync"),
        ("/api/utils/mcs", "get", {"params": {"smiles1": "CCO", "smiles2": "CCC"}}, "_mcs_sync"),
    ],
)
def test_molecule_utility_routes_do_not_return_internal_exception_text(
    monkeypatch, path, method, kwargs, function_name
):
    from src.web.routes import molecule_utility_routes

    def fail(*_args, **_kwargs):
        raise RuntimeError("secret C:/private/model.bin")

    monkeypatch.setattr(molecule_utility_routes, function_name, fail)

    async def invoke_in_threadpool(function, *args):
        return function(*args)

    support = type(
        "Support",
        (),
        {
            "_invoke_in_threadpool": staticmethod(invoke_in_threadpool),
            "logger": type(
                "Logger",
                (),
                {
                    "error": staticmethod(lambda *_args, **_kwargs: None),
                    "exception": staticmethod(lambda *_args, **_kwargs: None),
                },
            )(),
        },
    )()
    app = FastAPI()
    molecule_utility_routes.setup_molecule_utility_routes(app, _support=support)

    with TestClient(app) as client:
        response = getattr(client, method)(path, **kwargs)

    assert response.status_code == 500
    assert "secret" not in response.text
    assert "C:/private/model.bin" not in response.text
