import time
from pathlib import Path


def test_creative_status_reports_any_active_job(monkeypatch, tmp_path):
    import creative_agent
    creative_agent._jobs.clear()
    creative_agent._counter = 0
    j1 = creative_agent.Job(creative_agent._next_id(), "image", status="running")
    j2 = creative_agent.Job(creative_agent._next_id(), "video", status="queued")
    creative_agent._jobs[j1.id] = j1
    creative_agent._jobs[j2.id] = j2
    info = creative_agent.latest_status()
    assert info["active"] is True
    assert {j["kind"] for j in info["active_jobs"]} == {"image", "video"}


def test_cancel_latest_generation_abandons_job():
    import creative_agent
    creative_agent._jobs.clear()
    creative_agent._counter = 0
    job = creative_agent.Job(creative_agent._next_id(), "image", status="running")
    creative_agent._jobs[job.id] = job
    assert creative_agent.cancel() is True
    assert job.status == "cancelled"


def test_cancelled_image_result_is_not_promoted(monkeypatch, tmp_path):
    import creative_agent
    class R:
        ok = True
        path = str(tmp_path / "done.png")
        message = ""
    class Studio:
        def generate_image(self, prompt):
            time.sleep(.05)
            Path(R.path).write_bytes(b"x")
            return R()
    creative_agent._jobs.clear(); creative_agent._latest_asset = None; creative_agent._counter = 0
    monkeypatch.setattr(creative_agent, "CreativeStudio", Studio)
    job = creative_agent.submit_image("x")
    assert creative_agent.cancel(job.id)
    time.sleep(.12)
    assert job.status == "cancelled"
    assert creative_agent.latest_asset() is None


def test_executor_legacy_video_is_truthfully_disabled():
    import executor
    msg = executor.generate_video("a city")
    assert "not enabled" in msg.lower()
    assert "openrouter" not in msg.lower()


def test_creative_status_exposes_latest_job_state():
    import creative_agent
    creative_agent._jobs.clear()
    creative_agent._counter = 0
    job = creative_agent.Job(creative_agent._next_id(), "image", status="failed", error="provider timeout")
    creative_agent._jobs[job.id] = job
    info = creative_agent.latest_status()
    assert info["job"]["status"] == "failed"
    assert info["recent_jobs"][0]["error"] == "provider timeout"
