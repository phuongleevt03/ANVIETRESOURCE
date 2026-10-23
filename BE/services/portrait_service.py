import os
import threading

import cv2
import base64
import numpy as np


# =========================================================
# YUNET FACE DETECTOR (OpenCV DNN, model ~230KB)
# =========================================================

YUNET_MODEL_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "models",
    "face_detection_yunet_2023mar.onnx"
)

YUNET_SCORE_THRESHOLD = 0.6

_yunet_detector = None
_yunet_lock = threading.Lock()


def _get_yunet_detector():
    """
    Nạp YuNet một lần rồi dùng lại.
    Trả về None nếu thiếu model hoặc OpenCV không hỗ trợ
    → khi đó dùng Haar Cascade.
    """

    global _yunet_detector

    if _yunet_detector is None:

        if not os.path.isfile(YUNET_MODEL_PATH):
            return None

        try:
            _yunet_detector = cv2.FaceDetectorYN.create(
                YUNET_MODEL_PATH,
                "",
                (320, 320),
                YUNET_SCORE_THRESHOLD,
                0.3,
                5000
            )
        except (AttributeError, cv2.error):
            return None

    return _yunet_detector


def detect_face_yunet(image):
    """
    Tìm khuôn mặt bằng YuNet.
    Trả về khuôn mặt lớn nhất (ảnh chân dung trên CCCD).
    """

    img_h, img_w = image.shape[:2]

    # FaceDetectorYN giữ trạng thái input size
    # → khoá lại khi gunicorn chạy nhiều thread
    with _yunet_lock:

        detector = _get_yunet_detector()

        if detector is None:
            return None

        detector.setInputSize((img_w, img_h))

        _, faces = detector.detect(image)

    if faces is None or len(faces) == 0:
        return None

    best = max(
        faces,
        key=lambda item: item[2] * item[3]
    )

    x1 = max(0, int(best[0]))
    y1 = max(0, int(best[1]))
    x2 = min(img_w, int(best[0] + best[2]))
    y2 = min(img_h, int(best[1] + best[3]))

    if x2 <= x1 or y2 <= y1:
        return None

    return {
        "x": x1,
        "y": y1,
        "width": x2 - x1,
        "height": y2 - y1
    }


# =========================================================
# HAAR CASCADE FALLBACK
# =========================================================

FACE_CASCADE_PATH = (
    cv2.data.haarcascades
    + "haarcascade_frontalface_default.xml"
)

face_cascade = cv2.CascadeClassifier(
    FACE_CASCADE_PATH
)


def detect_face(image):
    """
    Tìm khuôn mặt bằng Haar Cascade.
    Dùng làm fallback / bước xác nhận.
    """

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    gray = cv2.equalizeHist(gray)

    faces = face_cascade.detectMultiScale(
        gray,
        scaleFactor=1.05,
        minNeighbors=3,
        minSize=(20, 20)
    )

    if len(faces) == 0:
        return None

    # Chọn khuôn mặt lớn nhất
    faces = sorted(
        faces,
        key=lambda item: item[2] * item[3],
        reverse=True
    )

    x, y, w, h = faces[0]

    return {
        "x": int(x),
        "y": int(y),
        "width": int(w),
        "height": int(h)
    }


# =========================================================
# CROP CHÂN DUNG
# =========================================================

def crop_portrait_from_bytes(file_bytes: bytes):

    # -----------------------------------------------------
    # 1. Đọc ảnh
    # -----------------------------------------------------

    image_array = np.frombuffer(
        file_bytes,
        dtype=np.uint8
    )

    image = cv2.imdecode(
        image_array,
        cv2.IMREAD_COLOR
    )

    if image is None:
        raise ValueError(
            "Không đọc được ảnh CCCD"
        )

    original_h, original_w = image.shape[:2]


    # -----------------------------------------------------
    # 2. Đưa ảnh về chiều rộng 1600px để dò mặt
    # -----------------------------------------------------

    detect_image = image

    scale = 1.0

    if original_w != 1600:

        scale = 1600 / original_w

        new_w = int(original_w * scale)
        new_h = int(original_h * scale)

        detect_image = cv2.resize(
            image,
            (new_w, new_h),
            interpolation=(
                cv2.INTER_CUBIC if scale > 1
                else cv2.INTER_AREA
            )
        )


    # -----------------------------------------------------
    # 3. YuNet tìm mặt
    # -----------------------------------------------------

    detector_name = "YuNet"

    face_info = detect_face_yunet(
        detect_image
    )


    # -----------------------------------------------------
    # 4. Fallback: Haar Cascade
    # -----------------------------------------------------

    if face_info is None:

        detector_name = "Haar"

        face_info = detect_face(
            detect_image
        )


    # -----------------------------------------------------
    # 5. Nếu vẫn không tìm được
    # -----------------------------------------------------

    if face_info is None:

        raise ValueError(
            "Không tìm thấy khuôn mặt trên ảnh CCCD"
        )


    # -----------------------------------------------------
    # 6. Đưa tọa độ về ảnh gốc
    # -----------------------------------------------------

    x = face_info["x"]
    y = face_info["y"]
    w = face_info["width"]
    h = face_info["height"]

    if scale != 1.0:

        x = int(x / scale)
        y = int(y / scale)
        w = int(w / scale)
        h = int(h / scale)


    # -----------------------------------------------------
    # 7. Mở rộng vùng crop
    # -----------------------------------------------------

    left_expand = int(w * 1.0)
    right_expand = int(w * 1.0)

    top_expand = int(h * 1.2)
    bottom_expand = int(h * 1.5)

    x1 = max(
        0,
        x - left_expand
    )

    y1 = max(
        0,
        y - top_expand
    )

    x2 = min(
        original_w,
        x + w + right_expand
    )

    y2 = min(
        original_h,
        y + h + bottom_expand
    )


    # -----------------------------------------------------
    # 8. Crop
    # -----------------------------------------------------

    portrait = image[
        y1:y2,
        x1:x2
    ]


    if portrait.size == 0:

        raise ValueError(
            "Không crop được vùng chân dung"
        )


    # -----------------------------------------------------
    # 9. Chuẩn hóa tỷ lệ
    # -----------------------------------------------------

    target_ratio = 0.78

    ph, pw = portrait.shape[:2]

    current_ratio = pw / ph


    if current_ratio > target_ratio:

        new_width = int(
            ph * target_ratio
        )

        center_x = pw // 2

        crop_x1 = max(
            0,
            center_x - new_width // 2
        )

        crop_x2 = min(
            pw,
            crop_x1 + new_width
        )

        portrait = portrait[
            :,
            crop_x1:crop_x2
        ]

    else:

        new_height = int(
            pw / target_ratio
        )

        center_y = ph // 2

        crop_y1 = max(
            0,
            center_y - new_height // 2
        )

        crop_y2 = min(
            ph,
            crop_y1 + new_height
        )

        portrait = portrait[
            crop_y1:crop_y2,
            :
        ]


    # -----------------------------------------------------
    # 10. Resize ảnh chân dung
    # -----------------------------------------------------

    portrait = cv2.resize(
        portrait,
        (600, 770),
        interpolation=cv2.INTER_CUBIC
    )


    # -----------------------------------------------------
    # 11. Encode JPG
    # -----------------------------------------------------

    success, encoded = cv2.imencode(
        ".jpg",
        portrait,
        [
            cv2.IMWRITE_JPEG_QUALITY,
            95
        ]
    )

    if not success:

        raise ValueError(
            "Không thể mã hóa ảnh chân dung"
        )


    # -----------------------------------------------------
    # 12. Base64
    # -----------------------------------------------------

    image_base64 = base64.b64encode(
        encoded.tobytes()
    ).decode("utf-8")


    image_data_url = (
        "data:image/jpeg;base64,"
        + image_base64
    )


    # -----------------------------------------------------
    # 13. Trả kết quả
    # -----------------------------------------------------

    return {

        "success": True,

        "image_base64":
            image_data_url,

        "width":
            portrait.shape[1],

        "height":
            portrait.shape[0],

        "face": {

            "x": x,
            "y": y,
            "width": w,
            "height": h
        },

        "crop": {

            "x1": x1,
            "y1": y1,
            "x2": x2,
            "y2": y2
        },

        "detector":
            detector_name
    }