import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services import openai_cccd


class OpenAICccdRequestTests(unittest.TestCase):
    def test_client_disables_retries_and_uses_timeout_below_worker_limit(self):
        with (
            patch.object(openai_cccd, "_client", None),
            patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}),
            patch.object(openai_cccd, "OpenAI") as openai_constructor,
        ):
            openai_cccd._get_client()

        openai_constructor.assert_called_once_with(
            api_key="test-key",
            timeout=90,
            max_retries=0,
        )

    def test_ocr_uses_model_default_temperature(self):
        client = Mock()
        client.chat.completions.create.return_value = SimpleNamespace(
            choices=[
                SimpleNamespace(message=SimpleNamespace(content="{}")),
            ],
        )

        with (
            patch.object(openai_cccd, "_get_client", return_value=client),
            patch.object(
                openai_cccd,
                "enhance_document_image",
                return_value=b"enhanced-image",
            ),
        ):
            openai_cccd.extract_cccd(b"front-image", b"back-image")

        request = client.chat.completions.create.call_args.kwargs
        self.assertEqual(request["model"], openai_cccd.MODEL_NAME)
        self.assertNotIn("temperature", request)
        image_parts = [
            part
            for part in request["messages"][1]["content"]
            if part["type"] == "image_url"
        ]
        self.assertEqual(len(image_parts), 4)
        self.assertTrue(
            all(part["image_url"]["detail"] == "high" for part in image_parts)
        )
        self.assertIn(
            "ảnh gốc và bản tăng độ rõ",
            request["messages"][1]["content"][0]["text"],
        )
        prompt = request["messages"][1]["content"][0]["text"]
        self.assertIn("Quê quán / Place of origin", prompt)
        self.assertIn("Nơi đăng ký khai sinh / Place of birth", prompt)


if __name__ == "__main__":
    unittest.main()
