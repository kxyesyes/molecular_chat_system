import asyncio
import json
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


class _FakeResponse:
    status_code = 200
    text = ""

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class _FakeStreamResponse:
    status_code = 200

    def __init__(self, chunks):
        self._chunks = chunks

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def aiter_bytes(self):
        for chunk in self._chunks:
            yield chunk


class _FakeClient:
    def __init__(self):
        self.post_calls = []
        self.stream_calls = []

    async def post(self, url, headers=None, json=None):
        self.post_calls.append({"url": url, "headers": headers, "json": json})
        return _FakeResponse(
            {"choices": [{"message": {"content": "API_OK"}}], "model": "demo-model"}
        )

    def stream(self, method, url, headers=None, json=None, timeout=None):
        self.stream_calls.append(
            {"method": method, "url": url, "headers": headers, "json": json}
        )
        lines = [
            b'data: {"choices":[{"delta":{"content":"A"}}]}\n',
            b'data: {"choices":[{"delta":{"content":"PI_OK"}}]}\n',
            b"data: [DONE]\n",
        ]
        return _FakeStreamResponse(lines)


class OpenAICompatibleModelTest(unittest.TestCase):
    def test_generate_posts_to_chat_completions_and_parses_content(self):
        from src.agent.openai_compatible_model import OpenAICompatibleModel

        client = _FakeClient()
        model = OpenAICompatibleModel(
            api_key="token",
            model_name="demo-model",
            base_url="https://api.example.com",
            client=client,
        )

        result = asyncio.run(model.generate("hello", temperature=0.2, max_tokens=12))

        self.assertEqual(result, "API_OK")
        self.assertEqual(client.post_calls[0]["url"], "https://api.example.com/v1/chat/completions")
        self.assertEqual(client.post_calls[0]["json"]["model"], "demo-model")
        self.assertEqual(client.post_calls[0]["json"]["messages"][0]["content"], "hello")
        self.assertEqual(client.post_calls[0]["headers"]["Authorization"], "Bearer token")

    def test_empty_api_key_returns_message_without_network_call(self):
        from src.agent.openai_compatible_model import OpenAICompatibleModel

        client = _FakeClient()
        model = OpenAICompatibleModel(
            api_key="",
            model_name="demo-model",
            base_url="https://api.example.com/v1/chat/completions",
            client=client,
        )

        result = asyncio.run(model.generate("hello"))

        self.assertIn("API Key", result)
        self.assertEqual(client.post_calls, [])

    def test_stream_generate_parses_sse_chunks(self):
        from src.agent.openai_compatible_model import OpenAICompatibleModel

        client = _FakeClient()
        model = OpenAICompatibleModel(
            api_key="token",
            model_name="demo-model",
            base_url="https://api.example.com/v1/chat/completions",
            client=client,
        )

        async def collect():
            return [chunk async for chunk in model.stream_generate("hello")]

        result = asyncio.run(collect())

        self.assertEqual("".join(result), "API_OK")
        self.assertEqual(result, ["API_OK"])
        self.assertEqual(client.stream_calls[0]["url"], "https://api.example.com/v1/chat/completions")
        self.assertTrue(client.stream_calls[0]["json"]["stream"])


if __name__ == "__main__":
    unittest.main()
