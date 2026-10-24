import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

import httpx
import openai
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services import openai_cccd


class OpenAICccdRequestTests(unittest.TestCase):
    def test_client_disables_retries_and_uses_timeout_below_worker_limit(self):
        with (
            patch.object(openai_cccd, "_clients", {}),
            patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}),
            patch.object(openai_cccd, "OpenAI") as openai_constructor,
        ):
            openai_cccd._get_client()

        openai_constructor.assert_called_once_with(
            api_key="test-key",
            timeout=45,
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
            patch.dict(
                os.environ,
                {
                    "OPENAI_API_KEY": "test-key",
                    "GROQ_API_KEY": "",
                    "OCR_PROVIDERS": "openai,groq",
                },
            ),
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
        self.assertEqual(len(image_parts), 2)
        self.assertEqual(
            [part["image_url"]["url"] for part in image_parts],
            [
                openai_cccd.image_to_data_url(b"enhanced-image", "image/jpeg"),
                openai_cccd.image_to_data_url(b"enhanced-image", "image/jpeg"),
            ],
        )
        self.assertTrue(
            all(part["image_url"]["detail"] == "high" for part in image_parts)
        )
        self.assertIn(
            "cân bằng tương phản",
            request["messages"][1]["content"][0]["text"],
        )
        prompt = request["messages"][1]["content"][0]["text"]
        self.assertIn("Quê quán / Place of origin", prompt)
        self.assertIn("Nơi đăng ký khai sinh / Place of birth", prompt)

    def test_ocr_falls_back_to_groq_when_openai_connection_fails(self):
        openai_client = Mock()
        openai_client.chat.completions.create.side_effect = openai.APIConnectionError(
            request=httpx.Request(
                "POST",
                "https://api.openai.com/v1/chat/completions",
            ),
        )
        groq_client = Mock()
        groq_client.chat.completions.create.return_value = SimpleNamespace(
            choices=[
                SimpleNamespace(message=SimpleNamespace(content="{}")),
            ],
        )

        with (
            patch.object(
                openai_cccd,
                "_get_client",
                side_effect=[openai_client, groq_client],
            ) as get_client,
            patch.dict(
                os.environ,
                {
                    "OPENAI_API_KEY": "openai-test-key",
                    "GROQ_API_KEY": "groq-test-key",
                    "GROQ_VISION_MODEL": "qwen-test-model",
                    "OCR_PROVIDERS": "openai,groq",
                },
            ),
            patch.object(
                openai_cccd,
                "enhance_document_image",
                return_value=b"enhanced-image",
            ),
        ):
            result = openai_cccd.extract_cccd(b"front-image", b"back-image")

        self.assertEqual(result["soGiayTo"], "")
        self.assertEqual(get_client.call_args_list, [call("openai"), call("groq")])
        groq_request = groq_client.chat.completions.create.call_args.kwargs
        self.assertEqual(groq_request["model"], "qwen-test-model")
        image_parts = [
            part
            for part in groq_request["messages"][1]["content"]
            if part["type"] == "image_url"
        ]
        self.assertEqual(len(image_parts), 2)
        self.assertTrue(
            all("detail" not in part["image_url"] for part in image_parts)
        )
        self.assertEqual(
            groq_request["response_format"]["json_schema"]["schema"],
            openai_cccd.CCCD_SCHEMA,
        )

    def test_ocr_falls_back_to_groq_when_openai_returns_invalid_json(self):
        openai_client = Mock()
        openai_client.chat.completions.create.return_value = SimpleNamespace(
            choices=[
                SimpleNamespace(message=SimpleNamespace(content="not-json")),
            ],
        )
        groq_client = Mock()
        groq_client.chat.completions.create.return_value = SimpleNamespace(
            choices=[
                SimpleNamespace(message=SimpleNamespace(content="{}")),
            ],
        )

        with (
            patch.object(
                openai_cccd,
                "_get_client",
                side_effect=[openai_client, groq_client],
            ) as get_client,
            patch.dict(
                os.environ,
                {
                    "OPENAI_API_KEY": "openai-test-key",
                    "GROQ_API_KEY": "groq-test-key",
                    "OCR_PROVIDERS": "openai,groq",
                },
            ),
            patch.object(
                openai_cccd,
                "enhance_document_image",
                return_value=b"enhanced-image",
            ),
        ):
            openai_cccd.extract_cccd(b"front-image", b"back-image")

        self.assertEqual(get_client.call_args_list, [call("openai"), call("groq")])

    def test_no_configured_provider_raises_configuration_error(self):
        with patch.dict(
            os.environ,
            {
                "OPENAI_API_KEY": "",
                "GROQ_API_KEY": "",
                "OCR_PROVIDERS": "openai,groq",
            },
        ):
            with self.assertRaises(openai_cccd.OCRConfigurationError):
                openai_cccd.extract_cccd(b"front-image", b"back-image")


if __name__ == "__main__":
    unittest.main()
