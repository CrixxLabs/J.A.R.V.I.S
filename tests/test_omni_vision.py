"""Unit tests for Native OS Surface Grounding & UI Parsing Engine (Module H)."""
from unittest.mock import MagicMock, patch
import pytest

from omni_vision import (
    OmniVisionEngine,
    locate_ui_element,
    extract_screen_hierarchy,
    click_element_by_semantic_target,
    get_normalized_coordinates,
)


@pytest.fixture
def engine():
    eng = OmniVisionEngine(screen_w=1920, screen_h=1080)
    mock_tree = [
        {
            "control_type": "Window",
            "name": "Visual Studio Code - J.A.R.V.I.S",
            "automation_id": "vscode_main",
            "bounding_box": [0, 0, 1920, 1080],
            "children": [
                {
                    "control_type": "Button",
                    "name": "Run Test",
                    "automation_id": "run_test_btn",
                    "bounding_box": [100, 50, 200, 90],
                },
                {
                    "control_type": "Edit",
                    "name": "Search Files",
                    "automation_id": "quick_open_input",
                    "bounding_box": [500, 20, 900, 60],
                },
            ],
        }
    ]
    eng.set_mock_ui_tree(mock_tree)
    return eng


def test_normalized_coordinates():
    box = (100, 200, 500, 600)
    coords = get_normalized_coordinates(box, screen_w=1000, screen_h=1000)
    assert coords["norm_left"] == 0.1
    assert coords["norm_top"] == 0.2
    assert coords["norm_right"] == 0.5
    assert coords["norm_bottom"] == 0.6
    assert coords["norm_cx"] == 0.3
    assert coords["norm_cy"] == 0.4
    assert coords["pixel_cx"] == 300
    assert coords["pixel_cy"] == 400


def test_extract_screen_hierarchy(engine):
    res = engine.extract_screen_hierarchy()
    assert res["success"] is True
    assert res["element_count"] > 0
    assert res["tree"][0]["name"] == "Visual Studio Code - J.A.R.V.I.S"


def test_locate_ui_element_success(engine):
    res = engine.locate_ui_element("Run Test")
    assert res["success"] is True
    assert res["status"] == "element_found"
    assert res["control_type"] == "Button"
    assert res["coords"]["pixel_cx"] == 150
    assert res["coords"]["pixel_cy"] == 70


def test_locate_ui_element_by_id(engine):
    res = engine.locate_ui_element("quick_open_input")
    assert res["success"] is True
    assert res["name"] == "Search Files"
    assert res["coords"]["pixel_cx"] == 700


def test_locate_ui_element_not_found(engine):
    res = engine.locate_ui_element("NonExistentButtonXYZ")
    assert res["success"] is False
    assert res["status"] == "element_not_found"


def test_click_element_by_semantic_target(engine):
    with patch("omni_vision.pyautogui") as mock_pyautogui:
        res = engine.click_element_by_semantic_target("Run Test", dry_run=False)
        assert res["success"] is True
        assert res["status"] == "clicked"
        assert res["coords"]["pixel_cx"] == 150
