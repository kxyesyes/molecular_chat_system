import tempfile
import unittest
import json
from unittest import mock
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
            persisted_text = path.read_text(encoding="utf-8")

        self.assertEqual(saved["provider"], "openai_compatible")
        self.assertEqual(loaded["base_url"], "https://api.example.com/v1/chat/completions")
        self.assertEqual(loaded["model_name"], "demo-model")
        self.assertEqual(saved["api_key"], "token")
        self.assertEqual(loaded["api_key"], "")
        self.assertNotIn("token", persisted_text)
        self.assertNotIn("api_key", persisted_text)
        self.assertFalse(loaded["stream"])

    def test_load_scrubs_legacy_plaintext_api_key(self):
        from src.web.llm_runtime_config import load_runtime_config

        with tempfile.TemporaryDirectory(prefix="llm_config_legacy_") as tmp:
            path = Path(tmp) / "llm_runtime_config.json"
            path.write_text(
                json.dumps(
                    {
                        "provider": "openai_compatible",
                        "base_url": "https://api.example.com",
                        "model_name": "demo-model",
                        "api_key": "legacy-secret-key",
                        "stream": True,
                    }
                ),
                encoding="utf-8",
            )

            loaded = load_runtime_config(path)
            persisted_text = path.read_text(encoding="utf-8")

        self.assertEqual(loaded["api_key"], "")
        self.assertNotIn("legacy-secret-key", persisted_text)
        self.assertNotIn("api_key", persisted_text)

    def test_app_merges_runtime_non_secret_config_with_environment_key(self):
        from src.web.app import MolecularChatApp
        from src.web.llm_runtime_config import save_runtime_config

        with tempfile.TemporaryDirectory(prefix="llm_config_env_") as tmp:
            path = Path(tmp) / "llm_runtime_config.json"
            save_runtime_config(
                path,
                {
                    "provider": "openai_compatible",
                    "base_url": "https://runtime.example.com",
                    "model_name": "runtime-model",
                    "api_key": "must-not-persist",
                    "stream": False,
                },
            )
            app = MolecularChatApp.__new__(MolecularChatApp)
            app.runtime_llm_config_path = path
            app.config = {}

            with mock.patch.dict(
                "os.environ",
                {
                    "OPENAI_COMPATIBLE_API_KEY": "runtime-environment-key",
                    "OPENAI_COMPATIBLE_BASE_URL": "https://env.example.com",
                    "OPENAI_COMPATIBLE_MODEL": "env-model",
                },
                clear=False,
            ):
                loaded = app._load_active_llm_config()

            persisted_text = path.read_text(encoding="utf-8")

        self.assertEqual(loaded["provider"], "openai_compatible")
        self.assertEqual(loaded["base_url"], "https://runtime.example.com")
        self.assertEqual(loaded["model_name"], "runtime-model")
        self.assertEqual(loaded["api_key"], "runtime-environment-key")
        self.assertNotIn("must-not-persist", persisted_text)
        self.assertNotIn("runtime-environment-key", persisted_text)


if __name__ == "__main__":
    unittest.main()
