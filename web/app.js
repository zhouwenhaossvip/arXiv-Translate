const $ = (id) => document.getElementById(id);
const steps = ["source", "baseline", "translating", "compiling"];
const form = $("taskForm");
const input = $("pdfInput");
const zone = $("dropzone");
let selectedFile = null;
let pollTimer = null;

function showError(message) {
  $("formError").textContent = message;
  $("formError").hidden = !message;
}

function selectFile(file) {
  if (!file) return;
  if (!file.name.toLowerCase().endsWith(".pdf") || file.size > 25 * 1024 * 1024 || !file.size) {
    selectedFile = null;
    zone.classList.remove("selected");
    showError("请选择不超过 25 MB 的 PDF 文件。");
    return;
  }
  selectedFile = file;
  zone.classList.add("selected");
  $("fileTitle").textContent = file.name;
  $("fileHint").textContent = `${(file.size / 1024 / 1024).toFixed(2)} MB · 点击可更换文件`;
  showError("");
}

input.addEventListener("change", () => selectFile(input.files[0]));
zone.addEventListener("dragover", (event) => { event.preventDefault(); zone.classList.add("dragging"); });
zone.addEventListener("dragleave", () => zone.classList.remove("dragging"));
zone.addEventListener("drop", (event) => {
  event.preventDefault();
  zone.classList.remove("dragging");
  selectFile(event.dataTransfer.files[0]);
});
$("toggleKey").addEventListener("click", () => {
  const field = $("apiKey");
  const visible = field.type === "password";
  field.type = visible ? "text" : "password";
  $("toggleKey").textContent = visible ? "隐藏" : "显示";
  $("toggleKey").setAttribute("aria-label", `${visible ? "隐藏" : "显示"} API Key`);
});

function readBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(",", 2)[1]);
    reader.onerror = () => reject(new Error("无法读取 PDF，请重新选择文件。"));
    reader.readAsDataURL(file);
  });
}

function renderTask(task) {
  $("progressCard").hidden = false;
  $("paperLabel").textContent = `${task.paper_id} · ${task.model}`;
  $("taskMessage").textContent = task.message || "";
  $("statePill").textContent = task.status === "done" ? "已完成" : task.status === "failed" ? "未完成" : "进行中";
  $("statePill").className = `state-pill ${task.status === "done" ? "done" : task.status === "failed" ? "failed" : ""}`;
  let index = steps.indexOf(task.step);
  if (task.step === "done") index = steps.length;
  if (task.step === "failed") index = -1;
  document.querySelectorAll(".progress-list li").forEach((item, i) => {
    item.classList.toggle("complete", index > i);
    item.classList.toggle("active", index === i);
  });
  if (task.segments_total !== null && task.segments_total !== undefined) {
    $("translateDetail").textContent = `已翻译 ${task.segments_done} / ${task.segments_total} 个片段`;
  }
  if (task.status === "done") {
    $("submitButton").disabled = false;
    $("submitButton").textContent = "翻译另一篇 →";
    $("resultCard").hidden = false;
    $("resultSummary").textContent = `${task.paper_id} · ${task.pages || "—"} 页 · 请检查译文与排版`;
    const pdfUrl = `/api/tasks/${task.id}/pdf`;
    $("pdfPreview").src = pdfUrl;
    $("downloadLink").href = `${pdfUrl}?download=1`;
    $("resultCard").scrollIntoView({ behavior: "smooth", block: "start" });
  }
  if (task.status === "failed") {
    $("submitButton").disabled = false;
    $("submitButton").textContent = "重新尝试 →";
  }
}

async function poll(token) {
  try {
    const response = await fetch(`/api/tasks/${token}`, { cache: "no-store" });
    if (!response.ok) throw new Error("无法读取任务状态。");
    const task = await response.json();
    renderTask(task);
    if (task.status === "working") pollTimer = setTimeout(() => poll(token), 1500);
  } catch (error) {
    $("taskMessage").textContent = `${error.message} 请刷新页面后重试。`;
    $("submitButton").disabled = false;
  }
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  showError("");
  if (!selectedFile) return showError("请先选择一篇 arXiv 论文 PDF。");
  const key = $("apiKey").value.trim();
  if (!key) return showError("请输入所选模型的 API Key。");
  clearTimeout(pollTimer);
  $("submitButton").disabled = true;
  $("submitButton").textContent = "正在提交…";
  $("resultCard").hidden = true;
  try {
    const pdf_base64 = await readBase64(selectedFile);
    const response = await fetch("/api/tasks", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ filename: selectedFile.name, pdf_base64, model: $("model").value, api_key: key }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "无法创建任务。");
    $("apiKey").value = "";
    renderTask(result);
    if (result.status === "working") pollTimer = setTimeout(() => poll(result.id), 1000);
  } catch (error) {
    showError(error.message || "提交失败，请重试。");
    $("submitButton").disabled = false;
    $("submitButton").textContent = "开始翻译 →";
  }
});
