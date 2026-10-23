import { requireAuthentication } from "./services/apiClient.js";
import { getFolder } from "./services/recordsService.js";

const folderId = new URLSearchParams(window.location.search).get("folderId");
const profileId = new URLSearchParams(window.location.search).get("profileId");
const saveButton = document.getElementById("saveProfileBtn");

if (profileId) saveButton.disabled = true;

try {
  const session = await requireAuthentication();
  if (session) {
    if (!folderId) {
      window.location.replace("./folders.html");
    } else {
      const folder = await getFolder(folderId);
      document.getElementById("folderTitle").textContent = folder.name;
      document.title = `Quét CCCD - ${folder.name}`;
      if (profileId) {
        document.getElementById("extractTitle").textContent =
          "Sửa hồ sơ nhân sự";
      }
      if (!profileId && folder.profileCount >= 50) {
        document.getElementById("pageError").textContent =
          "Thư mục đã đạt giới hạn 50 hồ sơ.";
        saveButton.disabled = true;
      }
      await import("./app.js?v=20261023-compress-upload");
    }
  }
} catch (error) {
  document.getElementById("pageError").textContent =
    `Không thể mở trang quét CCCD: ${error.message}`;
  saveButton.disabled = true;
}
