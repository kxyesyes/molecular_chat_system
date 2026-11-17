"""Homepage projects, source documents, and grounded document questions."""
import asyncio
import sqlite3
import time
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

from fastapi import FastAPI, File, HTTPException, Request, Response, UploadFile
from pydantic import BaseModel, Field, StringConstraints
from typing import Annotated, Literal

from src.web.project_workspace import (
    MAX_DOCUMENT_BYTES, document_prompt, document_text, require_project, workspace_db,
)
from src.web.request_auth import require_browser_session


_DOCUMENT_SOURCES = """
    WITH sources AS (
        SELECT id, project, name, rowid AS upload_order,
               CASE WHEN count(*) OVER same_name > 1
                    THEN row_number() OVER (same_name ORDER BY rowid) END AS upload_number
        FROM documents WINDOW same_name AS (PARTITION BY project, name)
    )
"""


class ProjectName(BaseModel):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]


class Question(BaseModel):
    question: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]


class Bookmark(BaseModel):
    kind: Literal["task", "run"]
    record: str = Field(min_length=1, max_length=128)


def setup_workspace_routes(app: FastAPI, root: Path, answer_document, run_store=None):
    base = "/api/workspace"

    def database(request):
        return workspace_db(root, require_browser_session(request))

    @app.get(base + "/projects")
    def projects(request: Request):
        with database(request) as db:
            return [dict(row) for row in db.execute("SELECT id,name FROM projects ORDER BY rowid DESC")]

    @app.post(base + "/projects", status_code=201)
    def create(request: Request, data: ProjectName):
        project = uuid4().hex
        with database(request) as db:
            try:
                db.execute("INSERT INTO projects VALUES (?,?)", (project, data.name))
            except sqlite3.IntegrityError:
                raise HTTPException(409, "项目名称已存在") from None
        return {"id": project, "name": data.name}

    @app.patch(base + "/projects/{project}")
    def rename(project: str, request: Request, data: ProjectName):
        with database(request) as db:
            require_project(db, project)
            try:
                db.execute("UPDATE projects SET name=? WHERE id=?", (data.name, project))
            except sqlite3.IntegrityError:
                raise HTTPException(409, "项目名称已存在") from None
        return {"id": project, "name": data.name}

    @app.get(base + "/projects/{project}")
    def detail(project: str, request: Request):
        with database(request) as db:
            result = require_project(db, project)
            result["documents"] = [dict(r) for r in db.execute(_DOCUMENT_SOURCES +
                "SELECT id,name,upload_number FROM sources WHERE project=? ORDER BY upload_order DESC", (project,))]
            result["questions"] = [dict(r) for r in db.execute(_DOCUMENT_SOURCES +
                """SELECT d.id AS document_id,d.name,d.upload_number,q.question,q.answer,q.created
                   FROM questions q JOIN sources d ON d.id=q.document WHERE d.project=? ORDER BY q.id DESC""", (project,))]
            result["bookmarks"] = [dict(r) for r in db.execute(
                "SELECT kind,record,title FROM bookmarks WHERE project=? ORDER BY rowid DESC", (project,))]
            return result

    @app.post(base + "/projects/{project}/documents", status_code=201)
    def upload(project: str, request: Request, file: UploadFile = File(...)):
        with database(request) as db:
            require_project(db, project)
            name = (file.filename or "").replace("\\", "/").split("/")[-1].strip()
            if not name or len(name) > 240 or any(ord(c) < 32 for c in name):
                raise HTTPException(422, "文件名无效")
            original = file.file.read(MAX_DOCUMENT_BYTES + 1)
            body = document_text(name, original)
            doc_id = uuid4().hex
            db.execute("INSERT INTO documents VALUES (?,?,?,?,?)", (doc_id, project, name, original, body))
        return {"id": doc_id, "name": name}

    @app.get(base + "/documents/{doc_id}")
    def download(doc_id: str, request: Request):
        with database(request) as db:
            doc = db.execute("SELECT name,original FROM documents WHERE id=?", (doc_id,)).fetchone()
            if not doc:
                raise HTTPException(404, "文档不存在")
            return Response(doc["original"], media_type="application/octet-stream", headers={
                "Content-Disposition": "attachment; filename*=UTF-8''" + quote(doc["name"], safe=""),
                "X-Content-Type-Options": "nosniff"})

    @app.post(base + "/documents/{doc_id}/questions")
    async def ask(doc_id: str, request: Request, data: Question):
        def prepare():
            with database(request) as db:
                row = db.execute("SELECT name,body FROM documents WHERE id=?", (doc_id,)).fetchone()
                if not row:
                    raise HTTPException(404, "文档不存在")
                recent = db.execute(
                    """SELECT question,substr(answer,1,1200) AS answer FROM questions
                       WHERE document=? ORDER BY id DESC LIMIT 2""", (doc_id,)).fetchall()
                return document_prompt(row["name"], row["body"], data.question, recent[::-1])

        prompt = await asyncio.to_thread(prepare)
        try:
            answer = await answer_document(prompt)
        except Exception:
            raise HTTPException(503, "文档问答失败，请检查主模型连接后重试") from None

        def archive():
            with database(request) as db:
                db.execute("INSERT INTO questions(document,question,answer,created) VALUES (?,?,?,?)",
                           (doc_id, data.question, answer, time.time()))
        await asyncio.to_thread(archive)
        return {"answer": answer}

    @app.get(base + "/questions")
    def questions(request: Request):
        with database(request) as db:
            return [dict(r) for r in db.execute(_DOCUMENT_SOURCES +
                """SELECT d.project,p.name AS project_name,d.id AS document_id,d.name,d.upload_number,
                          q.question,q.answer,q.created
                   FROM questions q JOIN sources d ON d.id=q.document JOIN projects p ON p.id=d.project
                   ORDER BY q.id DESC LIMIT 100""")]

    @app.get(base + "/runs")
    def runs(request: Request):
        owner = require_browser_session(request)
        return run_store().list_workspace_runs(owner) if run_store else []

    @app.get(base + "/runs/{trace}/reference")
    def reference(trace: str, request: Request):
        from .agent_run_routes import _owned_run
        owner = require_browser_session(request)
        store = run_store()
        if not _owned_run(store, trace, owner):
            raise HTTPException(404, "分析记录不存在")
        return store.latest_result_reference(trace, owner)

    @app.post(base + "/projects/{project}/bookmarks")
    async def bookmark(project: str, request: Request, data: Bookmark):
        owner = require_browser_session(request)
        if data.kind == "task":
            lookup = getattr(app.state, "workspace_task_lookup", None)
            if lookup is None:
                raise HTTPException(503, "任务记录服务暂不可用")
            try:
                task, _, _ = await lookup(request, data.record, refresh=False)
            except KeyError:
                raise HTTPException(404, "分析记录不存在") from None
            title = task.task_type
        else:
            from .agent_run_routes import _owned_run
            owned = await asyncio.to_thread(lambda: _owned_run(run_store(), data.record, owner))
            if not owned:
                raise HTTPException(404, "分析记录不存在")
            title = (owned.get("query") or "首页分析")[:100]

        def save():
            with database(request) as db:
                require_project(db, project)
                db.execute("INSERT OR REPLACE INTO bookmarks VALUES (?,?,?,?)",
                           (data.kind, data.record, title, project))
        await asyncio.to_thread(save)
        return {"project": project}
