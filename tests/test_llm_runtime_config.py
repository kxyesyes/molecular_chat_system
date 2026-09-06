import asyncio
import tempfile
import unittest
import json
from unittest import mock
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


class LLMRuntimeConfigTest(unittest.TestCase):
    def test_env_snapshot_signature_matches_the_exact_written_content(self):
        from src.web.llm_runtime_config import save_llm_env_config_snapshot

        with tempfile.TemporaryDirectory(prefix="llm_env_signature_") as tmp:
            path = Path(tmp) / ".env"
            _config, signature = save_llm_env_config_snapshot(
                path,
                {
                    "provider": "ollama",
                    "base_url": "http://127.0.0.1:11434",
                    "model_name": "gmm-llama:latest",
                    "stream": True,
                },
            )
            actual = __import__("hashlib").sha256(path.read_bytes()).hexdigest()

        self.assertEqual(signature, actual)

    def test_save_rejects_symlink_env_target_without_replacing_link(self):
        from src.web.llm_runtime_config import save_llm_env_config

        with tempfile.TemporaryDirectory(prefix="llm_env_symlink_") as tmp:
            target = Path(tmp) / "managed.env"
            link = Path(tmp) / ".env"
            target.write_text("EXISTING=value\n", encoding="utf-8")
            try:
                link.symlink_to(target)
            except OSError as exc:
                self.skipTest(f"symlink unavailable: {exc}")

            with self.assertRaises(ValueError):
                save_llm_env_config(
                    link,
                    {
                        "provider": "ollama",
                        "base_url": "http://127.0.0.1:11434",
                        "model_name": "gmm-llama:latest",
                    },
                )

            self.assertTrue(link.is_symlink())
            self.assertEqual(target.read_text(encoding="utf-8"), "EXISTING=value\n")

    def test_save_rejects_symlink_env_target_before_writing(self):
        from src.web.llm_runtime_config import save_llm_env_config

        with tempfile.TemporaryDirectory(prefix="llm_env_symlink_guard_") as tmp:
            path = Path(tmp) / ".env"
            with mock.patch.object(Path, "is_symlink", return_value=True):
                with self.assertRaises(ValueError):
                    save_llm_env_config(
                        path,
                        {
                            "provider": "ollama",
                            "base_url": "http://127.0.0.1:11434",
                            "model_name": "gmm-llama:latest",
                        },
                    )

            self.assertFalse(path.exists())

    def test_lock_path_uses_configured_private_directory(self):
        from src.web.llm_runtime_config import _lock_path_for

        with tempfile.TemporaryDirectory(prefix="llm_lock_dir_") as tmp:
            lock_root = Path(tmp) / "private-locks"
            with mock.patch.dict(
                "os.environ",
                {"MEDCHAT_LLM_LOCK_DIR": str(lock_root)},
                clear=False,
            ):
                lock_path = _lock_path_for(Path(tmp) / ".env")

        self.assertEqual(lock_path.parent, lock_root)
        self.assertTrue(lock_path.name.startswith("llm-"))

    def test_save_llm_env_config_preserves_unrelated_lines_and_replaces_duplicates(self):
        from src.web.llm_runtime_config import save_llm_env_config

        with tempfile.TemporaryDirectory(prefix="llm_env_") as tmp:
            path = Path(tmp) / ".env"
            path.write_text(
                "# local settings\n"
                "MEDCHAT_PORT=6001\n"
                "OPENAI_COMPATIBLE_API_KEY=old-one\n"
                "OPENAI_COMPATIBLE_API_KEY=old-two\n",
                encoding="utf-8",
            )

            save_llm_env_config(
                path,
                {
                    "provider": "openai_compatible",
                    "base_url": "https://api.example.com/chat/completions",
                    "model_name": "example-model",
                    "api_key": "sk-test-persisted",
                    "stream": True,
                },
            )
            persisted = path.read_text(encoding="utf-8")

        self.assertIn("# local settings", persisted)
        self.assertIn("MEDCHAT_PORT=6001", persisted)
        self.assertEqual(persisted.count("OPENAI_COMPATIBLE_API_KEY="), 1)
        self.assertIn("OPENAI_COMPATIBLE_API_KEY=sk-test-persisted", persisted)
        self.assertIn(
            "OPENAI_COMPATIBLE_BASE_URL=https://api.example.com/chat/completions",
            persisted,
        )
        self.assertIn("OPENAI_COMPATIBLE_MODEL=example-model", persisted)
        self.assertIn("MEDCHAT_LLM_PROVIDER=openai_compatible", persisted)
        self.assertIn("MEDCHAT_LLM_STREAM=true", persisted)

    def test_save_llm_env_config_blank_key_keeps_existing_key(self):
        from src.web.llm_runtime_config import save_llm_env_config

        with tempfile.TemporaryDirectory(prefix="llm_env_keep_") as tmp:
            path = Path(tmp) / ".env"
            path.write_text(
                "OPENAI_COMPATIBLE_API_KEY=keep-this-key\n",
                encoding="utf-8",
            )

            save_llm_env_config(
                path,
                {
                    "provider": "openai_compatible",
                    "base_url": "https://api.example.com",
                    "model_name": "next-model",
                    "api_key": "",
                },
            )
            persisted = path.read_text(encoding="utf-8")

        self.assertIn("OPENAI_COMPATIBLE_API_KEY=keep-this-key", persisted)
        self.assertIn("OPENAI_COMPATIBLE_MODEL=next-model", persisted)

    def test_save_llm_env_config_clear_removes_provider_key(self):
        from src.web.llm_runtime_config import save_llm_env_config

        with tempfile.TemporaryDirectory(prefix="llm_env_clear_") as tmp:
            path = Path(tmp) / ".env"
            path.write_text(
                "OPENAI_COMPATIBLE_API_KEY=remove-this-key\n"
                "MEDCHAT_PORT=6001\n",
                encoding="utf-8",
            )

            save_llm_env_config(
                path,
                {
                    "provider": "openai_compatible",
                    "base_url": "https://api.example.com",
                    "model_name": "example-model",
                    "api_key": "",
                },
                clear_api_key=True,
            )
            persisted = path.read_text(encoding="utf-8")

        self.assertNotIn("OPENAI_COMPATIBLE_API_KEY=", persisted)
        self.assertIn("MEDCHAT_PORT=6001", persisted)

    def test_save_llm_env_config_can_clear_previous_provider_key(self):
        from src.web.llm_runtime_config import save_llm_env_config

        with tempfile.TemporaryDirectory(prefix="llm_env_clear_previous_") as tmp:
            path = Path(tmp) / ".env"
            path.write_text(
                "OPENAI_COMPATIBLE_API_KEY=remove-openai-key\n"
                "OLLAMA_MODEL=old-model\n",
                encoding="utf-8",
            )

            save_llm_env_config(
                path,
                {
                    "provider": "ollama",
                    "base_url": "http://127.0.0.1:11434",
                    "model_name": "gmm-llama:latest",
                    "api_key": "",
                },
                clear_api_key_providers=("openai_compatible",),
            )
            persisted = path.read_text(encoding="utf-8")

        self.assertNotIn("OPENAI_COMPATIBLE_API_KEY=", persisted)
        self.assertIn("MEDCHAT_LLM_PROVIDER=ollama", persisted)

    def test_load_llm_env_config_reads_requested_provider_not_active_provider(self):
        from src.web.llm_runtime_config import load_llm_env_config

        with tempfile.TemporaryDirectory(prefix="llm_env_load_provider_") as tmp:
            path = Path(tmp) / ".env"
            path.write_text(
                "MEDCHAT_LLM_PROVIDER=openai_compatible\n"
                "OPENAI_COMPATIBLE_API_KEY=openai-key\n"
                "MODELSCOPE_API_KEY=modelscope-key\n"
                "MODELSCOPE_BASE_URL=https://modelscope.example.com\n"
                "MODELSCOPE_MODEL=Vendor/Model\n",
                encoding="utf-8",
            )

            loaded = load_llm_env_config(path, "modelscope")

        self.assertEqual(loaded["provider"], "modelscope")
        self.assertEqual(loaded["api_key"], "modelscope-key")
        self.assertEqual(loaded["model_name"], "Vendor/Model")

    def test_save_llm_env_config_rejects_multiline_values_without_modifying_file(self):
        from src.web.llm_runtime_config import save_llm_env_config

        with tempfile.TemporaryDirectory(prefix="llm_env_injection_") as tmp:
            path = Path(tmp) / ".env"
            original = "MEDCHAT_PORT=6001\n"
            path.write_text(original, encoding="utf-8")

            with self.assertRaises(ValueError):
                save_llm_env_config(
                    path,
                    {
                        "provider": "openai_compatible",
                        "base_url": "https://api.example.com\nINJECTED=value",
                        "model_name": "example-model",
                        "api_key": "sk-test",
                    },
                )

            persisted = path.read_text(encoding="utf-8")

        self.assertEqual(persisted, original)

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
                clear=True,
            ):
                loaded = app._load_active_llm_config()

            persisted_text = path.read_text(encoding="utf-8")

        self.assertEqual(loaded["provider"], "openai_compatible")
        self.assertEqual(loaded["base_url"], "https://runtime.example.com")
        self.assertEqual(loaded["model_name"], "runtime-model")
        self.assertEqual(loaded["api_key"], "runtime-environment-key")
        self.assertNotIn("must-not-persist", persisted_text)
        self.assertNotIn("runtime-environment-key", persisted_text)

    def test_modelscope_environment_config_does_not_require_yaml_section(self):
        from src.web.app import MolecularChatApp

        app = MolecularChatApp.__new__(MolecularChatApp)
        app.config = {"inference": {"stream": True}}

        with mock.patch.dict(
            "os.environ",
            {
                "MODELSCOPE_API_KEY": "modelscope-test-key",
                "MODELSCOPE_BASE_URL": "https://modelscope.example.com/v1/chat/completions",
                "MODELSCOPE_MODEL": "Vendor/Test-Model",
            },
            clear=True,
        ), mock.patch.dict(app.config, {"modelscope": {}}, clear=False):
            loaded = app._llm_config_from_yaml()

        self.assertEqual(loaded["provider"], "modelscope")
        self.assertEqual(loaded["api_key"], "modelscope-test-key")
        self.assertEqual(loaded["model_name"], "Vendor/Test-Model")

    def test_runtime_provider_selects_its_own_key_when_multiple_keys_exist(self):
        from src.web.app import MolecularChatApp
        from src.web.llm_runtime_config import save_runtime_config

        with tempfile.TemporaryDirectory(prefix="llm_provider_select_") as tmp:
            runtime_path = Path(tmp) / "runtime.json"
            save_runtime_config(
                runtime_path,
                {
                    "provider": "modelscope",
                    "base_url": "https://modelscope.example.com",
                    "model_name": "Vendor/Model",
                    "stream": True,
                },
            )
            app = MolecularChatApp.__new__(MolecularChatApp)
            app.runtime_llm_config_path = runtime_path
            app.config = {"inference": {"stream": True}, "modelscope": {}}

            with mock.patch.dict(
                "os.environ",
                {
                    "OPENAI_COMPATIBLE_API_KEY": "openai-key",
                    "MODELSCOPE_API_KEY": "modelscope-key",
                },
                clear=True,
            ):
                loaded = app._load_active_llm_config()

        self.assertEqual(loaded["provider"], "modelscope")
        self.assertEqual(loaded["api_key"], "modelscope-key")

    def test_env_provider_marker_is_canonical_over_stale_runtime_cache(self):
        from src.web.app import MolecularChatApp
        from src.web.llm_runtime_config import save_runtime_config

        with tempfile.TemporaryDirectory(prefix="llm_env_canonical_") as tmp:
            runtime_path = Path(tmp) / "runtime.json"
            save_runtime_config(
                runtime_path,
                {
                    "provider": "openai_compatible",
                    "base_url": "https://stale.example.com",
                    "model_name": "stale-model",
                    "stream": False,
                },
            )
            app = MolecularChatApp.__new__(MolecularChatApp)
            app.runtime_llm_config_path = runtime_path
            app.config = {"inference": {"stream": True}, "modelscope": {}}

            with mock.patch.dict(
                "os.environ",
                {
                    "MEDCHAT_LLM_PROVIDER": "modelscope",
                    "MEDCHAT_LLM_STREAM": "true",
                    "MODELSCOPE_API_KEY": "canonical-key",
                    "MODELSCOPE_BASE_URL": "https://canonical.example.com",
                    "MODELSCOPE_MODEL": "Canonical/Model",
                },
                clear=True,
            ):
                loaded = app._load_active_llm_config()

        self.assertEqual(loaded["provider"], "modelscope")
        self.assertEqual(loaded["base_url"], "https://canonical.example.com")
        self.assertEqual(loaded["model_name"], "Canonical/Model")
        self.assertEqual(loaded["api_key"], "canonical-key")

    def test_second_worker_refreshes_model_after_env_changes(self):
        from src.web.app import MolecularChatApp
        from src.web.llm_runtime_config import save_llm_env_config

        with tempfile.TemporaryDirectory(prefix="llm_worker_refresh_") as tmp:
            env_path = Path(tmp) / ".env"
            runtime_path = Path(tmp) / "runtime.json"
            save_llm_env_config(
                env_path,
                {
                    "provider": "ollama",
                    "base_url": "http://127.0.0.1:11434",
                    "model_name": "gmm-llama:latest",
                    "stream": True,
                },
            )
            app = MolecularChatApp.__new__(MolecularChatApp)
            app.runtime_llm_env_path = env_path
            app.runtime_llm_config_path = runtime_path
            app.config = {"inference": {"stream": True}, "modelscope": {}}
            app.active_llm_config = {
                "provider": "ollama",
                "base_url": "http://127.0.0.1:11434",
                "model_name": "gmm-llama:latest",
                "api_key": "",
                "stream": True,
            }
            app._llm_config_lock = asyncio.Lock()
            app._llm_env_signature = app._llm_env_file_signature()
            applied = {}
            app._apply_llm_config = lambda config: applied.update(config) or config

            save_llm_env_config(
                env_path,
                {
                    "provider": "modelscope",
                    "base_url": "https://modelscope.example.com",
                    "model_name": "Vendor/Model",
                    "api_key": "worker-shared-key",
                    "stream": False,
                },
            )
            refreshed = asyncio.run(app._refresh_llm_config_from_env())

        self.assertTrue(refreshed)
        self.assertEqual(applied["provider"], "modelscope")
        self.assertEqual(applied["api_key"], "worker-shared-key")

    def test_custom_runtime_provider_reuses_openai_compatible_key(self):
        from src.web.app import MolecularChatApp
        from src.web.llm_runtime_config import save_runtime_config

        with tempfile.TemporaryDirectory(prefix="llm_custom_select_") as tmp:
            runtime_path = Path(tmp) / "runtime.json"
            save_runtime_config(
                runtime_path,
                {
                    "provider": "custom",
                    "base_url": "https://custom.example.com",
                    "model_name": "custom-model",
                    "stream": True,
                },
            )
            app = MolecularChatApp.__new__(MolecularChatApp)
            app.runtime_llm_config_path = runtime_path
            app.config = {"inference": {"stream": True}}

            with mock.patch.dict(
                "os.environ",
                {"OPENAI_COMPATIBLE_API_KEY": "custom-shared-key"},
                clear=True,
            ):
                loaded = app._load_active_llm_config()

        self.assertEqual(loaded["provider"], "custom")
        self.assertEqual(loaded["api_key"], "custom-shared-key")


if __name__ == "__main__":
    unittest.main()
