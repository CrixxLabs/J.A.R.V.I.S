import time
from pathlib import Path
import creative_agent

def test_creative_capability_definitions():
    import status_registry
    assert "creative_agent.py" in status_registry.CAPABILITY_DEFINITIONS["IMAGE_GENERATION"]["implemented_by"]
    assert "OPENROUTER" not in status_registry.CAPABILITY_DEFINITIONS["VIDEO_GENERATION"].get("dependencies", [])

def test_background_image_job_does_not_block(monkeypatch, tmp_path):
    class R: ok=True; path=str(tmp_path/"done.png"); message=""
    class Studio:
        def generate_image(self, prompt):
            time.sleep(.08); Path(R.path).write_bytes(b"x"); return R()
    monkeypatch.setattr(creative_agent, "CreativeStudio", Studio)
    start=time.monotonic(); job=creative_agent.submit_image("x"); elapsed=time.monotonic()-start
    assert elapsed < .05
    for _ in range(50):
        if job.status=="done": break
        time.sleep(.01)
    assert job.status=="done" and Path(job.path).exists()

def test_latest_asset_open_contract(monkeypatch, tmp_path):
    f=tmp_path/"x.png"; f.write_bytes(b"x")
    creative_agent._set_latest(str(f), "image")
    monkeypatch.setattr(creative_agent.os, "startfile", lambda p: None, raising=False)
    monkeypatch.setattr(creative_agent.subprocess, "Popen", lambda *args, **kwargs: None)
    assert creative_agent.open_asset()

def test_planner_creative_capability_question_is_not_action():
    import planner
    action, response, _ = planner.ask("Can you generate images and videos?")
    assert action is None
    assert "image generation is " in response.lower()
    assert "image-to-video is " in response.lower()
    assert "text-to-video" in response.lower()

def test_planner_direct_image_request_is_action():
    import planner
    action, _, _ = planner.ask("Generate an image of a black and gold arc reactor")
    assert action and action["action"] == "generate_image"

def test_planner_opinion_does_not_generate():
    import planner
    action, _, _ = planner.ask("What do you think about AI image generation?")
    assert not action or action.get("action") != "generate_image"
