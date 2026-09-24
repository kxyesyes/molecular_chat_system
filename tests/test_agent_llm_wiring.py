import sys
import unittest
from unittest.mock import patch
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
    def setUp(self):
        from tests.agent.test_target_driven_design_workflow import FakeTool

        self.factory_models = []

        def factory(generator_llm):
            self.factory_models.append(generator_llm)
            generator = FakeTool("llm_molecular_generator")
            generator.llm = generator_llm
            consumer = FakeTool("property_calculator")
            consumer.llm = None
            return [generator, consumer]

        factory_patch = patch("src.agent.tools.get_all_tools", side_effect=factory)
        self.addCleanup(factory_patch.stop)
        factory_patch.start()

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
        self.assertEqual(self.factory_models, [local_generator_llm])

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
        self.assertIs(agent.tools["property_calculator"].llm, next_llm)

        result = agent.execute("Generate 1 molecule", temperature=0.23)
        self.assertTrue(result["success"])
        self.assertIs(agent.tools["llm_molecular_generator"].llm, local_generator_llm)
        self.assertEqual(agent.tools["property_calculator"].inputs, [])

    def test_generation_query_forces_molecular_generator_first(self):
        from src.agent.react_agent import ReActMolecularAgent

        agent = ReActMolecularAgent(
            llm=_FakeLLM(),
            molecular_generator_llm=_LocalGeneratorLLM(),
        )
        query = "随机生成一个类药分子"
        result = agent.execute(query, temperature=0.23)

        self.assertTrue(result["success"])
        self.assertEqual(result["tools_used"], ["llm_molecular_generator"])
        inputs = agent.tools["llm_molecular_generator"].inputs
        self.assertEqual(len(inputs), 1)
        self.assertEqual(inputs[0]["query"], query)
        self.assertEqual(inputs[0]["metadata"]["requested_count"], 1)
        self.assertEqual(inputs[0]["metadata"]["temperature"], 0.23)
        self.assertEqual(agent.tools["property_calculator"].inputs, [])


if __name__ == "__main__":
    unittest.main()
