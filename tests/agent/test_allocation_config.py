from pathlib import Path

import yaml
import pytest


@pytest.mark.parametrize('profile', ['ollama_config.yaml', 'modelscope_config.yaml'])
def test_allocation_window_covers_external_model_inference_budget(profile):
    config = yaml.safe_load(
        (Path(__file__).parents[2] / "config" / profile).read_text(encoding="utf-8")
    )
    assert config["agent"]["allocation_timeout_seconds"] >= config["inference"]["timeout"]
    assert 0 < config['agent']['allocation_progress_seconds'] < config['agent']['allocation_timeout_seconds']
