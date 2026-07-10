from pathlib import Path
import sys
import unittest
from unittest import mock


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import src.agent.tools.llm_molecular_generator as generator_module
from src.agent.tools.llm_molecular_generator import LLMMolecularGenerator


class FakeLLM:
    model_name = "gmm-llama:latest"

    def generate(self, prompt, temperature=0.7, max_tokens=1000):
        return "OCC1CN(Cc2ccc3[nH]c(=O)c(NC(=O)C4CCC4)c3c2)CCC1\nCCO"


class FakeChem:
    @staticmethod
    def MolFromSmiles(smiles):
        if smiles == "CCO":
            return {"smiles": smiles}
        return None

    @staticmethod
    def MolToSmiles(mol, canonical=True):
        return mol["smiles"]


class DuplicateCanonicalLLM:
    model_name = "gmm-llama:latest"

    def generate(self, prompt, temperature=0.7, max_tokens=1000):
        return "OCC\nCCO\nCCN"


class DuplicateCanonicalChem:
    @staticmethod
    def MolFromSmiles(smiles):
        if smiles in {"OCC", "CCO", "CCN"}:
            return {"smiles": smiles}
        return None

    @staticmethod
    def MolToSmiles(mol, canonical=True):
        return "CCO" if mol["smiles"] in {"OCC", "CCO"} else mol["smiles"]


class ProseThenSmilesLLM:
    model_name = "gmm-llama:latest"

    def generate(self, prompt, temperature=0.7, max_tokens=1000):
        return "Here are two molecules:\n1. CCO\nSMILES: CCN\nThanks"


class CountingChem:
    calls = []

    @classmethod
    def MolFromSmiles(cls, smiles):
        cls.calls.append(smiles)
        if smiles in {"CCO", "CCN"}:
            return {"smiles": smiles}
        return None

    @staticmethod
    def MolToSmiles(mol, canonical=True):
        return mol["smiles"]


class LLMMolecularGeneratorTest(unittest.TestCase):
    def test_generation_intent_parses_chinese_number_words(self):
        generator = LLMMolecularGenerator(llm_model=FakeLLM())

        intent = generator._analyze_generation_intent("随机生成五个类药分子")

        self.assertEqual(intent["count"], 5)
        self.assertIn("drug-like properties", intent["objectives"])

    def test_invalid_generated_smiles_are_filtered_before_result_metadata(self):
        generator = LLMMolecularGenerator(llm_model=FakeLLM())
        intent = {
            "type": "description",
            "requirements": "generate 1 molecule",
            "count": 1,
            "temperature": 0.7,
        }

        with (
            mock.patch.object(generator_module, "RDKIT_AVAILABLE", True),
            mock.patch.object(generator_module, "Chem", FakeChem, create=True),
        ):
            result = generator._generate_with_retry(intent, max_attempts=1)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["smiles"], "CCO")
        self.assertEqual(result[0]["model"], "gmm-llama:latest")

    def test_generation_filters_duplicates_after_canonicalization(self):
        generator = LLMMolecularGenerator(llm_model=DuplicateCanonicalLLM())
        intent = {
            "type": "description",
            "requirements": "generate 2 molecules",
            "count": 2,
            "temperature": 0.7,
        }

        with (
            mock.patch.object(generator_module, "RDKIT_AVAILABLE", True),
            mock.patch.object(generator_module, "Chem", DuplicateCanonicalChem, create=True),
        ):
            result = generator._generate_with_retry(intent, max_attempts=1)

        self.assertEqual([item["smiles"] for item in result], ["CCO", "CCN"])

    def test_generation_prefilters_prose_before_rdkit_validation(self):
        generator = LLMMolecularGenerator(llm_model=ProseThenSmilesLLM())
        intent = {
            "type": "description",
            "requirements": "generate 2 molecules",
            "count": 2,
            "temperature": 0.7,
        }
        CountingChem.calls = []

        with (
            mock.patch.object(generator_module, "RDKIT_AVAILABLE", True),
            mock.patch.object(generator_module, "Chem", CountingChem, create=True),
        ):
            result = generator._generate_with_retry(intent, max_attempts=1)

        self.assertEqual([item["smiles"] for item in result], ["CCO", "CCN"])
        self.assertEqual(CountingChem.calls, ["CCO", "CCN"])
        self.assertNotIn("Here", CountingChem.calls)
        self.assertNotIn("Thanks", CountingChem.calls)


if __name__ == "__main__":
    unittest.main()
