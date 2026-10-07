"""Activity training jobs must not expose another browser session's status."""

from src.activity import trainer


def test_training_job_status_is_bound_to_the_submitting_session(monkeypatch):
    class Thread:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def start(self):
            return None

    monkeypatch.setattr(trainer.threading, "Thread", Thread)
    job_id = trainer.submit_training_job(
        file_path="unused.csv",
        owner_session_id="session-owner",
    )
    try:
        assert trainer.get_job_status(job_id, owner_session_id="session-owner") is not None
        assert trainer.get_job_status(job_id, owner_session_id="session-foreign") is None
        assert "owner_session_id" not in trainer.get_job_status(
            job_id, owner_session_id="session-owner"
        )
    finally:
        trainer.training_jobs.pop(job_id, None)


def test_training_job_list_events_and_cancel_are_owner_scoped(monkeypatch):
    class Thread:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def start(self):
            return None

    monkeypatch.setattr(trainer.threading, "Thread", Thread)
    job_id = trainer.submit_training_job(
        file_path="unused.csv",
        owner_session_id="session-owner",
    )
    try:
        trainer.training_jobs[job_id]["logs"].extend(["started", "progress 10%"])

        owner_jobs = trainer.list_training_jobs("session-owner")
        assert [job["job_id"] for job in owner_jobs] == [job_id]
        assert trainer.list_training_jobs("session-foreign") == []
        assert "owner_session_id" not in owner_jobs[0]
        assert "_cancel_event" not in owner_jobs[0]

        events = trainer.get_training_events(job_id, owner_session_id="session-owner")
        assert [event["sequence"] for event in events] == [1, 2]
        assert [event["message"] for event in events] == ["started", "progress 10%"]
        assert trainer.get_training_events(
            job_id, owner_session_id="session-foreign"
        ) is None

        assert trainer.cancel_training_job(
            job_id, owner_session_id="session-foreign"
        ) is None
        canceled = trainer.cancel_training_job(
            job_id, owner_session_id="session-owner"
        )
        assert canceled["cancel_requested"] is True
        assert trainer.training_jobs[job_id]["_cancel_event"].is_set()
    finally:
        trainer.training_jobs.pop(job_id, None)


def test_training_loop_honors_cancel_before_loading_dependencies(monkeypatch):
    class Thread:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def start(self):
            return None

    monkeypatch.setattr(trainer.threading, "Thread", Thread)
    job_id = trainer.submit_training_job(
        file_path="unused.csv",
        owner_session_id="session-owner",
    )
    try:
        trainer.cancel_training_job(job_id, owner_session_id="session-owner")
        instance = trainer.ActivityTrainer(job_id)
        instance._train_loop(
            "unused.csv", "target", "regression", 1, 0.001, 1, 0.2,
            1, 8, 0.0, 1, "MSE", "Cosine", "random", 42, "smiles",
        )
        assert trainer.get_job_status(
            job_id, owner_session_id="session-owner"
        )["state"] == "canceled"
    finally:
        trainer.training_jobs.pop(job_id, None)


def test_early_training_cancellation_removes_uploaded_dataset(monkeypatch, tmp_path):
    class Thread:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def start(self):
            return None

    monkeypatch.setattr(trainer.threading, "Thread", Thread)
    dataset = tmp_path / "uploaded.csv"
    dataset.write_text("smiles,target\nCC,1\n", encoding="utf-8")
    job_id = trainer.submit_training_job(
        file_path=str(dataset),
        owner_session_id="session-owner",
    )
    try:
        trainer.cancel_training_job(job_id, owner_session_id="session-owner")
        instance = trainer.ActivityTrainer(job_id)
        instance._train_loop(
            str(dataset), "target", "regression", 1, 0.001, 1, 0.2,
            1, 8, 0.0, 1, "MSE", "Cosine", "random", 42, "smiles",
        )
        assert not dataset.exists()
    finally:
        trainer.training_jobs.pop(job_id, None)
