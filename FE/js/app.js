import {
  initUploadForm,
  getUploadState,
  setAvatarImage,
  cancelOCRProcessing,
} from "./components/uploadForm.js?v=20261023-compress-upload";
import {
  initProfileForm,
  getFormData,
  setFormData,
} from "./components/profileForm.js?v=20261021-combined-place-ocr-fix";
import {
  saveProfile,
  getProfileById,
} from "./components/storage.js?v=20261021-full-address-district";
import {
  initAddressData,
  initAddressCombobox,
  initCountryCombobox,
  getProvinces,
} from "./components/addressForm.js?v=20261020-multi-value-combobox";
import {
  loadAddressMapping,
  findExactAdministrativeNameInAddress,
  isCurrentAddress,
  normalizeAddressText,
  normalizeAdministrativeName,
} from "./addressMapping.js?v=20261021-address-exact-resolution";

// ============================================================
// STATE
// ============================================================

let editingId = null;
let originalProfile = null;
let pendingCreateId = crypto.randomUUID();
let addressReady = null;
let addressMappingRecords = [];
let verificationNeedsReview = false;
let lastMappedCurrentAddressData = null;
let lastScannedHKTTData = null;
let lastMappedHKTTData = null;
let lastAddressMappingView = null;
const pageParams = new URLSearchParams(window.location.search);
const folderId = pageParams.get("folderId");
const profileId = pageParams.get("profileId");

// ============================================================
// NORMALIZATION
// ============================================================

function normalizeText(value) {
  return normalizeAddressText(value);
}

function sameAdminName(a, b, kind = "") {
  const left = normalizeAdministrativeName(a, kind);
  const right = normalizeAdministrativeName(b, kind);
  return Boolean(left && right && left === right);
}

// So sánh tỉnh theo dữ liệu mapping, không gắn cứng tên tỉnh nào.
function sameOldProvinceName(ocrProvince, mappingProvince, fullAddress = "") {
  const ocrKey = normalizeAdministrativeName(ocrProvince, "province");
  const mappingKey = normalizeAdministrativeName(mappingProvince, "province");

  if (ocrKey && mappingKey && ocrKey === mappingKey) return true;

  return containsAdministrativeName(fullAddress || ocrProvince, mappingProvince, "province");
}

function isAdministrativeAcronym(value, administrativeName, kind) {
  const valueKey = normalizeAdministrativeName(value, kind).replace(/\s+/g, "");
  const nameKey = normalizeAdministrativeName(administrativeName, kind);
  if (!valueKey || !nameKey || valueKey.length < 2) return false;
  const initials = nameKey
    .split(" ")
    .filter(Boolean)
    .map((word) => word[0])
    .join("");
  return valueKey === initials;
}

function containsAdministrativeName(text, administrativeName, kind) {
  const normalizedText = normalizeText(text);
  const key = normalizeAdministrativeName(administrativeName, kind);
  if (!normalizedText || !key) return false;
  if (` ${normalizedText} `.includes(` ${key} `)) return true;

  const parts = String(text || "")
    .split(",")
    .map((part) => part.trim())
    .filter(Boolean);
  return parts.some((part) =>
    isAdministrativeAcronym(part, administrativeName, kind),
  );
}

function escapeRegExp(value) {
  return String(value).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function firstNonEmpty(...values) {
  for (const value of values) {
    if (value === undefined || value === null) continue;

    if (typeof value === "string") {
      if (value.trim()) return value.trim();
      continue;
    }

    if (typeof value === "object") {
      if (Object.keys(value).length) return value;
      continue;
    }

    return value;
  }

  return "";
}

// ============================================================
// SCAN PAGE
// ============================================================

function closeExtractModal() {
  if (cancelOCRProcessing()) return;

  window.location.assign(`./profiles.html?folderId=${encodeURIComponent(folderId)}`);
}

function handleCloseExtractClick() {
  if (cancelOCRProcessing()) return;

  const destination = document.body.classList.contains("image-review-active")
    ? "./index.html"
    : "./profiles.html";
  window.location.assign(
    `${destination}?folderId=${encodeURIComponent(folderId)}`,
  );
}

document
  .getElementById("closeExtractBtn")
  ?.addEventListener("click", handleCloseExtractClick);

document
  .getElementById("cancelBtn")
  ?.addEventListener("click", closeExtractModal);

document
  .getElementById("applyHKTTMappingBtn")
  ?.addEventListener("click", () => {
    if (!lastMappedHKTTData || !lastAddressMappingView) return;
    setFormData(lastMappedHKTTData);
    syncFullAddressFields();
    showAddressMapping({ ...lastAddressMappingView, applied: true });
  });

document
  .getElementById("restoreScannedHKTTBtn")
  ?.addEventListener("click", () => {
    if (!lastScannedHKTTData || !lastAddressMappingView) return;
    setFormData(lastScannedHKTTData);
    syncFullAddressFields();
    showAddressMapping({ ...lastAddressMappingView, applied: false });
  });

document
  .getElementById("autoFillMappedAddress")
  ?.addEventListener("change", (event) => {
    if (event.currentTarget.checked) {
      fillCurrentAddress(lastMappedCurrentAddressData);
    }
  });

// ============================================================
// NORMALIZE OCR PAYLOAD
// ============================================================

function hasOCRFields(obj) {
  if (!obj || typeof obj !== "object") return false;

  const keys = [
    "soGiayTo",
    "hoTen",
    "HKTT",
    "hktt",
    "noiThuongTru",
    "noiThườngTrú",
    "thuongTru",
    "noiCuTru",
    "noiCưTru",
    "residence",
    "permanentAddress",
    "address",
    "diaChiHKTT",
    "Nơi thường trú",
    "Nơi thường trú:",
    "Nơi cư trú",
    "Nơi cư trú:",
  ];

  return keys.some((key) => obj[key] !== undefined && obj[key] !== null);
}

function preserveVerificationMetadata(data, ...envelopes) {
  if (!data || typeof data !== "object" || data.verification) {
    return data;
  }

  const verification = envelopes.find(
    (envelope) => envelope?.verification && typeof envelope.verification === "object",
  )?.verification;
  return verification ? { ...data, verification } : data;
}

function normalizeOCRData(eventDetail) {
  if (!eventDetail || typeof eventDetail !== "object") {
    return null;
  }

  if (hasOCRFields(eventDetail)) {
    return eventDetail;
  }

  if (eventDetail.data && typeof eventDetail.data === "object") {
    const data = eventDetail.data;

    if (hasOCRFields(data)) {
      return preserveVerificationMetadata(data, eventDetail);
    }

    if (data.data && typeof data.data === "object") {
      return preserveVerificationMetadata(data.data, data, eventDetail);
    }
  }

  return eventDetail;
}

// ============================================================
// OCR ADDRESS HELPERS
// ============================================================

// Ưu tiên nhãn trên CCCD mới/cũ trước các trường dữ liệu chung.

function getOCRAddressSource(data) {
  if (!data || typeof data !== "object") return {};

  const source = firstNonEmpty(
    data["Nơi cư trú"],
    data["Nơi cư trú:"],
    data.noiCuTru,
    data.noiCưTrú,
    data["Nơi thường trú"],
    data["Nơi thường trú:"],
    data.noiThuongTru,
    data.noiThườngTrú,
    data.thuongTru,
    data.residence,
    data.permanentAddress,
    data.diaChiHKTT,
    data.HKTT,
    data.hktt,
    data.address,
  );

  if (typeof source === "string") {
    return {
      diaChi: source.trim(),
      diaChiGoc: source.trim(),
      quocGia: "Việt Nam",
      tinh: "",
      quanHuyen: "",
      phuongXa: "",
      soNha: "",
    };
  }

  if (!source || typeof source !== "object") return {};

  const province = firstNonEmpty(
    source.tinh,
    source.tinhThanh,
    source.province,
    source.provinceName,
  );

  const district = firstNonEmpty(
    source.quanHuyen,
    source.quan,
    source.huyen,
    source.district,
    source.districtName,
  );

  const ward = firstNonEmpty(
    source.phuongXa,
    source.phuong,
    source.xa,
    source.ward,
    source.wardName,
  );

  // Chỉ lấy trường địa chỉ chi tiết, không lấy nguyên chuỗi địa chỉ.
  const house = firstNonEmpty(
    source.soNha,
    source.diaChiChiTiet,
    source.addressDetail,
    source.houseNumber,
    source.streetAddress,
  );

  const rawAddress = firstNonEmpty(
    source.diaChiGoc,
    source.fullAddress,
    source.diaChi,
    source.address,
    data.diaChiGoc,
    data.diaChiHKTT,
    data.fullAddress,
    data.address,
    [house, ward, district, province].filter(Boolean).join(", "),
  );

  return {
    ...source,
    quocGia: firstNonEmpty(source.quocGia, source.country, "Việt Nam"),
    tinh: province,
    quanHuyen: district,
    phuongXa: ward,
    soNha: house,
    diaChiGoc: rawAddress,
  };
}

function getOCRDistrict(hktt) {
  return String(
    hktt?.quanHuyen ?? hktt?.quan ?? hktt?.huyen ?? hktt?.district ?? "",
  ).trim();
}

function getOCRWard(hktt) {
  return String(
    hktt?.phuongXa ?? hktt?.phuong ?? hktt?.xa ?? hktt?.ward ?? "",
  ).trim();
}

function getOCRProvince(hktt) {
  return String(hktt?.tinh ?? hktt?.tinhThanh ?? hktt?.province ?? "").trim();
}

function getOCRHouse(hktt) {
  const house = String(
    hktt?.soNha ??
      hktt?.diaChiChiTiet ??
      hktt?.addressDetail ??
      hktt?.houseNumber ??
      hktt?.streetAddress ??
      "",
  ).trim();

  if (house) return house;

  // Chỉ dùng chuỗi gốc làm dự phòng khi không có địa chỉ chi tiết.
  return String(
    hktt?.diaChiGoc ?? hktt?.fullAddress ?? hktt?.diaChi ?? hktt?.address ?? "",
  ).trim();
}

function getOCRCountry(hktt) {
  return String(hktt?.quocGia ?? hktt?.country ?? "Việt Nam").trim();
}

function normalizeOCRWard(value) {
  let ward = String(value ?? "")
    .normalize("NFC")
    .trim()
    .replace(/\s+/g, " ");

  if (!ward) return "";

  ward = ward.split(/[,;|]/, 1)[0].trim();
  ward = ward.replace(/^p\.(?=\S)/i, "Phường ");
  ward = ward.replace(/^p\s+(?=\S)/i, "Phường ");
  ward = ward.replace(/^x\.(?=\S)/i, "Xã ");
  ward = ward.replace(/^x\s+(?=\S)/i, "Xã ");
  ward = ward.replace(/^tt\.(?=\S)/i, "Thị trấn ");
  ward = ward.replace(/^tt\s+(?=\S)/i, "Thị trấn ");

  return ward.replace(/\s+/g, " ").trim();
}

// ============================================================
// ADDRESS MATCHING HELPERS
// ============================================================

function getProvincesSafely() {
  const provinces = getProvinces();
  return Array.isArray(provinces) ? provinces : [];
}

function findCurrentProvinceFromName(name) {
  if (!name) return null;

  const matches = getProvincesSafely().filter((item) =>
    sameAdminName(item?.name?.local, name, "province"),
  );

  return matches.length === 1 ? matches[0].name.local : null;
}

function findCurrentWard(provinceName, wardName) {
  if (!provinceName || !wardName) return null;

  const provinceMatches = getProvincesSafely().filter((item) =>
    sameAdminName(item?.name?.local, provinceName, "province"),
  );

  if (provinceMatches.length !== 1) return null;

  const wards = provinceMatches[0].ward;
  if (!Array.isArray(wards)) return null;

  const matches = wards.filter((item) =>
    sameAdminName(item?.name?.local, wardName, "ward"),
  );

  return matches.length === 1 ? matches[0].name.local : null;
}

function findCurrentProvinceInText(text) {
  const normalizedText = normalizeText(text);
  if (!normalizedText) return null;

  const matches = getProvincesSafely().filter((province) => {
    const name = province?.name?.local;
    const normalizedName = normalizeText(name);

    return (
      normalizedName && ` ${normalizedText} `.includes(` ${normalizedName} `)
    );
  });

  return matches.length === 1 ? matches[0].name.local : null;
}

function findCurrentWardInText(provinceName, text) {
  if (!provinceName || !text) return null;

  const province = getProvincesSafely().find((item) =>
    sameAdminName(item?.name?.local, provinceName, "province"),
  );

  if (!Array.isArray(province?.ward)) return null;

  const normalizedText = normalizeText(text);

  const matches = province.ward.filter((ward) => {
    const name = ward?.name?.local;
    const normalizedName = normalizeText(name);

    return (
      normalizedName && ` ${normalizedText} `.includes(` ${normalizedName} `)
    );
  });

  return matches.length === 1 ? matches[0].name.local : null;
}

function findOldNamesInsideAddress(addressText, kind) {
  const text = normalizeText(addressText);
  if (!text) return [];
  const addressParts = String(addressText || "")
    .split(",")
    .map((part) => part.trim())
    .filter(Boolean);

  const fieldName =
    kind === "province"
      ? "oldProvince"
      : kind === "ward"
        ? "oldWard"
        : "oldDistrict";
  const unique = new Map();

  for (const record of addressMappingRecords) {
    const originalName = String(record?.[fieldName] || "").trim();
    if (!originalName) continue;

    const key = normalizeAdministrativeName(originalName, kind);
    if (!key || unique.has(key)) continue;

    const safeSubstringMatch =
      key.length >= 6 &&
      !/^\d+$/.test(key) &&
      ` ${text} `.includes(` ${key} `);
    if (
      addressParts.some((part) => sameAdminName(part, originalName, kind)) ||
      addressParts.some((part) =>
        isAdministrativeAcronym(part, originalName, kind),
      ) ||
      safeSubstringMatch
    ) {
      unique.set(key, originalName);
    }
  }

  return [...unique.values()];
}

// ============================================================
// PARSE ADDRESS TEXT
// ============================================================

function parseAddressText(rawAddress) {
  const text = String(rawAddress || "")
    .normalize("NFC")
    .replace(/\s+/g, " ")
    .trim();

  if (!text) return null;

  const parts = text
    .split(",")
    .map((part) => part.trim())
    .filter(Boolean);

  let province = findCurrentProvinceInText(text);

  if (!province && parts.length) {
    province = findCurrentProvinceFromName(parts[parts.length - 1]);
  }

  if (province) {
    const ward = findCurrentWardInText(province, text);

    if (ward) {
      let remainder = text;

      for (const name of [province, ward]) {
        const escaped = escapeRegExp(name);
        remainder = remainder.replace(
          new RegExp(`(^|,\\s*)${escaped}(?=\\s*(,|$))`, "i"),
          " ",
        );
      }

      remainder = remainder
        .split(",")
        .map((part) => part.trim())
        .filter(Boolean)
        .join(", ")
        .replace(/^[,\s]+|[,\s]+$/g, "")
        .trim();

      return {
        quocGia: "Việt Nam",
        tinh: province,
        quanHuyen: "",
        phuongXa: ward,
        soNha: remainder,
        diaChiGoc: text,
      };
    }
  }

  return null;
}

// ============================================================
// PARSE OLD ADDRESS
// ============================================================

function parseOldAddress(hktt) {
  const rawProvince = getOCRProvince(hktt);
  let rawDistrict = getOCRDistrict(hktt);
  const rawWard = normalizeOCRWard(getOCRWard(hktt));
  const rawHouse = getOCRHouse(hktt);
  const rawFullAddress = String(hktt?.diaChiGoc || "").trim();
  const addressProvince = findExactAdministrativeNameInAddress(
    rawFullAddress,
    addressMappingRecords,
    "oldProvince",
    "province",
  );
  const addressDistrict = findExactAdministrativeNameInAddress(
    rawFullAddress,
    addressMappingRecords,
    "oldDistrict",
    "district",
  );
  const addressWard = findExactAdministrativeNameInAddress(
    rawFullAddress,
    addressMappingRecords,
    "oldWard",
    "ward",
  );

  // So sánh tên tỉnh/huyện sau khi chuẩn hóa văn bản.
  // Nếu huyện trùng tỉnh thì coi như OCR đã điền nhầm.
  if (
    rawDistrict &&
    rawProvince &&
    normalizeText(rawDistrict) === normalizeText(rawProvince)
  ) {
    rawDistrict = "";
  }

  const fullText = [rawHouse, rawWard, rawDistrict, rawProvince, rawFullAddress]
    .filter(Boolean)
    .join(", ");

  const provinceNamesInAddress = findOldNamesInsideAddress(
    fullText,
    "province",
  );

  const districtNamesInAddress = findOldNamesInsideAddress(
    fullText,
    "district",
  );
  const wardNamesInAddress = findOldNamesInsideAddress(fullText, "ward");

  const resolveMappedName = (rawName, names, kind, field) => {
    const knownNames = new Map();
    for (const record of addressMappingRecords) {
      const name = String(record?.[field] || "").trim();
      const key = normalizeAdministrativeName(name, kind);
      if (key && !knownNames.has(key)) knownNames.set(key, name);
    }
    const exact = [...knownNames.values()].filter((name) =>
      sameAdminName(rawName, name, kind),
    );
    if (exact.length === 1) return exact[0];
    if (names.length === 1) return names[0];
    return rawName;
  };

  const oldProvince =
    addressProvince ||
    resolveMappedName(
      rawProvince,
      provinceNamesInAddress,
      "province",
      "oldProvince",
    );

  const oldDistrict =
    addressDistrict ||
    resolveMappedName(
      rawDistrict,
      districtNamesInAddress,
      "district",
      "oldDistrict",
    );

  const oldWard =
    addressWard ||
    resolveMappedName(rawWard, wardNamesInAddress, "ward", "oldWard");

  return {
    rawProvince,
    rawDistrict,
    rawWard,
    rawHouse,
    fullText,
    provinceNamesInAddress,
    districtNamesInAddress,
    oldProvince,
    oldDistrict,
    oldWard,
  };
}

// ============================================================
// CLEAN CURRENT HOUSE ADDRESS
// ============================================================

function cleanCurrentHouseAddress(
  address,
  oldWard,
  oldDistrict,
  oldProvince,
  mapping = null,
) {
  let result = String(address || "")
    .normalize("NFC")
    .replace(/\s+/g, " ")
    .replace(/\s*,\s*/g, ", ")
    .trim();

  if (!result) return "";

  const administrativeParts = [
    oldWard,
    mapping?.oldWard,
    oldDistrict,
    mapping?.oldDistrict,
    oldProvince,
    mapping?.oldProvince,
    ...findOldNamesInsideAddress(result, "district"),
    ...findOldNamesInsideAddress(result, "province"),
  ].filter(Boolean);

  const administrativeKeys = new Set(
    administrativeParts.map((part) => normalizeText(part)),
  );

  const administrativeNames = [
    [oldWard, "ward"],
    [mapping?.oldWard, "ward"],
    [oldDistrict, "district"],
    [mapping?.oldDistrict, "district"],
    [oldProvince, "province"],
    [mapping?.oldProvince, "province"],
    ...findOldNamesInsideAddress(result, "ward").map((name) => [name, "ward"]),
    ...findOldNamesInsideAddress(result, "district").map((name) => [name, "district"]),
    ...findOldNamesInsideAddress(result, "province").map((name) => [name, "province"]),
  ].filter(([name]) => name);

  result = result
    .split(",")
    .map((part) => part.trim())
    .filter(
      (part) =>
        !administrativeKeys.has(normalizeText(part)) &&
        !administrativeNames.some(([name, kind]) =>
          sameAdminName(part, name, kind),
        ),
    )
    .join(", ");

  const districtNames = [
    oldDistrict,
    mapping?.oldDistrict,
    ...findOldNamesInsideAddress(result, "district"),
  ].filter(Boolean);

  const provinceNames = [
    oldProvince,
    mapping?.oldProvince,
    ...findOldNamesInsideAddress(result, "province"),
  ].filter(Boolean);

  const oldParts = [
    oldWard,
    mapping?.oldWard,
    ...districtNames,
    ...provinceNames,
  ]
    .filter(Boolean)
    .map((part) => String(part).normalize("NFC").trim())
    .filter(
      (part, index, array) =>
        array.findIndex(
          (other) => normalizeText(other) === normalizeText(part),
        ) === index,
    )
    .sort((a, b) => b.length - a.length);

  let changed = true;

  while (changed) {
    changed = false;

    for (const part of oldParts) {
      const escaped = escapeRegExp(part);
      const pattern = new RegExp(`(?:,\\s*|\\s+)${escaped}\\s*$`, "i");

      if (pattern.test(result)) {
        result = result.replace(pattern, "").trim();
        result = result.replace(/[,;\s]+$/g, "").trim();
        changed = true;
      }
    }
  }

  return result
    .replace(/\s*,\s*/g, ", ")
    .replace(/^[,;\s]+|[,;\s]+$/g, "")
    .trim();
}

// ============================================================
// CHECK WHETHER ADDRESS IS ALREADY CURRENT
// ============================================================

function checkCurrentAddress(hktt) {
  const rawProvince = getOCRProvince(hktt);
  const rawWard = normalizeOCRWard(getOCRWard(hktt));

  if (!rawProvince || !rawWard) return null;

  const provinces = getProvincesSafely();

  if (!provinces.length) {
    console.warn("Chưa tải được hierarchy.json.");
    return null;
  }

  const currentProvince = findCurrentProvinceFromName(rawProvince);
  if (!currentProvince) return null;

  const currentWard = findCurrentWard(currentProvince, rawWard);
  if (!currentWard) return null;

  if (!isCurrentAddress(currentProvince, currentWard, provinces)) {
    return null;
  }

  return {
    province: currentProvince,
    ward: currentWard,
  };
}

// ============================================================
// NORMALIZE MAPPING RESULT
// ============================================================

function uniqueMappingTargets(records) {
  const unique = new Map();

  for (const item of records || []) {
    if (!item?.newProvince || !item?.newWard) continue;

    const key = `${normalizeAdministrativeName(
      item.newProvince,
      "province",
    )}|${normalizeAdministrativeName(item.newWard, "ward")}`;

    if (!unique.has(key)) {
      unique.set(key, item);
    }
  }

  return [...unique.values()];
}

function formatMappingResult(item) {
  return {
    status: "matched",
    oldProvince: item.oldProvince || "",
    oldDistrict: item.oldDistrict || "",
    oldWard: item.oldWard || "",
    currentProvince: item.newProvince || "",
    currentWard: item.newWard || "",
    newProvince: item.newProvince || "",
    newWard: item.newWard || "",
    source: item.source || "addressMapping.json",
    confidence: item.confidence || "exact",
    record: item,
  };
}

// ============================================================
// FIND MAPPING FOR HKTT
// ============================================================

function findMappingForHKTT(hktt) {
  const parsed = parseOldAddress(hktt);

  const normalize = (value, kind = "") =>
    normalizeAdministrativeName(value, kind);

  const wardKey = normalize(parsed.oldWard, "ward");
  const provinceKey = normalize(parsed.oldProvince, "province");
  const districtKey = normalize(parsed.oldDistrict, "district");

  const addressContainsName = (name, kind) => {
    const key = normalize(name, kind);
    if (!key) return false;
    return (
      containsAdministrativeName(parsed.fullText, name, kind) ||
      (key.length >= 6 &&
        !/^\d+$/.test(key) &&
        ` ${normalizeText(parsed.fullText)} `.includes(` ${key} `))
    );
  };

  if ((!wardKey || !provinceKey) && !parsed.fullText) {
    console.warn("Thiếu tỉnh hoặc xã/phường để tìm mapping.");
    return null;
  }

  const wardRecords = addressMappingRecords.filter((item) => {
    if (!item?.oldWard) return false;
    return wardKey
      ? normalize(item.oldWard, "ward") === wardKey
      : addressContainsName(item.oldWard, "ward");
  });

  console.log("Số bản ghi khớp xã/phường:", wardRecords.length);

  const provinceRecords = wardRecords.filter(
    (item) => {
      if (!item?.oldProvince) return false;
      if (provinceKey) {
        return (
          normalize(item.oldProvince, "province") === provinceKey ||
          sameOldProvinceName(
            parsed.oldProvince,
            item.oldProvince,
            parsed.fullText,
          )
        );
      }
      return addressContainsName(item.oldProvince, "province");
    },
  );

  console.log("Số bản ghi khớp xã/phường + tỉnh:", provinceRecords.length);

  if (!provinceRecords.length) {
    console.warn("Không có bản ghi địa chỉ phù hợp trong addressMapping.json.");
    return null;
  }

  // Nếu OCR có huyện thì ưu tiên khớp huyện.
  let candidates = provinceRecords;

  if (districtKey) {
    const districtRecords = provinceRecords.filter(
      (item) =>
        item?.oldDistrict &&
        normalize(item.oldDistrict, "district") === districtKey,
    );

    if (districtRecords.length) {
      candidates = districtRecords;
    } else {
      // Older cards may show Long Điền while mapping data groups it under Long Đất.
      const historicalDistrictAliases = [
        {
          province: "Bà Rịa - Vũng Tàu",
          scannedDistrict: "Long Điền",
          mappingDistrict: "Long Đất",
        },
      ];
      const districtAlias = historicalDistrictAliases.find(
        (alias) =>
          normalize(alias.province, "province") === provinceKey &&
          normalize(alias.scannedDistrict, "district") === districtKey,
      );
      const aliasedDistrictRecords = districtAlias
        ? provinceRecords.filter(
            (item) =>
              item?.oldDistrict &&
              normalize(item.oldDistrict, "district") ===
                normalize(districtAlias.mappingDistrict, "district"),
          )
        : [];

      if (aliasedDistrictRecords.length) {
        candidates = aliasedDistrictRecords;
      } else {
        console.warn("Không khớp huyện; thử đối chiếu theo tỉnh và xã/phường.");
      }
    }
  }

  const targets = uniqueMappingTargets(candidates);

  if (targets.length !== 1) {
    console.warn("Mapping không có một đích duy nhất; không tự chọn.");

    return {
      status: "multiple",
    };
  }

  const result = formatMappingResult(targets[0]);
  return result;
}

// ============================================================
// BUILD FULL ADDRESS
// ============================================================

function buildFullAddress({
  soNha = "",
  phuongXa = "",
  quanHuyen = "",
  tinh = "",
}) {
  return [soNha, phuongXa, quanHuyen, tinh]
    .map((value) => String(value || "").trim())
    .filter((value) => value && value.toLocaleLowerCase("vi") !== "không áp dụng")
    .join(", ");
}

function normalizeCurrentAddressBeforeSave(formData) {
  const soNha = String(formData.soNhaHienNay || "").trim();
  const phuongXa = String(formData.phuongXaHienNay || "").trim();
  const quanHuyen = String(formData.quanHuyenHienNay || "").trim();
  const tinh = String(formData.tinhHienNay || "").trim();

  formData.diaChiHienNay = buildFullAddress({
    soNha,
    phuongXa,
    quanHuyen,
    tinh,
  });

  return formData;
}

function syncFullAddressFields() {
  const hkttFull = document.getElementById("diaChiHKTT");

  if (hkttFull) {
    hkttFull.value = buildFullAddress({
      soNha: document.getElementById("soNhaHKTT")?.value,
      phuongXa: document.getElementById("phuongXaHKTT")?.value,
      quanHuyen: document.getElementById("quanHuyenHKTT")?.value,
      tinh: document.getElementById("HKTT_Tinh")?.value,
    });
  }

  const currentFull = document.getElementById("diaChiHienNay");

  if (currentFull) {
    currentFull.value = buildFullAddress({
      soNha: document.getElementById("soNhaHienNay")?.value,
      phuongXa: document.getElementById("phuongXaHienNay")?.value,
      quanHuyen: document.getElementById("quanHuyenHienNay")?.value,
      tinh: document.getElementById("tinhHienNay")?.value,
    });
  }
}

function fillCurrentAddress(addressData) {
  if (!addressData) return;
  setFormData(addressData);
  syncFullAddressFields();
}

function showAddressMapping({
  originalAddress = "",
  mappedAddress = "",
  source = "",
  status = "unavailable",
  currentAddress = false,
  applied = false,
  canApply = false,
} = {}) {
  const panel = document.getElementById("addressMappingPanel");
  const badge = document.getElementById("addressMappingBadge");
  const dateRow = document.getElementById("addressMappingDate");
  const original = document.getElementById("addressMappingOriginal");
  const originalRow = document.getElementById("addressMappingOriginalRow");
  const target = document.getElementById("addressMappingCurrent");
  const targetRow = document.getElementById("addressMappingTargetRow");
  const sourceElement = document.getElementById("addressMappingSource");
  const applyButton = document.getElementById("applyHKTTMappingBtn");
  const restoreButton = document.getElementById("restoreScannedHKTTBtn");
  if (
    !panel ||
    !badge ||
    !original ||
    !target ||
    !applyButton ||
    !restoreButton
  ) {
    return;
  }

  panel.hidden = !originalAddress;
  panel.classList.toggle("is-mapped", applied);
  if (dateRow) dateRow.hidden = applied;
  if (originalRow) originalRow.hidden = !applied;
  if (targetRow) targetRow.hidden = applied;
  original.textContent = originalAddress || "Chưa có dữ liệu quét";
  target.textContent =
    mappedAddress ||
    (status === "multiple"
      ? "Địa chỉ cũ thuộc khu vực được chia thành nhiều phường mới; dữ liệu hiện có chưa xác định được phường mới chính xác."
      : "Chưa tìm thấy mapping duy nhất");
  if (sourceElement) {
    sourceElement.textContent = source ? `(Theo ${source})` : "";
    sourceElement.hidden = !source || applied;
  }
  badge.className = "address-mapping-badge";
  if (applied) {
    badge.textContent = "Đã chuyển sang địa giới mới";
    badge.classList.add("is-mapped");
  } else if (currentAddress) {
    badge.textContent = "Địa giới mới";
    badge.classList.add("is-current");
  } else {
    badge.textContent = "Địa giới cũ";
  }
  applyButton.hidden = applied;
  applyButton.disabled = !canApply;
  restoreButton.hidden = !applied;
}

// ============================================================
// CURRENT ADDRESS DATA
// ============================================================

function buildCurrentAddressData({
  quocGia = "Việt Nam",
  tinh = "",
  phuongXa = "",
  soNha = "",
} = {}) {
  return {
    quocGiaHienNay: quocGia || "Việt Nam",
    tinhHienNay: tinh,
    phuongXaHienNay: phuongXa,
    soNhaHienNay: soNha,
    quanHuyenHienNay: "Không áp dụng",
  };
}

function detectCCCDType(data) {
  if (!data || typeof data !== "object") {
    return "unknown";
  }

  // 1. Ưu tiên trường loại giấy tờ do OCR trả về.
  const explicitType = String(
    data.loaiCCCD ??
      data.cccdType ??
      data.cardType ??
      data.loaiGiayTo ??
      data.documentType ??
      "",
  )
    .normalize("NFC")
    .toLowerCase()
    .trim();

  if (
    explicitType.includes("cmnd") ||
    explicitType.includes("chứng minh nhân dân") ||
    explicitType.includes("9 số") ||
    explicitType.includes("9 digit") ||
    explicitType.includes("old")
  ) {
    return "old";
  }

  if (explicitType.includes("mới") || explicitType.includes("new")) {
    return "new";
  }

  // 2. CCCD/thẻ căn cước mới thường dùng nhãn "Nơi cư trú".
  const newAddressLabels = [
    "Nơi cư trú",
    "Nơi cư trú:",
    "noiCuTru",
    "noiCưTrú",
  ];

  if (
    newAddressLabels.some(
      (key) =>
        data[key] !== undefined &&
        data[key] !== null &&
        String(data[key]).trim() !== "",
    )
  ) {
    return "new";
  }

  // 3. CCCD cũ thường dùng nhãn "Nơi thường trú".
  const oldAddressLabels = [
    "Nơi thường trú",
    "Nơi thường trú:",
    "noiThuongTru",
    "noiThườngTrú",
    "thuongTru",
  ];

  if (
    oldAddressLabels.some(
      (key) =>
        data[key] !== undefined &&
        data[key] !== null &&
        String(data[key]).trim() !== "",
    )
  ) {
    return "old";
  }

  // 4. Kiểm tra các trường thông tin OCR trả về.
  // Không tự suy luận loại thẻ chỉ dựa vào địa chỉ HKTT.
  return "unknown";
}

// ============================================================
// FILL FORM FROM OCR
// ============================================================

async function fillFormFromOCR(data) {
  if (!data || typeof data !== "object") {
    console.warn("Dữ liệu OCR không hợp lệ.");
    return;
  }

  if (addressReady) {
    await addressReady;
  }

  const hktt = getOCRAddressSource(data);
  const addressString = String(hktt?.diaChiGoc || hktt?.diaChi || "").trim();

  if (addressString && (!getOCRProvince(hktt) || !getOCRWard(hktt))) {
    const parsedCurrentAddress = parseAddressText(addressString);

    if (parsedCurrentAddress) {
      Object.assign(hktt, parsedCurrentAddress);
    }
  }

  const parsedOldAddress = parseOldAddress(hktt);
  const oldProvince = getOCRProvince(hktt) || parsedOldAddress.oldProvince;
  const rawOldDistrict =
    getOCRDistrict(hktt) || parsedOldAddress.oldDistrict;

  const oldDistrict =
    rawOldDistrict &&
    oldProvince &&
    sameAdminName(rawOldDistrict, oldProvince, "district")
      ? ""
      : rawOldDistrict;

  const oldWard =
    normalizeOCRWard(getOCRWard(hktt)) || parsedOldAddress.oldWard;
  const oldHouse = getOCRHouse(hktt);
  const country = getOCRCountry(hktt);
  const scannedHouse = cleanCurrentHouseAddress(
    oldHouse,
    oldWard,
    oldDistrict,
    oldProvince,
  );

  const cccdType = detectCCCDType(data);

  console.log("Loại CCCD nhận diện:", cccdType);

  const currentAddress = checkCurrentAddress(hktt);

  // Chỉ map khi địa chỉ OCR chưa được nhận diện là địa giới hiện hành.
  const mapping = !currentAddress ? findMappingForHKTT(hktt) : null;

  const mappedOldDistrict =
    mapping?.status === "matched" && mapping.oldDistrict
      ? mapping.oldDistrict
      : oldDistrict;

  const currentHouse = cleanCurrentHouseAddress(
    oldHouse,
    oldWard,
    mappedOldDistrict,
    oldProvince,
    mapping?.status === "matched" ? mapping : null,
  );

  const formData = {
    soGiayTo: data.soGiayTo || "",
    hoDem: data.hoDem || "",
    ten: data.ten || "",
    hoTen: data.hoTen || "",
    ngaySinh: data.ngaySinh || "",
    gioiTinh: data.gioiTinh || "",
    quocTich: data.quocTich || "",
    danToc: data.danToc || "",
    noiSinhNguyenQuan: [
      data.noiSinhNguyenQuan,
      data.noiSinh,
      data.nguyenQuan,
    ]
      .map((value) => String(value || "").trim())
      .filter(
        (value, index, values) =>
          value &&
          values.findIndex(
            (item) => item.toLocaleLowerCase("vi") === value.toLocaleLowerCase("vi"),
          ) === index,
      )
      .join(" / "),
    nguyenQuan: data.nguyenQuan || "",
    noiSinh: data.noiSinh || "",
    noiCapGiayTo: data.noiCapGiayTo || "",
    ngayCap: data.ngayCap || "",
    ngayHetHan: data.ngayHetHan || "",

    // Giữ địa chỉ trên giấy tờ ở phần HKTT.
    quocGiaHKTT: country,
    HKTT_Tinh: oldProvince,
    phuongXaHKTT: oldWard,
    soNhaHKTT: scannedHouse,
    quanHuyenHKTT: oldDistrict || mapping?.oldDistrict || "Không áp dụng",
  };
  lastScannedHKTTData = {
    quocGiaHKTT: formData.quocGiaHKTT,
    HKTT_Tinh: formData.HKTT_Tinh,
    phuongXaHKTT: formData.phuongXaHKTT,
    soNhaHKTT: formData.soNhaHKTT,
    quanHuyenHKTT: formData.quanHuyenHKTT,
  };
  lastMappedHKTTData = null;

  let currentAddressData = null;
  let mappedAddressText = "";
  lastMappedCurrentAddressData = null;

  if (currentAddress) {
    currentAddressData = buildCurrentAddressData({
      quocGia: country,
      tinh: currentAddress.province,
      phuongXa: currentAddress.ward,
      soNha: currentHouse,
    });
    mappedAddressText = buildFullAddress({
      soNha: currentHouse,
      phuongXa: currentAddress.ward,
      tinh: currentAddress.province,
    });

  } else if (mapping?.status === "matched") {
    const province = findCurrentProvinceFromName(
      mapping.currentProvince || mapping.newProvince,
    );

    const ward = province
      ? findCurrentWard(province, mapping.currentWard || mapping.newWard)
      : null;

    if (province && ward) {
      lastMappedHKTTData = {
        quocGiaHKTT: country,
        HKTT_Tinh: province,
        phuongXaHKTT: ward,
        soNhaHKTT: currentHouse,
        quanHuyenHKTT: "Không áp dụng",
      };
      lastMappedCurrentAddressData = buildCurrentAddressData({
        quocGia: country,
        tinh: province,
        phuongXa: ward,
        soNha: currentHouse,
      });
      mappedAddressText = buildFullAddress({
        soNha: currentHouse,
        phuongXa: ward,
        tinh: province,
      });
      if (document.getElementById("autoFillMappedAddress")?.checked) {
        currentAddressData = lastMappedCurrentAddressData;
      }

    } else {
      console.warn("Đích mapping không khớp hierarchy.json. Không tự đoán địa chỉ mới.");
    }
  } else if (mapping?.status === "multiple") {
    console.info(
      "Địa chỉ thuộc khu vực chia phường; giữ nguyên nếu dữ liệu không phân định được đích.",
    );
  } else {
    console.warn(
      "Không tìm được mapping duy nhất. Giữ nguyên thông tin đã xác định, không tự đoán.",
    );
  }

  if (currentAddressData) {
    Object.assign(formData, currentAddressData);
  }

  setFormData(formData);
  syncFullAddressFields();
  lastAddressMappingView = {
    originalAddress: addressString || buildFullAddress({
      soNha: oldHouse,
      phuongXa: oldWard,
      quanHuyen: oldDistrict,
      tinh: oldProvince,
    }),
    mappedAddress: mappedAddressText,
    source:
      mapping?.status === "matched"
        ? mapping.record?.source || ""
        : "",
    status:
      mapping?.status === "matched" && !mappedAddressText
        ? "unavailable"
        : mapping?.status || "unavailable",
    currentAddress: Boolean(currentAddress),
    canApply: Boolean(lastMappedHKTTData),
  };
  showAddressMapping(lastAddressMappingView);

}

function clearDocumentVerification() {
  verificationNeedsReview = false;
  const summary = document.getElementById("verificationSummary");
  const messages = document.getElementById("verificationMessages");
  const acknowledgementLabel = document.getElementById("verificationAcknowledgement");
  const addressStatuses = [
    document.getElementById("ocrHouseholdAddress"),
    document.getElementById("qrHouseholdAddress"),
    document.getElementById("householdAddressStatus"),
  ];
  if (summary) summary.hidden = true;
  if (acknowledgementLabel) acknowledgementLabel.hidden = true;
  if (messages) messages.replaceChildren();
  for (const status of addressStatuses) {
    if (!status) continue;
    status.textContent = "";
    status.className = "";
  }
  document.querySelectorAll(".field.validation-review").forEach((field) => {
    field.classList.remove("validation-review");
  });
}

function showDocumentVerification(verification = {}) {
  clearDocumentVerification();
  const summary = document.getElementById("verificationSummary");
  const messages = document.getElementById("verificationMessages");
  if (!summary || !messages) return;
  const acknowledgementLabel = document.getElementById("verificationAcknowledgement");
  const ocrAddressElement = document.getElementById("ocrHouseholdAddress");
  const qrAddressElement = document.getElementById("qrHouseholdAddress");
  const addressStatusElement = document.getElementById("householdAddressStatus");

  const labels = {
    soGiayTo: "Số CCCD",
    hoTen: "Họ và tên",
    hoDem: "Họ và đệm",
    ten: "Tên",
    ngaySinh: "Ngày sinh",
    gioiTinh: "Giới tính",
    ngayCap: "Ngày cấp",
    ngayHetHan: "Ngày hết hạn",
  };
  const lines = [];
  const qrStatus =
    verification.qrStatus === "read"
      ? (verification.qrFieldsCorrected || []).length
        ? "corrected"
        : "matched"
      : verification.qrStatus;
  const addressComparison = verification.householdAddress || {};
  const addressStatusDisplay = {
    matched: ["khớp", "is-matched"],
    mismatch: ["không khớp — đã dùng địa chỉ QR", "is-corrected"],
    ocr_missing: ["OCR không đọc được — đã dùng địa chỉ QR", "is-corrected"],
    unavailable: ["không đọc được QR", "is-unreadable"],
  };
  const addressStatus =
    addressStatusDisplay[addressComparison.status] ||
    addressStatusDisplay.unavailable;
  if (ocrAddressElement) {
    ocrAddressElement.textContent =
      addressComparison.ocrAddress || "không đọc được";
  }
  if (qrAddressElement) {
    qrAddressElement.textContent =
      addressComparison.qrAddress || "không đọc được";
  }
  if (addressStatusElement) {
    addressStatusElement.textContent = addressStatus[0];
    addressStatusElement.classList.add(addressStatus[1]);
  }

  if (qrStatus === "corrected") {
    const corrections = verification.qrFieldsCorrected || [];
    for (const correction of corrections) {
      if (correction.field === "HKTT.diaChiGoc") continue;
      const label = labels[correction.field] || correction.label || correction.field;
      const previous = correction.from || "(trống)";
      lines.push(`QR đã sửa ${label}: ${previous} → ${correction.to}.`);
    }
    if (!corrections.length) {
      const fields = (verification.qrFieldsApplied || [])
        .map((field) => labels[field] || field)
        .join(", ");
      lines.push(`QR đã cập nhật: ${fields}. Hãy kiểm tra các trường được tô vàng.`);
    }
  } else if (verification.qrReadStatus === "unrecognized" || qrStatus === "unrecognized") {
    lines.push("Đã phát hiện QR nhưng nội dung không thuộc định dạng được hỗ trợ; không dùng QR để sửa dữ liệu.");
  }

  for (const warning of verification.warnings || []) {
    if (/\bMRZ\b/i.test(warning)) continue;
    lines.push(warning);
  }

  const list = document.createDocumentFragment();
  for (const line of lines) {
    const item = document.createElement("li");
    item.textContent = line;
    list.append(item);
  }
  messages.append(list);
  messages.hidden = lines.length === 0;
  summary.classList.toggle(
    "is-review",
    lines.length > 0 ||
      addressComparison.status !== "matched",
  );

  const hiddenMRZFields = new Set(verification.mrzReviewFields || []);
  const visibleReviewFields =
    verification.nonMrzReviewFields || verification.reviewFields || [];
  for (const fieldName of visibleReviewFields) {
    if (hiddenMRZFields.has(fieldName) || fieldName === "mrzLines") continue;
    const field = document.getElementById(fieldName);
    field?.closest(".field")?.classList.add("validation-review");
  }
  verificationNeedsReview = verification.requiresReview === true;
  if (acknowledgementLabel) {
    acknowledgementLabel.hidden = !verificationNeedsReview;
  }
  summary.hidden = false;
}

// ============================================================
// OCR EVENT
// ============================================================

window.addEventListener("cccdOCRStarted", () => {
  clearDocumentVerification();
  lastMappedCurrentAddressData = null;
  lastScannedHKTTData = null;
  lastMappedHKTTData = null;
  lastAddressMappingView = null;
  showAddressMapping();
});

window.addEventListener("cccdOCRCompleted", async (event) => {
  try {
    const data = normalizeOCRData(event.detail);
    await fillFormFromOCR(data);
    showDocumentVerification(data.verification);
  } catch (error) {
    console.error("Lỗi xử lý kết quả OCR.", error?.name || "Error");
  }
});

// ============================================================
// SAVE PROFILE
// ============================================================

document.getElementById("saveProfileBtn")?.addEventListener("click", async () => {
  const formData = normalizeCurrentAddressBeforeSave(getFormData());
  const uploadState = getUploadState();

  if (!formData.hoTen || !formData.hoTen.trim()) {
    alert("Vui lòng nhập Họ và tên trước khi lưu.");
    return;
  }

  const confirmation = document.getElementById("saveConfirmDialog");
  if (!confirmation?.showModal) {
    throw new Error("Trình duyệt không hỗ trợ hộp thoại xác nhận lưu hồ sơ.");
  }
  confirmation.showModal();
  const confirmed = await new Promise((resolve) => {
    confirmation.addEventListener(
      "close",
      () => resolve(confirmation.returnValue === "confirm"),
      { once: true },
    );
  });
  if (!confirmed) {
    return;
  }

  try {
    const profile = {
      id: editingId || pendingCreateId || crypto.randomUUID(),
      version: originalProfile?.version,
      ...formData,
      frontDataUrl: uploadState.frontDataUrl || originalProfile?.frontDataUrl || null,
      backDataUrl: uploadState.backDataUrl || originalProfile?.backDataUrl || null,
      portraitDataUrl:
        uploadState.portraitDataUrl || originalProfile?.portraitDataUrl || null,
    };
    await saveProfile(profile);
    alert("Đã lưu hồ sơ thành công!");
    closeExtractModal();
  } catch (error) {
    console.error("Không thể lưu hồ sơ.", error?.code || error?.name || "Error");
    alert(`Không thể lưu hồ sơ: ${error.message}`);
  }
});

// ============================================================
// INITIALIZE COMPONENTS
// ============================================================

initProfileForm();
initUploadForm();

// ============================================================
// INITIALIZE ADDRESS DATA
// ============================================================

async function initAddress() {
  console.log("Bắt đầu khởi tạo dữ liệu địa chỉ...");

  const provinces = await initAddressData();

  console.log(
    "Đã tải hierarchy.json:",
    Array.isArray(provinces) ? provinces.length : 0,
    "tỉnh/thành",
  );

  const mapping = await loadAddressMapping();

  addressMappingRecords = Array.isArray(mapping?.mappings)
    ? mapping.mappings
    : [];

  console.log(
    "Đã tải addressMapping.json:",
    addressMappingRecords.length,
    "bản ghi",
  );

  initCountryCombobox({
    inputId: "quocTich",
    optionsId: "quocTichOptions",
  });

  initCountryCombobox({
    inputId: "quocGiaHKTT",
    optionsId: "quocGiaHKTTOptions",
  });

  initAddressCombobox({
    provinceInputId: "HKTT_Tinh",
    provinceOptionsId: "HKTT_TinhOptions",
    wardInputId: "phuongXaHKTT",
    wardOptionsId: "phuongXaHKTTOptions",
  });

  initCountryCombobox({
    inputId: "quocGiaHienNay",
    optionsId: "quocGiaHienNayOptions",
  });

  initAddressCombobox({
    provinceInputId: "tinhHienNay",
    provinceOptionsId: "tinhHienNayOptions",
    wardInputId: "phuongXaHienNay",
    wardOptionsId: "phuongXaHienNayOptions",
  });

  console.log("Khởi tạo dữ liệu địa chỉ hoàn tất.");
}

addressReady = initAddress().catch((error) => {
  console.error("Lỗi khởi tạo dữ liệu địa chỉ:", error);
  throw error;
});

async function loadExistingProfile() {
  if (!profileId) return;
  try {
    await addressReady;
    originalProfile = await getProfileById(profileId);
    if (originalProfile.folderId !== folderId) {
      throw new Error("Hồ sơ không thuộc thư mục đang mở.");
    }
    editingId = originalProfile.id;
    pendingCreateId = null;
    setFormData(originalProfile);
    setAvatarImage(originalProfile.portraitDataUrl || null);
    const title = document.getElementById("extractTitle");
    if (title) title.textContent = `Sửa hồ sơ - ${originalProfile.hoTen}`;
    document.getElementById("saveProfileBtn").disabled = false;
  } catch (error) {
    document.getElementById("pageError").textContent =
      `Không thể mở hồ sơ: ${error.message}`;
    document.getElementById("saveProfileBtn").disabled = true;
  }
}

void loadExistingProfile();
