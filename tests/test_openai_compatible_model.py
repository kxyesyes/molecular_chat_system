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
    def __init__(self, stream_chunks=None, post_payload=None):
        self.post_calls = []
        self.stream_calls = []
        self.stream_chunks = stream_chunks
        self.post_payload = post_payload

    async def post(self, url, headers=None, json=None):
        self.post_calls.append({"url": url, "headers": headers, "json": json})
        return _FakeResponse(
            self.post_payload
            or {"choices": [{"message": {"content": "API_OK"}}], "model": "demo-model"}
        )

    def stream(self, method, url, headers=None, json=None, timeout=None):
        self.stream_calls.append(
            {"method": method, "url": url, "headers": headers, "json": json}
        )
        lines = self.stream_chunks or [
            b'data: {"choices":[{"delta":{"content":"A"}}]}\n',
            b'data: {"choices":[{"delta":{"content":"PI_OK"}}]}\n',
            b"data: [DONE]\n",
        ]
        return _FakeStreamResponse(lines)


class OpenAICompatibleModelTest(unittest.TestCase):
    def test_http_error_does_not_log_untrusted_response_body(self):
        from src.agent.openai_compatible_model import OpenAICompatibleModel

        secret_marker = "fake-upstream-secret-marker"

        class ErrorClient:
            async def post(self, *_args, **_kwargs):
                response = _FakeResponse({})
                response.status_code = 401
                response.text = f"Authorization: Bearer {secret_marker}"
                return response

        model = OpenAICompatibleModel(
            api_key="fake-client-key",
            model_name="demo-model",
            base_url="https://api.example.com",
            client=ErrorClient(),
        )

        with self.assertLogs("src.agent.openai_compatible_model", level="ERROR") as logs:
            result = asyncio.run(model.generate("hello"))

        rendered_logs = "\n".join(logs.output)
        self.assertNotIn(secret_marker, rendered_logs)
        self.assertNotIn(secret_marker, result)

    def test_response_metadata_is_isolated_between_concurrent_requests(self):
        from src.agent.openai_compatible_model import OpenAICompatibleModel

        model = OpenAICompatibleModel(
            api_key="token",
            model_name="demo-model",
            base_url="https://api.example.com",
            client=_FakeClient(),
        )

        async def record(reason):
            model.last_response_metadata = {"finish_reason": reason}
            await asyncio.sleep(0)
            return model.last_response_metadata["finish_reason"]

        async def run_concurrently():
            return await asyncio.gather(record("length"), record("stop"))

        self.assertEqual(
            asyncio.run(run_concurrently()),
            ["length", "stop"],
        )

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

    def test_deepseek_v4_disables_thinking_for_chat_content_adapter(self):
        from src.agent.openai_compatible_model import OpenAICompatibleModel

        client = _FakeClient()
        model = OpenAICompatibleModel(
            api_key="token",
            model_name="deepseek-v4-pro",
            base_url="https://api.deepseek.com/chat/completions",
            client=client,
        )

        asyncio.run(model.generate("hello"))

        self.assertEqual(
            client.post_calls[0]["json"]["thinking"],
            {"type": "disabled"},
        )

    def test_stream_generate_reports_empty_deepseek_answer_and_finish_reason(self):
        from src.agent.openai_compatible_model import OpenAICompatibleModel

        client = _FakeClient(
            stream_chunks=[
                b'data: {"choices":[{"delta":{"reasoning_content":"hidden"},"finish_reason":null}]}\n',
                b'data: {"choices":[{"delta":{"content":""},"finish_reason":"length"}],"usage":{"completion_tokens":12}}\n',
                b"data: [DONE]\n",
            ]
        )
        model = OpenAICompatibleModel(
            api_key="token",
            model_name="deepseek-v4-pro",
            base_url="https://api.deepseek.com/chat/completions",
            client=client,
        )

        async def collect():
            return [chunk async for chunk in model.stream_generate("hello")]

        result = asyncio.run(collect())

        self.assertIn("未收到有效正文", "".join(result))
        self.assertEqual(model.last_response_metadata["finish_reason"], "length")
        self.assertEqual(model.last_response_metadata["reasoning_chars"], 6)
        self.assertEqual(model.last_response_metadata["usage"]["completion_tokens"], 12)


if __name__ == "__main__":
    unittest.main()
