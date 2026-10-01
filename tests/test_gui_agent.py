"""Test suite for gui_agent.py vision-to-action automation."""
import pytest
from unittest.mock import patch, MagicMock

import gui_agent


class TestGUIAgent:
    """Test cases for vision-to-action coordinate translation."""

    def test_parse_coordinates_format_1(self):
        """Test parsing format: x: 0.5, y: 0.3"""
        response = "The button is located at x: 0.5, y: 0.3 on screen"
        coords = gui_agent._parse_coordinates_from_vision(response)

        assert coords is not None
        assert coords == (0.5, 0.3)

    def test_parse_coordinates_format_2(self):
        """Test parsing format: (0.45, 0.67)"""
        response = "Coordinates: (0.45, 0.67)"
        coords = gui_agent._parse_coordinates_from_vision(response)

        assert coords is not None
        assert coords == (0.45, 0.67)

    def test_parse_coordinates_format_3(self):
        """Test parsing format: position 0.25, 0.75"""
        response = "Found at position 0.25, 0.75"
        coords = gui_agent._parse_coordinates_from_vision(response)

        assert coords is not None
        assert coords == (0.25, 0.75)

    def test_parse_coordinates_out_of_bounds(self):
        """Test that out of bounds coordinates are rejected."""
        response = "x: 1.5, y: 0.3"
        coords = gui_agent._parse_coordinates_from_vision(response)

        # 1.5 is > 1.0, should be rejected or None
        assert coords is None

    def test_parse_coordinates_empty_response(self):
        """Test parsing empty response."""
        coords = gui_agent._parse_coordinates_from_vision("")
        assert coords is None

    def test_parse_coordinates_no_match(self):
        """Test response with no coordinates."""
        response = "I cannot see the button anywhere on this screen"
        coords = gui_agent._parse_coordinates_from_vision(response)
        assert coords is None

    def test_click_element_empty_description(self):
        """Test click with empty description."""
        result = gui_agent.click_element("")
        assert result is False

    @patch('gui_agent.vision.screenshot_to_base64')
    @patch('gui_agent.vision.analyze_image_base64')
    @patch('gui_agent.pyautogui')
    def test_click_element_success(self, mock_pyautogui, mock_analyze, mock_screenshot):
        """Test successful element click."""
        mock_screenshot.return_value = "fake_base64"
        mock_analyze.return_value = "x: 0.5, y: 0.5"
        mock_pyautogui.size.return_value = (1920, 1080)

        result = gui_agent.click_element("submit button")

        assert result is True
        mock_pyautogui.click.assert_called_once_with(960, 540)

    @patch('gui_agent.vision.screenshot_to_base64')
    @patch('gui_agent.vision.analyze_image_base64')
    def test_click_element_not_found(self, mock_analyze, mock_screenshot):
        """Test element not found handling."""
        mock_screenshot.return_value = "fake_base64"
        mock_analyze.return_value = "NOT_FOUND"

        result = gui_agent.click_element("nonexistent button")

        assert result is False

    @patch('gui_agent.click_element')
    @patch('gui_agent.pyautogui')
    def test_type_into_success(self, mock_pyautogui, mock_click):
        """Test typing into element."""
        mock_click.return_value = True

        result = gui_agent.type_into("search box", "test query")

        assert result is True
        mock_click.assert_called_once_with("search box")
        mock_pyautogui.typewrite.assert_called_once()

    def test_type_into_empty_description(self):
        """Test typing with empty description."""
        result = gui_agent.type_into("", "test")
        assert result is False

    @patch('gui_agent.vision.screenshot_to_base64')
    @patch('gui_agent.vision.analyze_image_base64')
    @patch('gui_agent.pyautogui')
    def test_hover_element_success(self, mock_pyautogui, mock_analyze, mock_screenshot):
        """Test hovering over element."""
        mock_screenshot.return_value = "fake_base64"
        mock_analyze.return_value = "x: 0.25, y: 0.25"
        mock_pyautogui.size.return_value = (1920, 1080)

        result = gui_agent.hover_element("menu item")

        assert result is True
        mock_pyautogui.moveTo.assert_called_once_with(480, 270, duration=0.3)
