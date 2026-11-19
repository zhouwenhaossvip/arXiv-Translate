const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const elements = new Map();
function element(id) {
  if (!elements.has(id)) {
    elements.set(id, {
      value: "", textContent: "", hidden: false, disabled: false,
      files: [], listeners: {}, className: "",
      classList: { add() {}, remove() {}, toggle() {} },
      addEventListener(name, listener) { this.listeners[name] = listener; },
      setAttribute() {}, scrollIntoView() {},
    });
  }
  return elements.get(id);
}

const requests = [];
const context = {
  document: { getElementById: element, querySelectorAll: () => [] },
  FileReader: class {
    readAsDataURL() { this.result = "data:application/pdf;base64,JVBERi0="; this.onload(); }
  },
  fetch: async (url, options) => {
    requests.push(JSON.parse(options.body));
    return { ok: true, json: async () => ({
      id: "a", paper_id: "2511.02841v2", model: "gpt-4o-mini",
      status: "working", step: "source", message: "", segments_total: null,
    }) };
  },
  setTimeout: () => 1,
  clearTimeout() {},
};
vm.runInNewContext(fs.readFileSync(path.join(__dirname, "..", "web", "app.js"), "utf8"), context);

element("pdfInput").files = [{ name: "2511.02841v2.pdf", size: 100 }];
element("pdfInput").listeners.change();
element("model").value = "gpt-4o-mini";
element("apiKey").value = "test-secret";

(async () => {
  await element("taskForm").listeners.submit({ preventDefault() {} });
  assert.equal(requests.length, 1);
  assert.equal(requests[0].api_key, "test-secret");
  assert.equal(element("model").value, "gpt-4o-mini");
  assert.equal(element("apiKey").value, "test-secret");
  context.renderTask({
    id: "a", paper_id: "2511.02841v2", model: "gpt-4o-mini",
    status: "failed", step: "failed", message: "未获取到该版本可用的 LaTeX 源码",
    segments_total: null,
  });
  assert.equal(element("taskMessage").className, "error-message");
  assert.equal(element("apiKey").value, "test-secret");
  console.log("Web form retains model and API Key after submission.");
})().catch((error) => { console.error(error); process.exitCode = 1; });
