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

    def test_rejects_faces_outside_card_portrait_region(self):
        faces = np.array(
            [
                [650, 120, 80, 90],
                [720, 260, 140, 150],
            ],
            dtype=np.float32,
        )

        face = portrait_service._select_card_portrait_face(faces, 1000, 630)

        self.assertIsNone(face)


class CropPortraitTest(unittest.TestCase):

    def test_invalid_bytes_raise(self):
        with self.assertRaises(ValueError):
            portrait_service.crop_portrait_from_bytes(b"not-an-image")

    def test_blank_card_raises_no_face(self):
        with self.assertRaises(ValueError):
            portrait_service.crop_portrait_from_bytes(_jpeg_bytes(_blank_card()))

    def test_rejects_detected_face_outside_portrait_region(self):
        box = {"x": 1050, "y": 250, "width": 180, "height": 180}
        with (
            mock.patch.object(portrait_service, "detect_face_yunet", return_value=box),
            mock.patch.object(portrait_service, "detect_face", return_value=box),
        ):
            with self.assertRaisesRegex(ValueError, "vùng ảnh mặt trước"):
                portrait_service.crop_portrait_from_bytes(
                    _jpeg_bytes(_blank_card(width=1600))
                )

    def test_response_format_with_yunet(self):
        # Detection is resized to a bounded long edge; return coordinates in source pixels.
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
        self.assertEqual(result["face"], {"x": 250, "y": 312, "width": 187, "height": 187})
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
