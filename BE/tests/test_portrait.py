import os
import sys
import unittest
from unittest import mock

import cv2
import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from services import portrait_service  # noqa: E402


def _jpeg_bytes(image):
    ok, buf = cv2.imencode(".jpg", image)
    assert ok
    return buf.tobytes()


def _blank_card(width=1000, height=630):
    return np.full((height, width, 3), 235, dtype=np.uint8)


class YuNetModelTest(unittest.TestCase):

    def test_model_path_is_absolute_and_exists(self):
        self.assertTrue(os.path.isabs(portrait_service.YUNET_MODEL_PATH))
        self.assertTrue(os.path.isfile(portrait_service.YUNET_MODEL_PATH))

    def test_detector_loads(self):
        self.assertIsNotNone(portrait_service._get_yunet_detector())

    def test_no_face_on_blank_card(self):
        self.assertIsNone(portrait_service.detect_face_yunet(_blank_card()))

    def test_prefers_face_in_card_portrait_region_over_larger_background_face(self):
        faces = np.array(
            [
                [120, 180, 90, 100],
                [640, 140, 250, 260],
            ],
            dtype=np.float32,
        )

        face = portrait_service._select_card_portrait_face(faces, 1000, 630)

        self.assertEqual(face[:4].tolist(), [120.0, 180.0, 90.0, 100.0])

    def test_falls_back_to_largest_face_when_portrait_region_is_empty(self):
        faces = np.array(
            [
                [650, 120, 80, 90],
                [720, 260, 140, 150],
            ],
            dtype=np.float32,
        )

        face = portrait_service._select_card_portrait_face(faces, 1000, 630)

        self.assertEqual(face[:4].tolist(), [720.0, 260.0, 140.0, 150.0])


class CropPortraitTest(unittest.TestCase):

    def test_invalid_bytes_raise(self):
        with self.assertRaises(ValueError):
            portrait_service.crop_portrait_from_bytes(b"not-an-image")

    def test_blank_card_raises_no_face(self):
        with self.assertRaises(ValueError):
            portrait_service.crop_portrait_from_bytes(_jpeg_bytes(_blank_card()))

    def test_response_format_with_yunet(self):
        # Ảnh rộng 1000px được đưa lên 1600px để dò → tọa độ phải quy về ảnh gốc
        box = {"x": 320, "y": 400, "width": 240, "height": 240}
        with mock.patch.object(portrait_service, "detect_face_yunet", return_value=box):
            result = portrait_service.crop_portrait_from_bytes(_jpeg_bytes(_blank_card()))

        self.assertEqual(
            set(result),
            {"success", "image_base64", "width", "height", "face", "crop", "detector"},
        )
        self.assertTrue(result["success"])
        self.assertTrue(result["image_base64"].startswith("data:image/jpeg;base64,"))
        self.assertEqual((result["width"], result["height"]), (600, 770))
        self.assertEqual(result["face"], {"x": 200, "y": 250, "width": 150, "height": 150})
        self.assertEqual(set(result["crop"]), {"x1", "y1", "x2", "y2"})
        self.assertEqual(result["detector"], "YuNet")

    def test_haar_fallback_when_yunet_finds_nothing(self):
        box = {"x": 320, "y": 400, "width": 240, "height": 240}
        with mock.patch.object(portrait_service, "detect_face_yunet", return_value=None), \
                mock.patch.object(portrait_service, "detect_face", return_value=box) as haar:
            result = portrait_service.crop_portrait_from_bytes(_jpeg_bytes(_blank_card()))

        haar.assert_called_once()
        self.assertEqual(result["detector"], "Haar")


if __name__ == "__main__":
    unittest.main()
