from src.agent.runtime import RuntimeLimits, get_default_limits


def test_default_limits_cover_long_running_tools():
    limits = get_default_limits()

    assert limits.llm_generate_seconds == 60
    assert limits.reverse_target_seconds == 120
    assert limits.docking_seconds == 180
    assert limits.reverse_target_concurrency == 2
    assert limits.docking_concurrency == 1


def test_runtime_limits_can_be_overridden_from_dict():
    limits = RuntimeLimits.from_dict(
        {
            "llm_generate_seconds": 30,
            "reverse_target_concurrency": 1,
        }
    )

    assert limits.llm_generate_seconds == 30
    assert limits.reverse_target_concurrency == 1
    assert limits.docking_seconds == 180
