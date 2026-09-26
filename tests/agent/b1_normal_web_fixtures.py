"""Actual temporary sources, never pre-executed Web evidence or host assets.

RAG setup follows test_rag_receipt_consumption.initialized_service, with cleanup
registered *before* initialize (including its failure/cancellation paths).
Reverse files use the existing synthetic RDKit writer, not a prediction double.
MockTransport supplies embedding protocol bytes, not live model inference.
"""
import asyncio
from contextlib import contextmanager, ExitStack
import json
from pathlib import Path
from types import SimpleNamespace

import faiss
import httpx
import numpy as np

from src.agent.tools.rag_search_tool import RAGSearchTool
from src.agent.tools.reverse_target_tool import ReverseTargetTool
from src.rag.index import RAGIndexManifest, atomic_save_index_pair, file_sha256
from src.rag.service import RAGSystem
from tests.test_reverse_target_invocation_receipts import (
    close_fixture_mmaps, make_writer_database,
)


@contextmanager
def temporary_b1_sources(directory, monkeypatch, *, empty=False):
    """Initialize synchronously before asyncio.run; yield zero prior invocations.

Tools borrow these sources. The app may acquire/close the injected RAG owner;
the outer fixture retires it if not transferred/closed and always owns reverse.
No activity predictor/weights or invented positive activity rows are installed.
"""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        raise RuntimeError('Initialize B1 sources outside the ASGI event loop')

    with ExitStack() as owners:
        patch = owners.enter_context(monkeypatch.context())
        directory.mkdir(parents=True, exist_ok=True)
        source = directory / 'synthetic.csv'
        source.write_text('SMILES,extension\nCCO,retained\nCCC,also retained\n',
                          encoding='utf-8')
        store = directory / 'vectors'
        index = faiss.IndexFlatIP(2)
        if not empty:
            index.add(np.asarray([[1., 0.], [0., 1.]], dtype=np.float32))
        manifest = RAGIndexManifest(
            schema_version=2, source_path=str(source), source_sha256=file_sha256(source),
            index_sha256='', embedding_model='synthetic-only', vector_dimension=2,
            vector_count=int(index.ntotal), row_mapping=[] if empty else [0, 1],
            created_at='2026-09-26T00:00:00+00:00')
        atomic_save_index_pair(index, Path(f'{store}.index'), manifest, faiss_module=faiss)
        rag = RAGSystem({'rag': {
            'csv_path': str(source), 'vector_store_path': str(store),
            'embedding_model': 'synthetic-only',
            'embedding_endpoint': 'http://synthetic.invalid/api/embeddings'}})

        def retire_rag():
            if not rag._closed:  # The actual app may already have retired its owner.
                rag.close()

        owners.callback(retire_rag)
        requests = []

        def reply(request):
            assert request.method == 'POST'
            assert str(request.url) == rag.embedding_endpoint
            requests.append(json.loads(request.content))
            return httpx.Response(200, json={'embedding': [1., 0.]})

        transport = owners.enter_context(httpx.MockTransport(reply))
        patch.setattr(httpx.HTTPTransport, 'handle_request',
                      lambda self, request: transport.handle_request(request))

        async def async_reply(self, request):
            return await transport.handle_async_request(request)

        patch.setattr(httpx.AsyncHTTPTransport, 'handle_async_request', async_reply)
        asyncio.run(rag.initialize())
        assert rag.is_initialized and rag.index_status == 'loaded'
        assert requests == []

        reverse = make_writer_database(directory / 'reverse',
                                       smiles=() if empty else ('CCO', 'CCC'))
        owners.callback(close_fixture_mmaps, reverse)
        owners.callback(reverse.close_strict)
        reverse.initialize_strict()
        rag_tool, reverse_tool = RAGSearchTool(rag), ReverseTargetTool(reverse)

        def retire_tool():
            if not reverse_tool._is_closed():
                reverse_tool.close()

        owners.callback(retire_tool)
        yield SimpleNamespace(rag=rag, reverse=reverse, rag_tool=rag_tool,
                              reverse_tool=reverse_tool, embedding_requests=requests)
