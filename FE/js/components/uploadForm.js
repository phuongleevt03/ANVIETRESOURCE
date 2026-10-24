// uploadForm.js
//
// Xác nhận mặt trước rồi crop chân dung trước khi cho chọn mặt sau.
// Chỉ sau khi xác nhận cả hai mặt mới gọi OCR.
// Ảnh CCCD không được lưu xuống máy.

// ============================================================
// STATE
// ============================================================

import { apiRequest } from "../services/apiClient.js";

let state = {
  frontFile: null,
  backFile: null,

  frontDataUrl: null,
  backDataUrl: null,

  portraitDataUrl: null,

  // Kết quả OCR từ dịch vụ Vision
  ocrData: null,
};

// ============================================================
// ============================================================
// REQUEST VERSION
// ============================================================

let portraitRequestId = 0;

// ============================================================
// OCR REQUEST VERSION
// ============================================================

let ocrRequestId = 0;
let imageSelectionRequestId = 0;
let imageReviewSide = null;
let isReviewProcessing = false;
let cameraStream = null;
let cameraRequestId = 0;
let cameraSide = null;

function updateBackAvailability() {
  const available = Boolean(state.portraitDataUrl);
  const section = document.getElementById("backUploadSection");
  const input = document.getElementById("backImageInput");
  const cameraButton = document.querySelector(
    '[data-camera-side="back"]',
  );

  if (section) {
    section.classList.toggle("is-locked", !available);
    section.setAttribute("aria-disabled", String(!available));
  }
  if (input) input.disabled = !available;
  if (cameraButton) cameraButton.disabled = !available;
}

function closeLiveCamera() {
  cameraRequestId++;
  cameraStream?.getTracks().forEach((track) => track.stop());
  cameraStream = null;

  const video = document.getElementById("liveCameraVideo");
  if (video) video.srcObject = null;

  const modal = document.getElementById("liveCameraModal");
  if (modal) modal.hidden = true;

  const captureButton = document.getElementById("cameraCapture");
  if (captureButton) captureButton.disabled = true;
}

async function openLiveCamera(side) {
  if (side === "back" && !state.portraitDataUrl) {
    alert("Vui lòng quét chân dung mặt trước thành công trước.");
    return;
  }

  if (!navigator.mediaDevices?.getUserMedia) {
    alert(
      "Trình duyệt không hỗ trợ mở camera trực tiếp. Hãy dùng localhost hoặc HTTPS và thử trình duyệt mới hơn.",
    );
    return;
  }

  closeLiveCamera();
  const requestId = cameraRequestId;
  const modal = document.getElementById("liveCameraModal");
  const video = document.getElementById("liveCameraVideo");
  const title = document.getElementById("liveCameraTitle");
  const status = document.getElementById("cameraStatus");
  const captureButton = document.getElementById("cameraCapture");
  if (!modal || !video || !title || !status || !captureButton) {
    throw new Error("Không tìm thấy giao diện camera.");
  }

  title.textContent =
    side === "front" ? "Chụp ảnh mặt trước CCCD" : "Chụp ảnh mặt sau CCCD";
  cameraSide = side;
  status.textContent = "Đang kết nối camera...";
  status.classList.remove("is-error");
  modal.hidden = false;

  try {
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: false,
      video: {
        facingMode: { ideal: "environment" },
        width: { ideal: 1920 },
        height: { ideal: 1080 },
      },
    });
    if (requestId !== cameraRequestId || modal.hidden) {
      stream.getTracks().forEach((track) => track.stop());
      return;
    }

    cameraStream = stream;
    video.srcObject = stream;
    await video.play();
    if (requestId !== cameraRequestId) return;

    status.textContent =
      "Đặt CCCD ngay ngắn trong khung hình, giữ máy ổn định rồi chụp.";
    captureButton.disabled = false;
    document.getElementById("cameraClose")?.focus();
  } catch (error) {
    if (requestId !== cameraRequestId) return;

    cameraStream?.getTracks().forEach((track) => track.stop());
    cameraStream = null;
    video.srcObject = null;
    const message =
      error.name === "NotAllowedError"
        ? "Bạn chưa cấp quyền camera. Hãy cho phép camera trong trình duyệt rồi thử lại."
        : error.name === "NotFoundError"
          ? "Không tìm thấy camera trên thiết bị."
          : `Không mở được camera: ${error.message}`;
    status.textContent = message;
    status.classList.add("is-error");
  }
}

async function captureLiveCamera(side) {
  const video = document.getElementById("liveCameraVideo");
  const canvas = document.getElementById("cameraCaptureCanvas");
  const status = document.getElementById("cameraStatus");
  const captureButton = document.getElementById("cameraCapture");
  if (!video?.videoWidth || !canvas || !status || !captureButton) {
    status && (status.textContent = "Camera chưa sẵn sàng. Hãy thử lại.");
    return;
  }

  const requestId = cameraRequestId;
  captureButton.disabled = true;
  canvas.width = video.videoWidth;
  canvas.height = video.videoHeight;
  canvas.getContext("2d")?.drawImage(video, 0, 0, canvas.width, canvas.height);

  const blob = await new Promise((resolve) =>
    canvas.toBlob(resolve, "image/jpeg", 0.92),
  );
  if (!blob) {
    if (requestId !== cameraRequestId) return;
    status.textContent = "Không tạo được ảnh. Hãy thử chụp lại.";
    status.classList.add("is-error");
    captureButton.disabled = false;
    return;
  }

  if (requestId !== cameraRequestId) return;
  const file = new File([blob], `cccd-${side}-${Date.now()}.jpg`, {
    type: "image/jpeg",
  });
  closeLiveCamera();
  await handleImageSelected(side, file);
}

// ============================================================
// GET STATE
// ============================================================

export function getUploadState() {
  return state;
}

export function cancelOCRProcessing() {
  if (!document.body.classList.contains("ocr-processing-active")) return false;

  ocrRequestId++;
  isReviewProcessing = false;
  setOCRStatus("", "idle");
  showImageStage(null);
  return true;
}

// ============================================================
// RESET
// ============================================================

export function resetUploadState() {
  closeLiveCamera();
  state = {
    frontFile: null,
    backFile: null,

    frontDataUrl: null,
    backDataUrl: null,

    portraitDataUrl: null,

    ocrData: null,
  };
  setPortraitStatus("", "idle");

  portraitRequestId++;

  ocrRequestId++;
  imageSelectionRequestId++;

  renderPreview("front", null);

  renderPreview("back", null);

  updateBackAvailability();
  showImageStage(null);

  setOCRStatus("", "idle");
  setAvatarImage(null);
}

function showImageStage(stage) {
  const frontSection = document.getElementById("frontUploadSection");
  const backSection = document.getElementById("backUploadSection");
  const review = document.getElementById("imageReview");
  const profileForm = document.getElementById("profileForm");
  const reviewImage = document.getElementById("imageReviewPreview");
  const title = document.getElementById("imageReviewTitle");
  const description = document.getElementById("imageReviewDescription");
  const continueButton = document.getElementById("reviewContinue");
  const cancelButton = document.getElementById("reviewCancel");
  const chooseAgainButton = document.getElementById("reviewChooseAgain");
  const takeAgainButton = document.getElementById("reviewTakeAgain");
  const backFromUpload = document.getElementById("backToFrontReview");
  const backFromReview = document.getElementById("backToFrontFromReview");
  const frontProgress = document.getElementById("frontReviewProgress");
  const backProgress = document.getElementById("backReviewProgress");
  const isReview = stage === "front-review" || stage === "back-review";
  const isPortraitStage = stage === "front-processing";
  const isBackSelect = stage === "back-select";
  const isOCRStage = stage === "ocr-processing";
  const isCompactUpload = isOCRStage;
  const portraitReadyNotice = document.getElementById("portraitReadyNotice");

  imageReviewSide = isReview ? stage.replace("-review", "") : null;
  document.body.classList.toggle("image-review-active", isReview);
  document.body.classList.toggle("back-select-active", isBackSelect);
  document.body.classList.toggle("scan-upload-active", isCompactUpload);
  document.body.classList.toggle("ocr-processing-active", isOCRStage);
  if (frontSection) frontSection.hidden = isReview;
  if (backSection) {
    backSection.hidden = isReview;
  }
  updateBackAvailability();
  if (review) review.hidden = !isReview;
  if (profileForm) {
    profileForm.hidden = isOCRStage;
  }
  if (portraitReadyNotice) {
    portraitReadyNotice.hidden =
      !state.portraitDataUrl ||
      (stage !== "back-select" &&
        stage !== null &&
        stage !== "ocr-processing");
    portraitReadyNotice.textContent =
      "Đã quét chân dung. Tải thêm ảnh mặt sau để tự điền thông tin.";
  }
  if (backFromUpload) backFromUpload.hidden = stage !== "back-select";
  if (backFromReview) backFromReview.hidden = stage !== "back-review";
  if (cancelButton) cancelButton.hidden = imageReviewSide !== "front";
  if (chooseAgainButton) chooseAgainButton.hidden = imageReviewSide === "front";
  if (takeAgainButton) takeAgainButton.hidden = imageReviewSide === "front";

  frontProgress?.classList.toggle(
    "is-active",
    stage === "front-review" ||
      stage === "front-processing" ||
      stage === "back-select" ||
      stage === "back-review",
  );
  frontProgress?.classList.toggle(
    "is-complete",
    stage === "back-select" || stage === "back-review",
  );
  backProgress?.classList.toggle(
    "is-active",
    stage === "back-review" || stage === "back-select",
  );

  if (stage === "front-processing") {
    setPortraitStatus("Đang quét chân dung", "loading");
  } else if (stage === null || stage === "front-review") {
    setPortraitStatus("", "idle");
  }

  if (!isReview) return;

  const sideLabel = imageReviewSide === "front" ? "mặt trước" : "mặt sau";
  const dataUrl =
    imageReviewSide === "front" ? state.frontDataUrl : state.backDataUrl;
  if (reviewImage) reviewImage.src = dataUrl || "";
  if (title) title.textContent = `Kiểm tra ảnh ${sideLabel}`;
  if (description) {
    description.textContent =
      "Đảm bảo ảnh rõ nét, đủ sáng và không bị mất góc trước khi tiếp tục.";
  }
  if (continueButton) {
    continueButton.textContent =
      imageReviewSide === "front"
        ? "Xác nhận"
        : "Ảnh rõ, bắt đầu quét CCCD";
  }
}

function setPortraitStatus(message, status) {
  const statusElement = document.getElementById("portraitStatus");
  const statusText = document.getElementById("portraitStatusText");
  if (!statusElement || !statusText) return;

  statusText.textContent = message;
  statusElement.hidden = !message;
  statusElement.classList.toggle("is-loading", status === "loading");
  statusElement.classList.toggle("is-success", status === "success");
  statusElement.classList.toggle("is-error", status === "error");
}

function setOCRStatus(message, status) {
  const statusElement = document.getElementById("ocrStatus");
  const statusText = document.getElementById("ocrStatusText");
  if (!statusElement || !statusText) return;

  statusText.textContent = message;
  statusElement.hidden = !message;
  statusElement.classList.toggle("is-loading", status === "loading");
  statusElement.classList.toggle("is-success", status === "success");
  statusElement.classList.toggle("is-error", status === "error");
}

// ============================================================
// RENDER PREVIEW
// ============================================================

function renderPreview(side, dataUrl) {
  const wrap = document.getElementById(
    side === "front" ? "frontPreviewWrap" : "backPreviewWrap",
  );

  if (!wrap) {
    return;
  }

  wrap.classList.toggle("has-image", Boolean(dataUrl));

  const placeholderText =
    side === "front"
      ? `
        <div class="upload-circle">
          ⬆
        </div>

        <div>
          <span class="link-text">
            Chọn ảnh
          </span>

          hoặc kéo ảnh
          <b>mặt trước</b>
          vào đây
        </div>

        <div class="upload-hint">
          Lưu ý: Kích thước ảnh lớn hơn 640 x 480
          <br/>
          định dạng .PNG, .JPG, .JPEG
        </div>
      `
      : `
        <div class="upload-circle">
          ⬆
        </div>

        <div>
          <span class="link-text">
            Chọn ảnh
          </span>

          hoặc kéo ảnh
          <b>mặt sau</b>
          vào đây
        </div>

        <div class="upload-hint">
          Lưu ý: Kích thước ảnh lớn hơn 640 x 480
          <br/>
          định dạng .PNG, .JPG, .JPEG
        </div>
      `;

  if (dataUrl) {
    wrap.innerHTML = `
      <img
        src="${dataUrl}"
        alt="preview"
      />
    `;
  } else {
    wrap.innerHTML = `
      <div class="upload-placeholder">
        ${placeholderText}
      </div>
    `;
  }
}

// ============================================================
// AVATAR
// ============================================================

export function setAvatarImage(dataUrl) {
  const img = document.getElementById("avatarImg");

  const icon = document.getElementById("avatarPlaceholderIcon");

  if (!img || !icon) {
    console.warn("Không tìm thấy avatarImg hoặc avatarPlaceholderIcon");

    return;
  }

  if (dataUrl) {
    img.src = dataUrl;

    img.classList.remove("hidden");

    icon.classList.add("hidden");
  } else {
    img.classList.add("hidden");

    img.removeAttribute("src");

    icon.classList.remove("hidden");
  }
}

// ============================================================
// FILE → DATA URL
// ============================================================

function readFileAsDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();

    reader.onload = () => resolve(reader.result);

    reader.onerror = reject;

    reader.readAsDataURL(file);
  });

  updateBackAvailability();
}

// ============================================================
// NÉN ẢNH TRƯỚC KHI GỬI
// ============================================================
//
// Vercel giới hạn mỗi request khoảng 4.5MB → đưa cạnh dài về
// tối đa 1600px và nén JPEG 0.85. Nếu không đọc được ảnh thì
// giữ nguyên file để Backend báo lỗi như cũ.
// ============================================================

const MAX_IMAGE_EDGE = 1600;
const JPEG_QUALITY = 0.85;

async function decodeImage(file) {
  if (window.createImageBitmap) {
    try {
      return await createImageBitmap(file, { imageOrientation: "from-image" });
    } catch {
      // Thử lại bằng thẻ <img> bên dưới
    }
  }

  const url = URL.createObjectURL(file);
  try {
    const img = new Image();
    img.src = url;
    await img.decode();
    return img;
  } finally {
    URL.revokeObjectURL(url);
  }
}

async function compressImageFile(file) {
  let image;
  try {
    image = await decodeImage(file);
  } catch {
    return file;
  }

  const width = image.width || image.naturalWidth;
  const height = image.height || image.naturalHeight;
  const ratio = Math.min(1, MAX_IMAGE_EDGE / Math.max(width, height));

  const canvas = document.createElement("canvas");
  canvas.width = Math.round(width * ratio);
  canvas.height = Math.round(height * ratio);

  const context = canvas.getContext("2d");
  // Nền trắng cho ảnh PNG trong suốt
  context.fillStyle = "#fff";
  context.fillRect(0, 0, canvas.width, canvas.height);
  context.drawImage(image, 0, 0, canvas.width, canvas.height);
  image.close?.();

  const blob = await new Promise((resolve) =>
    canvas.toBlob(resolve, "image/jpeg", JPEG_QUALITY),
  );
  if (!blob) return file;

  // Ảnh JPEG đã nhỏ sẵn thì giữ bản gốc
  if (ratio === 1 && file.type === "image/jpeg" && blob.size >= file.size) {
    return file;
  }

  const baseName = (file.name || "cccd").replace(/\.[^.]+$/, "");
  return new File([blob], `${baseName}.jpg`, { type: "image/jpeg" });
}

// ============================================================
// UPLOAD PORTRAIT
// ============================================================

async function uploadPortraitTest(file, requestId) {
  try {
    if (!file) {
      return false;
    }

    console.log("Đang gửi ảnh CCCD để crop chân dung...");

    const formData = new FormData();

    formData.append("front", file);

    const result = await apiRequest("/api/portrait", {
      method: "POST",
      body: formData,
    });

    if (!result.success) {
      throw new Error(result.message || "Không crop được ảnh chân dung");
    }

    // ========================================================
    // KIỂM TRA REQUEST
    // ========================================================

    if (requestId !== portraitRequestId) {
      console.log("Bỏ qua kết quả ảnh cũ.");

      return false;
    }

    // ========================================================
    // BACKEND TRẢ IMAGE BASE64
    // ========================================================

    const portraitDataUrl = result.image_base64;

    if (!portraitDataUrl) {
      throw new Error("Backend không trả ảnh chân dung");
    }

    console.log("Đã nhận ảnh chân dung từ Backend.");

    // ========================================================
    // LƯU STATE
    // ========================================================

    state.portraitDataUrl = portraitDataUrl;

    // ========================================================
    // HIỂN THỊ AVATAR
    // ========================================================

    setAvatarImage(portraitDataUrl);
    setPortraitStatus("Đã quét chân dung", "success");

    console.log("Đã lưu ảnh chân dung vào state.");

    console.log("Đã đưa ảnh chân dung vào khung avatar.");
    return true;
  } catch (error) {
    // Request cũ thì bỏ qua lỗi
    if (requestId !== portraitRequestId) {
      return false;
    }

    console.error("Lỗi crop chân dung.", error?.code || error?.name || "Error");

    setPortraitStatus("Không thể quét chân dung", "error");
    alert("Không thể lấy ảnh chân dung.\n\n" + error.message);
    return false;
  }
}

// ============================================================
// OCR CCCD
// ============================================================
//
// Gửi hai mặt đã được người dùng xác nhận lên Backend.
// ============================================================

async function scanCCCD(requestId) {
  try {
    // ========================================================
    // KIỂM TRA
    // ========================================================

    if (!state.frontFile) {
      return;
    }

    if (!state.backFile) {
      return;
    }

    console.log("========================================");

    console.log("ĐÃ CÓ ĐỦ 2 MẶT CCCD");

    console.log("Bắt đầu OCR CCCD...");

    console.log("========================================");

    setOCRStatus("Đang quét thông tin trên CCCD", "loading");
    window.dispatchEvent(new Event("cccdOCRStarted"));

    // ========================================================
    // FORM DATA
    // ========================================================

    const formData = new FormData();

    formData.append("front", state.frontFile);

    formData.append("back", state.backFile);

    // ========================================================
    // GỌI API
    // ========================================================

    const result = await apiRequest("/api/ocr-cccd", {
      method: "POST",
      body: formData,
    });

    // ========================================================
    // KIỂM TRA REQUEST MỚI NHẤT
    // ========================================================

    if (requestId !== ocrRequestId) {
      console.log("Bỏ qua kết quả OCR cũ.");

      return;
    }

    // ========================================================
    // KIỂM TRA API
    // ========================================================

    if (!result.success) {
      throw new Error(result.message || "Không thể đọc thông tin CCCD.");
    }

    // ========================================================
    // LƯU KẾT QUẢ
    // ========================================================

    state.ocrData = result.data;
    setOCRStatus("Đã quét thông tin trên CCCD", "success");

    console.log("========================================");

    console.log("OCR CCCD THÀNH CÔNG");

    console.log("========================================");

    // ========================================================
    // PHÁT EVENT CHO APP.JS
    // ========================================================
    //
    // app.js sẽ nhận:
    //
    // event.detail = {
    //   ...OCR data...
    // }
    //
    // ========================================================

    window.dispatchEvent(
      new CustomEvent("cccdOCRCompleted", {
        detail: {
          ...state.ocrData,

          // Gửi kèm thông tin đã upload đủ hai mặt.
          // app.js sẽ dùng thông tin OCR để phân loại,
          // không tự khẳng định loại CCCD chỉ từ tên file.
          _cccdUpload: {
            hasFront: Boolean(state.frontFile),
            hasBack: Boolean(state.backFile),
          },
        },
      }),
    );
  } catch (error) {
    if (requestId !== ocrRequestId) {
      return;
    }

    console.error("Lỗi OCR CCCD.", error?.code || error?.name || "Error");

    setOCRStatus("Không thể quét thông tin trên CCCD", "error");
    alert("Không thể đọc thông tin CCCD.\n\n" + error.message);
  }
}

// ============================================================
// IMAGE SELECTED
// ============================================================

async function handleImageSelected(side, selectedFile) {
  if (!selectedFile) {
    return;
  }
  if (side === "back" && !state.portraitDataUrl) {
    alert("Vui lòng quét chân dung mặt trước thành công trước.");
    return;
  }
  window.dispatchEvent(new Event("cccdOCRStarted"));
  setOCRStatus("", "idle");
  const selectionRequestId = ++imageSelectionRequestId;
  ocrRequestId++;

  const file = await compressImageFile(selectedFile);

  // ==========================================================
  // MẶT TRƯỚC
  // ==========================================================

  if (side === "front") {
    // --------------------------------------------------------
    // REQUEST MỚI
    // --------------------------------------------------------

    portraitRequestId++;

    // --------------------------------------------------------
    // XÓA AVATAR
    // --------------------------------------------------------

    setAvatarImage(null);

    // --------------------------------------------------------
    // XÓA OCR CŨ
    // --------------------------------------------------------

    state.ocrData = null;
    state.backFile = null;
    state.backDataUrl = null;
    state.portraitDataUrl = null;
    renderPreview("back", null);
    updateBackAvailability();
    setPortraitStatus("", "idle");

    // --------------------------------------------------------
    // ĐỌC ẢNH
    // --------------------------------------------------------

    const dataUrl = await readFileAsDataUrl(file);

    // --------------------------------------------------------
    // BỎ QUA KẾT QUẢ CỦA LẦN CHỌN CŨ
    // --------------------------------------------------------

    if (selectionRequestId !== imageSelectionRequestId) {
      return;
    }

    // --------------------------------------------------------
    // LƯU STATE
    // --------------------------------------------------------

    state.frontFile = file;

    state.frontDataUrl = dataUrl;

    // --------------------------------------------------------
    // PREVIEW
    // --------------------------------------------------------

    renderPreview("front", dataUrl);
    showImageStage("front-review");

    return;
  }

  // ==========================================================
  // MẶT SAU
  // ==========================================================

  const dataUrl = await readFileAsDataUrl(file);

  if (selectionRequestId !== imageSelectionRequestId) {
    return;
  }

  state.backFile = file;

  state.backDataUrl = dataUrl;

  // Preview
  renderPreview("back", dataUrl);
  showImageStage("back-review");
}

// ============================================================
// INIT UPLOAD FORM
// ============================================================

export function initUploadForm() {
  const bindFileInput = (inputId, side) => {
    const input = document.getElementById(inputId);
    input?.addEventListener("change", async (event) => {
      const file = event.target.files?.[0];
      if (!file) return;

      await handleImageSelected(side, file);
      input.value = "";
    });
  };

  bindFileInput("frontImageInput", "front");
  bindFileInput("backImageInput", "back");

  document.querySelectorAll("[data-camera-side]").forEach((button) => {
    button.addEventListener("click", () => {
      void openLiveCamera(button.dataset.cameraSide);
    });
  });

  document.getElementById("cameraClose")?.addEventListener("click", closeLiveCamera);
  document.getElementById("cameraCancel")?.addEventListener("click", closeLiveCamera);
  document.getElementById("liveCameraModal")?.addEventListener("click", (event) => {
    if (event.target === event.currentTarget) closeLiveCamera();
  });
  document.getElementById("cameraCapture")?.addEventListener("click", () => {
    void captureLiveCamera(cameraSide).catch((error) => {
      console.error("Không thể chụp ảnh từ camera.", error);
      const status = document.getElementById("cameraStatus");
      if (status) {
        status.textContent = `Không thể chụp ảnh: ${error.message}`;
        status.classList.add("is-error");
      }
      const captureButton = document.getElementById("cameraCapture");
      if (captureButton) captureButton.disabled = false;
    });
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !document.getElementById("liveCameraModal")?.hidden) {
      closeLiveCamera();
    }
  });

  document.getElementById("reviewChooseAgain")?.addEventListener("click", () => {
    const inputId =
      imageReviewSide === "front" ? "frontImageInput" : "backImageInput";
    document.getElementById(inputId)?.click();
  });

  document.getElementById("reviewTakeAgain")?.addEventListener("click", () => {
    void openLiveCamera(imageReviewSide);
  });

  document.getElementById("reviewCancel")?.addEventListener("click", () => {
    if (imageReviewSide !== "front") return;

    imageSelectionRequestId++;
    portraitRequestId++;
    ocrRequestId++;
    state.frontFile = null;
    state.backFile = null;
    state.frontDataUrl = null;
    state.backDataUrl = null;
    state.portraitDataUrl = null;
    state.ocrData = null;
    renderPreview("front", null);
    renderPreview("back", null);
    updateBackAvailability();
    setAvatarImage(null);
    setPortraitStatus("", "idle");
    showImageStage(null);
  });

  document.getElementById("backToFrontReview")?.addEventListener("click", () => {
    showImageStage("front-review");
  });

  document.getElementById("backToFrontFromReview")?.addEventListener("click", () => {
    showImageStage("front-review");
  });

  document.getElementById("reviewContinue")?.addEventListener("click", async () => {
    if (isReviewProcessing) return;

    if (imageReviewSide === "front") {
      isReviewProcessing = true;
      showImageStage("front-processing");
      try {
        const portraitRequest = ++portraitRequestId;
        const portraitReady = await uploadPortraitTest(
          state.frontFile,
          portraitRequest,
        );
        if (portraitReady) {
          updateBackAvailability();
          showImageStage("back-select");
        } else if (portraitRequest === portraitRequestId) {
          showImageStage("front-review");
        }
      } finally {
        isReviewProcessing = false;
      }
      return;
    }

    if (!state.frontFile || !state.backFile || !state.portraitDataUrl) {
      return;
    }

    isReviewProcessing = true;
    const reviewButtons = document.querySelectorAll("#imageReview button");
    reviewButtons.forEach((button) => {
      button.disabled = true;
    });
    document.getElementById("reviewContinue").textContent = "Đang xử lý...";

    try {
      showImageStage("ocr-processing");
      const currentOCRRequest = ++ocrRequestId;
      await scanCCCD(currentOCRRequest);
    } finally {
      isReviewProcessing = false;
      reviewButtons.forEach((button) => {
        button.disabled = false;
      });
      showImageStage(null);
    }
  });

  showImageStage(null);

  // ==========================================================
  // DRAG & DROP
  // ==========================================================

  ["frontPreviewWrap", "backPreviewWrap"].forEach((id) => {
    const el = document.getElementById(id);

    if (!el) {
      return;
    }

    const side = id === "frontPreviewWrap" ? "front" : "back";

    el.addEventListener("dragover", (e) => {
      e.preventDefault();
    });

    el.addEventListener("drop", async (e) => {
      e.preventDefault();

      const file = e.dataTransfer.files?.[0];

      if (!file) {
        return;
      }

      await handleImageSelected(side, file);
    });
  });
}
