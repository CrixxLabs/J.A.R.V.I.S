import planner

def test_whisper_fragment_image_routes_to_generation():
    action, _, _ = planner.ask("an image of a futuristic black and gold hark reactor")
    assert action and action["action"] == "generate_image"

def test_whisper_fragment_video_routes_deterministically():
    action, _, _ = planner.ask("a video of a futuristic city")
    assert action and action["action"] == "generate_video"

def test_image_ready_voice_phrase_routes_status():
    action, _, _ = planner.ask("the image ready")
    assert action and action["action"] == "creative_status"

def test_image_already_question_routes_status():
    action, _, _ = planner.ask("is the image already that i ask you for generation")
    assert action and action["action"] == "creative_status"

def test_image_generation_opinion_stays_conversation():
    action, _, _ = planner.ask("what do you think about AI image generation?")
    assert not action or action.get("action") != "generate_image"


def test_generation_status_natural_followups_route_status():
    for phrase in (
        "did it finish",
        "has the image finished",
        "any update on the image",
        "how's the image",
        "what's the generation status",
    ):
        action, _, _ = planner.ask(phrase)
        assert action and action["action"] == "creative_status"


def test_filler_does_not_reach_action_llm_path():
    for phrase in ("oh", "okay", "alright", "hmm", "yeah", "thanks"):
        action, response, model_type = planner.ask(phrase)
        assert action is None
        assert response
        assert model_type == "fast"
