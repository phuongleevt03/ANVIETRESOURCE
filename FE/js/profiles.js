import {
  bindLogoutButton,
  requireAuthentication,
} from "./services/apiClient.js";
import {
  deleteFolderProfiles,
  getFolder,
  getProfilesInFolder,
} from "./services/recordsService.js";

const params = new URLSearchParams(window.location.search);
const folderId = params.get("folderId");
const folderTitle = document.getElementById("folderTitle");
const profileCount = document.getElementById("profileCount");
const profileTable = document.getElementById("profileTable");
const profileTableBody = document.getElementById("profileTableBody");
const emptyState = document.getElementById("emptyState");
const status = document.getElementById("statusLine");
const search = document.getElementById("profileSearch");
const selectAllButton = document.getElementById("selectAllBtn");
const deleteSelectedButton = document.getElementById("deleteSelectedBtn");
const selectedCount = document.getElementById("selectedCount");
const selectedIds = new Set();
const MAX_PROFILES = 50;
let profiles = [];

function profileValue(profile, ...fields) {
  for (const field of fields) {
    const value = profile[field];
    if (value !== undefined && value !== null && String(value).trim()) {
      return String(value);
    }
  }
  return "";
}

function exportCellStyle(column, headerRow) {
  const headerColors = [
    "8CCF50",
    "8CCF50",
    "8CCF50",
    "8CCF50",
    "A6D96A",
    "A6D96A",
    "8CCF50",
    "A6D96A",
    "8CCF50",
    "8CCF50",
    "8CCF50",
    "8CCF50",
    "8CCF50",
    "8CCF50",
    "FFC000",
    "FFC000",
    "00B0F0",
    "00B0F0",
    "00B0F0",
    "B7DEE8",
  ];
  const fillColor = headerRow
    ? headerColors[column]
    : column === 0
      ? "FFFF00"
      : null;

  return {
    font: {
      name: "Times New Roman",
      sz: 13,
      color: { rgb: "000000" },
    },
    alignment: {
      horizontal: "center",
      vertical: "center",
    },
    border: {
      top: { style: "thin", color: { rgb: "000000" } },
      bottom: { style: "thin", color: { rgb: "000000" } },
      left: { style: "thin", color: { rgb: "000000" } },
      right: { style: "thin", color: { rgb: "000000" } },
    },
    ...(fillColor
      ? {
          fill: {
            patternType: "solid",
            fgColor: { rgb: fillColor },
          },
        }
      : {}),
  };
}

function filteredProfiles() {
  const keyword = search.value.trim().toLocaleLowerCase("vi");
  if (!keyword) return profiles;
  return profiles.filter((profile) =>
    [
      profile.hoTen,
      profile.soGiayTo,
      profile.soCCCD,
      profile.dienThoai,
      profile.loaiTho,
      profile["loạiThợ"],
      profile.nhaThau,
      profile["nhàThầu"],
    ]
      .map((value) => String(value ?? "").toLocaleLowerCase("vi"))
      .some((value) => value.includes(keyword)),
  );
}

function setStatus(message, isError = false) {
  status.textContent = message;
  status.classList.toggle("error", isError);
}

function updateSelectionControls(rows) {
  const visibleIds = rows.map((profile) => String(profile.id));
  const allSelected =
    visibleIds.length > 0 && visibleIds.every((id) => selectedIds.has(id));
  selectAllButton.textContent = allSelected ? "Bỏ chọn tất cả" : "Chọn tất cả";
  deleteSelectedButton.disabled = selectedIds.size === 0;
  selectedCount.textContent = `${selectedIds.size} hồ sơ được chọn`;
}

function makeCell(text) {
  const cell = document.createElement("td");
  cell.textContent = text;
  return cell;
}

function render() {
  const rows = filteredProfiles();
  profileCount.textContent = `${profiles.length}/${MAX_PROFILES} hồ sơ`;
  profileTableBody.replaceChildren();
  profileTable.hidden = rows.length === 0;
  emptyState.hidden = rows.length > 0;
  emptyState.textContent = profiles.length
    ? "Không tìm thấy hồ sơ phù hợp."
    : "Chưa có hồ sơ trong thư mục này. Chọn “Quét/Tạo hồ sơ mới” để thêm.";

  rows.forEach((profile, index) => {
    const id = String(profile.id);
    const row = document.createElement("tr");
    const checkCell = document.createElement("td");
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = selectedIds.has(id);
    checkbox.setAttribute("aria-label", `Chọn hồ sơ ${profile.hoTen || id}`);
    checkbox.addEventListener("change", () => {
      if (checkbox.checked) selectedIds.add(id);
      else selectedIds.delete(id);
      updateSelectionControls(rows);
    });
    checkCell.append(checkbox);
    row.append(checkCell, makeCell(String(index + 1)));

    const photoCell = document.createElement("td");
    if (profile.portraitDataUrl) {
      const photo = document.createElement("img");
      photo.className = "profile-thumb";
      photo.alt = profile.hoTen ? `Ảnh ${profile.hoTen}` : "Ảnh hồ sơ";
      photo.src = profile.portraitDataUrl;
      photoCell.append(photo);
    } else {
      const placeholder = document.createElement("span");
      placeholder.className = "profile-thumb profile-thumb-empty";
      placeholder.textContent = "—";
      placeholder.setAttribute("aria-label", "Chưa có ảnh");
      photoCell.append(placeholder);
    }
    row.append(photoCell);
    row.append(
      makeCell(profile.hoTen || ""),
      makeCell(profileValue(profile, "soGiayTo", "soCCCD")),
      makeCell(profile.dienThoai || ""),
      makeCell(profileValue(profile, "loaiTho", "loạiThợ")),
      makeCell(profileValue(profile, "nhaThau", "nhàThầu")),
    );

    const actions = document.createElement("td");
    const actionGroup = document.createElement("div");
    actionGroup.className = "row-actions";
    const edit = document.createElement("a");
    edit.className = "btn btn-primary";
    edit.textContent = "Sửa";
    edit.href = `./index.html?folderId=${encodeURIComponent(folderId)}&profileId=${encodeURIComponent(id)}`;
    const remove = document.createElement("button");
    remove.className = "btn btn-danger";
    remove.type = "button";
    remove.textContent = "Xóa";
    remove.addEventListener("click", async () => {
      if (
        !confirm(`Bạn có chắc muốn xóa hồ sơ "${profile.hoTen || id}" không?`)
      )
        return;
      remove.disabled = true;
      try {
        await deleteFolderProfiles(folderId, [id]);
        selectedIds.delete(id);
        setStatus("Đã xóa hồ sơ.");
        await loadProfiles();
      } catch (error) {
        setStatus(`Không thể xóa hồ sơ: ${error.message}`, true);
        remove.disabled = false;
      }
    });
    actionGroup.append(edit, remove);
    actions.append(actionGroup);
    row.append(actions);
    profileTableBody.append(row);
  });

  updateSelectionControls(rows);
}

async function loadProfiles() {
  profileTable.hidden = true;
  emptyState.hidden = false;
  emptyState.textContent = "Đang tải hồ sơ...";
  try {
    profiles = await getProfilesInFolder(folderId);
    const existingIds = new Set(profiles.map((profile) => String(profile.id)));
    for (const id of selectedIds) {
      if (!existingIds.has(id)) selectedIds.delete(id);
    }
    setStatus("");
    render();
  } catch (error) {
    profiles = [];
    render();
    emptyState.textContent = `Không thể tải hồ sơ: ${error.message}`;
    setStatus("Lỗi tải hồ sơ từ máy chủ.", true);
  }
}

search.addEventListener("input", render);
selectAllButton.addEventListener("click", () => {
  const rows = filteredProfiles();
  const allSelected =
    rows.length > 0 &&
    rows.every((profile) => selectedIds.has(String(profile.id)));
  for (const profile of rows) {
    const id = String(profile.id);
    if (allSelected) selectedIds.delete(id);
    else selectedIds.add(id);
  }
  render();
});
deleteSelectedButton.addEventListener("click", async () => {
  if (!selectedIds.size) return;
  const ids = [...selectedIds];
  if (!confirm(`Bạn có chắc muốn xóa ${ids.length} hồ sơ đã chọn không?`))
    return;
  deleteSelectedButton.disabled = true;
  try {
    await deleteFolderProfiles(folderId, ids);
    selectedIds.clear();
    setStatus(`Đã xóa ${ids.length} hồ sơ.`);
    await loadProfiles();
  } catch (error) {
    setStatus(`Không thể xóa hồ sơ: ${error.message}`, true);
  } finally {
    deleteSelectedButton.disabled = false;
    updateSelectionControls(filteredProfiles());
  }
});
document.getElementById("exportProfilesBtn").addEventListener("click", () => {
  if (!window.XLSX) {
    setStatus(
      "Không tải được thư viện xuất Excel. Hãy kiểm tra kết nối mạng.",
      true,
    );
    return;
  }
  if (!profiles.length) {
    setStatus("Chưa có hồ sơ nào để xuất.");
    return;
  }
  const rows = profiles.map((profile, index) => {
    const namePrefix = String(profile.hoDem || "")
      .trim()
      .split(/\s+/)
      .filter(Boolean);
    const ho = profileValue(profile, "ho") || namePrefix.shift() || "";
    const dem = profileValue(profile, "dem") || namePrefix.join(" ");
    const places = [profile.noiSinh, profile.nguyenQuan]
      .map((value) => String(value || "").trim())
      .filter(
        (value, index, all) =>
          value &&
          all.findIndex(
            (item) =>
              item.toLocaleLowerCase("vi") === value.toLocaleLowerCase("vi"),
          ) === index,
      );
    return {
      STT: index + 1,
      Họ: ho,
      và: dem,
      Tên: profile.ten || "",
      "Ngày sinh": profile.ngaySinh || "",
      "Giới tính": profile.gioiTinh || "",
      "Nơi sinh / Nguyên quán": profile.noiSinhNguyenQuan || places.join(" / "),
      "Quốc tịch": profile.quocTich || "",
      SĐT: profile.dienThoai || "",
      "Số CCCD": profileValue(profile, "soGiayTo", "soCCCD"),
      "Ngày cấp": profile.ngayCap || "",
      "Nơi cấp": profile.noiCapGiayTo || "",
      "Địa chỉ thường trú": profile.diaChiHKTT || "",
      "Địa chỉ hiện tại": profile.diaChiHienNay || "",
      "Nhà thầu": profileValue(profile, "nhaThau", "nhàThầu"),
      "Loại thợ": profileValue(profile, "loaiTho", "loạiThợ"),
      "Bằng cấp/CC": profile.bangCapChungChi || "",
      "Kinh nghiệm": profile.kinhNghiem || "",
      "Nơi đào tạo": profile.noiDaoTao || "",
      Note: profile.note || profile.Note || "",
    };
  });
  const workbook = window.XLSX.utils.book_new();
  const worksheet = window.XLSX.utils.json_to_sheet(rows);
  const range = window.XLSX.utils.decode_range(worksheet["!ref"]);
  for (let row = range.s.r; row <= range.e.r; row += 1) {
    for (let column = range.s.c; column <= range.e.c; column += 1) {
      const address = window.XLSX.utils.encode_cell({ r: row, c: column });
      worksheet[address] ??= { t: "s", v: "" };
      worksheet[address].s = exportCellStyle(column, row === range.s.r);
    }
  }
  window.XLSX.utils.book_append_sheet(workbook, worksheet, "Danh sách hồ sơ");
  window.XLSX.writeFile(workbook, `danh_sach_ho_so_${Date.now()}.xlsx`);
});

try {
  const session = await requireAuthentication();
  if (session) {
    bindLogoutButton(document.getElementById("logoutButton"));
    if (!folderId) {
      window.location.replace("./folders.html");
    } else {
      const folder = await getFolder(folderId);
      folderTitle.textContent = folder.name;
      document.title = folder.name;
      document.getElementById("scanCreateBtn").href =
        `./index.html?folderId=${encodeURIComponent(folderId)}`;
      document.getElementById("scanCreateBtn").hidden =
        folder.profileCount >= MAX_PROFILES;
      if (folder.profileCount >= MAX_PROFILES) {
        setStatus(`Thư mục đã đạt giới hạn ${MAX_PROFILES} hồ sơ.`);
      }
      await loadProfiles();
    }
  }
} catch (error) {
  setStatus(`Không thể mở danh sách hồ sơ: ${error.message}`, true);
}
