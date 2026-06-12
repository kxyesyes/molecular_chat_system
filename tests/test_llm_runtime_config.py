import tempfile
import unittest
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


class LLMRuntimeConfigTest(unittest.TestCase):
    def test_public_config_masks_api_key(self):
        from src.web.llm_runtime_config import public_llm_config

        public = public_llm_config(
            {
                "provider": "modelscope",
                "base_url": "https://api.example.com/v1/chat/completions",
                "model_name": "Vendor/Model",
                "api_key": "sk-secret-value",
                "stream": True,
            }
        )

        self.assertTrue(public["api_key_configured"])
        self.assertNotIn("api_key", public)
        self.assertEqual(public["api_key_hint"], "sk-s...alue")

    def test_save_and_load_runtime_config_normalizes_values(self):
        from src.web.llm_runtime_config import load_runtime_config, save_runtime_config

        with tempfile.TemporaryDirectory(prefix="llm_config_") as tmp:
            path = Path(tmp) / "llm_runtime_config.json"
            saved = save_runtime_config(
                path,
                {
                    "provider": "OpenAI_Compatible",
                    "base_url": " https://api.example.com/v1/chat/completions ",
                    "model_name": " demo-model ",
                    "api_key": " token ",
                    "stream": False,
                },
            )

            loaded = load_runtime_config(path)

        self.assertEqual(saved["provider"], "openai_compatible")
        self.assertEqual(loaded["base_url"], "https://api.example.com/v1/chat/completions")
        self.assertEqual(loaded["model_name"], "demo-model")
        self.assertEqual(loaded["api_key"], "token")
        self.assertFalse(loaded["stream"])


if __name__ == "__main__":
    unittest.main()
