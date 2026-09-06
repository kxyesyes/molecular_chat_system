import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


class _FakeLLM:
    model_name = "fake-llm"

    async def generate(self, *args, **kwargs):
        return "ok"


class _NextLLM:
    model_name = "next-llm"

    async def generate(self, *args, **kwargs):
        return "ok"


class _LocalGeneratorLLM:
    model_name = "gmm-llama:latest"

    async def generate(self, *args, **kwargs):
        return "CCO"


class AgentLLMWiringTest(unittest.TestCase):
    def test_agent_keeps_main_llm_separate_from_molecular_generator(self):
        from src.agent.react_agent import ReActMolecularAgent

        local_generator_llm = _LocalGeneratorLLM()
        agent = ReActMolecularAgent(
            llm=_FakeLLM(),
            molecular_generator_llm=local_generator_llm,
        )
        generator = agent.tools["llm_molecular_generator"]

        self.assertEqual(agent.llm.model_name, "fake-llm")
        self.assertIs(generator.llm, local_generator_llm)
        self.assertIsNot(generator.llm, agent.llm)

    def test_set_llm_does_not_replace_local_molecular_generator(self):
        from src.agent.react_agent import ReActMolecularAgent

        local_generator_llm = _LocalGeneratorLLM()
        agent = ReActMolecularAgent(
            llm=_FakeLLM(),
            molecular_generator_llm=local_generator_llm,
        )
        next_llm = _NextLLM()
        agent.set_llm(next_llm)

        self.assertIs(agent.llm, next_llm)
        self.assertIs(agent.tools["llm_molecular_generator"].llm, local_generator_llm)

    def test_generation_query_forces_molecular_generator_first(self):
        from src.agent.react_agent import ReActMolecularAgent

        agent = ReActMolecularAgent(
            llm=_FakeLLM(),
            molecular_generator_llm=_LocalGeneratorLLM(),
        )
        step = agent._generate_react_step("随机生成一个类药分子", [])

        self.assertEqual(step.action, "llm_molecular_generator")
        self.assertEqual(step.action_input, "随机生成一个类药分子")


if __name__ == "__main__":
    unittest.main()
