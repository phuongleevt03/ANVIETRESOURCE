let csrfToken = null;
let sessionRequest = null;

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
  const requestOptions = { ...options, method, headers, credentials: "include" };
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
  try {
    response = await fetch(`${apiBase()}${path}`, requestOptions);
  } catch (error) {
    throw new Error(`Không kết nối được backend tại ${apiBase()}. Kiểm tra máy chủ và mạng.`, { cause: error });
  }
  const contentType = response.headers.get("Content-Type") || "";
  const payload = contentType.includes("application/json") ? await response.json() : null;
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
    sessionRequest = request("/api/auth/session")
      .then((payload) => {
        csrfToken = payload.authenticated ? payload.csrfToken : null;
        return payload;
      })
      .finally(() => {
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
