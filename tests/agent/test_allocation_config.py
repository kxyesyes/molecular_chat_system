from pathlib import Path

import yaml


def test_allocation_window_covers_external_model_inference_budget():
    config = yaml.safe_load(
        (Path(__file__).parents[2] / "config" / "ollama_config.yaml").read_text(encoding="utf-8")
    )
    assert config["agent"]["allocation_timeout_seconds"] >= config["inference"]["timeout"]
