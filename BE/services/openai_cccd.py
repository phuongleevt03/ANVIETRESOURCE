import os
import json
import base64
import logging
from typing import Any

from dotenv import load_dotenv
import groq
from groq import Groq
import openai
from openai import OpenAI

from services.image_preprocessing import enhance_document_image


# ============================================================
# CONFIGURATION
# ============================================================

# Nạp .env từ thư mục backend, không phụ thuộc thư mục đang chạy lệnh.
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
load_dotenv(os.path.join(BASE_DIR, ".env"))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

MODEL_NAME = os.getenv("OPENAI_VISION_MODEL", "gpt-6-luna")
GROQ_MODEL_NAME = os.getenv("GROQ_VISION_MODEL", "qwen/qwen3.8-27b")
OCR_PROVIDER_TIMEOUT_SECONDS = 45
_clients = {}


class OCRConfigurationError(RuntimeError):
    pass


class OCRProviderError(RuntimeError):
    def __init__(self, failures: list[tuple[str, str]]):
        self.failures = tuple(failures)
        providers = ", ".join(provider for provider, _ in failures)
        super().__init__(f"Tất cả dịch vụ OCR đều lỗi: {providers}.")


def _provider_order() -> list[str]:
    configured = os.getenv("OCR_PROVIDERS", "openai,groq")
    providers = list(dict.fromkeys(
        name.strip().lower()
        for name in configured.split(",")
        if name.strip()
    ))
    unsupported = [provider for provider in providers if provider not in {"openai", "groq"}]
    if unsupported:
        raise OCRConfigurationError(
            "OCR_PROVIDERS chỉ hỗ trợ openai và groq."
        )

    return [
        provider
        for provider in providers
        if os.getenv(
            "OPENAI_API_KEY" if provider == "openai" else "GROQ_API_KEY",
            "",
        ).strip()
    ]


def configured_ocr_providers() -> list[str]:
    return _provider_order()


def _get_client(provider: str = "openai"):
    if provider not in {"openai", "groq"}:
        raise ValueError(f"Nhà cung cấp OCR không hợp lệ: {provider}")

    if provider not in _clients:
        api_key = os.getenv(
            "OPENAI_API_KEY" if provider == "openai" else "GROQ_API_KEY",
            "",
        ).strip()
        if not api_key:
            raise OCRConfigurationError(
                "Cần cấu hình OPENAI_API_KEY hoặc GROQ_API_KEY cho OCR."
            )

        client_class = OpenAI if provider == "openai" else Groq
        _clients[provider] = client_class(
            api_key=api_key,
            timeout=OCR_PROVIDER_TIMEOUT_SECONDS,
            max_retries=0,
        )
    return _clients[provider]


# ============================================================
# IMAGE HELPERS
# ============================================================

def _detect_mime_type(image_bytes: bytes) -> str:
    """Nhận diện MIME từ chữ ký ảnh phổ biến."""
    if image_bytes.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if image_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if image_bytes.startswith(b"RIFF") and image_bytes[8:12] == b"WEBP":
        return "image/webp"
    # Dùng JPEG làm mặc định tương thích với đầu vào JPG/JPEG.
    return "image/jpeg"


def image_to_data_url(image_bytes: bytes, mime_type: str | None = None) -> str:
    """Chuyển bytes ảnh thành data URL để gửi đến OpenAI."""
    if not image_bytes:
        raise ValueError("Dữ liệu ảnh CCCD đang rỗng.")

    detected_mime = mime_type or _detect_mime_type(image_bytes)
    encoded = base64.b64encode(image_bytes).decode("ascii")
    return f"data:{detected_mime};base64,{encoded}"


# ============================================================
# JSON SCHEMA
# ============================================================

CCCD_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "soGiayTo": {"type": "string"},
        "hoDem": {"type": "string"},
        "ten": {"type": "string"},
        "hoTen": {"type": "string"},
        "ngaySinh": {"type": "string"},
        "gioiTinh": {"type": "string"},
        "quocTich": {"type": "string"},
        "danToc": {"type": "string"},
        "nguyenQuan": {"type": "string"},
        "noiSinh": {"type": "string"},
        "noiCapGiayTo": {"type": "string"},
        "ngayCap": {"type": "string"},
        "ngayHetHan": {"type": "string"},
        "mrzLines": {
            "type": "array",
            "items": {"type": "string"},
        },
        "HKTT": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "quocGia": {"type": "string"},
                "tinh": {"type": "string"},
                "quanHuyen": {"type": "string"},
                "phuongXa": {"type": "string"},
                "soNha": {"type": "string"},
                "diaChiGoc": {"type": "string"},
            },
            "required": [
                "quocGia",
                "tinh",
                "quanHuyen",
                "phuongXa",
                "soNha",
                "diaChiGoc",
            ],
        },
    },
    "required": [
        "soGiayTo",
        "hoDem",
        "ten",
        "hoTen",
        "ngaySinh",
        "gioiTinh",
        "quocTich",
        "danToc",
        "nguyenQuan",
        "noiSinh",
        "noiCapGiayTo",
        "ngayCap",
        "ngayHetHan",
        "mrzLines",
        "HKTT",
    ],
}


# ============================================================
# OCR PROMPT
# ============================================================



OCR_PROMPT = """
Bạn là hệ thống OCR chuyên đọc CCCD và thẻ căn cước Việt Nam.
Đọc cả hai ảnh do người dùng gửi: ảnh mặt trước và ảnh mặt sau.

Chỉ trả về một JSON object đúng schema đã cung cấp.
Không trả Markdown, không thêm giải thích.

I. NHẬN DIỆN LOẠI THẺ VÀ VỊ TRÍ ĐỊA CHỈ

1. Kiểm tra riêng từng ảnh để xác định mặt trước và mặt sau.
2. CCCD cũ thường có thông tin "Nơi thường trú" ở mặt trước.
3. Thẻ căn cước/CCCD gắn chip mới thường có thông tin
   "Nơi cư trú / Place of residence" ở mặt sau.
4. Đây chỉ là dấu hiệu nhận biết, không phải điều kiện tuyệt đối.
5. Không phân loại chỉ dựa vào số định danh 9 hay 12 chữ số.
6. Ưu tiên nhãn thực tế nhìn thấy trên ảnh.
7. Nếu không tìm thấy địa chỉ ở mặt thường được dự đoán,
   hãy kiểm tra ảnh còn lại trước khi kết luận địa chỉ không có.
8. Không lấy thông tin từ ảnh này để thay thế trường khác
   khi chưa xác định được nhãn tương ứng.
9. Nếu không đọc được, trả chuỗi rỗng "".

II. NGUYÊN TẮC CHUNG

1. Đọc trực tiếp nội dung nhìn thấy trên ảnh.
2. Không suy đoán, không bịa và không tự sửa tên riêng.
3. Giữ nguyên tiếng Việt có dấu, địa danh và thứ tự chữ.
4. Không đổi địa giới hành chính cũ sang mới.
5. Tất cả giá trị phải là chuỗi, không dùng null.
6. Ngày tháng rõ ràng thì chuẩn hóa DD/MM/YYYY.
7. Phân biệt nhãn trường với nội dung của trường.
8. Không dùng ví dụ trong prompt làm dữ liệu mặc định.

III. THÔNG TIN CÁ NHÂN

Đọc chính xác:
- soGiayTo: số định danh cá nhân/số căn cước.
- hoTen: toàn bộ họ tên.
- hoDem: họ và tên đệm, không gồm tên cuối.
- ten: tên cuối.
- ngaySinh: ngày sinh.
- gioiTinh: giới tính.
- quocTich: quốc tịch.
- danToc: dân tộc nếu có.
- nguyenQuan: nội dung tại nhãn "Quê quán / Place of origin" trên CCCD
  cũ, hoặc "Nơi đăng ký khai sinh / Place of birth" trên thẻ mới nếu nhãn
  đó xuất hiện. Chép đúng địa danh nhìn thấy; không thay bằng địa chỉ cư trú.
- noiSinh: chỉ đọc trường có nhãn riêng "Nơi sinh / Place of birth" nếu có.
- noiCapGiayTo: cơ quan/nơi cấp.
- ngayCap: ngày cấp.
- ngayHetHan: ngày hết hạn.
- mrzLines: các dòng MRZ ở cuối thẻ nếu nhìn thấy rõ. Chép nguyên văn,
  giữ ký tự "<", đúng thứ tự dòng; nếu không thấy MRZ thì trả mảng rỗng.
  Không tự tạo hoặc sửa ký tự MRZ.
- MRZ thường có đúng 3 dòng, mỗi dòng 30 ký tự. Đếm chính xác cả ký tự "<";
  không bỏ các ký tự đệm ở cuối dòng hoặc giữa các trường.

QUAN TRỌNG VỀ NGUYÊN QUÁN:
- Kiểm tra cả hai ảnh để tìm trường nguyên quán theo đúng loại thẻ:
  "Quê quán / Place of origin" trên CCCD cũ hoặc
  "Nơi đăng ký khai sinh / Place of birth" trên thẻ mới.
- Nhãn "Nơi sinh / Place of birth" riêng biệt được ghi vào noiSinh; không
  tự coi đây là nguyên quán nếu trên thẻ có nhãn khác.
- Không nhầm các trường này với "Nơi cư trú / Place of residence".
- Không lấy địa chỉ cư trú để điền nguyên quán.
- Không tự suy ra nguyên quán từ HKTT.
- Nếu không thấy nhãn nguyên quán tương ứng hoặc không đọc được, trả "".

IV. HỌ TÊN

Giữ nguyên thứ tự họ tên trên thẻ.
Ví dụ minh họa "NGUYỄN VĂN AN":
hoTen = "NGUYỄN VĂN AN"
hoDem = "NGUYỄN VĂN"
ten = "AN"

V. ĐỊA CHỈ NƠI CƯ TRÚ / HKTT

Chỉ lấy địa chỉ theo nhãn thực tế trên thẻ:
- "Nơi thường trú" trên thẻ cũ nếu nhãn này xuất hiện.
- "Nơi cư trú / Place of residence" trên thẻ mới nếu nhãn này xuất hiện.

Không mặc định địa chỉ luôn nằm ở mặt trước hoặc mặt sau.
Hãy kiểm tra cả hai ảnh, tìm nhãn địa chỉ rồi mới trích xuất.

1. HKTT.diaChiGoc:
   Giữ toàn bộ địa chỉ đúng thứ tự trên thẻ.
   Chỉ chuẩn hóa khoảng trắng thừa.
2. HKTT.tinh:
   Chỉ điền tỉnh/thành cấp tỉnh khi có căn cứ rõ ràng.
3. HKTT.quanHuyen:
   Chỉ điền huyện/quận/thành phố thuộc tỉnh nếu có căn cứ.
   Nếu không có hoặc không chắc chắn, để rỗng.
4. HKTT.phuongXa:
   Chỉ điền phường/xã/thị trấn khi đọc được rõ ràng.
5. HKTT.soNha:
   Giữ phần địa chỉ chi tiết như số nhà, đường, xóm, thôn,
   ấp, tổ hoặc khu phố nếu đọc được.
   Không nhầm xóm/thôn với xã/phường.
6. Không tự đổi "Thị Tứ" thành "Thị Tư" hoặc ngược lại.
7. Nếu không chắc cách tách địa chỉ:
   - Giữ nguyên toàn bộ địa chỉ trong diaChiGoc.
   - Chỉ điền thành phần có căn cứ rõ ràng.
   - Không tự tạo thêm thành phần hành chính.
8. HKTT.quocGia:
   Chỉ điền khi có căn cứ; nếu không chắc thì để rỗng.
9. Không lấy nguyên quán, nơi sinh hoặc nơi cấp làm HKTT.
10. Không tự chuyển địa chỉ sang địa giới hành chính mới.
11. QUY TẮC XỬ LÝ ĐỊA CHỈ TRÊN CCCD CŨ:
- Địa chỉ trên CCCD cũ có thể sử dụng địa giới hành chính trước khi sắp xếp đơn vị hành chính.
- Không bắt buộc địa chỉ phải có đủ tỉnh, huyện, xã.
- Đọc địa chỉ theo đúng thứ tự và cách viết trên thẻ, không tự suy luận cấp hành chính chỉ dựa vào tên địa danh.
- Phải phân biệt phần địa chỉ chi tiết như số nhà, đường, xóm, thôn, tổ dân phố với tên phường/xã và quận/huyện.
- Không tự động coi thành phần đứng trước tên tỉnh là quận/huyện.
- Nếu không xác định chắc chắn được một thành phần hành chính, giữ nguyên địa chỉ trong HKTT.diaChiGoc và để trường thành phần đó là chuỗi rỗng.
- Không được làm mất bất kỳ phần nào của địa chỉ gốc khi tách thành các trường.
- Không dùng thông tin của một CCCD khác làm căn cứ điền địa chỉ.

VI. KIỂM TRA CUỐI

- Đối chiếu nguyên quán với nhãn quê quán hoặc nơi đăng ký khai sinh phù hợp.
- Đối chiếu HKTT với đúng nhãn địa chỉ cư trú.
- Kiểm tra không nhầm tỉnh với huyện.
- Kiểm tra không nhầm xóm/thôn với xã/phường.
- Giữ nguyên tên địa danh; không tự sửa chữ khi không chắc.
- Nếu không đọc được thành phần nào, trả chuỗi rỗng.
- Chỉ trả về JSON đúng schema.
"""




# ============================================================
# NORMALIZATION
# ============================================================

TEXT_FIELDS = [
    "soGiayTo",
    "hoDem",
    "ten",
    "hoTen",
    "ngaySinh",
    "gioiTinh",
    "quocTich",
    "danToc",
    "nguyenQuan",
    "noiSinh",
    "noiCapGiayTo",
    "ngayCap",
    "ngayHetHan",
]


def _as_string(value: Any) -> str:
    """Đảm bảo giá trị là chuỗi, không biến None thành 'None'."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)):
        return str(value).strip()
    return ""



def _normalize_result(data: dict) -> dict:
    """Chuẩn hóa OCR, không suy đoán địa chỉ còn thiếu."""
    result = {
        field: _as_string(data.get(field, ""))
        for field in TEXT_FIELDS
    }
    mrz_lines = data.get("mrzLines", [])
    if isinstance(mrz_lines, str):
        mrz_lines = mrz_lines.splitlines()
    result["mrzLines"] = [
        line.strip()
        for line in mrz_lines
        if isinstance(line, str) and line.strip()
    ] if isinstance(mrz_lines, list) else []

    hktt = data.get("HKTT", {})
    if not isinstance(hktt, dict):
        hktt = {}

    address = {
        "quocGia": _as_string(hktt.get("quocGia", "")),
        "tinh": _as_string(hktt.get("tinh", "")),
        "quanHuyen": _as_string(hktt.get("quanHuyen", "")),
        "phuongXa": _as_string(hktt.get("phuongXa", "")),
        "soNha": _as_string(hktt.get("soNha", "")),
        "diaChiGoc": _as_string(hktt.get("diaChiGoc", "")),
    }

    # Tránh ghi tên tỉnh vào cả trường tỉnh và huyện.
    def normalize_name(value: str) -> str:
        import unicodedata

        value = unicodedata.normalize("NFC", value or "")
        value = " ".join(value.casefold().split())
        return value

    if (
        address["tinh"]
        and address["quanHuyen"]
        and normalize_name(address["tinh"])
        == normalize_name(address["quanHuyen"])
    ):
        address["quanHuyen"] = ""

    # Nếu OCR chưa tách được địa chỉ, vẫn giữ chuỗi gốc.
    if not address["diaChiGoc"]:
        address["diaChiGoc"] = ", ".join(
            part
            for part in (
                address["soNha"],
                address["phuongXa"],
                address["quanHuyen"],
                address["tinh"],
            )
            if part
        )

    result["HKTT"] = address

    # Không lấy HKTT hoặc nơi sinh để tự điền nguyên quán.
    # Giữ nguyên giá trị OCR trả về.
    result["nguyenQuan"] = _as_string(data.get("nguyenQuan", ""))

    # Chỉ tách họ tên khi có họ tên nhưng thiếu trường ten.
    if result["hoTen"] and not result["ten"]:
        name_parts = result["hoTen"].split()
        if len(name_parts) >= 2:
            result["ten"] = name_parts[-1]
            result["hoDem"] = " ".join(name_parts[:-1])

    return result



# ============================================================
# VISION OCR PROVIDERS
# ============================================================

def _messages_for_provider(provider: str, front_image: str, back_image: str) -> list:
    def image_part(url: str) -> dict:
        image_url = {"url": url}
        if provider == "openai":
            image_url["detail"] = "high"
        return {"type": "image_url", "image_url": image_url}

    return [
        {
            "role": "system",
            "content": (
                "Bạn là hệ thống OCR tài liệu CCCD Việt Nam. "
                "Đọc chính xác chữ nhìn thấy trong ảnh. "
                "Phân biệt mặt trước và mặt sau. "
                "Không suy đoán hoặc tự điền thông tin còn thiếu. "
                "Chỉ trả về JSON đúng schema được cung cấp."
            ),
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": (
                        OCR_PROMPT
                        + "\n\nẢNH 1 là mặt trước CCCD đã được cân bằng tương phản "
                        "và làm nét nhẹ. Hãy trích xuất các trường nhìn thấy trên mặt trước, "
                        "bao gồm số giấy tờ, họ tên, ngày sinh, giới tính, quốc tịch, "
                        "quê quán và nơi cư trú nếu những trường đó xuất hiện. "
                        "Chỉ ghi nội dung nhìn thấy; không suy đoán chi tiết bị mờ."
                    ),
                },
                image_part(front_image),
                {
                    "type": "text",
                    "text": (
                        "ẢNH 2 là mặt sau CCCD đã được cân bằng tương phản "
                        "và làm nét nhẹ. Kiểm tra xem mặt sau có thông tin cần "
                        "trích xuất hay không. Chỉ sử dụng nội dung thực sự nhìn "
                        "thấy; không suy đoán chi tiết bị mờ hoặc lấy nội dung "
                        "của trường này thay cho trường khác."
                    ),
                },
                image_part(back_image),
            ],
        },
    ]


def _parse_provider_response(response) -> dict:
    choices = getattr(response, "choices", None)
    if not choices:
        raise ValueError("Nhà cung cấp OCR trả về kết quả rỗng.")

    content = getattr(
        getattr(choices[0], "message", None),
        "content",
        None,
    )
    if not isinstance(content, str) or not content.strip():
        raise ValueError("Nhà cung cấp OCR trả về nội dung rỗng.")

    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError("Nhà cung cấp OCR trả về JSON không hợp lệ.") from exc

    if not isinstance(data, dict):
        raise ValueError("Kết quả OCR không phải JSON object.")
    return _normalize_result(data)


def _model_for_provider(provider: str) -> str:
    if provider == "openai":
        return os.getenv("OPENAI_VISION_MODEL", MODEL_NAME)
    return os.getenv("GROQ_VISION_MODEL", GROQ_MODEL_NAME)


def extract_cccd(front_bytes: bytes, back_bytes: bytes) -> dict:
    """
    Nhận bytes ảnh CCCD hai mặt, thử lần lượt các dịch vụ Vision đã cấu hình và trả dict
    tương thích với API Flask /api/ocr-cccd.
    """
    if not front_bytes:
        raise ValueError("Chưa có ảnh mặt trước CCCD.")
    if not back_bytes:
        raise ValueError("Chưa có ảnh mặt sau CCCD.")

    providers = _provider_order()
    if not providers:
        raise OCRConfigurationError(
            "Cần cấu hình OPENAI_API_KEY hoặc GROQ_API_KEY cho OCR."
        )

    front_image = image_to_data_url(
        enhance_document_image(front_bytes),
        "image/jpeg",
    )
    back_image = image_to_data_url(
        enhance_document_image(back_bytes),
        "image/jpeg",
    )

    failures = []
    for provider in providers:
        sdk_error = openai.APIError if provider == "openai" else groq.APIError
        try:
            response = _get_client(provider).chat.completions.create(
                model=_model_for_provider(provider),
                messages=_messages_for_provider(
                    provider,
                    front_image,
                    back_image,
                ),
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "cccd",
                        "strict": True,
                        "schema": CCCD_SCHEMA,
                    },
                },
            )
            result = _parse_provider_response(response)
        except (sdk_error, ValueError) as error:
            failures.append((provider, type(error).__name__))
            logger.warning(
                "OCR provider %s failed (%s); continuing to next configured provider.",
                provider,
                type(error).__name__,
            )
            continue

        logger.info(
            "OCR completed with provider %s model %s; has document number: %s; "
            "has household address: %s",
            provider,
            _model_for_provider(provider),
            bool(result["soGiayTo"]),
            bool(result["HKTT"]["diaChiGoc"]),
        )
        return result

    raise OCRProviderError(failures)