const STORAGE_KEY = "egyid.applicationIds";
const CUSTOMER_KEY = "egyid.customerId";

function customerId() {
  let id = localStorage.getItem(CUSTOMER_KEY);
  if (!id) {
    id = crypto.randomUUID();
    localStorage.setItem(CUSTOMER_KEY, id);
  }
  return id;
}
const frontInput = document.getElementById("front-file");
const backInput = document.getElementById("back-file");
const submitBtn = document.getElementById("submit");
const resubmitBtn = document.getElementById("resubmit");
const statusLine = document.getElementById("status-line");
const panel = document.getElementById("status-panel");
let frontUrl = null;
let backUrl = null;
let current = null;
let pollTimer = null;

function loadIds() {
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEY) || "[]");
  } catch {
    return [];
  }
}

function saveId(id) {
  const ids = loadIds().filter((item) => item !== id);
  ids.unshift(id);
  localStorage.setItem(STORAGE_KEY, JSON.stringify(ids.slice(0, 20)));
}

function bindDrop(zoneId, input) {
  const zone = document.getElementById(zoneId);
  zone.addEventListener("dragover", (event) => {
    event.preventDefault();
    zone.classList.add("over");
  });
  zone.addEventListener("dragleave", () => zone.classList.remove("over"));
  zone.addEventListener("drop", (event) => {
    event.preventDefault();
    zone.classList.remove("over");
    const file = event.dataTransfer.files[0];
    if (!file) return;
    const transfer = new DataTransfer();
    transfer.items.add(file);
    input.files = transfer.files;
    input.dispatchEvent(new Event("change"));
  });
}

function setFileMeta(input, metaId) {
  const file = input.files[0];
  document.getElementById(metaId).textContent = file
    ? `${file.name} · ${(file.size / 1024).toFixed(0)} KB`
    : "";
}

function showPreview(input, previewId, metaId, holder) {
  const file = input.files[0];
  const preview = document.getElementById(previewId);
  if (holder.url) URL.revokeObjectURL(holder.url);
  if (!file) {
    preview.classList.add("empty");
    preview.textContent = "No image";
    holder.url = null;
    setFileMeta(input, metaId);
    return;
  }
  holder.url = URL.createObjectURL(file);
  preview.classList.remove("empty");
  preview.innerHTML = "";
  const img = document.createElement("img");
  img.alt = file.name;
  img.src = holder.url;
  preview.appendChild(img);
  setFileMeta(input, metaId);
}

function bothReady() {
  return Boolean(frontInput.files[0] && backInput.files[0]);
}

function syncButtons() {
  const ready = bothReady();
  const resubmitting = Boolean(current && current.can_resubmit);
  submitBtn.disabled = !ready || resubmitting;
  resubmitBtn.disabled = !(resubmitting && ready);
}

function statusClass(status) {
  if (status === "approved") return "completed";
  if (status === "rejected") return "failed";
  return "needs_review";
}

function statusTitle(status) {
  return {
    approved: "Approved — you can continue",
    pending_review: "Pending human review",
    rejected: "Rejected",
    resubmission_required: "Resubmission required",
  }[status] || status;
}

function formStatusTitle(status) {
  return {
    approved: "Application form accepted",
    pending_review: "Application form pending review",
    rejected: "Application form rejected",
    resubmission_required: "Application form resubmission required",
  }[status] || "";
}

function renderStatus(app) {
  current = app;
  statusLine.classList.remove("error");
  panel.classList.remove("hidden");
  const banner = document.getElementById("status-banner");
  banner.className = `decision ${statusClass(app.status)}`;
  banner.innerHTML = `<div><strong>${statusTitle(app.status)}</strong></div>`;
  const message = app.customer_message || "";
  document.getElementById("status-message").textContent = message;
  statusLine.textContent = message;
  document.getElementById("app-id-line").textContent = `Application ${app.id}`;
  const next = document.getElementById("next-stage");
  const formReady = Boolean(app.next_stage && app.next_stage.available);
  next.classList.toggle("hidden", !formReady);
  if (formReady) {
    const form = app.application_form;
    document.getElementById("form-detail").textContent =
      (form && form.message) || app.next_stage.detail || "Download the blank form, fill it by hand, then upload the completed page.";
    document.getElementById("form-status-line").textContent = formStatusTitle(app.form_status);
    const blank = document.getElementById("blank-form-link");
    blank.href = app.next_stage.blank_form_url || "#";
    const canUpload = Boolean(app.next_stage.can_upload);
    document.getElementById("form-upload").classList.toggle("hidden", !canUpload);
    formSubmitBtn.disabled = !(canUpload && formInput.files[0]);
    renderFormResult(form);
  }
  document.getElementById("resubmit-box").classList.toggle("hidden", !app.can_resubmit);
  syncButtons();
  if (app.status === "pending_review" || app.form_status === "pending_review") startPoll(app.id);
  else stopPoll();
}

function renderFormResult(payload) {
  const box = document.getElementById("form-json");
  if (!payload) {
    box.classList.add("hidden");
    box.textContent = "";
    return;
  }
  box.classList.remove("hidden");
  box.textContent = JSON.stringify(payload, null, 2);
}

function formatWhen(value) {
  if (!value) return "";
  const dt = new Date(value);
  return Number.isNaN(dt.getTime()) ? value : dt.toLocaleString();
}

function renderHistory(items) {
  const root = document.getElementById("history");
  if (!items.length) {
    root.innerHTML = "<p class='lede'>No applications yet.</p>";
    return;
  }
  root.innerHTML = items
    .map(
      (app) => `<button type="button" class="queue-item ${statusClass(app.status)}" data-id="${app.id}">
        <strong>${statusTitle(app.status)}</strong>
        <span>${app.id.slice(0, 8)} · ${formatWhen(app.updated_at)}</span>
      </button>`
    )
    .join("");
  root.querySelectorAll("[data-id]").forEach((button) => {
    button.addEventListener("click", () => loadApplication(button.getAttribute("data-id")));
  });
}

async function loadApplication(id) {
  const response = await fetch(`/v1/applications/${id}`);
  if (!response.ok) return null;
  const app = await response.json();
  renderStatus(app);
  return app;
}

async function refreshHistory() {
  const response = await fetch(`/v1/customers/${customerId()}/applications`);
  const items = response.ok ? (await response.json()).applications || [] : [];
  const known = new Set(items.map((item) => item.id));
  for (const id of loadIds()) {
    if (known.has(id)) continue;
    const extra = await fetch(`/v1/applications/${id}`);
    if (extra.ok) items.push(await extra.json());
  }
  localStorage.setItem(STORAGE_KEY, JSON.stringify(items.map((item) => item.id).slice(0, 20)));
  renderHistory(items);
  if (!current && items[0]) renderStatus(items[0]);
}

function startPoll(id) {
  stopPoll();
  pollTimer = setInterval(async () => {
    const app = await loadApplication(id);
    if (app && app.status !== "pending_review" && app.form_status !== "pending_review") refreshHistory();
  }, 4000);
}

function stopPoll() {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = null;
}

async function postPair(url) {
  const body = new FormData();
  body.append("front", frontInput.files[0]);
  body.append("back", backInput.files[0]);
  body.append("customer_id", customerId());
  const response = await fetch(url, { method: "POST", body });
  const payload = await response.json();
  if (!response.ok) {
    const detail = (Array.isArray(payload.reasons) && payload.reasons.length)
      ? payload.reasons.join("\n")
      : payload.customer_message
      || payload.detail;
    const message = typeof detail === "string"
      ? detail
      : Array.isArray(detail)
        ? detail.map((item) => item.msg || item).join(" ")
        : `Request failed (${response.status})`;
    throw new Error(message);
  }
  return payload;
}

bindDrop("drop-front", frontInput);
bindDrop("drop-back", backInput);
frontInput.addEventListener("change", () => {
  showPreview(frontInput, "front-preview", "front-meta", { set url(v) { frontUrl = v; }, get url() { return frontUrl; } });
  syncButtons();
});
backInput.addEventListener("change", () => {
  showPreview(backInput, "back-preview", "back-meta", { set url(v) { backUrl = v; }, get url() { return backUrl; } });
  syncButtons();
});

submitBtn.addEventListener("click", async () => {
  if (!bothReady()) return;
  submitBtn.disabled = true;
  statusLine.classList.remove("error");
  statusLine.textContent = "Submitting and processing…";
  try {
    const app = await postPair("/v1/applications");
    statusLine.classList.remove("error");
    saveId(app.id);
    renderStatus(app);
    await refreshHistory();
  } catch (error) {
    statusLine.classList.add("error");
    statusLine.textContent = error.message;
  } finally {
    syncButtons();
  }
});

resubmitBtn.addEventListener("click", async () => {
  if (!current || !bothReady()) return;
  resubmitBtn.disabled = true;
  statusLine.textContent = "Resubmitting…";
  try {
    const app = await postPair(`/v1/applications/${current.id}/resubmit`);
    statusLine.classList.remove("error");
    saveId(app.id);
    renderStatus(app);
    await refreshHistory();
  } catch (error) {
    statusLine.classList.add("error");
    statusLine.textContent = error.message;
  } finally {
    syncButtons();
  }
});

const formInput = document.getElementById("form-file");
const formSubmitBtn = document.getElementById("form-submit");
formInput.addEventListener("change", () => {
  const file = formInput.files[0];
  document.getElementById("form-meta").textContent = file
    ? `${file.name} · ${(file.size / 1024).toFixed(0)} KB`
    : "";
  const canUpload = Boolean(current && current.next_stage && current.next_stage.can_upload);
  formSubmitBtn.disabled = !(canUpload && file);
});
formSubmitBtn.addEventListener("click", async () => {
  if (!current || !formInput.files[0] || !(current.next_stage && current.next_stage.can_upload)) return;
  formSubmitBtn.disabled = true;
  statusLine.classList.remove("error");
  statusLine.textContent = "Analyzing completed form…";
  try {
    const body = new FormData();
    body.append("document", formInput.files[0]);
    const response = await fetch(`/v1/applications/${current.id}/form`, {
      method: "POST",
      body,
    });
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.detail || `Request failed (${response.status})`);
    }
    renderStatus(payload);
    await refreshHistory();
    statusLine.textContent = (payload.application_form && payload.application_form.message) || "Form submitted.";
  } catch (error) {
    statusLine.classList.add("error");
    statusLine.textContent = error.message;
  } finally {
    const canUpload = Boolean(current && current.next_stage && current.next_stage.can_upload);
    formSubmitBtn.disabled = !(canUpload && formInput.files[0]);
  }
});

refreshHistory().catch(() => {
  document.getElementById("history").innerHTML = "<p class='lede'>Could not load saved applications.</p>";
});
