"use strict";

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => Array.from(document.querySelectorAll(selector));
const STATUS = {match: "符合", partial: "部分符合", mismatch: "不符合", missing: "產品未記載", uncertain: "待釐清"};
const RUN_STATUS = {queued: "排隊中", running: "比對中", completed: "已完成", cancelled: "已停止", interrupted: "服務中斷", failed: "執行失敗"};
const DECISION = {pending: "尚待確認", confirmed: "已確認", changed: "已修正", reopened: "重新待確認"};
const ACTIVE = new Set(["queued", "running"]);
const PAGE_SIZE = 40;
const state = {projects: [], project: null, runs: [], run: null, settings: {}, step: "documents", selectedStandards: new Set(), previewDoc: null, detailId: null, detailVersion: 0, page: 1, selectionEpoch: 0, runSelectionEpoch: 0, mutationEpoch: 0, pollTimer: null, polling: false, connected: false};

function escapeHTML(value) { return String(value ?? "").replace(/[&<>"']/g, (c) => ({"&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;"})[c]); }
function dateLabel(value) { if (!value) return "—"; const date = new Date(value); return Number.isNaN(date.valueOf()) ? String(value) : date.toLocaleString("zh-TW", {year:"numeric",month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit"}); }
function integer(value) { return Number(value || 0).toLocaleString("zh-TW"); }
function storageGet(key, fallback = "") { try { return localStorage.getItem(key) ?? fallback; } catch { return fallback; } }
function storageSet(key, value) { try { localStorage.setItem(key, value); } catch { /* Storage is optional. */ } }
function isDemo() { return Boolean(state.project?.is_demo || state.project?.demo); }
function effectiveStatus(row) { return ["confirmed", "changed"].includes(row.review?.decision) && row.review?.final_status ? row.review.final_status : row.status; }
function isReviewed(row) { return ["confirmed", "changed"].includes(row.review?.decision); }
function pill(status) { return `<span class="status-pill ${escapeHTML(status)}">${escapeHTML(STATUS[status] || status)}</span>`; }
function notice(message, kind = "success") { const box = $("#notification"); box.textContent = message; box.className = `notification ${kind}`; box.hidden = false; }
function setDialog(id, open) { const dialog = document.getElementById(id); if (open && !dialog.open) { dialog.querySelectorAll(".dialog-error").forEach((box) => box.remove()); dialog.showModal(); } else if (!open && dialog.open) dialog.close(); }
function showError(error) {
  const message = error?.message || "操作未完成，請稍後再試。";
  const dialog = $("dialog[open]");
  if (dialog && dialog.id !== "review-dialog") {
    let box = dialog.querySelector(".dialog-error");
    if (!box) { box = document.createElement("div"); box.className = "notice notice-error dialog-error"; box.setAttribute("role", "alert"); dialog.querySelector(".dialog-heading").after(box); }
    box.textContent = message;
  }
  notice(message, "error");
}
function errorText(detail) { if (typeof detail === "string") return detail; if (Array.isArray(detail)) return detail.map((item) => `${(item.loc || []).join(" / ")}: ${item.msg || JSON.stringify(item)}`).join("；"); return JSON.stringify(detail); }

async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  if (options.body && !(options.body instanceof FormData)) headers.set("Content-Type", "application/json");
  let response;
  try { response = await fetch(path, {...options, headers, credentials:"same-origin", cache:"no-store"}); }
  catch { throw new Error("無法連線到本機比對服務，請確認啟動視窗仍在執行。已完成的結果會保留。"); }
  let data;
  const type = response.headers.get("content-type") || "";
  if (type.includes("application/json")) data = await response.json();
  else data = {detail: await response.text()};
  if (!response.ok) { const error = new Error(errorText(data.detail || `操作失敗（HTTP ${response.status}）`)); error.status = response.status; throw error; }
  return data;
}
async function withBusy(button, action, label = "處理中…") {
  const old = button.textContent; button.disabled = true; button.textContent = label;
  try { return await action(); } catch (error) { showError(error); }
  finally { button.disabled = false; button.textContent = old; }
}
function bind(selector, event, action) { $(selector).addEventListener(event, (ev) => { Promise.resolve(action(ev)).catch(showError); }); }

function renderProjects() {
  $("#project-list").innerHTML = state.projects.length ? state.projects.map((project) => `<button class="project-item ${state.project?.id === project.id ? "active" : ""}" data-project="${escapeHTML(project.id)}">${escapeHTML(project.name)}<small>${project.is_demo ? "範例專案 · " : ""}${dateLabel(project.created_at)}</small></button>`).join("") : '<p class="helper">尚無專案。按 ＋ 建立，或先試用範例。</p>';
}
function renderHeader() {
  $("#welcome").hidden = !!state.project;
  $("#workspace").hidden = !state.project;
  $("#project-title").textContent = state.project?.name || "讓每一項規格，都有據可查。";
  $("#project-subtitle").textContent = state.project ? `建立於 ${dateLabel(state.project.created_at)} · ${(state.project.documents || []).length} 份文件 · 原文與確認紀錄可追溯` : "匯入文件、逐條比對、人工確認，保留完整追溯紀錄。";
  $("#demo-banner").hidden = !isDemo();
  $("#run-mode-label").hidden = !isDemo();
}
function setStep(step) {
  state.step = step;
  $$(".step").forEach((button) => { const active = button.dataset.step === step; button.classList.toggle("active", active); if (active) button.setAttribute("aria-current", "step"); else button.removeAttribute("aria-current"); });
  $$(".workspace-panel").forEach((panel) => { panel.hidden = panel.id !== `panel-${step}`; });
  if (step === "review") renderReview();
  if (step === "reports") renderReports();
}
function renderDocuments() {
  const docs = state.project?.documents || [];
  $("#document-count").textContent = `${docs.length} 份文件 · ${docs.filter((doc) => doc.extraction_confirmed).length} 份已確認原文`;
  $("#document-list").innerHTML = docs.length ? docs.map((doc) => `<article class="document-row"><div class="document-type">${escapeHTML(doc.name.split(".").pop().slice(0,5).toUpperCase())}</div><div class="document-info"><strong>${escapeHTML(doc.name)}</strong><small>${doc.role === "product" ? "產品規格" : "技術規範"} · ${integer(doc.blocks?.length)} 個原文區塊${doc.warnings?.length ? ` · <span class="warning-text">${doc.warnings.length} 項抽取提醒</span>` : ""}</small></div><span class="status-pill ${doc.extraction_confirmed ? "match" : "partial"}">${doc.extraction_confirmed ? "原文已確認" : "待確認原文"}</span><button class="button button-small button-secondary" data-preview="${escapeHTML(doc.id)}">${doc.extraction_confirmed ? "查看原文" : "預覽並確認"}</button>${isDemo() ? "" : `<button class="button button-small button-quiet" data-remove-doc="${escapeHTML(doc.id)}" aria-label="移出文件 ${escapeHTML(doc.name)}">移出</button>`}</article>`).join("") : '<div class="empty-state">將產品文件與技術規範加入這個專案，就可以開始。</div>';
  const productExists = docs.some((doc) => doc.role === "product");
  $("#product-upload button[type=submit]").disabled = productExists || isDemo();
  $("#product-file").disabled = productExists || isDemo();
  $("#standard-upload button[type=submit]").disabled = isDemo();
  $("#standard-files").disabled = isDemo();
  renderChecklist();
}
function renderChecklist() {
  const docs = state.project?.documents || [];
  const standards = docs.filter((doc) => doc.role === "standard");
  $("#standard-checklist").innerHTML = standards.length ? standards.map((doc) => `<label class="standard-option"><input type="checkbox" data-standard="${escapeHTML(doc.id)}" ${state.selectedStandards.has(doc.id) ? "checked" : ""} ${!doc.extraction_confirmed || !doc.blocks?.length ? "disabled" : ""}><span>${escapeHTML(doc.name)}<small>${integer(doc.blocks?.length)} 個區塊 · ${doc.extraction_confirmed ? "原文已確認" : "請先回到步驟 01 確認原文"}</small></span></label>`).join("") : '<p class="helper">尚未加入技術規範。請回到步驟 01 匯入文件。</p>';
  renderReadiness();
}
function renderReadiness() {
  const docs = state.project?.documents || [];
  const product = docs.find((doc) => doc.role === "product");
  const selected = docs.filter((doc) => doc.role === "standard" && state.selectedStandards.has(doc.id) && doc.extraction_confirmed && doc.blocks?.length);
  const mode = isDemo() ? $("#run-mode").value : "local";
  const ready = Boolean(product?.extraction_confirmed && product?.blocks?.length && selected.length);
  $("#start-run").disabled = !ready;
  $("#start-run").textContent = mode === "demo" ? "開始範例示範比對 →" : "開始本機模型比對 →";
  $("#start-readiness").textContent = !product ? "請先匯入一份產品規格文件。" : !product.extraction_confirmed ? "請先預覽並確認產品文件原文。" : !selected.length ? "請選擇至少一份已確認的技術規範。" : `將比對 ${selected.length} 份規範、${integer(selected.reduce((sum, doc) => sum + doc.blocks.length, 0))} 個區塊。每個區塊都會保留判斷與追溯資料。`;
}
function renderSettingsSummary() {
  $("#model-name").textContent = state.settings.model || "尚未設定模型";
  $("#model-endpoint").textContent = state.settings.base_url || "http://127.0.0.1:1234/v1";
  const badge = $("#connection-badge");
  badge.textContent = state.connected ? "● 本機模型連線成功" : state.settings.model ? "已設定模型 · 尚未測試連線" : "尚未測試模型連線";
  badge.classList.toggle("connected", state.connected);
}

async function refreshProjects() {
  const data = await api("/api/projects"); state.projects = Array.isArray(data) ? data : data.projects || []; renderProjects();
}
async function selectProject(id) {
  const epoch = ++state.selectionEpoch;
  clearTimeout(state.pollTimer); state.run = null; state.detailId = null; state.page = 1;
  setDialog("review-dialog", false); setDialog("document-dialog", false);
  const [project, runsData] = await Promise.all([api(`/api/projects/${encodeURIComponent(id)}`), api(`/api/projects/${encodeURIComponent(id)}/runs`)]);
  if (epoch !== state.selectionEpoch) return;
  state.project = project; state.runs = Array.isArray(runsData) ? runsData : runsData.runs || [];
  state.selectedStandards = new Set((project.documents || []).filter((doc) => doc.role === "standard" && doc.extraction_confirmed && doc.blocks?.length).map((doc) => doc.id));
  $("#run-mode").value = isDemo() ? "demo" : "local";
  storageSet("specheck-project", id);
  $("#filter-standard").value = ""; $("#filter-status").value = ""; $("#filter-review").value = ""; $("#filter-search").value = "";
  renderProjects(); renderHeader(); renderDocuments(); renderRunList(); renderMonitor(); renderReview(); renderReports(); setStep("documents");
  if (state.runs.length) await selectRun(state.runs[0].id, false);
}
async function refreshProjectDocuments() {
  const id = state.project.id;
  const project = await api(`/api/projects/${encodeURIComponent(id)}`);
  if (state.project?.id !== id) return;
  state.project = project;
  const ids = new Set(project.documents.map((doc) => doc.id));
  state.selectedStandards = new Set([...state.selectedStandards].filter((item) => ids.has(item)));
  renderHeader(); renderDocuments();
}
async function uploadFiles(role, form) {
  if (!state.project) return;
  const fileInput = role === "product" ? $("#product-file") : $("#standard-files");
  const files = Array.from(fileInput.files || []);
  if (!files.length) return;
  const projectId = state.project.id;
  const button = form.querySelector("button[type=submit]");
  await withBusy(button, async () => {
    let completed = 0;
    const failures = [];
    for (const file of files) {
      button.textContent = `匯入 ${completed + failures.length + 1} / ${files.length}…`;
      const body = new FormData(); body.append("file", file); body.append("role", role);
      try { await api(`/api/projects/${encodeURIComponent(projectId)}/documents`, {method:"POST", body}); completed++; }
      catch (error) { failures.push(`${file.name}：${error.message}`); }
    }
    form.reset();
    if (state.project?.id === projectId) await refreshProjectDocuments();
    notice(`已匯入 ${completed} 份文件。${failures.length ? ` ${failures.length} 份未成功：${failures.join("；")}` : "請逐份預覽並確認抽取的原文。"}`, failures.length ? "error" : "success");
  }, "正在匯入…");
  renderDocuments();
}
function previewDocument(id) {
  const doc = state.project.documents.find((item) => item.id === id); if (!doc) return;
  state.previewDoc = doc;
  $("#preview-role").textContent = doc.role === "product" ? "產品規格文件" : "技術規範文件";
  $("#preview-name").textContent = doc.name;
  $("#preview-warnings").innerHTML = doc.warnings?.length ? `<div class="notice notice-demo"><strong>文字抽取提醒</strong><ul>${doc.warnings.map((item) => `<li>${escapeHTML(item)}</li>`).join("")}</ul></div>` : '<div class="notice">請核對數值、單位、表格欄位與順序。原文區塊涵蓋抽取文字，不能保證圖像或全部語意已辨識。</div>';
  $("#preview-count").textContent = `${integer(doc.blocks?.length)} 個原文區塊 · SHA-256 ${doc.sha256?.slice(0, 16) || "—"}…`;
  $("#preview-original").href = `/api/projects/${encodeURIComponent(state.project.id)}/documents/${encodeURIComponent(doc.id)}/original`;
  $("#preview-blocks").innerHTML = doc.blocks?.length ? doc.blocks.map((block) => `<div class="source-block"><span class="source-block-label">${escapeHTML(block.id)} · ${escapeHTML(block.location)}</span><p>${escapeHTML(block.text)}</p></div>`).join("") : '<div class="empty-state">沒有可比對的文字。請先使用 OCR 或整理成可抽取文字的檔案，再重新匯入。</div>';
  $("#preview-check").checked = !!doc.extraction_confirmed;
  $("#preview-check").disabled = !!doc.extraction_confirmed || !doc.blocks?.length;
  $("#confirm-document").hidden = !!doc.extraction_confirmed;
  $("#confirm-document").disabled = true;
  $("#preview-confirmed").hidden = !doc.extraction_confirmed;
  setDialog("document-dialog", true);
}
async function confirmDocument() {
  if (!state.previewDoc || !$("#preview-check").checked) return;
  const docId = state.previewDoc.id, projectId = state.project.id, role = state.previewDoc.role;
  await withBusy($("#confirm-document"), async () => {
    await api(`/api/projects/${encodeURIComponent(projectId)}/documents/${encodeURIComponent(docId)}/confirm`, {method:"POST"});
    if (state.project?.id !== projectId) return;
    if (role === "standard") state.selectedStandards.add(docId);
    await refreshProjectDocuments();
    if (state.previewDoc?.id === docId && $("#document-dialog").open) previewDocument(docId);
    notice("已確認文件原文，這份文件可以加入比對。");
  });
}
async function removeDocument(id) {
  const doc = state.project.documents.find((item) => item.id === id); if (!doc) return;
  if (!window.confirm(`要從這個專案移出「${doc.name}」嗎？\n既有比對批次的原文快照與紀錄會保留。`)) return;
  await api(`/api/projects/${encodeURIComponent(state.project.id)}/documents/${encodeURIComponent(id)}`, {method:"DELETE"});
  await refreshProjectDocuments(); notice("已移出文件。既有比對紀錄仍然保留，可重新匯入文件。");
}

function renderRunList() {
  $("#run-list").innerHTML = state.runs.length ? state.runs.map((run) => `<article class="run-row"><span class="status-pill ${run.status === "completed" ? "match" : ACTIVE.has(run.status) ? "partial" : "uncertain"}">${RUN_STATUS[run.status] || escapeHTML(run.status)}</span><div class="run-info"><strong>${dateLabel(run.created_at)}</strong>${run.mode === "demo" ? ' <span class="status-pill partial">示範</span>' : ""}<small>${integer(run.completed)} / ${integer(run.total)} 個區塊 · 批次 ${escapeHTML(run.id.slice(0,8))}</small></div><button class="button button-small button-secondary" data-run="${escapeHTML(run.id)}">查看結果 →</button></article>`).join("") : '<div class="empty-state">尚無比對批次。確認文件並選好規範後，即可開始。</div>';
  const options = state.runs.length ? state.runs.map((run) => `<option value="${escapeHTML(run.id)}">${dateLabel(run.created_at)} · ${RUN_STATUS[run.status] || escapeHTML(run.status)}${run.mode === "demo" ? " · 示範" : ""} · ${escapeHTML(run.id.slice(0,6))}</option>`).join("") : '<option value="">尚無比對批次</option>';
  for (const selector of ["#review-run-select", "#report-run-select"]) { $(selector).innerHTML = options; if (state.run) $(selector).value = state.run.id; $(selector).disabled = !state.runs.length; }
}
function updateRun(run) {
  state.run = run;
  const index = state.runs.findIndex((item) => item.id === run.id);
  const brief = {...run}; delete brief.results; delete brief.documents;
  if (index >= 0) state.runs[index] = brief; else state.runs.unshift(brief);
  renderRunList(); renderMonitor();
  if (state.step === "review") renderReview();
  if (state.step === "reports") renderReports();
  schedulePoll();
}
async function selectRun(id, navigate = true) {
  if (!id) return;
  const epoch = state.selectionEpoch, runEpoch = ++state.runSelectionEpoch;
  const run = await api(`/api/runs/${encodeURIComponent(id)}`);
  if (epoch !== state.selectionEpoch || runEpoch !== state.runSelectionEpoch || run.project_id !== state.project?.id) return;
  state.page = 1; $("#filter-standard").value = "";
  updateRun(run);
  if (navigate) setStep("review");
}
async function refreshRuns() {
  if (!state.project) return;
  const projectId = state.project.id;
  const data = await api(`/api/projects/${encodeURIComponent(projectId)}/runs`);
  if (state.project.id !== projectId) return;
  state.runs = Array.isArray(data) ? data : data.runs || [];
  renderRunList();
  if (state.run) { const run = await api(`/api/runs/${encodeURIComponent(state.run.id)}`); if (run.project_id === state.project.id) updateRun(run); }
}
function renderMonitor() {
  const run = state.run;
  $("#run-monitor").hidden = !run;
  if (!run) return;
  const total = Number(run.total || 0), completed = Number(run.completed || 0);
  const percent = total ? Math.min(100, completed / total * 100) : 0;
  const active = ACTIVE.has(run.status);
  $("#run-monitor").innerHTML = `<div class="monitor-top"><div class="monitor-title"><strong>${RUN_STATUS[run.status] || escapeHTML(run.status)}${run.mode === "demo" ? " · 示範資料" : ""}</strong>${integer(completed)} / ${integer(total)} 個區塊 · ${percent.toFixed(0)}% <span class="muted">· 批次 ${escapeHTML(run.id.slice(0,8))}</span></div><div class="monitor-actions">${active ? '<button id="cancel-run" class="button button-small button-secondary">停止比對</button>' : ["cancelled", "interrupted", "failed"].includes(run.status) ? '<button id="resume-run" class="button button-small button-primary">繼續未完成項目</button>' : ""}<button class="button button-small button-quiet" data-go="review">查看結果 →</button></div></div><div class="progress-track" role="progressbar" aria-label="規範比對進度" aria-valuenow="${completed}" aria-valuemax="${total}" aria-valuemin="0"><span style="width:${percent}%"></span></div><p class="monitor-note">${active ? "正在逐區塊檢查產品原文；每完成一項就會儲存。停止指令會在目前模型請求返回後生效。" : run.status === "completed" ? "本次區塊處理已完成。請核對產品證據與差異，AI 不保證找出全部語意差異。" : "已完成結果保留；可繼續處理未完成區塊。未完成時的排名僅供暫時參考。"}${run.error ? ` ${escapeHTML(run.error)}` : ""}</p>`;
}
function schedulePoll() {
  clearTimeout(state.pollTimer);
  if (!state.run || !ACTIVE.has(state.run.status)) return;
  state.pollTimer = setTimeout(pollRun, 1800);
}
async function pollRun() {
  if (!state.run || state.polling) { schedulePoll(); return; }
  const id = state.run.id, epoch = state.selectionEpoch, mutationEpoch = state.mutationEpoch;
  state.polling = true;
  try {
    const run = await api(`/api/runs/${encodeURIComponent(id)}`);
    if (state.run?.id === id && epoch === state.selectionEpoch && mutationEpoch === state.mutationEpoch) updateRun(run);
    else schedulePoll();
  }
  catch (error) { if (state.run?.id === id) { notice(`${error.message} 將自動重試。`, "error"); state.pollTimer = setTimeout(pollRun, 5000); } }
  finally { state.polling = false; }
}
async function startRun(modeOverride) {
  if (!state.project) return;
  const mode = modeOverride || (isDemo() ? $("#run-mode").value : "local");
  if (mode === "local" && !state.settings.model) { openSettings(); notice("請先選擇本機模型、測試連線並儲存設定。", "error"); return; }
  const projectId = state.project.id;
  const standardIds = state.project.documents.filter((doc) => doc.role === "standard" && state.selectedStandards.has(doc.id) && doc.extraction_confirmed).map((doc) => doc.id);
  if (!standardIds.length) { notice("請先選擇至少一份已確認原文的規範。", "error"); return; }
  const run = await api(`/api/projects/${encodeURIComponent(projectId)}/runs`, {method:"POST", body:JSON.stringify({standard_ids:standardIds,mode})});
  if (state.project?.id !== projectId) return;
  state.page = 1; updateRun(run); setStep("review");
  notice(mode === "demo" ? "範例比對已啟動。這些結果是固定示範判斷，不代表模型能力。" : "本機比對已啟動。可以隨時查看已完成的項目與原文證據。");
}
async function controlRun(action) {
  const id = state.run?.id; if (!id) return;
  const button = $(`#${action === "cancel" ? "cancel" : "resume"}-run`);
  await withBusy(button, async () => {
    const run = await api(`/api/runs/${encodeURIComponent(id)}/${action}`, {method:"POST"});
    if (state.run?.id === id) updateRun(run);
    notice(action === "cancel" ? "已送出停止指令；目前模型請求完成後會停止，已完成結果會保留。" : "已繼續比對，會略過已儲存的區塊。");
  });
}

function filteredRows() {
  const standard = $("#filter-standard").value, status = $("#filter-status").value, review = $("#filter-review").value, search = $("#filter-search").value.trim().toLocaleLowerCase();
  return (state.run?.results || []).filter((row) => (!standard || row.standard_id === standard) && (!status || effectiveStatus(row) === status) && (!review || (review === "reviewed" ? isReviewed(row) : !isReviewed(row))) && (!search || [row.standard_name,row.location,row.requirement,row.explanation,...(row.differences || []),...(row.evidence || []).map((item) => item.quote),row.review?.note,row.review?.reviewer].join(" ").toLocaleLowerCase().includes(search)));
}
function renderReview() {
  const run = state.run;
  $("#review-empty").hidden = !!run; $("#review-content").hidden = !run;
  if (!run) return;
  const filter = $("#filter-standard").value;
  const standards = (run.documents || []).filter((doc) => doc.role === "standard");
  $("#filter-standard").innerHTML = '<option value="">全部規範</option>' + standards.map((doc) => `<option value="${escapeHTML(doc.id)}">${escapeHTML(doc.name)}</option>`).join("");
  if (standards.some((doc) => doc.id === filter)) $("#filter-standard").value = filter;
  const rankings = [...(run.rankings || [])].sort((a,b) => Number(b.score || 0) - Number(a.score || 0));
  $("#ranking-list").innerHTML = rankings.map((item,index) => `<button class="ranking-card ${$("#filter-standard").value === item.standard_id ? "selected" : ""}" data-ranking="${escapeHTML(item.standard_id)}"><span class="ranking-label">${index + 1}. ${escapeHTML(item.standard_name)}</span><span class="ranking-score">${Number(item.score || 0).toFixed(1)}<small>/ 100${run.status !== "completed" ? " · 暫定" : ""}</small></span><span class="ranking-detail">文件符合度參考分數<br>已處理 ${integer(item.completed)} / ${integer(item.total)} · 已確認 ${integer(item.reviewed)}<br>符合 ${integer(item.counts?.match)} · 部分 ${integer(item.counts?.partial)} · 不符合 ${integer(item.counts?.mismatch)}<br>未記載 ${integer(item.counts?.missing)} · 待釐清 ${integer(item.counts?.uncertain)}</span></button>`).join("");
  renderResultTable();
}
function renderResultTable() {
  const rows = filteredRows(), all = state.run?.results || [], pages = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
  state.page = Math.min(Math.max(1,state.page),pages);
  const visible = rows.slice((state.page - 1) * PAGE_SIZE,state.page * PAGE_SIZE);
  $("#result-summary").textContent = `顯示 ${integer(rows.length)} / ${integer(all.length)} 項 · 全批次 ${integer(all.filter(isReviewed).length)} 項已確認，${integer(all.filter((row) => !isReviewed(row)).length)} 項待確認`;
  $("#result-rows").innerHTML = visible.length ? visible.map((row) => `<tr data-result="${escapeHTML(row.id)}"><td><button class="row-open" data-result="${escapeHTML(row.id)}" aria-label="查看 ${escapeHTML(row.standard_name)} ${escapeHTML(row.location)}">${escapeHTML(row.standard_name)}</button><span class="cell-source">${escapeHTML(row.location)} · ${escapeHTML(row.block_id)}</span></td><td><span class="cell-text">${escapeHTML(row.requirement)}</span></td><td><span class="cell-text">${escapeHTML((row.differences || []).join("；") || row.explanation || "—")}</span></td><td>${pill(effectiveStatus(row))}${row.review?.decision === "changed" ? `<span class="cell-source">AI：${escapeHTML(STATUS[row.status])}</span>` : ""}</td><td><span class="status-pill ${isReviewed(row) ? "reviewed" : "pending"}">${DECISION[row.review?.decision] || "尚待確認"}</span>${row.review?.reviewer ? `<span class="cell-source">${escapeHTML(row.review.reviewer)}</span>` : ""}</td></tr>`).join("") : `<tr><td class="table-empty" colspan="5">${all.length ? "沒有符合目前篩選條件的項目。" : "尚無已完成區塊。比對開始後，結果會陸續顯示。"}</td></tr>`;
  $("#page-summary").textContent = `第 ${state.page} / ${pages} 頁 · 每頁 ${PAGE_SIZE} 項`;
  $("#prev-page").disabled = state.page <= 1; $("#next-page").disabled = state.page >= pages;
  $("#next-pending").disabled = !rows.some((row) => !isReviewed(row));
}
function renderDetailMeta(row) {
  const manual = isReviewed(row) ? `${row.review.decision === "changed" ? "人工修正" : "人工確認"} ${pill(effectiveStatus(row))}` : row.review?.decision === "reopened" ? '<span class="status-pill pending">已重新開啟，待確認</span>' : "";
  $("#detail-meta").innerHTML = `AI 判斷 ${pill(row.status)}${manual}<span>模型自報信心 ${Math.round(Number(row.confidence || 0) * 100)}%（非實測正確率）</span>${state.run.mode === "demo" ? '<span class="status-pill partial">固定示範結果</span>' : ""}`;
}
function openResult(id) {
  const row = state.run?.results?.find((item) => item.id === id); if (!row) return;
  state.detailId = id; state.detailVersion = Number(row.review?.version || 0);
  $("#detail-location").textContent = `${row.standard_name} · ${row.location} · ${row.block_id}`;
  renderDetailMeta(row);
  $("#detail-requirement").textContent = row.requirement;
  const product = (state.run.documents || []).find((doc) => doc.role === "product");
  $("#detail-evidence").innerHTML = row.evidence?.length ? row.evidence.map((item) => { const block = product?.blocks?.find((candidate) => candidate.id === item.block_id); return `<div class="evidence-quote"><cite>${escapeHTML(product?.name || "產品文件")} · ${escapeHTML(item.location)} · ${escapeHTML(item.block_id)}</cite><blockquote>${escapeHTML(item.quote)}</blockquote>${block ? `<details><summary>展開完整產品原文區塊</summary><p>${escapeHTML(block.text)}</p></details>` : ""}</div>`; }).join("") : '<p class="helper">這一項沒有可驗證的產品原文引文。請檢查原始產品文件；資訊未記載不等於產品不符合。</p>';
  if (product) {
    $("#detail-evidence").insertAdjacentHTML("beforeend", `<div class="snapshot-source-links"><a href="/api/projects/${encodeURIComponent(state.run.project_id)}/documents/${encodeURIComponent(product.id)}/original">下載本批次產品原始文件 ↓</a><details id="full-product-source"><summary>查閱完整產品原文快照（${integer(product.blocks?.length)} 個區塊）</summary><label class="helper">搜尋產品原文<input id="product-source-search" type="search" placeholder="輸入數值、單位或關鍵字"></label><div id="product-source-blocks"></div></details></div>`);
    const renderSource = () => {
      const term = $("#product-source-search").value.trim().toLocaleLowerCase();
      const blocks = (product.blocks || []).filter((block) => !term || `${block.location} ${block.text}`.toLocaleLowerCase().includes(term));
      $("#product-source-blocks").innerHTML = blocks.length ? blocks.map((block) => `<div class="source-block"><span class="source-block-label">${escapeHTML(block.id)} · ${escapeHTML(block.location)}</span><p>${escapeHTML(block.text)}</p></div>`).join("") : '<p class="helper">沒有符合搜尋的區塊。</p>';
    };
    $("#full-product-source").addEventListener("toggle", () => { if ($("#full-product-source").open) renderSource(); });
    $("#product-source-search").addEventListener("input", renderSource);
  }
  $("#detail-requirement").parentElement.querySelector(".source-download")?.remove();
  const standardLink = document.createElement("a"); standardLink.className = "source-download";
  standardLink.href = `/api/projects/${encodeURIComponent(state.run.project_id)}/documents/${encodeURIComponent(row.standard_id)}/original`;
  standardLink.textContent = "下載本批次規範原始文件 ↓";
  $("#detail-requirement").after(standardLink);
  $("#detail-explanation").textContent = row.explanation || "尚無說明。";
  $("#detail-differences").innerHTML = (row.differences || []).map((difference) => `<li>${escapeHTML(difference)}</li>`).join("");
  $("#detail-warnings").innerHTML = row.warnings?.length ? `<div class="notice notice-demo"><strong>需注意</strong><ul>${row.warnings.map((item) => `<li>${escapeHTML(item)}</li>`).join("")}</ul></div>` : "";
  $("#detail-coverage").textContent = `產品原文分段完成 ${integer(row.product_coverage?.scanned)} / ${integer(row.product_coverage?.total)}。分段完成數代表執行覆蓋，不代表已辨識全部原子條件或語意差異。`;
  $("#detail-version").textContent = `紀錄版本 v${state.detailVersion}`;
  $("#review-decision").value = "confirmed";
  $("#review-final-status").value = row.status;
  $("#reviewer").value = storageGet("specheck-reviewer");
  $("#review-note").value = "";
  $("#review-error").hidden = true;
  updateReviewControls(); renderHistory(row); setDialog("review-dialog", true);
}
function updateReviewControls() {
  const decision = $("#review-decision").value;
  const row = state.run?.results?.find((item) => item.id === state.detailId);
  $("#review-final-status").disabled = decision !== "changed";
  if (row && decision !== "changed") $("#review-final-status").value = row.status;
  $("#review-note").required = decision === "changed" || decision === "reopened";
  $("#review-note").placeholder = decision === "reopened" ? "請填寫重新開啟的原因（必填）。" : decision === "changed" ? "請填寫修正依據與原因（必填）。" : "記錄確認依據，可填原始文件位置或補充說明。";
}
function renderHistory(row) {
  const history = row.history || [];
  $("#history-count").textContent = `（${history.length} 筆）`;
  $("#review-history").innerHTML = history.length ? [...history].reverse().map((event) => `<article class="history-item"><strong>v${integer(event.version)} · ${escapeHTML(event.reviewer)} · ${escapeHTML(DECISION[event.decision] || event.decision)}${event.final_status ? ` → ${escapeHTML(STATUS[event.final_status])}` : ""}</strong><small>${dateLabel(event.created_at)}</small><p>${escapeHTML(event.note || "未填寫附註")}</p></article>`).join("") : '<p class="helper">尚無人工確認紀錄。</p>';
}
async function saveReview(event) {
  event.preventDefault();
  const rowId = state.detailId, runId = state.run?.id; if (!rowId || !runId) return;
  const form = $("#review-form"), button = form.querySelector('button[type="submit"]');
  const payload = {decision:$("#review-decision").value, final_status:$("#review-decision").value === "reopened" ? null : $("#review-final-status").value, reviewer:$("#reviewer").value.trim(), note:$("#review-note").value.trim(), expected_version:state.detailVersion};
  if (!payload.reviewer || (["changed", "reopened"].includes(payload.decision) && !payload.note)) { $("#review-error").textContent = "請填寫確認人員；修正或重新開啟時也需填寫原因。"; $("#review-error").hidden = false; return; }
  button.disabled = true; button.textContent = "儲存中…"; $("#review-next").disabled = true; $("#review-error").hidden = true;
  state.mutationEpoch++;
  try {
    const row = await api(`/api/results/${encodeURIComponent(rowId)}/reviews`, {method:"POST",body:JSON.stringify(payload)});
    state.mutationEpoch++;
    storageSet("specheck-reviewer",payload.reviewer);
    if (state.run?.id !== runId) { notice("人工確認已儲存至原比對批次。"); return; }
    const index = state.run.results.findIndex((item) => item.id === row.id); if (index >= 0) state.run.results[index] = row;
    if (state.detailId === rowId) {
      state.detailVersion = Number(row.review?.version || 0); $("#detail-version").textContent = `紀錄版本 v${state.detailVersion} · 已儲存`;
      renderDetailMeta(row); renderHistory(row); $("#review-note").value = "";
    }
    const run = await api(`/api/runs/${encodeURIComponent(runId)}`); if (run.id === state.run?.id) updateRun(run);
    notice("人工確認已儲存，這次操作已新增至確認歷程。");
    if (state.detailId === rowId) { $("#review-error").textContent = "✓ 本次確認已儲存。可繼續下一筆，或留下新的確認紀錄。"; $("#review-error").className = "notice"; $("#review-error").hidden = false; }
  } catch (error) {
    if (state.detailId !== rowId || state.run?.id !== runId) { showError(error); return; }
    $("#review-error").className = "notice notice-error";
    $("#review-error").textContent = error.status === 409 ? "這一項已有其他新確認紀錄，為避免覆寫，本次未儲存。你的草稿仍保留；請先複製草稿，關閉並重新開啟這一列後再確認。" : error.message;
    $("#review-error").hidden = false;
    if (error.status === 409) { try { const run = await api(`/api/runs/${encodeURIComponent(runId)}`); if (state.run?.id === runId) updateRun(run); } catch { /* Keep the current draft visible. */ } }
  } finally { button.disabled = false; button.textContent = "儲存本次確認"; $("#review-next").disabled = false; schedulePoll(); }
}
function nextPending() {
  const rows = filteredRows().filter((row) => !isReviewed(row));
  if (!rows.length) { notice("目前篩選範圍內，所有已產生的結果都已確認。"); return; }
  const current = rows.findIndex((row) => row.id === state.detailId);
  const next = rows[current < 0 ? 0 : (current + 1) % rows.length];
  if ($("#review-dialog").open && $("#review-note").value.trim() && !window.confirm("本次說明尚未儲存。要放棄這段草稿並開啟下一筆嗎？")) return;
  openResult(next.id);
}
function renderReports() {
  const run = state.run;
  $("#report-empty").hidden = !!run; $("#report-content").hidden = !run;
  if (!run) return;
  const rows = run.results || [], reviewed = rows.filter(isReviewed).length;
  $("#report-stats").innerHTML = [["全部規範區塊",run.total],["已產生結果",rows.length],["已人工確認",reviewed],["尚待人工確認",rows.length - reviewed]].map(([label,value]) => `<div class="stat-card"><span>${label}</span><strong>${integer(value)}</strong></div>`).join("");
  for (const format of ["html","xlsx","json"]) $("#export-" + format).href = `/api/runs/${encodeURIComponent(run.id)}/export?format=${format}`;
  const docs = run.documents || [];
  $("#snapshot-summary").innerHTML = `<dl class="snapshot-grid"><dt>比對批次</dt><dd>${escapeHTML(run.id)}</dd><dt>執行模式</dt><dd>${run.mode === "demo" ? "範例固定示範（沒有呼叫模型）" : "本機模型實際比對"}</dd><dt>執行狀態</dt><dd>${RUN_STATUS[run.status] || escapeHTML(run.status)}${run.status !== "completed" ? " · 尚未完成，參考分數為暫定" : ""}</dd><dt>建立／完成</dt><dd>${dateLabel(run.created_at)} ／ ${dateLabel(run.finished_at)}</dd><dt>模型</dt><dd>${escapeHTML(run.settings?.model || (run.mode === "demo" ? "固定示範判斷" : "—"))}</dd><dt>來源快照</dt><dd><ul class="snapshot-files">${docs.map((doc) => `<li>${escapeHTML(doc.name)} · ${doc.role === "product" ? "產品" : "規範"} · ${integer(doc.blocks?.length)} 區塊<br><small>SHA-256：${escapeHTML(doc.sha256 || "—")}</small></li>`).join("")}</ul></dd></dl>`;
  $("#snapshot-json").textContent = JSON.stringify({id:run.id,mode:run.mode,status:run.status,created_at:run.created_at,finished_at:run.finished_at,settings:run.settings,documents:run.documents},null,2);
  renderExportHistory();
}
function renderExportHistory() {
  const exports = state.run?.exports || [];
  $("#export-history").innerHTML = exports.length ? `<div class="table-scroll"><table class="results-table"><thead><tr><th>時間</th><th>格式與檔案</th><th>SHA-256 指紋</th><th>封存報告</th></tr></thead><tbody>${[...exports].reverse().map((item) => `<tr><td>${dateLabel(item.created_at)}</td><td><strong>${escapeHTML(item.format.toUpperCase())}</strong><span class="cell-source">${escapeHTML(item.filename)}</span></td><td><span class="hash-value">${escapeHTML(item.sha256)}</span></td><td><a class="button button-small button-secondary" href="/api/exports/${encodeURIComponent(item.id)}/download">下載原封存檔 ↓</a></td></tr>`).join("")}</tbody></table></div>` : '<p class="helper">尚無已封存的匯出紀錄。下載上方任一格式後，按重新整理。</p>';
}
async function refreshExports() {
  if (!state.run) return;
  const id = state.run.id;
  const data = await api(`/api/runs/${encodeURIComponent(id)}/exports`);
  if (state.run?.id !== id) return;
  state.run.exports = Array.isArray(data) ? data : data.exports || [];
  renderExportHistory();
}

function openSettings() {
  const settings = state.settings;
  $("#setting-base-url").value = settings.base_url || "http://127.0.0.1:1234/v1";
  $("#setting-model").value = settings.model || "";
  $("#setting-api-key").value = ""; $("#setting-clear-key").checked = false;
  $("#api-key-hint").textContent = settings.has_api_key ? "已儲存金鑰；留空會保留。金鑰不會加入報告。" : "尚未儲存金鑰；沒有要求驗證的本機服務可留空。";
  $("#setting-temperature").value = settings.temperature ?? 0.1;
  $("#setting-max-tokens").value = settings.max_tokens ?? 1800;
  $("#setting-timeout").value = settings.timeout ?? 180;
  $("#setting-context-chars").value = settings.context_chars ?? 6000;
  $("#setting-structured").checked = settings.structured_output !== false;
  $("#connection-result").hidden = true;
  setDialog("settings-dialog",true);
}
function readSettings() {
  return {base_url:$("#setting-base-url").value.trim(), model:$("#setting-model").value.trim(),api_key:$("#setting-api-key").value,clear_api_key:$("#setting-clear-key").checked,temperature:Number($("#setting-temperature").value),max_tokens:Number($("#setting-max-tokens").value),timeout:Number($("#setting-timeout").value),context_chars:Number($("#setting-context-chars").value),structured_output:$("#setting-structured").checked};
}
async function testConnection() {
  await withBusy($("#test-connection"),async () => {
    const box = $("#connection-result"); box.hidden = false; box.className = "notice"; box.textContent = "正在連線至本機服務…";
    try {
      const result = await api("/api/connection",{method:"POST",body:JSON.stringify({settings:readSettings()})});
      const models = result.models || [];
      $("#model-options").innerHTML = models.map((model) => `<option value="${escapeHTML(model.id)}"></option>`).join("");
      $("#setting-model-select").innerHTML = '<option value="">選擇模型</option>' + models.map((model) => `<option value="${escapeHTML(model.id)}">${escapeHTML(model.id)}</option>`).join("");
      $("#model-select-label").hidden = !models.length;
      if (!$("#setting-model").value && models.length) $("#setting-model").value = models[0].id;
      $("#setting-model-select").value = $("#setting-model").value;
      box.textContent = `${result.message || "連線成功"} · 偵測到 ${models.length} 個模型。請確認選取的模型 ID，並按「儲存設定」。`;
      state.connected = true; renderSettingsSummary();
    } catch(error) { state.connected = false; renderSettingsSummary(); box.className = "notice notice-error"; box.textContent = error.message; }
  },"連線測試中…");
}
async function createDemo(button) {
  await withBusy(button,async () => {
    const project = await api("/api/demo",{method:"POST"});
    await refreshProjects(); await selectProject(project.id); await startRun("demo");
  },"建立範例中…");
}

function setupEvents() {
  for (const selector of ["#new-project","#welcome-create"]) bind(selector,"click",() => { $("#project-name").value = ""; setDialog("project-dialog",true); $("#project-name").focus(); });
  for (const selector of ["#settings-button","#compare-settings"]) bind(selector,"click",openSettings);
  for (const selector of ["#demo-button","#welcome-demo"]) bind(selector,"click",(event) => createDemo(event.currentTarget));
  $$("[data-close]").forEach((button) => button.addEventListener("click",() => setDialog(button.dataset.close,false)));
  $$("[data-step]").forEach((button) => button.addEventListener("click",() => setStep(button.dataset.step)));
  document.addEventListener("click",(event) => {
    const go = event.target.closest("[data-go]"); if (go) setStep(go.dataset.go);
    const project = event.target.closest("[data-project]"); if (project) selectProject(project.dataset.project).catch(showError);
    const preview = event.target.closest("[data-preview]"); if (preview) previewDocument(preview.dataset.preview);
    const remove = event.target.closest("[data-remove-doc]"); if (remove) removeDocument(remove.dataset.removeDoc).catch(showError);
    const run = event.target.closest("[data-run]"); if (run) selectRun(run.dataset.run).catch(showError);
    const row = event.target.closest("[data-result]"); if (row) openResult(row.dataset.result);
    const ranking = event.target.closest("[data-ranking]"); if (ranking) { $("#filter-standard").value = $("#filter-standard").value === ranking.dataset.ranking ? "" : ranking.dataset.ranking; state.page = 1; renderReview(); }
    if (event.target.closest("#cancel-run")) controlRun("cancel").catch(showError);
    if (event.target.closest("#resume-run")) controlRun("resume").catch(showError);
  });
  bind("#project-form","submit",async (event) => {
    event.preventDefault();
    const name = $("#project-name").value.trim(); if (!name) return;
    await withBusy($("#project-form button[type=submit]"),async () => {
      const project = await api("/api/projects",{method:"POST",body:JSON.stringify({name})});
      setDialog("project-dialog",false); await refreshProjects(); await selectProject(project.id); notice("專案已建立。請加入產品規格與技術規範。");
    });
  });
  bind("#product-upload","submit",(event) => { event.preventDefault(); return uploadFiles("product",event.currentTarget); });
  bind("#standard-upload","submit",(event) => { event.preventDefault(); return uploadFiles("standard",event.currentTarget); });
  bind("#preview-check","change",() => { $("#confirm-document").disabled = !$("#preview-check").checked || !state.previewDoc?.blocks?.length; });
  bind("#confirm-document","click",confirmDocument);
  bind("#standard-checklist","change",(event) => { const input = event.target.closest("[data-standard]"); if (!input) return; if (input.checked) state.selectedStandards.add(input.dataset.standard); else state.selectedStandards.delete(input.dataset.standard); renderReadiness(); });
  bind("#run-mode","change",renderReadiness);
  bind("#start-run","click",async (event) => { await withBusy(event.currentTarget,() => startRun(),"建立比對中…"); renderReadiness(); });
  bind("#refresh-runs","click",(event) => withBusy(event.currentTarget,refreshRuns));
  bind("#refresh-exports","click",(event) => withBusy(event.currentTarget,refreshExports));
  for (const selector of ["#review-run-select","#report-run-select"]) bind(selector,"change",(event) => selectRun(event.target.value,false));
  for (const selector of ["#filter-standard","#filter-status","#filter-review"]) bind(selector,"change",() => { state.page = 1; renderReview(); });
  bind("#filter-search","input",() => { state.page = 1; renderResultTable(); });
  bind("#prev-page","click",() => { state.page--; renderResultTable(); }); bind("#next-page","click",() => { state.page++; renderResultTable(); });
  bind("#next-pending","click",nextPending); bind("#review-next","click",nextPending);
  bind("#review-decision","change",updateReviewControls); bind("#review-form","submit",saveReview);
  bind("#setting-model-select","change",(event) => { if (event.target.value) $("#setting-model").value = event.target.value; });
  bind("#test-connection","click",testConnection);
  bind("#setting-base-url","input",() => { state.connected = false; renderSettingsSummary(); });
  bind("#settings-form","submit",async (event) => {
    event.preventDefault();
    const button = event.currentTarget.querySelector('button[type="submit"]');
    await withBusy(button,async () => { state.settings = await api("/api/settings",{method:"PUT",body:JSON.stringify(readSettings())}); renderSettingsSummary(); setDialog("settings-dialog",false); notice("模型設定已儲存。新的比對批次會使用這組設定；既有批次維持原設定。"); });
  });
}
async function init() {
  setupEvents();
  try {
    const [, settings] = await Promise.all([refreshProjects(),api("/api/settings")]);
    state.settings = settings; renderSettingsSummary();
    const remembered = storageGet("specheck-project");
    if (state.projects.length) await selectProject(state.projects.some((project) => project.id === remembered) ? remembered : state.projects[0].id);
  } catch (error) { showError(error); }
}
init();
