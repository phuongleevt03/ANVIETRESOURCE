let csrfToken = null;
let sessionRequest = null;
const SESSION_WAKE_RETRY_DELAYS = [0, 2000, 4000, 7000, 10000, 12000];
const SESSION_WAKE_REQUEST_TIMEOUT_MS = 8000;

function showBackendWakeNotice(message) {
  let notice = document.getElementById("backendWakeNotice");
  if (!notice) {
    notice = document.createElement("div");
    notice.id = "backendWakeNotice";
    notice.setAttribute("role", "status");
    notice.setAttribute("aria-live", "polite");
    notice.style.cssText =
      "position:fixed;z-index:10000;top:16px;left:50%;transform:translateX(-50%);" +
      "width:min(92vw,560px);padding:12px 16px;border-radius:10px;" +
      "background:#1e293b;color:#fff;box-shadow:0 8px 24px #0003;" +
      "font:500 14px/1.45 Arial,sans-serif;text-align:center";
    document.body.append(notice);
  }
  notice.textContent = message;
}

function hideBackendWakeNotice() {
  document.getElementById("backendWakeNotice")?.remove();
}

function isTransientSessionError(error) {
  return [0, 502, 503, 504].includes(error?.status);
}

function apiBase() {
  const configured = String(window.APP_CONFIG?.apiBase || "").trim();
  if (configured) return configured.replace(/\/$/, "");
  if (window.location.port === "8000") return window.location.origin;
  if (window.location.port === "5500") {
    const backend = new URL(window.location.origin);
    backend.port = "8000";
    return backend.origin;
  }
  if (["localhost", "127.0.0.1", "::1", "[::1]"].includes(window.location.hostname)) {
    const host = window.location.hostname.includes(":")
      ? `[${window.location.hostname.replace(/^\[|\]$/g, "")}]`
      : window.location.hostname;
    return `http://${host}:8000`;
  }
  // Production: Flask (Render) hoặc Vercel (chuyển tiếp /api sang Render) cùng origin
  return window.location.origin;
}

async function request(path, options = {}) {
  const method = String(options.method || "GET").toUpperCase();
  const headers = new Headers(options.headers || {});
  const { timeoutMs, ...fetchOptions } = options;
  const requestOptions = { ...fetchOptions, method, headers, credentials: "include" };
  delete requestOptions.skipCsrf;
  if (options.body !== undefined && !(options.body instanceof FormData) && typeof options.body !== "string") {
    headers.set("Content-Type", "application/json");
    requestOptions.body = JSON.stringify(options.body);
  }
  if (!["GET", "HEAD", "OPTIONS"].includes(method) && !options.skipCsrf) {
    if (!csrfToken) await getSession();
    if (!csrfToken) throw new Error("Phiên đăng nhập đã hết hạn. Vui lòng đăng nhập lại.");
    headers.set("X-CSRF-Token", csrfToken);
  }
  let response;
  let timeoutId;
  if (timeoutMs) {
    const controller = new AbortController();
    requestOptions.signal = controller.signal;
    timeoutId = window.setTimeout(() => controller.abort(), timeoutMs);
  }
  try {
    response = await fetch(`${apiBase()}${path}`, requestOptions);
  } catch (error) {
    const requestError = new Error(
      `Không kết nối được backend tại ${apiBase()}. Kiểm tra máy chủ và mạng.`,
      { cause: error },
    );
    requestError.status = 0;
    requestError.code =
      error?.name === "AbortError" ? "REQUEST_TIMEOUT" : "NETWORK_ERROR";
    throw requestError;
  } finally {
    if (timeoutId) window.clearTimeout(timeoutId);
  }
  const contentType = response.headers.get("Content-Type") || "";
  let payload = null;
  if (contentType.includes("application/json")) {
    try {
      payload = await response.json();
    } catch (error) {
      const parseError = new Error("Backend trả về phản hồi không hợp lệ.", {
        cause: error,
      });
      parseError.status = response.status;
      parseError.code = "INVALID_RESPONSE";
      throw parseError;
    }
  }
  if (response.status === 401) {
    csrfToken = null;
    window.dispatchEvent(new Event("api:unauthorized"));
  }
  if (!response.ok) {
    const error = new Error(payload?.message || `Yêu cầu thất bại (HTTP ${response.status}).`);
    error.status = response.status;
    error.code = payload?.code || "API_ERROR";
    throw error;
  }
  return payload;
}

window.addEventListener("api:unauthorized", () => {
  if (window.location.pathname.endsWith("/login.html")) return;
  const next = `${window.location.pathname}${window.location.search}${window.location.hash}`;
  const url = new URL("./login.html", window.location.href);
  url.searchParams.set("next", next);
  window.location.replace(url.href);
});

export async function getSession() {
  if (!sessionRequest) {
    sessionRequest = (async () => {
      const wakeNoticeTimer = window.setTimeout(() => {
        showBackendWakeNotice(
          "Đang kết nối và đánh thức máy chủ Render. Lần đầu có thể mất khoảng một phút...",
        );
      }, 4000);

      try {
        for (let attempt = 0; attempt < SESSION_WAKE_RETRY_DELAYS.length; attempt += 1) {
          const delay = SESSION_WAKE_RETRY_DELAYS[attempt];
          if (delay) {
            await new Promise((resolve) => window.setTimeout(resolve, delay));
          }
          if (attempt > 0) {
            showBackendWakeNotice(
              `Máy chủ đang khởi động; đang tự thử lại (${attempt}/${SESSION_WAKE_RETRY_DELAYS.length - 1})...`,
            );
          }

          try {
            const payload = await request("/api/auth/session", {
              timeoutMs: SESSION_WAKE_REQUEST_TIMEOUT_MS,
            });
            if (!payload || typeof payload.authenticated !== "boolean") {
              const responseError = new Error(
                "Backend chưa sẵn sàng trả dữ liệu phiên đăng nhập.",
              );
              responseError.status = 502;
              responseError.code = "INVALID_SESSION_RESPONSE";
              throw responseError;
            }
            csrfToken = payload.authenticated ? payload.csrfToken : null;
            return payload;
          } catch (error) {
            if (
              !isTransientSessionError(error) ||
              attempt === SESSION_WAKE_RETRY_DELAYS.length - 1
            ) {
              throw error;
            }
          }
        }
      } catch (error) {
        if (isTransientSessionError(error)) {
          const wakeError = new Error(
            "Máy chủ Render vẫn đang khởi động hoặc tạm thời không phản hồi. " +
              "Vui lòng đợi một chút rồi tải lại trang.",
            { cause: error },
          );
          wakeError.status = error.status;
          wakeError.code = error.code;
          throw wakeError;
        }
        throw error;
      } finally {
        window.clearTimeout(wakeNoticeTimer);
        hideBackendWakeNotice();
      }
    })().finally(() => {
      sessionRequest = null;
    });
  }
  return sessionRequest;
}

export async function requireAuthentication() {
  const session = await getSession();
  if (session.authenticated) return session;
  const next = `${window.location.pathname}${window.location.search}${window.location.hash}`;
  const url = new URL("./login.html", window.location.href);
  url.searchParams.set("next", next);
  window.location.replace(url.href);
  return null;
}

export async function login(email, password) {
  const payload = await request("/api/auth/login", {
    method: "POST",
    body: { email, password },
    skipCsrf: true,
  });
  csrfToken = payload.csrfToken;
  return payload;
}

export async function logout() {
  await request("/api/auth/logout", { method: "POST" });
  csrfToken = null;
}

export function bindLogoutButton(button) {
  if (!button) return;
  button.addEventListener("click", async () => {
    button.disabled = true;
    try {
      await logout();
      window.location.replace("./login.html");
    } catch (error) {
      button.disabled = false;
      window.alert(`Không thể đăng xuất: ${error.message}`);
    }
  });
}

export function apiRequest(path, options = {}) {
  return request(path, options);
}
