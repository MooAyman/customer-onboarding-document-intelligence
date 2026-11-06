const FIELDS = [
  { group: "Front Side", path: "front.full_name_ar", label: "Full name", kind: "person_name", required: true, editable: true, reasons: ["front_name"] },
  { group: "Front Side", path: "front.national_id", label: "National ID", kind: "nid", required: false, editable: true, reasons: ["usable_nid", "nid_single_side", "front_back_nid_match"] },
  { group: "Front Side", path: "front.address_ar", label: "Address", kind: "address", required: true, editable: true, reasons: ["front_address"] },
  { group: "Front Side", path: "front.card_serial", label: "Card serial", kind: "serial", required: true, editable: true, reasons: ["front_serial"] },
  { group: "Front Side", path: "front.date_of_birth_printed", label: "Printed date of birth", kind: "dob", required: false, editable: true, reasons: ["printed_derived_dob_match"], hint: "YYYY/MM/DD e.g. 1999/01/15" },
  { group: "Front Side", path: "derived.date_of_birth", label: "Date of birth (from NID)", kind: "dob", required: true, editable: true, reasons: ["age_at_least_18", "printed_derived_dob_match"], hint: "YYYY/MM/DD e.g. 1999/01/15" },
  { group: "Front Side", path: "derived.gender", label: "Gender (from NID)", kind: "gender", required: true, editable: true, reasons: ["printed_derived_gender_match"] },
  { group: "Front Side", path: "derived.birth_governorate", label: "Birth governorate", kind: "governorate", required: false, editable: true, reasons: [] },
  { group: "Front Side", path: "derived.birth_governorate_code", label: "Governorate code", kind: "gov_code", required: false, editable: true, reasons: [] },
  { group: "Front Side", path: "front.portrait_present", label: "Portrait", kind: "presence", required: false, editable: true, reasons: ["front_portrait"], hint: "true or false" },
  { group: "Front Side", path: "front.header_present", label: "Header", kind: "presence", required: false, editable: true, reasons: [], hint: "true or false" },
  { group: "Back Side", path: "back.national_id", label: "National ID", kind: "nid", required: false, editable: true, reasons: ["usable_nid", "nid_single_side", "front_back_nid_match"] },
  { group: "Back Side", path: "back.expiry_date", label: "Expiry date (card valid until)", kind: "expiry", required: true, editable: true, reasons: ["back_expiry", "expiry_not_past"], hint: "YYYY/MM/DD e.g. 2030/05/17" },
  { group: "Back Side", path: "derived.is_expired", label: "Expired", kind: "expired", required: false, editable: true, reasons: ["expiry_not_past"], hint: "true or false" },
  { group: "Back Side", path: "back.issue_date", label: "Issue date", kind: "month_date", required: false, editable: true, reasons: [], hint: "YYYY/MM e.g. 2021/11" },
  { group: "Back Side", path: "back.gender_printed", label: "Printed gender", kind: "gender", required: false, editable: true, reasons: ["printed_derived_gender_match"] },
  { group: "Back Side", path: "back.religion_printed", label: "Religion", kind: "religion", required: false, editable: true, reasons: [] },
  { group: "Back Side", path: "back.marital_status_ar", label: "Marital status", kind: "marital", required: false, editable: true, reasons: [] },
  { group: "Back Side", path: "back.profession_ar", label: "Profession", kind: "text", required: false, editable: true, reasons: [] },
  { group: "Back Side", path: "back.husband_name_ar", label: "Husband name", kind: "person_name", required: false, editable: true, reasons: [] },
  { group: "Back Side", path: "back.barcode_present", label: "Barcode (existence)", kind: "presence", required: false, editable: true, reasons: [], hint: "true or false" },
];
const MSG = {
  required: "Required field is empty.",
  nameDigits: "Name must not contain digits.",
  nid: "NID must contain exactly 14 digits and pass structural/date validation.",
  date: "Invalid date or expired date.",
  dob: "Enter a valid date of birth as YYYY/MM/DD, e.g. 1999/01/15.",
  expiry: "Enter a valid expiry date as YYYY/MM/DD, e.g. 2030/05/17.",
  issue: "Enter a valid issue date as YYYY/MM, e.g. 2021/11.",
  nidMismatch: "Front and back National ID numbers do not match.",
  categorical: "Unsupported categorical value.",
  serial: "Invalid serial number format.",
  presence: "Enter true or false.",
  expired: "Expired must be true if the expiry date is in the past, and false if it is still valid.",
  govCode: "Governorate code must be a valid two-digit Egyptian NID code.",
  govName: "Governorate name must match the governorate code.",
  govNid: "Governorate must match the National ID.",
  age: "Applicant must be at least 18 years old.",
};
const GOVERNORATES = {
  "01": "القاهرة", "02": "الإسكندرية", "03": "بورسعيد", "04": "السويس",
  "11": "دمياط", "12": "الدقهلية", "13": "الشرقية", "14": "القليوبية",
  "15": "كفر الشيخ", "16": "الغربية", "17": "المنوفية", "18": "البحيرة",
  "19": "الإسماعيلية", "21": "الجيزة", "22": "بني سويف", "23": "الفيوم", "24": "المنيا",
  "25": "أسيوط", "26": "سوهاج", "27": "قنا", "28": "أسوان", "29": "الأقصر",
  "31": "البحر الأحمر", "32": "الوادي الجديد", "33": "مطروح", "34": "شمال سيناء",
  "35": "جنوب سيناء", "88": "خارج الجمهورية",
};
const GOVERNORATES_EN = {
  "01": "Cairo", "02": "Alexandria", "03": "Port Said", "04": "Suez",
  "11": "Damietta", "12": "Dakahlia", "13": "Sharqia", "14": "Qalyubia",
  "15": "Kafr El Sheikh", "16": "Gharbia", "17": "Monufia", "18": "Beheira",
  "19": "Ismailia", "21": "Giza", "22": "Beni Suef", "23": "Faiyum", "24": "Minya",
  "25": "Asyut", "26": "Sohag", "27": "Qena", "28": "Aswan", "29": "Luxor",
  "31": "Red Sea", "32": "New Valley", "33": "Matruh", "34": "North Sinai",
  "35": "South Sinai", "88": "Outside the republic",
};
function codeForGovName(name) {
  const text = (name || "").trim();
  for (const [code, arabic] of Object.entries(GOVERNORATES)) {
    if (arabic === text) return code;
  }
  for (const [code, english] of Object.entries(GOVERNORATES_EN)) {
    if (english === text) return code;
  }
  return "";
}
function arabicGovernorate(name) {
  const code = codeForGovName(name);
  return code ? GOVERNORATES[code] : name;
}
const RELIGION = ["مسلم", "مسلمة", "مسيحي", "مسيحية", "يهودي", "يهودية", "-"];
const MARITAL_AR = ["أعزب", "اعزب", "عزباء", "متزوج", "متزوجة", "مطلق", "مطلقة", "أرمل", "ارمل", "أرملة", "ارملة"];
const PENCIL = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z"/></svg>';

const acceptBtn = document.getElementById("accept");
const rejectBtn = document.getElementById("reject");
const resubmitBtn = document.getElementById("resubmit");
const statusLine = document.getElementById("status-line");
const reasonBox = document.getElementById("decision-reason");
let applications = [];
let current = null;
let lastResult = null;
let corrections = {};
let editingPath = null;
let localMessages = {};

function lookup(obj, path) {
  return path.split(".").reduce((acc, key) => (acc ? acc[key] : undefined), obj);
}
function isArabic(text) {
  return /[\u0600-\u06FF]/.test(text || "");
}
function digitsOnly(text) {
  const map = { "٠": "0", "١": "1", "٢": "2", "٣": "3", "٤": "4", "٥": "5", "٦": "6", "٧": "7", "٨": "8", "٩": "9", "۰": "0", "۱": "1", "۲": "2", "۳": "3", "۴": "4", "۵": "5", "۶": "6", "۷": "7", "۸": "8", "۹": "9" };
  return [...String(text || "")].map((ch) => map[ch] || ch).filter((ch) => /\d/.test(ch)).join("");
}
function parseIsoDate(value) {
  const match = String(value || "").match(/^(\d{4})-(\d{2})(?:-(\d{2}))?$/);
  if (!match) return null;
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = match[3] ? Number(match[3]) : 1;
  const date = new Date(year, month - 1, day);
  if (date.getFullYear() !== year || date.getMonth() !== month - 1 || date.getDate() !== day) return null;
  return date;
}
function calendarDate(year, month, day) {
  const date = new Date(year, month - 1, day);
  if (date.getFullYear() !== year || date.getMonth() !== month - 1 || date.getDate() !== day) return null;
  return date;
}
function westernDigitsKeepSep(value) {
  const map = { "٠": "0", "١": "1", "٢": "2", "٣": "3", "٤": "4", "٥": "5", "٦": "6", "٧": "7", "٨": "8", "٩": "9", "۰": "0", "۱": "1", "۲": "2", "۳": "3", "۴": "4", "۵": "5", "۶": "6", "۷": "7", "۸": "8", "۹": "9" };
  return [...String(value || "")].map((ch) => map[ch] || ch).join("").replace(/\s+/g, "");
}
function parseFullDate(value) {
  const western = westernDigitsKeepSep(value);
  const match = western.match(/^(\d{4})[/.\-](\d{1,2})[/.\-](\d{1,2})$/);
  if (match) return calendarDate(Number(match[1]), Number(match[2]), Number(match[3]));
  const digits = digitsOnly(western);
  if (digits.length !== 8) return null;
  return calendarDate(Number(digits.slice(0, 4)), Number(digits.slice(4, 6)), Number(digits.slice(6, 8)));
}
function parseMonthDate(value) {
  const western = westernDigitsKeepSep(value);
  if (/^(\d{4})[/.\-](\d{1,2})[/.\-](\d{1,2})$/.test(western)) return null;
  const match = western.match(/^(\d{4})[/.\-](\d{1,2})$/);
  if (match) return calendarDate(Number(match[1]), Number(match[2]), 1);
  const digits = digitsOnly(western);
  if (digits.length !== 6) return null;
  return calendarDate(Number(digits.slice(0, 4)), Number(digits.slice(4, 6)), 1);
}
function fieldByPath(path) {
  return FIELDS.find((item) => item.path === path);
}
function expectedExpired(expiryText) {
  const parsed = parseFullDate(expiryText);
  if (!parsed) return null;
  return parsed < new Date(new Date().toDateString()) ? "true" : "false";
}
function liveValue(field) {
  if (!field) return "";
  if (editingPath === field.path) {
    const input = document.getElementById("edit-input");
    if (input) return input.value;
  }
  if (field.path === "derived.is_expired" && !Object.prototype.hasOwnProperty.call(corrections, field.path)) {
    const expected = expectedExpired(liveValue(fieldByPath("back.expiry_date")));
    if (expected) return expected;
  }
  return finalOf(field);
}
function govPairError(path, value) {
  const codePath = "derived.birth_governorate_code";
  const namePath = "derived.birth_governorate";
  const code = digitsOnly(path === codePath ? value : liveValue(fieldByPath(codePath)));
  const name = (path === namePath ? value : liveValue(fieldByPath(namePath)) || "").trim();
  if (code && name && GOVERNORATES[code] && codeForGovName(name) !== code) return [MSG.govName];
  const front = nidDigits(liveValue(fieldByPath("front.national_id")));
  const back = nidDigits(liveValue(fieldByPath("back.national_id")));
  const nid = front || back;
  if (code.length === 2 && nid && nid.slice(7, 9) !== code) return [MSG.govNid];
  return [];
}
function nidPairError(path, value) {
  const otherPath = path === "front.national_id" ? "back.national_id" : path === "back.national_id" ? "front.national_id" : "";
  if (!otherPath) return [];
  const self = nidDigits(value);
  const other = nidDigits(liveValue(fieldByPath(otherPath)));
  if (self && other && self !== other) return [MSG.nidMismatch];
  return [];
}
function nidDigits(value) {
  const digits = digitsOnly(value);
  return digits.length === 14 ? digits : "";
}
function nidOk(value) {
  const digits = digitsOnly(value);
  if (digits.length !== 14 || !["2", "3"].includes(digits[0])) return false;
  const year = (digits[0] === "2" ? 1900 : 2000) + Number(digits.slice(1, 3));
  return Boolean(parseIsoDate(`${year}-${digits.slice(3, 5)}-${digits.slice(5, 7)}`));
}
function nidBorn(value) {
  const digits = digitsOnly(value);
  if (!nidOk(value)) return null;
  const year = (digits[0] === "2" ? 1900 : 2000) + Number(digits.slice(1, 3));
  return parseIsoDate(`${year}-${digits.slice(3, 5)}-${digits.slice(5, 7)}`);
}
function ageYears(born) {
  if (!born) return null;
  const today = new Date();
  let age = today.getFullYear() - born.getFullYear();
  if ((today.getMonth() < born.getMonth()) || (today.getMonth() === born.getMonth() && today.getDate() < born.getDate())) age -= 1;
  return age;
}
function liveNidAgeOk() {
  const front = liveValue(fieldByPath("front.national_id"));
  const back = liveValue(fieldByPath("back.national_id"));
  const born = nidBorn(nidOk(front) ? front : back);
  const age = ageYears(born);
  return age == null || age >= 18;
}
function validateField(field, value, edited) {
  const text = (value || "").trim();
  if (!text) return field.required ? [MSG.required] : [];
  if (field.kind === "person_name") {
    if (digitsOnly(text)) return [MSG.nameDigits];
    return [];
  }
  if (field.kind === "nid") {
    const msgs = nidOk(text) ? [] : [MSG.nid];
    const age = ageYears(nidBorn(text));
    if (age != null && age < 18) msgs.push(MSG.age);
    return msgs.concat(nidPairError(field.path, text));
  }
  if (field.kind === "dob") {
    const parsed = parseFullDate(text);
    if (!parsed) return [MSG.dob];
    if (parsed > new Date()) return [MSG.date];
    if (field.path === "derived.date_of_birth") {
      const age = ageYears(parsed);
      if (age != null && age < 18) return [MSG.age];
    }
    return [];
  }
  if (field.kind === "expiry") {
    const parsed = parseFullDate(text);
    if (!parsed) return [MSG.expiry];
    return parsed < new Date(new Date().toDateString()) ? [MSG.date] : [];
  }
  if (field.kind === "month_date") return parseMonthDate(text) ? [] : [MSG.issue];
  if (field.kind === "serial") return /^[A-Z]{2}\s?\d{6,10}$/i.test(text) ? [] : [MSG.serial];
  if (field.kind === "gender") {
    if (["ذكر", "أنثى", "انثى", "male", "female", "Male", "Female"].includes(text)) return [];
    return [MSG.categorical];
  }
  if (field.kind === "religion") return RELIGION.includes(text) ? [] : [MSG.categorical];
  if (field.kind === "marital") {
    if (MARITAL_AR.includes(text) || ["single", "married", "divorced", "widowed", "Single", "Married", "Divorced", "Widowed"].includes(text)) return [];
    return [MSG.categorical];
  }
  if (field.kind === "presence") return ["true", "false"].includes(text.toLowerCase()) ? [] : [MSG.presence];
  if (field.kind === "expired") {
    if (!["true", "false"].includes(text.toLowerCase())) return [MSG.presence];
    const expected = expectedExpired(liveValue(fieldByPath("back.expiry_date")));
    if (expected && text.toLowerCase() !== expected) return [MSG.expired];
    return [];
  }
  if (field.kind === "gov_code") {
    const code = digitsOnly(text);
    if (!(code.length === 2 && GOVERNORATES[code])) return [MSG.govCode];
    return govPairError(field.path, text);
  }
  if (field.kind === "governorate") {
    if (!codeForGovName(text)) return [MSG.govName];
    return govPairError(field.path, text);
  }
  return [];
}
function originalOf(field) {
  return lookup(lastResult, field.path) || {};
}
function finalOf(field) {
  if (Object.prototype.hasOwnProperty.call(corrections, field.path)) return corrections[field.path];
  return originalOf(field).value || "";
}
function flagged(field) {
  const reasons = (lastResult && lastResult.reasons) || [];
  const value = originalOf(field);
  return field.reasons.some((name) => reasons.includes(name)) || (field.required && ["unreadable", "absent"].includes(value.status));
}
function fieldMessages(field) {
  if (localMessages[field.path]) return localMessages[field.path];
  if (field.kind === "display") return [];
  return validateField(field, finalOf(field), Object.prototype.hasOwnProperty.call(corrections, field.path));
}
function nidsMatch() {
  const front = nidDigits(liveValue(fieldByPath("front.national_id")));
  const back = nidDigits(liveValue(fieldByPath("back.national_id")));
  if (front && back) return front === back;
  return true;
}
function reviewReady() {
  if (!lastResult) return false;
  const front = liveValue(fieldByPath("front.national_id"));
  const back = liveValue(fieldByPath("back.national_id"));
  const fieldsOk = FIELDS.every((field) => {
    if (field.kind === "display") return true;
    const edited = Object.prototype.hasOwnProperty.call(corrections, field.path) || editingPath === field.path;
    return validateField(field, liveValue(field), edited).length === 0;
  });
  return fieldsOk && (nidOk(front) || nidOk(back)) && nidsMatch() && liveNidAgeOk();
}
function escapeHtml(value) {
  return String(value).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;");
}
function percentLabel(confidence) {
  if (confidence == null || confidence === "") return null;
  const pct = Math.round(Number(confidence) * 100);
  if (Number.isNaN(pct)) return null;
  return `${pct}%`;
}
function confidenceMarkup(field, data) {
  const isDetection = field.kind === "presence" || data.source === "visual";
  const hasExtractedValue = data.status === "present" && data.value != null && data.value !== "" && !isDetection;
  const extraction = hasExtractedValue ? percentLabel(data.confidence) : null;
  let html = `<span class="confidence" title="Extraction confidence for a parsed value. Not validation and not correctness.">${extraction || "N/A"}</span>`;
  const detection = isDetection ? percentLabel(data.confidence) : null;
  if (detection) {
    html += `<span class="confidence detection" title="Detector score that the feature is present. Not extraction confidence and not correctness.">detection ${detection}</span>`;
  }
  return html;
}
function valueCell(text, raw, field, data) {
  const shown = text ? escapeHtml(text) : "—";
  const dir = isArabic(text) ? ' dir="rtl"' : "";
  const rawBit = raw && raw !== text ? `<span class="raw"${isArabic(raw) ? ' dir="rtl"' : ""}>OCR: ${escapeHtml(raw)}</span>` : "";
  return `<td class="value"${dir}>${shown}${confidenceMarkup(field, data)}${rawBit}</td>`;
}
function autoClass(decision) {
  return { completed: "completed", failed: "failed", needs_review: "needs_review" }[decision] || "needs_review";
}

function renderQueue() {
  const root = document.getElementById("queue");
  const pending = applications.filter((item) => item.status === "pending_review").length;
  document.getElementById("queue-summary").textContent = `${applications.length} total · ${pending} pending review`;
  root.innerHTML = applications
    .map((item) => `<button type="button" class="queue-item ${autoClass(item.status === "approved" ? "completed" : item.status === "rejected" ? "failed" : "needs_review")}${current && current.id === item.id ? " active" : ""}" data-id="${item.id}">
      <strong>${item.status}</strong>
      <span>${escapeHtml(item.applicant_name || "Unnamed")} · ${item.automated_decision || ""}</span>
    </button>`)
    .join("") || "<p class='lede'>No applications yet.</p>";
  root.querySelectorAll("[data-id]").forEach((button) => {
    button.addEventListener("click", () => openApplication(button.getAttribute("data-id")));
  });
}

function renderBanners() {
  const auto = lastResult || {};
  document.getElementById("auto-banner").className = `decision ${autoClass(auto.decision)}`;
  document.getElementById("auto-banner").innerHTML = `<div><strong>Automated: ${auto.decision || "—"}</strong><ul class="reasons">${(auto.reasons || []).map((item) => `<li>${escapeHtml(item)}</li>`).join("") || "<li>None</li>"}</ul></div>`;
  const pending = [];
  FIELDS.forEach((field) => fieldMessages(field).forEach((msg) => pending.push(`${field.label}: ${msg}`)));
  if (!nidsMatch()) pending.push(`National ID: ${MSG.nidMismatch}`);
  const ready = reviewReady();
  document.getElementById("review-banner").className = `decision ${current && current.status !== "pending_review" ? autoClass(current.status === "approved" ? "completed" : "failed") : ready ? "completed" : "needs_review"}`;
  document.getElementById("review-banner").innerHTML = `<div><strong>Reviewer: ${current ? current.status : "pending"}</strong><ul class="reasons">${pending.length ? pending.map((item) => `<li>${escapeHtml(item)}</li>`).join("") : "<li>Ready to accept if the card matches</li>"}</ul></div>`;
  document.getElementById("review-issues").innerHTML = pending.map((item) => `<li>${escapeHtml(item)}</li>`).join("");
}

function renderFields() {
  const root = document.getElementById("field-groups");
  root.innerHTML = "";
  const groups = [...new Set(FIELDS.map((field) => field.group))];
  for (const group of groups) {
    const wrap = document.createElement("div");
    wrap.className = "group";
    wrap.innerHTML = `<h3>${group}</h3><table><thead><tr><th>Field</th><th>Value <span class="format-hint">% = extraction confidence for a parsed value; detection = visual/presence detector. Neither is correctness.</span></th><th>OCR status</th><th></th></tr></thead><tbody></tbody></table>`;
    const body = wrap.querySelector("tbody");
    for (const field of FIELDS.filter((item) => item.group === group)) {
      const data = originalOf(field);
      const messages = fieldMessages(field);
      const row = document.createElement("tr");
      if (flagged(field) || messages.length) row.className = "flag";
      const shown = field.kind === "governorate" ? arabicGovernorate(finalOf(field)) : finalOf(field);
      const extracted = data.value == null || data.value === "" ? "" : String(data.value);
      const edited = Object.prototype.hasOwnProperty.call(corrections, field.path);
      const raw = !edited && data.raw_ocr && data.raw_ocr !== extracted ? data.raw_ocr : "";
      row.innerHTML = `
        <td>${escapeHtml(field.label)}${field.hint ? `<span class="format-hint">${escapeHtml(field.hint)}</span>` : ""}${flagged(field) ? ' <span class="badge needs">needs review</span>' : ""}${field.required ? ' <span class="badge">required</span>' : ""}</td>
        ${valueCell(shown, raw, field, data)}
        <td><span class="badge ${data.status || "absent"}">${data.status || "absent"}</span></td>
        <td>${field.editable && current && current.status === "pending_review" ? `<button class="icon-edit" type="button" data-edit="${field.path}" aria-label="Edit ${field.label}">${PENCIL}</button>` : ""}${edited ? ' <span class="badge present">corrected</span>' : ""}</td>`;
      body.appendChild(row);
      if (editingPath === field.path) {
        const editor = document.createElement("tr");
        editor.className = "editor";
        editor.innerHTML = `<td colspan="4"><div class="editor-row"><input class="review" id="edit-input" dir="auto" autocomplete="off" /><button type="button" id="edit-apply">Apply</button><button type="button" id="edit-cancel">Cancel</button></div><p class="msg" id="edit-msg"></p></td>`;
        body.appendChild(editor);
        const input = editor.querySelector("#edit-input");
        if (field.hint) input.placeholder = field.hint;
        input.value = field.kind === "governorate" ? arabicGovernorate(finalOf(field)) : finalOf(field);
        input.focus();
        const showMsg = (items) => {
          editor.querySelector("#edit-msg").textContent = items.join(" ");
        };
        const syncLive = () => {
          const items = validateField(field, input.value, true);
          showMsg(items);
          if (current && current.status === "pending_review") acceptBtn.disabled = !reviewReady();
        };
        showMsg(validateField(field, input.value, true));
        input.addEventListener("input", syncLive);
        editor.querySelector("#edit-apply").addEventListener("click", () => {
          let next = input.value.trim();
          if (field.kind === "presence" || field.kind === "expired") next = next.toLowerCase();
          if (field.kind === "governorate") next = arabicGovernorate(next) || next;
          const items = validateField(field, next, true);
          if (!next && !field.required) {
            delete corrections[field.path];
            delete localMessages[field.path];
          } else {
            corrections[field.path] = next;
            localMessages[field.path] = items;
          }
          editingPath = null;
          refreshDetail();
        });
        editor.querySelector("#edit-cancel").addEventListener("click", () => {
          editingPath = null;
          refreshDetail();
        });
      }
    }
    root.appendChild(wrap);
  }
  root.querySelectorAll("[data-edit]").forEach((button) => {
    button.addEventListener("click", () => {
      editingPath = button.getAttribute("data-edit");
      refreshDetail();
    });
  });
}

function renderChecks() {
  const box = document.getElementById("checks");
  box.innerHTML = ((lastResult && lastResult.checks) || [])
    .map((check) => `<div class="check ${check.passed ? "ok" : "fail"} ${check.severity}"><span>${check.name}</span><span>${check.passed ? "pass" : check.severity} · ${check.detail}</span></div>`)
    .join("");
  const ocr = (lastResult && lastResult.ocr_ms) || {};
  document.getElementById("timing").textContent = Object.keys(ocr).length
    ? `OCR ${Object.entries(ocr).map(([key, value]) => `${key} ${value}ms`).join(" · ")}`
    : "";
}

function setPreview(id, url) {
  const box = document.getElementById(id);
  box.classList.remove("empty");
  box.innerHTML = `<img alt="${id}" src="${url}" />`;
}

function refreshDetail() {
  if (!current || !lastResult) return;
  document.getElementById("detail").classList.remove("hidden");
  setPreview("front-preview", current.front_url);
  setPreview("back-preview", current.back_url);
  renderBanners();
  renderFields();
  renderChecks();
  const pending = current.status === "pending_review";
  acceptBtn.disabled = !(pending && reviewReady());
  rejectBtn.disabled = !pending;
  resubmitBtn.disabled = !pending;
  document.getElementById("review-summary").textContent = pending
    ? "Correct flagged fields if needed, then accept, reject, or request new images."
    : `This application is ${current.status}.`;
}

async function loadQueue() {
  const response = await fetch("/v1/review/applications");
  const payload = await response.json();
  applications = payload.applications || [];
  renderQueue();
  if (!current) return;
  const summary = applications.find((item) => item.id === current.id);
  if (summary && summary.status !== current.status) {
    await openApplication(current.id);
  }
}

async function openApplication(id) {
  const response = await fetch(`/v1/review/applications/${id}`);
  if (!response.ok) return;
  current = await response.json();
  lastResult = current.extraction;
  corrections = { ...(current.corrections || {}) };
  editingPath = null;
  localMessages = {};
  reasonBox.value = current.reviewer_reason || "";
  refreshDetail();
  renderQueue();
}

async function decide(action) {
  if (!current) return;
  const reason = reasonBox.value.trim();
  if ((action === "reject" || action === "resubmit") && !reason) {
    statusLine.textContent = "Enter a reason for the customer.";
    return;
  }
  if (action === "accept" && !reviewReady()) return;
  statusLine.textContent = "Saving decision…";
  const response = await fetch(`/v1/review/applications/${current.id}/decision`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action, corrections, reason }),
  });
  const payload = await response.json();
  if (!response.ok) {
    statusLine.textContent = payload.detail || "Decision was not saved.";
    if (payload.review && payload.review.fields) {
      localMessages = {};
      payload.review.fields.forEach((item) => {
        if (item.review_validation && !item.review_validation.ok) {
          localMessages[item.id] = item.review_validation.messages;
        }
      });
      refreshDetail();
    }
    return;
  }
  statusLine.textContent = "Decision saved. The customer portal will show this status.";
  await loadQueue();
  await openApplication(current.id);
}

acceptBtn.addEventListener("click", () => decide("accept"));
rejectBtn.addEventListener("click", () => decide("reject"));
resubmitBtn.addEventListener("click", () => decide("resubmit"));
loadQueue();
setInterval(loadQueue, 5000);
