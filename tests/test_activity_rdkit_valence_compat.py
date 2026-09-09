"""Exercise old/new valence APIs without changing the installed RDKit runtime."""
import importlib
from types import SimpleNamespace

import pytest
import torch
from rdkit import Chem


@pytest.mark.parametrize("api", ["legacy", "modern"])
@pytest.mark.parametrize("smiles", ["CCO", "c1ccncc1"])
def test_atom_features_keep_explicit_valence_encoding_across_rdkit_apis(monkeypatch, api, smiles):
    module = importlib.import_module("src.activity.rg_mpnn.molecular_network.mol_feature.atom_feature")
    mol = Chem.MolFromSmiles(smiles)
    explicit = object()
    monkeypatch.setattr(module, "rdchem", SimpleNamespace(**(
        {"ValenceType": SimpleNamespace(EXPLICIT=explicit)} if api == "modern" else {}
    )))
    calls = []

    class Atom:
        def __init__(self, atom):
            self.atom = atom

        def __getattr__(self, name):
            if name == "GetValence":
                if api == "legacy":
                    raise AttributeError(name)
                def get_valence(kind):
                    assert kind is explicit
                    calls.append("modern")
                    return self.atom.GetExplicitValence()
                return get_valence
            if name == "GetExplicitValence":
                def get_legacy():
                    assert api == "legacy", "modern API must not use deprecated accessor"
                    calls.append("legacy")
                    return self.atom.GetExplicitValence()
                return get_legacy
            return getattr(self.atom, name)

    proxy = SimpleNamespace(GetAtoms=lambda: [Atom(atom) for atom in mol.GetAtoms()], GetRingInfo=mol.GetRingInfo)
    features = module.atom_feature(proxy, hybrid=False, aromatic=False, exp_val=True, Hs=False)
    assert calls == [api] * mol.GetNumAtoms()
    # Atom symbol encoding is unchanged; explicit valence remains the last channel group.
    expected = torch.stack([torch.as_tensor(module.onehot(atom.GetExplicitValence(), module.exp_val_types, other=True),
                                           dtype=torch.float32) for atom in mol.GetAtoms()])
    assert torch.equal(features[:, len(module.atom_types):], expected)
