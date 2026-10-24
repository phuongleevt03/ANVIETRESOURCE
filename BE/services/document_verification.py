import logging
import re
import unicodedata
from datetime import date, datetime

import cv2
import numpy as np

import zxingcpp


logger = logging.getLogger(__name__)

MRZ_FIELD_NAMES = {
    "soGiayTo": "Số giấy tờ",
    "ngaySinh": "Ngày sinh",
    "gioiTinh": "Giới tính",
    "ngayHetHan": "Ngày hết hạn",
    "hoTen": "Họ và tên",
}


def _normalize_mrz_lines(value):
    if isinstance(value, str):
        lines = value.splitlines()
    elif isinstance(value, list):
        lines = value
    else:
        return []

    return [
        re.sub(r"\s+", "", line).upper().replace("«", "<")
        for line in lines
        if isinstance(line, str) and line.strip()
    ]


def _fit_mrz_filler(line, trailing=False):
    if len(line) == 30:
        return line
    if trailing:
        content = line.rstrip("<")
        if len(content) <= 30:
            return content.ljust(30, "<")
        return line

    filler_runs = list(re.finditer(r"<+", line))
    if not filler_runs:
        return line
    filler_run = max(filler_runs, key=lambda match: match.end() - match.start())
    adjusted_length = filler_run.end() - filler_run.start() + 30 - len(line)
    if adjusted_length < 1:
        return line
    return (
        line[: filler_run.start()]
        + "<" * adjusted_length
        + line[filler_run.end() :]
    )


def _mrz_check_digit(value):
    weights = (7, 3, 1)
    total = 0
    for index, character in enumerate(value):
        if character == "<":
            digit = 0
        elif character.isdigit():
            digit = int(character)
        elif "A" <= character <= "Z":
            digit = ord(character) - ord("A") + 10
        else:
            return None
        total += digit * weights[index % len(weights)]
    return str(total % 10)


def _valid_mrz_date(value):
    if not re.fullmatch(r"\d{6}", value):
        return False
    year = int(value[:2])
    century = 2000 if year <= date.today().year % 100 else 1900
    try:
        datetime(century + year, int(value[2:4]), int(value[4:6]))
    except ValueError:
        return False
    return True


def validate_td1_mrz(value):
    """Validate an ICAO TD1 identity-document MRZ and return its data fields."""
    lines = _normalize_mrz_lines(value)
    if len(lines) == 3:
        lines = [
            _fit_mrz_filler(lines[0]),
            _fit_mrz_filler(lines[1]),
            _fit_mrz_filler(lines[2], trailing=True),
        ]
    if len(lines) != 3 or any(len(line) != 30 for line in lines):
        return {"valid": False, "reason": "Cấu trúc MRZ không đúng định dạng TD1."}

    if any(not re.fullmatch(r"[A-Z0-9<]{30}", line) for line in lines):
        return {"valid": False, "reason": "MRZ chứa ký tự không hợp lệ."}

    first, second, _name_line = lines
    if first[:2] not in {"I<", "A<", "C<"}:
        return {"valid": False, "reason": "Mã loại giấy tờ MRZ không hợp lệ."}
    if "<<" not in lines[2]:
        return {"valid": False, "reason": "Dòng họ tên trong MRZ không đúng cấu trúc."}
    document_number = first[5:14]
    full_document_number = first[15:27]
    if not re.fullmatch(r"\d{12}", full_document_number):
        full_document_number = ""

    birth_date = second[0:6]
    expiry_date = second[8:14]
    checks = (
        (document_number, first[14]),
        (birth_date, second[6]),
        (expiry_date, second[14]),
    )
    if any(_mrz_check_digit(field) != digit for field, digit in checks):
        return {"valid": False, "reason": "Check digit của MRZ không hợp lệ."}
    if full_document_number and (
        not first[29].isdigit()
        or _mrz_check_digit(full_document_number) != first[29]
    ):
        return {"valid": False, "reason": "Check digit số định danh 12 số trong MRZ không hợp lệ."}

    if not _valid_mrz_date(birth_date) or not _valid_mrz_date(expiry_date):
        return {"valid": False, "reason": "Ngày sinh hoặc ngày hết hạn trong MRZ không hợp lệ."}

    composite = (
        first[5:30]
        + second[0:7]
        + second[8:15]
        + second[18:29]
    )
    if _mrz_check_digit(composite) != second[29]:
        return {"valid": False, "reason": "Check digit tổng hợp của MRZ không hợp lệ."}

    sex = second[7]
    if sex not in {"M", "F", "X", "<"}:
        return {"valid": False, "reason": "Ký tự giới tính trong MRZ không hợp lệ."}

    return {
        "valid": True,
        "documentNumber": full_document_number or document_number.rstrip("<"),
        "documentNumber9": document_number.rstrip("<"),
        "birthDate": birth_date,
        "sex": sex,
        "expiryDate": expiry_date,
        "name": " ".join(
            _name_line.rstrip("<").replace("<<", " ").replace("<", " ").split()
        ),
    }


def _normalize_qr_date(value):
    text = str(value or "").strip()
    digits = re.sub(r"\D", "", text)
    formats = []
    if len(digits) == 8:
        if digits.startswith(("19", "20")):
            formats.append(("%Y%m%d", digits))
        formats.append(("%d%m%Y", digits))
    for date_format in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d"):
        formats.append((date_format, text))

    for date_format, candidate in formats:
        try:
            return datetime.strptime(candidate, date_format).strftime("%d/%m/%Y")
        except ValueError:
            continue
    return ""


def _normalize_qr_gender(value):
    normalized = str(value or "").strip().casefold()
    if normalized in {"nam", "male", "m"}:
        return "Nam"
    if normalized in {"nữ", "nu", "female", "f"}:
        return "Nữ"
    return ""


def parse_vietnam_id_qr(value):
    """Parse the supported pipe-delimited Vietnamese identity-card QR payload."""
    if not isinstance(value, str):
        return None
    parts = [part.strip() for part in value.strip().split("|")]
    if len(parts) < 7 or not re.fullmatch(r"\d{12}", parts[0]):
        return None

    issue_date = _normalize_qr_date(parts[6])
    if not issue_date and len(parts) > 7:
        issue_date = _normalize_qr_date(parts[7])

    payload = {
        "soGiayTo": parts[0],
        "hoTen": parts[2],
        "ngaySinh": _normalize_qr_date(parts[3]),
        "gioiTinh": _normalize_qr_gender(parts[4]),
        "diaChiGoc": parts[5],
        "ngayCap": issue_date,
    }
    return payload


def _decode_qr(image_bytes):
    image = cv2.imdecode(np.frombuffer(image_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        return ""

    height, width = image.shape[:2]
    longest_side = max(height, width)
    if longest_side > 1800:
        scale = 1800 / longest_side
        image = cv2.resize(
            image,
            None,
            fx=scale,
            fy=scale,
            interpolation=cv2.INTER_AREA,
        )

    enlarged = cv2.resize(image, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    contrast = cv2.createCLAHE(
        clipLimit=2.0,
        tileGridSize=(8, 8),
    ).apply(gray)
    enlarged_contrast = cv2.resize(
        contrast,
        None,
        fx=2,
        fy=2,
        interpolation=cv2.INTER_CUBIC,
    )
    blurred = cv2.GaussianBlur(contrast, (0, 0), 1.0)
    sharpened = cv2.addWeighted(contrast, 1.5, blurred, -0.5, 0)
    candidates = [image, enlarged, contrast, enlarged_contrast, sharpened]

    if min(gray.shape[:2]) >= 3:
        block_size = min(31, min(gray.shape[:2]))
        if block_size % 2 == 0:
            block_size -= 1
        if block_size >= 3:
            candidates.append(
                cv2.adaptiveThreshold(
                    contrast,
                    255,
                    cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                    cv2.THRESH_BINARY,
                    block_size,
                    5,
                )
            )

    for candidate in candidates:
        for barcode in zxingcpp.read_barcodes(candidate):
            if barcode.format == zxingcpp.BarcodeFormat.QRCode and barcode.text:
                return barcode.text.strip()

    search_image = image
    height, width = search_image.shape[:2]

    window_size = min(height, width, max(96, round(min(height, width) * 0.2)))
    step = max(24, window_size // 3)
    x_positions = list(range(0, width - window_size + 1, step))
    y_positions = list(range(0, height - window_size + 1, step))
    if not x_positions or x_positions[-1] != width - window_size:
        x_positions.append(width - window_size)
    if not y_positions or y_positions[-1] != height - window_size:
        y_positions.append(height - window_size)

    expected_qr_x = width * 0.7
    expected_qr_y = height * 0.47
    windows = sorted(
        (
            (x, y)
            for y in y_positions
            for x in x_positions
        ),
        key=lambda position: (
            (position[0] + window_size / 2 - expected_qr_x) ** 2
            + (position[1] + window_size / 2 - expected_qr_y) ** 2
        ),
    )

    for x, y in windows[:20]:
        crop = cv2.cvtColor(
            search_image[y : y + window_size, x : x + window_size],
            cv2.COLOR_BGR2GRAY,
        )
        enlarged_crop = cv2.resize(
            crop,
            None,
            fx=4,
            fy=4,
            interpolation=cv2.INTER_CUBIC,
        )
        block_size = min(31, min(enlarged_crop.shape[:2]))
        if block_size % 2 == 0:
            block_size -= 1
        if block_size < 3:
            continue
        thresholded_crop = cv2.adaptiveThreshold(
            enlarged_crop,
            255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY,
            block_size,
            5,
        )
        for barcode in zxingcpp.read_barcodes(thresholded_crop):
            if barcode.format == zxingcpp.BarcodeFormat.QRCode and barcode.text:
                return barcode.text.strip()
    return ""


def read_vietnam_id_qr(front_bytes, back_bytes):
    detected_unrecognized = False
    for image_bytes in (front_bytes, back_bytes):
        try:
            raw_value = _decode_qr(image_bytes)
        except cv2.error as error:
            logger.warning("QR decoding failed (%s).", type(error).__name__)
            continue
        if not raw_value:
            continue
        parsed = parse_vietnam_id_qr(raw_value)
        if parsed:
            return parsed, "read"
        detected_unrecognized = True
    return (None, "unrecognized") if detected_unrecognized else (None, "not_found")


def _normalized_document_number(value):
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper()).rstrip("<")


def _value_to_mrz_date(value):
    text = str(value or "").strip()
    digits = re.sub(r"\D", "", text)
    if len(digits) == 8:
        if digits.startswith(("19", "20")):
            return digits[2:4] + digits[4:6] + digits[6:8]
        return digits[6:8] + digits[2:4] + digits[:2]
    return ""


def _mrz_date_display(value, is_expiry=False):
    year = int(value[:2])
    century = 2000 if is_expiry or year <= date.today().year % 100 else 1900
    return f"{value[4:6]}/{value[2:4]}/{century + year:04d}"


def _same_gender(first, second):
    def normalize(value):
        text = str(value or "").strip().casefold()
        if text in {"nam", "male", "m"}:
            return "M"
        if text in {"nữ", "nu", "female", "f"}:
            return "F"
        if text in {"x", "khác", "other"}:
            return "X"
        return ""

    return normalize(first) == normalize(second) and bool(normalize(first))


def _normalize_person_name(value):
    text = str(value or "").replace("Đ", "D").replace("đ", "d")
    decomposed = unicodedata.normalize("NFKD", text)
    without_marks = "".join(
        character for character in decomposed
        if not unicodedata.combining(character)
    )
    return " ".join(re.findall(r"[a-z0-9]+", without_marks.casefold()))


def _same_text(first, second):
    return " ".join(
        unicodedata.normalize("NFC", str(first or "")).casefold().split()
    ) == " ".join(
        unicodedata.normalize("NFC", str(second or "")).casefold().split()
    )


def _same_person_name(first, second):
    first_name = _normalize_person_name(first)
    second_name = _normalize_person_name(second)
    return bool(first_name and first_name == second_name)


def _normalize_address_for_comparison(value):
    text = str(value or "").replace("Đ", "D").replace("đ", "d")
    decomposed = unicodedata.normalize("NFKD", text)
    without_marks = "".join(
        character for character in decomposed
        if not unicodedata.combining(character)
    ).casefold()
    return "".join(character for character in without_marks if character.isalnum())


def _parse_display_date(value):
    text = str(value or "").strip()
    if not re.fullmatch(r"\d{2}/\d{2}/\d{4}", text):
        return None
    try:
        return datetime.strptime(text, "%d/%m/%Y").date()
    except ValueError:
        return None


def _identity_number_warnings(identity_number, gender, birth_date):
    if re.fullmatch(r"\d{9}", str(identity_number or "")):
        return []
    if not re.fullmatch(r"\d{12}", str(identity_number or "")):
        return ["Số định danh phải gồm đúng 12 chữ số."]

    warnings = []
    province_code = int(identity_number[:3])
    if not 1 <= province_code <= 96:
        warnings.append("Ba số đầu của số định danh không phải mã tỉnh hợp lệ.")

    gender_centuries = {
        "0": ("Nam", 1900),
        "1": ("Nữ", 1900),
        "2": ("Nam", 2000),
        "3": ("Nữ", 2000),
        "4": ("Nam", 2100),
        "5": ("Nữ", 2100),
        "6": ("Nam", 2200),
        "7": ("Nữ", 2200),
        "8": ("Nam", 2300),
        "9": ("Nữ", 2300),
    }
    gender_century = gender_centuries.get(identity_number[3])
    if not gender_century:
        warnings.append("Chữ số thứ tư của số định danh không hợp lệ.")
        return warnings

    encoded_gender, century = gender_century
    if gender and not _same_text(gender, encoded_gender):
        warnings.append(
            f"Số định danh cho biết giới tính {encoded_gender}, khác với thông tin đã đọc."
        )

    birth = _parse_display_date(birth_date)
    if birth and century + int(identity_number[4:6]) != birth.year:
        warnings.append(
            "Thế kỷ và năm sinh mã hóa trong số định danh không khớp ngày sinh."
        )
    return warnings


def verify_cccd_result(ocr_data, front_bytes, back_bytes):
    """Apply supported QR fields, then validate MRZ and emit review-only suggestions."""
    result = dict(ocr_data)
    qr_data, qr_read_status = read_vietnam_id_qr(front_bytes, back_bytes)
    qr_fields_applied = []
    qr_corrections = []
    review_fields = set()
    initial_household_address = result.get("HKTT")
    old_address = (
        str(initial_household_address.get("diaChiGoc", "") or "")
        if isinstance(initial_household_address, dict)
        else ""
    )
    qr_address = ""
    household_address_status = "unavailable"

    if qr_data:
        qr_field_labels = {
            "soGiayTo": "Số giấy tờ",
            "ngaySinh": "Ngày sinh",
            "gioiTinh": "Giới tính",
            "ngayCap": "Ngày cấp",
            "hoTen": "Họ và tên",
            "hoDem": "Họ và đệm",
            "ten": "Tên",
            "HKTT.diaChiGoc": "Địa chỉ HKTT",
        }

        def apply_qr_field(field, value):
            if not value:
                return
            old_value = result.get(field, "")
            qr_fields_applied.append(field)
            same_value = (
                _same_person_name(old_value, value)
                if field in {"hoTen", "hoDem", "ten"}
                else _same_text(old_value, value)
            )
            if same_value:
                return
            if field == "hoTen" and str(old_value or "").isupper():
                value = str(value).upper()
            qr_corrections.append(
                {
                    "field": field,
                    "label": qr_field_labels[field],
                    "from": str(old_value or ""),
                    "to": str(value),
                }
            )
            result[field] = value
            review_fields.add(field)

        for field in ("soGiayTo", "ngaySinh", "gioiTinh", "ngayCap", "hoTen"):
            value = qr_data.get(field, "")
            apply_qr_field(field, value)
        name_parts = str(qr_data.get("hoTen", "")).split()
        if len(name_parts) >= 2:
            apply_qr_field("hoDem", " ".join(name_parts[:-1]))
            apply_qr_field("ten", name_parts[-1])
        household_address = result.get("HKTT")
        if not isinstance(household_address, dict):
            household_address = {}
            result["HKTT"] = household_address
        qr_address = qr_data.get("diaChiGoc", "")
        old_address = str(household_address.get("diaChiGoc", "") or "")
        normalized_ocr_address = _normalize_address_for_comparison(old_address)
        normalized_qr_address = _normalize_address_for_comparison(qr_address)
        if not qr_address:
            household_address_status = "unavailable"
        elif not old_address:
            household_address_status = "ocr_missing"
        elif normalized_ocr_address == normalized_qr_address:
            household_address_status = "matched"
        else:
            household_address_status = "mismatch"

        if qr_address:
            qr_fields_applied.append("HKTT.diaChiGoc")
        if qr_address and household_address_status != "matched":
            household_address["diaChiGoc"] = qr_address
            for field in ("tinh", "quanHuyen", "phuongXa", "soNha"):
                if field in household_address:
                    household_address[field] = ""
            qr_corrections.append(
                {
                    "field": "HKTT.diaChiGoc",
                    "label": qr_field_labels["HKTT.diaChiGoc"],
                    "from": old_address,
                    "to": qr_address,
                }
            )
            review_fields.add("diaChiHKTT")

    qr_status = "corrected" if qr_corrections else "matched"
    if not qr_data:
        qr_status = "unreadable"

    mrz_lines = _normalize_mrz_lines(result.get("mrzLines", []))
    mrz_status = "not_found"
    warnings = []
    suggestions = []
    mrz_review_fields = set()
    non_mrz_review_fields = set()

    def add_review_warning(field, message):
        warnings.append(message)
        review_fields.add(field)
        non_mrz_review_fields.add(field)

    def add_mrz_review_warning(field, message):
        warnings.append(message)
        review_fields.add(field)
        mrz_review_fields.add(field)

    if mrz_lines:
        mrz = validate_td1_mrz(mrz_lines)
        if not mrz["valid"]:
            mrz_status = "invalid"
            add_mrz_review_warning(
                "mrzLines",
                f"MRZ không được dùng để đối chiếu: {mrz['reason']}",
            )
        else:
            mrz_status = "valid"
            comparisons = (
                (
                    "soGiayTo",
                    _normalized_document_number(result.get("soGiayTo")),
                    _normalized_document_number(mrz["documentNumber"]),
                    mrz["documentNumber"],
                ),
                (
                    "ngaySinh",
                    _value_to_mrz_date(result.get("ngaySinh")),
                    mrz["birthDate"],
                    _mrz_date_display(mrz["birthDate"]),
                ),
                (
                    "gioiTinh",
                    "M" if _same_gender(result.get("gioiTinh"), "M") else
                    "F" if _same_gender(result.get("gioiTinh"), "F") else
                    "X" if _same_gender(result.get("gioiTinh"), "X") else "",
                    mrz["sex"],
                    {"M": "Nam", "F": "Nữ", "X": "Khác"}.get(mrz["sex"], ""),
                ),
                (
                    "ngayHetHan",
                    _value_to_mrz_date(result.get("ngayHetHan")),
                    mrz["expiryDate"],
                    _mrz_date_display(mrz["expiryDate"], is_expiry=True),
                ),
                (
                    "hoTen",
                    _normalize_person_name(result.get("hoTen")),
                    _normalize_person_name(mrz["name"]),
                    mrz["name"],
                ),
            )
            for field, actual, expected, suggestion in comparisons:
                if field == "gioiTinh" and expected == "<":
                    continue
                if actual and actual != expected:
                    add_mrz_review_warning(
                        field,
                        f"{MRZ_FIELD_NAMES[field]} trên MRZ khác với thông tin đã đọc."
                    )
                    suggestions.append(
                        {
                            "field": field,
                            "label": MRZ_FIELD_NAMES[field],
                            "value": suggestion,
                        }
                    )
    identity_number = str(result.get("soGiayTo", "") or "").strip()
    if not identity_number:
        add_review_warning("soGiayTo", "Không đọc được số định danh.")
    else:
        for warning in _identity_number_warnings(
            identity_number,
            result.get("gioiTinh", ""),
            result.get("ngaySinh", ""),
        ):
            add_review_warning("soGiayTo", warning)

    for field, label in (
        ("hoTen", "họ và tên"),
        ("ngaySinh", "ngày sinh"),
        ("gioiTinh", "giới tính"),
        ("ngayCap", "ngày cấp"),
        ("ngayHetHan", "ngày hết hạn"),
    ):
        if not str(result.get(field, "") or "").strip():
            add_review_warning(field, f"Không đọc được {label}; cần kiểm tra hoặc nhập tay.")

    today = date.today()
    parsed_dates = {}
    for field, label in (
        ("ngaySinh", "ngày sinh"),
        ("ngayCap", "ngày cấp"),
        ("ngayHetHan", "ngày hết hạn"),
    ):
        value = str(result.get(field, "") or "").strip()
        if not value:
            continue
        if field == "ngayHetHan" and "thời hạn" in value.casefold():
            continue
        parsed = _parse_display_date(value)
        parsed_dates[field] = parsed
        if parsed is None:
            add_review_warning(field, f"{label.capitalize()} không đúng định dạng DD/MM/YYYY.")

    birth = parsed_dates.get("ngaySinh")
    issue = parsed_dates.get("ngayCap")
    expiry = parsed_dates.get("ngayHetHan")
    if birth and birth > today:
        add_review_warning("ngaySinh", "Ngày sinh ở tương lai.")
    if issue and issue > today:
        add_review_warning("ngayCap", "Ngày cấp ở tương lai.")
    if birth and issue and issue <= birth:
        add_review_warning("ngayCap", "Ngày cấp phải sau ngày sinh.")
    if issue and expiry and expiry <= issue:
        add_review_warning("ngayHetHan", "Ngày hết hạn phải sau ngày cấp.")
    if expiry and expiry < today:
        add_review_warning("ngayHetHan", "Giấy tờ đã hết hạn.")

    gender = str(result.get("gioiTinh", "") or "").strip()
    if gender and not (
        _same_gender(gender, "M") or _same_gender(gender, "F")
    ):
        add_review_warning("gioiTinh", "Giới tính cần là Nam hoặc Nữ.")

    full_name = str(result.get("hoTen", "") or "").strip()
    last_name = str(result.get("ten", "") or "").strip()
    if full_name and last_name:
        composed_name = " ".join(
            part
            for part in (str(result.get("hoDem", "") or "").strip(), last_name)
            if part
        )
        if not _same_text(composed_name, full_name):
            add_review_warning("hoTen", "Họ đệm và tên không khớp với họ và tên.")

    household_address = result.get("HKTT")
    if not isinstance(household_address, dict) or not str(
        household_address.get("diaChiGoc", "") or ""
    ).strip():
        add_review_warning(
            "diaChiHKTT",
            "Không đọc được địa chỉ cư trú; cần kiểm tra hoặc nhập tay.",
        )

    return {
        **result,
        "verification": {
            "qrStatus": qr_status,
            "qrReadStatus": qr_read_status,
            "qrFieldsApplied": list(dict.fromkeys(qr_fields_applied)),
            "qrFieldsCorrected": qr_corrections,
            "householdAddress": {
                "ocrAddress": old_address,
                "qrAddress": str(qr_address or ""),
                "status": household_address_status,
            },
            "mrzStatus": mrz_status,
            "warnings": warnings,
            "suggestions": suggestions,
            "reviewFields": sorted(review_fields),
            "mrzReviewFields": sorted(mrz_review_fields),
            "nonMrzReviewFields": sorted(non_mrz_review_fields),
            "requiresReview": True,
        },
    }
