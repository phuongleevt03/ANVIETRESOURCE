import ipaddress
import logging
import os
import secrets
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlsplit

import cv2
from dotenv import load_dotenv
from flask import Flask, request, jsonify, redirect, send_from_directory
from flask_cors import CORS
from flask_migrate import Migrate
from sqlalchemy.exc import SQLAlchemyError

from services.portrait_service import (
    crop_portrait_from_bytes
)
from services.document_verification import verify_cccd_result

load_dotenv(Path(__file__).resolve().parent / ".env")

# ============================================================
# OPENAI OCR
# ============================================================

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    RateLimitError,
)
from services.openai_cccd import OpenAIConfigurationError, extract_cccd
from auth_api import auth_api, authenticated, create_account
from database import db
from migration_api import migration_api
from records_api import records_api

cv2.setNumThreads(1)


# ============================================================
# FLASK
# ============================================================

app = Flask(__name__)
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "FE"
app_environment = os.getenv("APP_ENV", "development").strip().lower()
database_url = os.getenv("DATABASE_URL", "").strip()
if database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql+psycopg://", 1)
elif database_url.startswith("postgresql://"):
    database_url = database_url.replace("postgresql://", "postgresql+psycopg://", 1)
app.config.update(
    SECRET_KEY=os.getenv("SECRET_KEY") or secrets.token_urlsafe(48),
    SQLALCHEMY_DATABASE_URI=database_url or f"sqlite:///{(Path(app.instance_path) / 'hr_profiles.sqlite3').as_posix()}",
    SQLALCHEMY_TRACK_MODIFICATIONS=False,
    SQLALCHEMY_ENGINE_OPTIONS={"pool_pre_ping": True},
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SECURE=os.getenv("SESSION_COOKIE_SECURE", "false").lower() in {"1", "true", "yes"},
    SESSION_COOKIE_SAMESITE="Lax",
    PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
)
if app_environment == "production" and not os.getenv("SECRET_KEY"):
    raise RuntimeError("SECRET_KEY phải được cấu hình khi chạy production.")
if app_environment == "production" and not database_url.startswith("postgresql+psycopg://"):
    raise RuntimeError("Production yêu cầu DATABASE_URL trỏ tới PostgreSQL (không dùng SQLite).")
db.init_app(app)
migrate = Migrate(app, db)
app.register_blueprint(auth_api)
app.register_blueprint(records_api)
app.register_blueprint(migration_api)

ALLOWED_ORIGINS = [
    item.strip().rstrip("/")
    for item in os.getenv(
        "ALLOWED_ORIGINS",
        "http://localhost:5500,http://127.0.0.1:5500,http://localhost:8000,http://127.0.0.1:8000",
    ).split(",")
    if item.strip()
]
if app_environment == "production" and (
    not ALLOWED_ORIGINS or any(not item.startswith("https://") for item in ALLOWED_ORIGINS)
):
    raise RuntimeError("Production yêu cầu ALLOWED_ORIGINS là các origin HTTPS.")
CORS(
    app,
    resources={r"/api/*": {"origins": ALLOWED_ORIGINS}},
    supports_credentials=True,
    allow_headers=["Content-Type", "X-CSRF-Token", "If-Match"],
    methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
)
LOCAL_ONLY = os.getenv("LOCAL_ONLY", "false" if app_environment == "production" else "true").lower() in {"1", "true", "yes"}
ALLOWED_CLIENT_NETWORKS = [
    ipaddress.ip_network(item.strip(), strict=False)
    for item in os.getenv("ALLOWED_CLIENT_NETWORKS", "").split(",")
    if item.strip()
]
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ============================================================
# CONFIG
# ============================================================

MAX_CONTENT_LENGTH = 10 * 1024 * 1024

app.config[
    "MAX_CONTENT_LENGTH"
] = MAX_CONTENT_LENGTH


@app.before_request
def restrict_api_access():
    if not request.path.startswith("/api/"):
        return None
    try:
        address = ipaddress.ip_address(request.remote_addr or "")
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
            address = address.ipv4_mapped
    except ValueError:
        return jsonify({"success": False, "code": "CLIENT_ADDRESS_INVALID", "message": "Không xác định được địa chỉ thiết bị."}), 403
    if LOCAL_ONLY and not address.is_loopback:
        return jsonify({"success": False, "code": "LOCAL_ACCESS_ONLY", "message": "Chỉ cho phép kết nối local."}), 403
    if not LOCAL_ONLY and not address.is_loopback:
        if not ALLOWED_CLIENT_NETWORKS and app_environment != "production":
            return jsonify({"success": False, "code": "NETWORK_POLICY_REQUIRED", "message": "Cần cấu hình ALLOWED_CLIENT_NETWORKS để cho phép truy cập qua mạng."}), 403
        if ALLOWED_CLIENT_NETWORKS and not any(address in network for network in ALLOWED_CLIENT_NETWORKS):
            return jsonify({"success": False, "code": "NETWORK_NOT_ALLOWED", "message": "Thiết bị không thuộc mạng được phép."}), 403
    origin = request.headers.get("Origin")
    if origin and origin.rstrip("/") not in ALLOWED_ORIGINS:
        return jsonify({"success": False, "code": "ORIGIN_NOT_ALLOWED", "message": "Origin không được phép."}), 403
    return None


@app.after_request
def protect_api_response(response):
    if request.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@app.errorhandler(SQLAlchemyError)
def database_failure(error):
    logger.error("Database operation failed (%s).", type(error).__name__)
    db.session.rollback()
    return jsonify({"success": False, "code": "DATABASE_UNAVAILABLE", "message": "Không thể đọc hoặc lưu dữ liệu. Vui lòng thử lại."}), 503


@app.errorhandler(500)
def internal_failure(error):
    logger.error("Unhandled server error (%s).", type(error).__name__)
    db.session.rollback()
    return jsonify({"success": False, "code": "INTERNAL_ERROR", "message": "Máy chủ gặp lỗi. Vui lòng thử lại."}), 500


@app.get("/")
def frontend_index():
    return redirect("/folders.html")


@app.get("/<path:frontend_path>")
def frontend_static(frontend_path):
    if frontend_path.startswith("api/"):
        return jsonify({"success": False, "code": "NOT_FOUND", "message": "Không tìm thấy API."}), 404
    return send_from_directory(FRONTEND_DIR, frontend_path)


@app.cli.command("create-user")
def create_user_command():
    from getpass import getpass
    email = input("Email: ").strip()
    role = input("Vai trò [admin/employee] (admin): ").strip() or "admin"
    password = getpass("Mật khẩu (ít nhất 12 ký tự): ")
    if password != getpass("Nhập lại mật khẩu: "):
        raise SystemExit("Mật khẩu nhập lại không khớp.")
    try:
        with app.app_context():
            user = create_account(email, password, role)
            created_email, created_role = user.email, user.role
    except ValueError as error:
        raise SystemExit(str(error)) from error
    print(f"Đã tạo tài khoản {created_email} với vai trò {created_role}.")


ALLOWED_EXTENSIONS = {
    "png",
    "jpg",
    "jpeg"
}


# ============================================================
# CHECK FILE
# ============================================================

def allowed_file(filename):

    return (
        "."
        in filename
        and
        filename.rsplit(
            ".",
            1
        )[1].lower()
        in ALLOWED_EXTENSIONS
    )


# ============================================================
# HEALTH CHECK
# ============================================================

@app.route(
    "/api/health",
    methods=["GET"]
)
def health():

    return jsonify({
        "success": True,
        "status": "ok",
        "service": "portrait-extraction + openai-ocr"
    })


# ============================================================
# PORTRAIT API
# ============================================================

@app.route(
    "/api/portrait",
    methods=["POST"]
)
@authenticated
def portrait():
    from auth_api import require_csrf
    csrf_error = require_csrf()
    if csrf_error:
        return csrf_error

    try:

        # ----------------------------------------------------
        # Kiểm tra file
        # ----------------------------------------------------

        if "front" not in request.files:

            return jsonify({
                "success": False,
                "message":
                    "Chưa gửi ảnh CCCD mặt trước"
            }), 400

        file = request.files["front"]

        if file.filename == "":

            return jsonify({
                "success": False,
                "message":
                    "Tên file không hợp lệ"
            }), 400

        # ----------------------------------------------------
        # Kiểm tra định dạng
        # ----------------------------------------------------

        if not allowed_file(
            file.filename
        ):

            return jsonify({
                "success": False,
                "message":
                    "Chỉ chấp nhận PNG, JPG, JPEG"
            }), 400

        # ----------------------------------------------------
        # Đọc ảnh trực tiếp vào RAM
        # ----------------------------------------------------

        file_bytes = file.read()

        if not file_bytes:

            return jsonify({
                "success": False,
                "message":
                    "File ảnh rỗng"
            }), 400

        # ----------------------------------------------------
        # Crop chân dung
        #
        # KHÔNG LƯU FILE
        # ----------------------------------------------------

        result = crop_portrait_from_bytes(
            file_bytes
        )

        # ----------------------------------------------------
        # Trả ảnh Base64
        # ----------------------------------------------------

        return jsonify({
            "success": True,

            "message":
                "Đã crop chân dung thành công",

            "image_base64":
                result["image_base64"],

            "width":
                result["width"],

            "height":
                result["height"],

            "face":
                result["face"],

            "crop":
                result["crop"]
        }), 200

    except ValueError as e:

        return jsonify({
            "success": False,
            "message": str(e)
        }), 422

    except Exception as error:
        logger.error("Portrait processing failed (%s).", type(error).__name__)

        return jsonify({
            "success": False,
            "message":
                "Có lỗi xảy ra khi xử lý ảnh"
        }), 500


# ============================================================
# OCR CCCD - OPENAI VISION
# ============================================================

@app.route(
    "/api/ocr-cccd",
    methods=["POST"]
)
@authenticated
def ocr_cccd():
    from auth_api import require_csrf
    csrf_error = require_csrf()
    if csrf_error:
        return csrf_error

    try:

        # ----------------------------------------------------
        # KIỂM TRA MẶT TRƯỚC
        # ----------------------------------------------------

        if "front" not in request.files:

            return jsonify({
                "success": False,
                "message":
                    "Chưa gửi ảnh CCCD mặt trước"
            }), 400

        # ----------------------------------------------------
        # KIỂM TRA MẶT SAU
        # ----------------------------------------------------

        if "back" not in request.files:

            return jsonify({
                "success": False,
                "message":
                    "Chưa gửi ảnh CCCD mặt sau"
            }), 400

        front_file = request.files["front"]
        back_file = request.files["back"]

        # ----------------------------------------------------
        # KIỂM TRA TÊN FILE
        # ----------------------------------------------------

        if front_file.filename == "":
            return jsonify({
                "success": False,
                "message":
                    "Tên file mặt trước không hợp lệ"
            }), 400

        if back_file.filename == "":
            return jsonify({
                "success": False,
                "message":
                    "Tên file mặt sau không hợp lệ"
            }), 400

        # ----------------------------------------------------
        # KIỂM TRA ĐỊNH DẠNG
        # ----------------------------------------------------

        if not allowed_file(
            front_file.filename
        ):

            return jsonify({
                "success": False,
                "message":
                    "Ảnh mặt trước chỉ chấp nhận PNG, JPG, JPEG"
            }), 400

        if not allowed_file(
            back_file.filename
        ):

            return jsonify({
                "success": False,
                "message":
                    "Ảnh mặt sau chỉ chấp nhận PNG, JPG, JPEG"
            }), 400

        # ----------------------------------------------------
        # ĐỌC ẢNH VÀO RAM
        #
        # Không lưu ảnh CCCD xuống ổ đĩa
        # ----------------------------------------------------

        front_bytes = front_file.read()
        back_bytes = back_file.read()

        if not front_bytes:

            return jsonify({
                "success": False,
                "message":
                    "Ảnh CCCD mặt trước rỗng"
            }), 400

        if not back_bytes:

            return jsonify({
                "success": False,
                "message":
                    "Ảnh CCCD mặt sau rỗng"
            }), 400

        # ----------------------------------------------------
        # GỌI OPENAI VISION
        # ----------------------------------------------------

        result = extract_cccd(
            front_bytes,
            back_bytes
        )
        result = verify_cccd_result(result, front_bytes, back_bytes)

        # ----------------------------------------------------
        # TRẢ KẾT QUẢ
        # ----------------------------------------------------

        return jsonify({
            "success": True,
            "message":
                "Đã đọc và đối chiếu thông tin CCCD",
            "data":
                result
        }), 200

    except ValueError as e:

        return jsonify({
            "success": False,
            "message": str(e)
        }), 422

    

    except OpenAIConfigurationError as error:
        return jsonify({
            "success": False,
            "code": "OPENAI_NOT_CONFIGURED",
            "message": str(error)
        }), 503

    except RateLimitError as error:
        body = getattr(error, "body", None)
        details = body.get("error", {}) if isinstance(body, dict) else {}
        code = getattr(error, "code", None)
        if not code and isinstance(details, dict):
            code = details.get("code")
        if code == "insufficient_quota":
            return jsonify({
                "success": False,
                "code": "OPENAI_QUOTA_EXCEEDED",
                "message": "Dự án OpenAI đã hết hạn mức hoặc chưa bật billing."
            }), 402
        return jsonify({
            "success": False,
            "code": "OPENAI_RATE_LIMIT",
            "message": "OpenAI đang giới hạn yêu cầu. Vui lòng thử lại sau."
        }), 429

    except AuthenticationError:
        logger.error("OpenAI rejected the configured credentials.")
        return jsonify({
            "success": False,
            "code": "OPENAI_AUTH_FAILED",
            "message": "OpenAI từ chối API key. Kiểm tra OPENAI_API_KEY trong BE/.env rồi khởi động lại backend."
        }), 503

    except APITimeoutError:
        logger.warning("OpenAI OCR request timed out.")
        return jsonify({
            "success": False,
            "code": "OPENAI_TIMEOUT",
            "message": "OpenAI xử lý yêu cầu OCR quá lâu. Vui lòng thử lại với ảnh rõ hơn hoặc ít dung lượng hơn."
        }), 504

    except APIConnectionError:
        logger.warning("OpenAI service could not be reached.")
        return jsonify({
            "success": False,
            "code": "OPENAI_UNAVAILABLE",
            "message": "Không thể kết nối OpenAI. Kiểm tra mạng rồi thử lại."
        }), 503

    except APIStatusError as error:
        logger.error("OpenAI returned API status %s.", error.status_code)
        return jsonify({
            "success": False,
            "code": "OPENAI_REQUEST_FAILED",
            "message": "OpenAI không thể hoàn tất yêu cầu OCR."
        }), 502

    except Exception as error:
        logger.error("OCR processing failed (%s).", type(error).__name__)

        return jsonify({
            "success": False,
            "message": "Có lỗi xảy ra khi đọc thông tin CCCD"
        }), 500


# ============================================================
# FILE TOO LARGE
# ============================================================

@app.errorhandler(413)
def file_too_large(_error):

    return jsonify({
        "success": False,
        "code": "FILE_TOO_LARGE",
        "message": "Dữ liệu gửi lên vượt giới hạn 10 MB."
    }), 413


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":
    if app_environment == "production":
        raise SystemExit("Không dùng Flask development server trong production.")
    # app.run(host=os.getenv("HOST", "127.0.0.1"), port=8000, debug=False)
    app.run(host=os.getenv("HOST", "0.0.0.0"), port=8000, debug=False)