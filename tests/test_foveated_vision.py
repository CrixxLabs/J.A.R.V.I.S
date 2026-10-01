"""Tests for Foveated Saccadic Vision Grounding Engine — MARK VIII."""
import unittest
from unittest.mock import MagicMock, patch

try:
    from PIL import Image
    _PIL_AVAILABLE = True
except ImportError:
    Image = None
    _PIL_AVAILABLE = False

import foveated_vision
from status_registry import EvidenceLevel, get_registry


class TestFoveatedVision(unittest.TestCase):
    def test_parse_box_or_coords(self):
        # Normalized bounding box [ymin, xmin, ymax, xmax]
        box_str = "[0.10, 0.20, 0.30, 0.40]"
        box = foveated_vision._parse_box_or_coords(box_str)
        self.assertIsNotNone(box)
        self.assertAlmostEqual(box[0], 0.20)  # xmin
        self.assertAlmostEqual(box[1], 0.10)  # ymin
        self.assertAlmostEqual(box[2], 0.40)  # xmax
        self.assertAlmostEqual(box[3], 0.30)  # ymax

        # Coordinates x: 0.5, y: 0.3
        coord_str = "Found at x: 0.5, y: 0.3 on screen"
        box2 = foveated_vision._parse_box_or_coords(coord_str)
        self.assertIsNotNone(box2)
        self.assertLessEqual(box2[0], 0.5)
        self.assertGreaterEqual(box2[2], 0.5)

    @unittest.skipUnless(_PIL_AVAILABLE, "PIL not installed")
    def test_saccadic_crop(self):
        # Create a mock 1920x1080 image
        img = Image.new("RGB", (1920, 1080), color="blue")
        coarse_box = (0.5, 0.5, 0.6, 0.6)  # center region

        cropped, bounds = foveated_vision.saccadic_crop(img, coarse_box, foveal_size=(512, 512))
        self.assertIsNotNone(cropped)
        self.assertEqual(cropped.size, (512, 512))
        x1, y1, x2, y2 = bounds
        self.assertEqual(x2 - x1, 512)
        self.assertEqual(y2 - y1, 512)
        self.assertGreaterEqual(x1, 0)
        self.assertLessEqual(x2, 1920)

    @patch("vision.analyze_image_base64")
    def test_peripheral_scan(self, mock_analyze):
        mock_analyze.return_value = "The element is located at [0.15, 0.25, 0.35, 0.45]"
        box = foveated_vision.peripheral_scan("dummy_b64", "Submit Button")
        self.assertIsNotNone(box)
        self.assertAlmostEqual(box[0], 0.25)
        self.assertAlmostEqual(box[1], 0.15)

    @patch("vision.analyze_image_base64")
    def test_foveal_ground(self, mock_analyze):
        mock_analyze.return_value = "Center of element is at x: 0.5, y: 0.5"
        crop_bounds = (100, 200, 612, 712)  # width 512, height 512
        coords = foveated_vision.foveal_ground("dummy_b64", "Search Bar", crop_bounds)
        self.assertIsNotNone(coords)
        # Expected: 100 + 0.5*512 = 356, 200 + 0.5*512 = 456
        self.assertEqual(coords, (356, 456))

    @unittest.skipUnless(_PIL_AVAILABLE, "PIL not installed")
    @patch("vision.analyze_image_base64")
    def test_foveated_locate_element_full_pipeline(self, mock_analyze):
        # 1st call for peripheral, 2nd call for foveal
        mock_analyze.side_effect = [
            "[0.4, 0.4, 0.6, 0.6]",
            "x: 0.5, y: 0.5",
        ]
        img = Image.new("RGB", (1920, 1080), color="white")
        coords = foveated_vision.foveated_locate_element("Play button", pil_image=img)
        self.assertIsNotNone(coords)
        self.assertIsInstance(coords, tuple)
        self.assertEqual(len(coords), 2)


if __name__ == "__main__":
    unittest.main()
