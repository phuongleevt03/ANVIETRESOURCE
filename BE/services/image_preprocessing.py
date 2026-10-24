import cv2
import numpy as np


MAX_DOCUMENT_EDGE = 1600
MAX_MODEL_IMAGE_BYTES = 1_500_000


def enhance_document_image(image_bytes: bytes) -> bytes:
    """Create a bounded-size, sharpened JPEG for vision OCR."""
    if not image_bytes:
        raise ValueError("Dữ liệu ảnh CCCD đang rỗng.")

    image = cv2.imdecode(
        np.frombuffer(image_bytes, dtype=np.uint8),
        cv2.IMREAD_COLOR,
    )
    if image is None:
        raise ValueError("Không thể giải mã ảnh CCCD để tăng độ rõ.")

    height, width = image.shape[:2]
    shortest_side = min(height, width)
    longest_side = max(height, width)
    scale = min(
        MAX_DOCUMENT_EDGE / longest_side,
        max(1.0, 900 / shortest_side),
    )
    if scale != 1:
        image = cv2.resize(
            image,
            None,
            fx=scale,
            fy=scale,
            interpolation=cv2.INTER_CUBIC if scale > 1 else cv2.INTER_AREA,
        )

    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
    luminance, channel_a, channel_b = cv2.split(lab)
    luminance = cv2.createCLAHE(
        clipLimit=1.5,
        tileGridSize=(8, 8),
    ).apply(luminance)
    balanced = cv2.cvtColor(
        cv2.merge((luminance, channel_a, channel_b)),
        cv2.COLOR_LAB2BGR,
    )
    del lab, luminance, channel_a, channel_b, image

    blurred = cv2.GaussianBlur(balanced, (0, 0), 1.0)
    sharpened = cv2.addWeighted(balanced, 1.35, blurred, -0.35, 0)
    del balanced, blurred

    while True:
        for quality in (85, 75, 65):
            success, encoded = cv2.imencode(
                ".jpg",
                sharpened,
                [cv2.IMWRITE_JPEG_QUALITY, quality],
            )
            if not success:
                raise ValueError("Không thể mã hóa ảnh CCCD đã tăng độ rõ.")
            if len(encoded) <= MAX_MODEL_IMAGE_BYTES:
                return encoded.tobytes()

        height, width = sharpened.shape[:2]
        if max(height, width) <= 900:
            raise ValueError("Không thể nén ảnh CCCD xuống dung lượng OCR cho phép.")

        resized = cv2.resize(
            sharpened,
            None,
            fx=0.8,
            fy=0.8,
            interpolation=cv2.INTER_AREA,
        )
        del sharpened
        sharpened = resized
