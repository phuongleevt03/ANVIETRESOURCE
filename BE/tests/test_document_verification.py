import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import cv2
import numpy as np
import zxingcpp

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.document_verification import (
    _decode_qr,
    _mrz_check_digit,
    parse_vietnam_id_qr,
    read_vietnam_id_qr,
    validate_td1_mrz,
    verify_cccd_result,
)
from services.openai_cccd import _normalize_result


def make_td1(
    document_number="123456789",
    birth_date="900101",
    sex="M",
    expiry_date="300101",
    identity_number="",
    name="DOE<<JOHN",
):
    optional_data = (
        f"{identity_number}{'<' * (14 - len(identity_number))}"
        f"{_mrz_check_digit(identity_number)}"
        if identity_number
        else "<" * 15
    )
    first = (
        f"I<VNM{document_number}{_mrz_check_digit(document_number)}"
        f"{optional_data}"
    )
    second_prefix = (
        f"{birth_date}{_mrz_check_digit(birth_date)}{sex}"
        f"{expiry_date}{_mrz_check_digit(expiry_date)}VNM{'<' * 11}"
    )
    composite = (
        first[5:30]
        + second_prefix[0:7]
        + second_prefix[8:15]
        + second_prefix[18:29]
    )
    second = f"{second_prefix}{_mrz_check_digit(composite)}"
    third = name.ljust(30, "<")
    return [first, second, third]


class DocumentVerificationTests(unittest.TestCase):
    def test_supported_vietnam_qr_payload_parses_fields(self):
        parsed = parse_vietnam_id_qr(
            "012345678901|0987654321|NGUYEN VAN A|01011990|Nam|Ha Noi|Ha Noi|02022020"
        )
        self.assertEqual(parsed["soGiayTo"], "012345678901")
        self.assertEqual(parsed["hoTen"], "NGUYEN VAN A")
        self.assertEqual(parsed["ngaySinh"], "01/01/1990")
        self.assertEqual(parsed["gioiTinh"], "Nam")
        self.assertEqual(parsed["ngayCap"], "02/02/2020")
        seven_part_payload = parse_vietnam_id_qr(
            "012345678901|0987654321|NGUYEN VAN A|01011990|Nam|Ha Noi|02022020"
        )
        self.assertIsNotNone(seven_part_payload)
        self.assertEqual(seven_part_payload["ngayCap"], "02/02/2020")

    def test_qr_parser_rejects_unrecognized_payload(self):
        self.assertIsNone(parse_vietnam_id_qr("not a supported QR"))
        self.assertIsNone(
            parse_vietnam_id_qr("12345678901|secondary|Name|01011990|Nam|Address|02022020")
        )

    @patch("services.document_verification._decode_qr", return_value="unsupported|payload")
    def test_detected_but_unsupported_qr_is_not_used(self, _decode_qr_mock):
        parsed, status = read_vietnam_id_qr(b"front", b"back")
        self.assertIsNone(parsed)
        self.assertEqual(status, "unrecognized")

    def test_original_image_qr_is_decoded_by_zxing_cpp(self):
        payload = "012345678901|0987654321|NGUYEN VAN A|01011990|Nam|Ha Noi|Ha Noi|02022020"
        qr = cv2.QRCodeEncoder_create().encode(payload)
        qr = cv2.copyMakeBorder(
            cv2.resize(qr, None, fx=8, fy=8, interpolation=cv2.INTER_NEAREST),
            32,
            32,
            32,
            32,
            cv2.BORDER_CONSTANT,
            value=255,
        )
        encoded, image_bytes = cv2.imencode(".png", qr)
        self.assertTrue(encoded)

        parsed, status = read_vietnam_id_qr(image_bytes.tobytes(), b"")
        self.assertEqual(status, "read")
        self.assertEqual(parsed["soGiayTo"], "012345678901")

    def test_qr_decoder_recovers_low_contrast_blurred_code(self):
        payload = (
            "012345678901|0987654321|NGUYEN VAN A|01011990|Nam|Ha Noi|"
            "Ha Noi|02022020"
        )
        qr = cv2.QRCodeEncoder_create().encode(payload)
        qr = cv2.copyMakeBorder(
            cv2.resize(qr, None, fx=5, fy=5, interpolation=cv2.INTER_NEAREST),
            24,
            24,
            24,
            24,
            cv2.BORDER_CONSTANT,
            value=255,
        )
        card = np.full((qr.shape[0] + 120, qr.shape[1] + 180, 3), 245, dtype=np.uint8)
        low_contrast_qr = np.clip(
            qr.astype(np.float32) * 0.35 + 150,
            0,
            255,
        ).astype(np.uint8)
        card[60 : 60 + qr.shape[0], 90 : 90 + qr.shape[1]] = cv2.cvtColor(
            low_contrast_qr,
            cv2.COLOR_GRAY2BGR,
        )
        blurred_card = cv2.GaussianBlur(card, (5, 5), 1.2)
        encoded, image_bytes = cv2.imencode(
            ".jpg",
            blurred_card,
            [cv2.IMWRITE_JPEG_QUALITY, 45],
        )
        self.assertTrue(encoded)

        self.assertEqual(_decode_qr(image_bytes.tobytes()), payload)

    def test_qr_decoder_finds_small_code_inside_full_card_photo(self):
        payload = (
            "012345678901|0987654321|TEST PERSON|01011990|Nam|"
            "TEST ADDRESS, TEST WARD, TEST CITY|02022020"
        )
        qr = cv2.QRCodeEncoder_create().encode(payload)
        qr = cv2.copyMakeBorder(
            cv2.resize(
                qr,
                (80, 80),
                interpolation=cv2.INTER_NEAREST,
            ),
            2,
            2,
            2,
            2,
            cv2.BORDER_CONSTANT,
            value=255,
        )
        card_photo = np.full((720, 1280, 3), (190, 220, 215), dtype=np.uint8)
        qr_color = cv2.cvtColor(qr, cv2.COLOR_GRAY2BGR)
        card_photo[298 : 298 + qr.shape[0], 850 : 850 + qr.shape[1]] = qr_color
        encoded, image_bytes = cv2.imencode(
            ".jpg",
            card_photo,
            [cv2.IMWRITE_JPEG_QUALITY, 90],
        )
        self.assertTrue(encoded)

        self.assertEqual(_decode_qr(image_bytes.tobytes()), payload)

    @patch("services.document_verification.zxingcpp.read_barcodes", return_value=[])
    def test_qr_decoder_bounds_image_size_and_crop_attempts(self, read_barcodes):
        image = np.full((2400, 3200, 3), 255, dtype=np.uint8)
        encoded, image_bytes = cv2.imencode(".jpg", image)
        self.assertTrue(encoded)

        self.assertEqual(_decode_qr(image_bytes.tobytes()), "")

        self.assertLessEqual(len(read_barcodes.call_args_list), 8)
        original_size = max(read_barcodes.call_args_list[0].args[0].shape[:2])
        self.assertLessEqual(original_size, 1400)
        for call in read_barcodes.call_args_list:
            self.assertEqual(
                call.kwargs["formats"],
                zxingcpp.BarcodeFormat.QRCode,
            )
            self.assertFalse(call.kwargs["try_downscale"])

    @patch("services.document_verification.zxingcpp.read_barcodes")
    def test_qr_decoder_tries_original_then_two_times_upscaled_image(self, read_barcodes):
        read_barcodes.side_effect = [
            [],
            [SimpleNamespace(format=zxingcpp.BarcodeFormat.QRCode, text="payload")],
        ]
        encoded, image_bytes = cv2.imencode(
            ".png",
            255 * np.ones((10, 20, 3), dtype=np.uint8),
        )
        self.assertTrue(encoded)

        self.assertEqual(_decode_qr(image_bytes.tobytes()), "payload")
        original = read_barcodes.call_args_list[0].args[0]
        enlarged = read_barcodes.call_args_list[1].args[0]
        self.assertEqual(original.shape[:2], (10, 20))
        self.assertEqual(enlarged.shape[:2], (20, 40))

    @patch("services.document_verification.zxingcpp.read_barcodes")
    def test_qr_decoder_tries_contrast_enhanced_grayscale_after_originals(
        self,
        read_barcodes,
    ):
        read_barcodes.side_effect = [
            [],
            [],
            [SimpleNamespace(format=zxingcpp.BarcodeFormat.QRCode, text="payload")],
        ]
        encoded, image_bytes = cv2.imencode(
            ".png",
            255 * np.ones((40, 80, 3), dtype=np.uint8),
        )
        self.assertTrue(encoded)

        self.assertEqual(_decode_qr(image_bytes.tobytes()), "payload")
        contrast_candidate = read_barcodes.call_args_list[2].args[0]
        self.assertEqual(contrast_candidate.ndim, 2)
        self.assertEqual(contrast_candidate.shape, (40, 80))

    def test_td1_mrz_requires_valid_structure_and_check_digits(self):
        lines = make_td1()
        parsed = validate_td1_mrz(lines)
        self.assertTrue(parsed["valid"])
        self.assertEqual(parsed["documentNumber"], "123456789")
        self.assertEqual(parsed["birthDate"], "900101")
        self.assertEqual(parsed["sex"], "M")
        self.assertEqual(parsed["expiryDate"], "300101")

        invalid = list(lines)
        invalid[1] = invalid[1][:6] + ("0" if invalid[1][6] != "0" else "1") + invalid[1][7:]
        self.assertFalse(validate_td1_mrz(invalid)["valid"])
        self.assertFalse(validate_td1_mrz(lines[:2])["valid"])

    def test_td1_mrz_restores_missing_filler_characters_before_checking(self):
        lines = make_td1(name="DOE<<JOHN")
        shortened = [
            lines[0].replace("<<<", "<", 1),
            lines[1].replace("<<<", "<", 1),
            lines[2].replace("<<<", "<", 1),
        ]

        parsed = validate_td1_mrz(shortened)

        self.assertTrue(parsed["valid"])
        self.assertEqual(parsed["documentNumber"], "123456789")

    def test_td1_mrz_does_not_repair_added_data_characters(self):
        lines = make_td1()
        lines[0] += "X"

        self.assertFalse(validate_td1_mrz(lines)["valid"])

    def test_td1_mrz_checks_twelve_digit_identity_in_optional_zone(self):
        identity_number = "012345678901"
        lines = make_td1(identity_number=identity_number)
        parsed = validate_td1_mrz(lines)
        self.assertTrue(parsed["valid"])
        self.assertEqual(parsed["documentNumber"], identity_number)

        invalid = list(lines)
        invalid[0] = invalid[0][:29] + (
            "0" if invalid[0][29] != "0" else "1"
        )
        self.assertFalse(validate_td1_mrz(invalid)["valid"])

    @patch("services.document_verification.read_vietnam_id_qr")
    def test_valid_matching_mrz_does_not_create_warnings(self, read_qr):
        read_qr.return_value = (None, "not_found")
        result = verify_cccd_result(
            {
                "soGiayTo": "123456789",
                "hoTen": "Nguyễn Văn A",
                "ngaySinh": "01/01/1990",
                "gioiTinh": "Nam",
                "ngayCap": "01/01/2020",
                "ngayHetHan": "01/01/2030",
                "HKTT": {"diaChiGoc": "Ha Noi"},
                "mrzLines": make_td1(name="NGUYEN<<VAN<A"),
            },
            b"front",
            b"back",
        )
        self.assertEqual(result["verification"]["mrzStatus"], "valid")
        self.assertEqual(result["verification"]["warnings"], [])
        self.assertEqual(result["verification"]["suggestions"], [])

    @patch("services.document_verification.read_vietnam_id_qr")
    def test_qr_status_records_each_ocr_field_it_corrected(self, read_qr):
        read_qr.return_value = (
            {
                "soGiayTo": "012345678901",
                "hoTen": "NGUYEN VAN A",
                "ngaySinh": "01/01/1990",
                "gioiTinh": "Nam",
                "diaChiGoc": "Ha Noi",
                "ngayCap": "02/02/2020",
            },
            "read",
        )
        result = verify_cccd_result(
            {
                "soGiayTo": "OCR-OLD",
                "hoTen": "OCR NAME",
                "ngaySinh": "01/01/1990",
                "gioiTinh": "Nam",
                "ngayCap": "01/01/2020",
                "mrzLines": [],
            },
            b"front",
            b"back",
        )
        verification = result["verification"]
        self.assertEqual(verification["qrStatus"], "corrected")
        corrections = {
            correction["field"]: correction
            for correction in verification["qrFieldsCorrected"]
        }
        self.assertEqual(corrections["soGiayTo"]["from"], "OCR-OLD")
        self.assertEqual(corrections["soGiayTo"]["to"], "012345678901")
        self.assertIn("hoDem", corrections)
        self.assertIn("ten", corrections)
        self.assertIn("HKTT.diaChiGoc", corrections)
        self.assertEqual(result["HKTT"]["diaChiGoc"], "Ha Noi")

    @patch("services.document_verification.read_vietnam_id_qr")
    def test_matching_qr_is_reported_without_corrections(self, read_qr):
        read_qr.return_value = (
            {
                "soGiayTo": "012345678901",
                "hoTen": "NGUYEN VAN A",
                "ngaySinh": "01/01/1990",
                "gioiTinh": "Nam",
                "ngayCap": "02/02/2020",
                "diaChiGoc": "123 Duong Le Loi, Phuong Ben Thanh, Quan 1, TP Ho Chi Minh",
            },
            "read",
        )
        result = verify_cccd_result(
            {
                "soGiayTo": "012345678901",
                "hoTen": "NGUYEN VAN A",
                "hoDem": "NGUYEN VAN",
                "ten": "A",
                "ngaySinh": "01/01/1990",
                "gioiTinh": "Nam",
                "ngayCap": "02/02/2020",
                "HKTT": {
                    "diaChiGoc": "123 Đường Lê Lợi, Phường Bến Thành, Quận 1, TP Hồ Chí Minh",
                    "tinh": "Hồ Chí Minh",
                    "quanHuyen": "Quận 1",
                    "phuongXa": "Phường Bến Thành",
                    "soNha": "123 Đường Lê Lợi",
                },
                "mrzLines": [],
            },
            b"front",
            b"back",
        )
        self.assertEqual(result["verification"]["qrStatus"], "matched")
        self.assertEqual(result["verification"]["qrFieldsCorrected"], [])
        self.assertEqual(
            result["verification"]["householdAddress"],
            {
                "ocrAddress": "123 Đường Lê Lợi, Phường Bến Thành, Quận 1, TP Hồ Chí Minh",
                "qrAddress": "123 Duong Le Loi, Phuong Ben Thanh, Quan 1, TP Ho Chi Minh",
                "status": "matched",
            },
        )

    @patch("services.document_verification.read_vietnam_id_qr")
    def test_qr_name_diacritics_alone_do_not_report_corrections(self, read_qr):
        read_qr.return_value = (
            {
                "soGiayTo": "012345678901",
                "hoTen": "TRẦN TRUNG HUY",
                "ngaySinh": "01/10/2000",
                "gioiTinh": "Nam",
                "ngayCap": "02/01/2025",
                "diaChiGoc": "780/37J1 Binh Gia, Phuong 11, Vung Tau",
            },
            "read",
        )
        result = verify_cccd_result(
            {
                "soGiayTo": "012345678901",
                "hoTen": "TRAN TRUNG HUY",
                "hoDem": "TRAN TRUNG",
                "ten": "HUY",
                "ngaySinh": "01/10/2000",
                "gioiTinh": "Nam",
                "ngayCap": "02/01/2025",
                "HKTT": {
                    "diaChiGoc": "780/37J1 Binh Gia, Phuong 11, Vung Tau",
                },
                "mrzLines": [],
            },
            b"front",
            b"back",
        )

        self.assertEqual(result["verification"]["qrStatus"], "matched")
        self.assertEqual(result["verification"]["qrFieldsCorrected"], [])
        self.assertEqual(result["hoTen"], "TRAN TRUNG HUY")
        self.assertEqual(result["hoDem"], "TRAN TRUNG")
        self.assertEqual(result["ten"], "HUY")

    @patch("services.document_verification.read_vietnam_id_qr")
    def test_qr_household_address_wins_when_it_differs_from_ocr(self, read_qr):
        read_qr.return_value = (
            {
                "soGiayTo": "012345678901",
                "diaChiGoc": "45 Duong Tran Hung Dao, Phuong 1, Quan 5, TP Ho Chi Minh",
            },
            "read",
        )
        result = verify_cccd_result(
            {
                "soGiayTo": "012345678901",
                "HKTT": {
                    "diaChiGoc": "OCR address",
                    "tinh": "Hồ Chí Minh",
                    "quanHuyen": "Quận 3",
                    "phuongXa": "Phường 2",
                    "soNha": "12 Đường Sai",
                },
                "mrzLines": [],
            },
            b"front",
            b"back",
        )

        self.assertEqual(
            result["HKTT"]["diaChiGoc"],
            "45 Duong Tran Hung Dao, Phuong 1, Quan 5, TP Ho Chi Minh",
        )
        self.assertEqual(result["HKTT"]["tinh"], "")
        self.assertEqual(result["HKTT"]["phuongXa"], "")
        self.assertEqual(
            result["verification"]["householdAddress"]["status"],
            "mismatch",
        )
        correction = next(
            item
            for item in result["verification"]["qrFieldsCorrected"]
            if item["field"] == "HKTT.diaChiGoc"
        )
        self.assertEqual(correction["from"], "OCR address")

    @patch("services.document_verification.read_vietnam_id_qr", return_value=(None, "not_found"))
    def test_address_comparison_keeps_ocr_value_when_qr_is_unavailable(self, _read_qr):
        result = verify_cccd_result(
            {"HKTT": {"diaChiGoc": "OCR address"}, "mrzLines": []},
            b"front",
            b"back",
        )

        self.assertEqual(
            result["verification"]["householdAddress"],
            {
                "ocrAddress": "OCR address",
                "qrAddress": "",
                "status": "unavailable",
            },
        )
        self.assertEqual(result["HKTT"]["diaChiGoc"], "OCR address")

    @patch("services.document_verification.read_vietnam_id_qr")
    def test_mrz_mismatch_returns_suggestion_without_overwriting_qr_or_ocr(self, read_qr):
        read_qr.return_value = (
            {
                "soGiayTo": "012345678901",
                "hoTen": "NGUYEN VAN A",
                "ngaySinh": "01/01/1990",
                "gioiTinh": "Nam",
                "ngayCap": "02/02/2020",
            },
            "read",
        )
        result = verify_cccd_result(
            {
                "soGiayTo": "OCR-OLD",
                "hoTen": "OCR NAME",
                "ngaySinh": "01/01/1990",
                "gioiTinh": "Nam",
                "ngayHetHan": "01/01/2031",
                "mrzLines": make_td1(),
            },
            b"front",
            b"back",
        )

        self.assertEqual(result["soGiayTo"], "012345678901")
        self.assertEqual(result["hoTen"], "NGUYEN VAN A")
        self.assertEqual(result["ngayHetHan"], "01/01/2031")
        self.assertTrue(result["verification"]["requiresReview"])
        self.assertIn("01/01/2030", [item["value"] for item in result["verification"]["suggestions"]])
        self.assertIn("ngayHetHan", result["verification"]["reviewFields"])
        self.assertIn("hoTen", result["verification"]["reviewFields"])
        name_suggestion = next(
            item
            for item in result["verification"]["suggestions"]
            if item["field"] == "hoTen"
        )
        self.assertEqual(name_suggestion["value"], "DOE JOHN")

    @patch("services.document_verification.read_vietnam_id_qr")
    def test_invalid_mrz_is_warned_and_never_used(self, read_qr):
        read_qr.return_value = (None, "not_found")
        lines = make_td1()
        lines[0] = "X" + lines[0][1:]
        result = verify_cccd_result(
            {"soGiayTo": "012345678901", "mrzLines": lines},
            b"front",
            b"back",
        )
        self.assertEqual(result["soGiayTo"], "012345678901")
        self.assertEqual(result["verification"]["mrzStatus"], "invalid")
        self.assertEqual(result["verification"]["qrStatus"], "unreadable")
        self.assertEqual(result["verification"]["suggestions"], [])
        self.assertIn("mrzLines", result["verification"]["mrzReviewFields"])
        self.assertNotIn("mrzLines", result["verification"]["nonMrzReviewFields"])
        self.assertTrue(result["verification"]["warnings"])

    def test_ocr_normalization_preserves_transcribed_mrz_lines(self):
        result = _normalize_result(
            {"mrzLines": [" I<VNM123 ", "", "900101M3001019VNM"]},
        )
        self.assertEqual(result["mrzLines"], ["I<VNM123", "900101M3001019VNM"])

    @patch("services.document_verification.read_vietnam_id_qr")
    def test_id_structure_warns_when_gender_conflicts_with_encoded_id(self, read_qr):
        read_qr.return_value = (None, "not_found")
        result = verify_cccd_result(
            {
                "soGiayTo": "001190123456",
                "hoTen": "NGUYEN VAN A",
                "hoDem": "NGUYEN VAN",
                "ten": "A",
                "ngaySinh": "01/01/1990",
                "gioiTinh": "Nam",
                "ngayCap": "01/01/2020",
                "ngayHetHan": "01/01/2030",
                "HKTT": {"diaChiGoc": "Ha Noi"},
            },
            b"front",
            b"back",
        )

        self.assertTrue(
            any("giới tính Nữ" in warning for warning in result["verification"]["warnings"])
        )
        self.assertIn("soGiayTo", result["verification"]["reviewFields"])

    @patch("services.document_verification.read_vietnam_id_qr")
    def test_invalid_calendar_date_is_flagged_for_review(self, read_qr):
        read_qr.return_value = (None, "not_found")
        result = verify_cccd_result(
            {
                "soGiayTo": "123456789",
                "hoTen": "NGUYEN VAN A",
                "hoDem": "NGUYEN VAN",
                "ten": "A",
                "ngaySinh": "31/02/1990",
                "gioiTinh": "Nam",
                "ngayCap": "01/01/2020",
                "ngayHetHan": "01/01/2030",
                "HKTT": {"diaChiGoc": "Ha Noi"},
            },
            b"front",
            b"back",
        )

        self.assertTrue(
            any("Ngày sinh không đúng định dạng" in warning for warning in result["verification"]["warnings"])
        )
        self.assertIn("ngaySinh", result["verification"]["reviewFields"])


if __name__ == "__main__":
    unittest.main()
