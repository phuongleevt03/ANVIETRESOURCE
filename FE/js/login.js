import { getSession, login } from "./services/apiClient.js";

const form = document.getElementById("loginForm");
const errorElement = document.getElementById("loginError");
const submitButton = document.getElementById("loginButton");
const passwordInput = document.getElementById("password");
const togglePasswordButton = document.getElementById("togglePassword");

togglePasswordButton.addEventListener("click", () => {
  const shouldShowPassword = passwordInput.type === "password";
  passwordInput.type = shouldShowPassword ? "text" : "password";
  togglePasswordButton.setAttribute("aria-pressed", String(shouldShowPassword));
  togglePasswordButton.setAttribute(
    "aria-label",
    shouldShowPassword ? "Ẩn mật khẩu" : "Hiện mật khẩu",
  );
});

function getSafeNextUrl() {
  const candidate = new URLSearchParams(window.location.search).get("next") || "./folders.html";
  const target = new URL(candidate, window.location.href);
  if (target.origin !== window.location.origin) return "./folders.html";
  if (
    target.pathname === "/" ||
    target.pathname.endsWith("/index.html") && !target.searchParams.has("folderId") ||
    target.pathname.endsWith("/login.html")
  ) {
    return "./folders.html";
  }
  return target.href;
}

try {
  const session = await getSession();
  if (session.authenticated) window.location.replace(getSafeNextUrl());
} catch (error) {
  errorElement.textContent = error.message;
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  errorElement.textContent = "";
  submitButton.disabled = true;
  try {
    await login(document.getElementById("email").value, document.getElementById("password").value);
    window.location.replace(getSafeNextUrl());
  } catch (error) {
    errorElement.textContent = error.message;
  } finally {
    submitButton.disabled = false;
  }
});
