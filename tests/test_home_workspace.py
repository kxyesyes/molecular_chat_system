from io import BytesIO
from zipfile import ZipFile

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.web.agent_session_config import setup_agent_sessions


def test_project_document_question_archive_and_owner_isolation(tmp_path, monkeypatch):
    from src.web.routes.workspace_routes import setup_workspace_routes
    monkeypatch.setenv("MEDCHAT_AGENT_SESSION_DB", str(tmp_path / "session.db"))
    prompts = []

    async def answer(prompt):
        prompts.append(prompt)
        return "终点是溶解度。"

    def build():
        app = FastAPI()
        setup_agent_sessions(app)
        setup_workspace_routes(app, tmp_path / "projects", answer)
        return TestClient(app, base_url="http://localhost")

    owner, stranger = build(), build()
    base = "/api/workspace"
    project = owner.post(base + "/projects", json={"name": "实验一"}).json()["id"]
    root = f"{base}/projects/{project}"
    assert owner.patch(root, json={"name": "资料组"}).status_code == 200
    doc = owner.post(root + "/documents", files={"file": ("研究.md", "终点是溶解度。".encode())}).json()["id"]
    path = f"{base}/documents/{doc}"
    assert owner.get(path).content == "终点是溶解度。".encode()
    assert stranger.get(path).status_code == 404
    assert stranger.post(path + "/questions", json={"question": "终点？"}).status_code == 404
    assert not prompts
    result = owner.post(path + "/questions", json={"question": "研究终点是什么？"})
    assert result.json()["answer"] == "终点是溶解度。"
    assert "研究终点是什么？" in prompts[0] and "终点是溶解度。" in prompts[0]
    reopened = build()
    reopened.cookies.update(owner.cookies)
    state = reopened.get(base + "/projects").json()
    assert any(p["name"] == "资料组" for p in state)
    detail = reopened.get(root).json()
    assert detail["documents"][0]["name"] == "研究.md"
    assert detail["questions"][0]["answer"] == result.json()["answer"]
    assert reopened.post(path + "/questions", json={"question":"它在文中出现了吗？"}).status_code == 200
    assert "研究终点是什么？" in prompts[-1]
    assert stranger.get(root).status_code == 404
    assert owner.post(root + "/documents", files={"file": ("空.txt", b"  ")}).status_code == 422
    assert owner.post(root + "/documents", files={"file": ("data.exe", b"word")}).status_code == 415
    assert owner.post(base + "/projects", json={"name":"  "}).status_code == 422
    assert owner.post(base + "/projects", json={"name":"资料组"}).status_code == 409


def test_archive_uses_verified_result_and_owner(tmp_path, monkeypatch):
    from src.agent.persistence.sqlite_store import SQLiteAgentStateStore
    from src.agent.persistence.result_details import publish_result_details
    from src.web.routes.workspace_routes import setup_workspace_routes
    from src.web.agent_session import COOKIE_NAME

    monkeypatch.setenv("MEDCHAT_AGENT_SESSION_DB", str(tmp_path / "sessions.db"))
    app = FastAPI()
    setup_agent_sessions(app)
    store = SQLiteAgentStateStore(tmp_path / "state" / "agent.sqlite")
    setup_workspace_routes(app, tmp_path / "projects", None, lambda: store)
    client, other = [TestClient(app, base_url="http://localhost") for _ in range(2)]
    base = "/api/workspace"
    project = client.post(base + "/projects", json={"name":"旧分析"}).json()["id"]
    different = other.post(base + "/projects", json={"name":"独立项目"}).json()["id"]
    owner = app.state.agent_session_store.resolve(client.cookies.get(COOKIE_NAME))
    trace = "a" * 32
    store.start_run({"trace_id":trace, "status":"completed", "user_id":owner,
                     "session_id":owner, "query":"已保存的分析"})
    data = {"format":"summary_details_v1", "trace_id":trace, "status":"completed",
            "tool_result_sequence":[], "display_redacted":False, "warnings":[], "error":None}
    reference = publish_result_details(store, trace, session_id=owner, payload=data)
    assert reference
    assert client.get(base + "/runs").json()[0]["title"] == "已保存的分析"
    assert other.get(base + "/runs").json() == []
    assert client.get(base + "/runs/" + trace + "/reference").json() == reference
    assert other.get(base + "/runs/" + trace + "/reference").status_code == 404
    body = {"kind":"run", "record":trace}
    assert other.post(f"{base}/projects/{different}/bookmarks", json=body).status_code == 404
    assert client.post(f"{base}/projects/{project}/bookmarks", json=body).status_code == 200
    assert client.get(f"{base}/projects/{project}").json()["bookmarks"][0]["title"] == "已保存的分析"


def test_docx_and_long_document_retrieval():
    from src.web.project_workspace import document_text, document_prompt
    buf = BytesIO()
    with ZipFile(buf, "w") as doc:
        doc.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:p><w:r><w:t>采样时间</w:t></w:r></w:p></w:document>')
    assert document_text("报告.docx", buf.getvalue()) == "采样时间"
    prompt = document_prompt("长文档", "a" * 18000 + "采样时间：下午三时", "采样时间是什么？")
    assert "[13] 采样时间：下午三时" in prompt
    assert len(prompt) < 13000


def test_configured_model_receives_source_and_provider_failure_is_retryable(tmp_path, monkeypatch):
    import json
    import httpx
    from src.system.openai_compatible_model import OpenAICompatibleModel
    from src.web.project_workspace import generate_document_answer
    from src.web.routes.workspace_routes import setup_workspace_routes
    monkeypatch.setenv("MEDCHAT_AGENT_SESSION_DB", str(tmp_path / "sessions.db"))
    calls = []

    def upstream(request):
        calls.append(json.loads(request.content))
        if len(calls) == 1:
            return httpx.Response(503)
        return httpx.Response(200, json={"choices":[{"message":{"content":"室温 [1]"},"finish_reason":"stop"}]})

    model = OpenAICompatibleModel("fixture", "reading-model", "https://example.invalid/v1",
                                  client=httpx.AsyncClient(transport=httpx.MockTransport(upstream)))
    app = FastAPI()
    setup_agent_sessions(app)
    setup_workspace_routes(app, tmp_path / "projects",
                           lambda prompt: generate_document_answer(model, prompt))
    with TestClient(app, base_url="http://localhost") as client:
        base = "/api/workspace"
        p = client.post(base + "/projects", json={"name":"实验"}).json()["id"]
        d = client.post(f"{base}/projects/{p}/documents",files={"file":("资料.txt","样品保持室温。".encode())}).json()["id"]
        path = f"{base}/documents/{d}/questions"
        question = {"question":"样品温度？"}
        assert client.post(path, json=question).status_code == 503
        assert client.get(base + "/questions").json() == []
        assert client.post(path, json=question).json()["answer"] == "室温 [1]"
        assert all(call["model"] == "reading-model" and "tools" not in call for call in calls)
        assert "样品保持室温。" in calls[1]["messages"][0]["content"]


def test_task_bookmark_requires_same_owner(tmp_path, monkeypatch):
    from src.task_runtime import routes
    from src.task_runtime.manager import TaskManager
    from src.web.routes.workspace_routes import setup_workspace_routes
    from src.web.agent_session import COOKIE_NAME
    monkeypatch.setenv("MEDCHAT_AGENT_SESSION_DB", str(tmp_path / "sessions.db"))
    app = FastAPI()
    manager = TaskManager(tmp_path / "tasks.sqlite")
    monkeypatch.setattr(routes, "get_task_manager", lambda: manager)
    setup_agent_sessions(app)
    routes.setup_task_routes(app)
    setup_workspace_routes(app, tmp_path / "projects", None)
    base = "/api/workspace/projects"
    with TestClient(app, base_url="http://localhost") as a, TestClient(app, base_url="http://localhost") as b:
        pa, pb = [c.post(base, json={"name":"项目"}).json()["id"] for c in (a,b)]
        owner = app.state.agent_session_store.resolve(a.cookies.get(COOKIE_NAME))
        manager.store.create("t1","activity_prediction",{},owner_session_id=owner)
        body = {"kind":"task", "record":"t1"}
        assert b.post(f"{base}/{pb}/bookmarks",json=body).status_code == 404
        for _ in range(2):
            assert a.post(f"{base}/{pa}/bookmarks",json=body).status_code == 200
        assert len(a.get(f"{base}/{pa}").json()["bookmarks"]) == 1
    manager.executor.shutdown(wait=True)
