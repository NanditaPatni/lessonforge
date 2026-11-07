import pytest


@pytest.fixture(autouse=True)
def _no_retry_wait(monkeypatch):
    """Automatic step retry waits 20s in real use; tests don't wait."""
    from app.pipeline import orchestrator
    monkeypatch.setattr(orchestrator, "AUTO_RETRY_WAIT", 0)
