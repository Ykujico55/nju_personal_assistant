import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { webcrypto } from "node:crypto";
import vm from "node:vm";
import test from "node:test";

class Node {
  constructor(tag = "div", { text = "", attrs = {}, children = [], style = {}, value = "" } = {}) {
    this.tagName = tag.toUpperCase();
    this.attrs = { ...attrs };
    this.children = [];
    this.parentElement = null;
    this.style = { display: "block", visibility: "visible", opacity: "1", ...style };
    this.ownText = text;
    this._value = value;
    this.mutations = 0;
    for (const child of children) this.append(child);
  }
  append(child) { child.parentElement = this; this.children.push(child); return child; }
  get textContent() { return this.ownText + this.children.map((child) => child.textContent).join(""); }
  get innerText() { return this.textContent; }
  get value() { return this._value; }
  set value(value) { this.mutations += 1; this._value = value; }
  get disabled() { return this.hasAttribute("disabled"); }
  get readOnly() { return this.hasAttribute("readonly"); }
  get isContentEditable() { return this.getAttribute("contenteditable") === "true"; }
  get hidden() { return this.hasAttribute("hidden"); }
  get ownerDocument() {
    let node = this;
    while (node.parentElement) node = node.parentElement;
    return node;
  }
  getClientRects() { return this.hidden ? [] : [{}]; }
  getAttribute(name) { return Object.hasOwn(this.attrs, name) ? String(this.attrs[name]) : null; }
  hasAttribute(name) { return Object.hasOwn(this.attrs, name); }
  setAttribute(name, value) { this.mutations += 1; this.attrs[name] = value; }
  click() { this.mutations += 1; }
  dispatchEvent() { this.mutations += 1; }
  contains(node) { return node === this || this.children.some((child) => child.contains(node)); }
  matches(selector) {
    return selector.split(",").some((piece) => {
      let value = piece.trim();
      if (!value) return false;
      let good = true;
      value = value.replace(/\[([\w-]+)(?:(\*=|=)["']?([^"'\]]*)["']?)?\]/g,
        (_, name, operator, wanted) => {
          const actual = this.getAttribute(name);
          if (actual === null || (operator === "=" && actual !== wanted) ||
              (operator === "*=" && !actual.includes(wanted))) good = false;
          return "";
        });
      const tag = value.match(/^[a-zA-Z][\w-]*/)?.[0];
      if (tag && tag.toUpperCase() !== this.tagName) good = false;
      const classes = (this.getAttribute("class") || "").split(/\s+/);
      for (const match of value.matchAll(/\.([\w-]+)/g)) if (!classes.includes(match[1])) good = false;
      const id = value.match(/#([\w-]+)/)?.[1];
      if (id && this.getAttribute("id") !== id) good = false;
      return good;
    });
  }
  querySelectorAll(selector) {
    const result = [];
    for (const child of this.children) {
      if (child.matches(selector)) result.push(child);
      result.push(...child.querySelectorAll(selector));
    }
    return result;
  }
}
class Document extends Node {
  constructor(children = [], href = "https://www.zhipin.com/web/geek/chat") {
    super("document", { children });
    this.location = { href };
    this.defaultView = { getComputedStyle: (node) => node.style };
    Object.defineProperty(this, "cookie", { get() { throw Error("cookie read"); } });
    Object.defineProperty(this, "innerText", { get() { throw Error("whole document text read"); } });
  }
}
const el = (tag, attrs = {}, text = "", children = []) => new Node(tag, { attrs, text, children });
const jobURL = "https://www.zhipin.com/job_detail/abc12345.html";
const expected = { job_id: "abc12345", url: jobURL, company: "测试公司", recruiter: "李女士" };
function card({ id = "abc12345", company = "测试公司", hidden = false, label = "立即沟通" } = {}) {
  const node = el("li", { class: "job-card-wrapper" }, "", [
    el("a", { href: "/job_detail/" + id + ".html?securityId=not-exported#tracking" }, "职位"),
    el("span", { class: "job-name" }, "后端工程师"),
    el("span", { class: "company-name" }, company),
    el("span", { class: "boss-name" }, "李女士"),
    el("span", { class: "job-area" }, "南京"),
    el("span", { class: "salary" }, "15-25K"),
    el("div", { class: "job-desc" }, "Python 服务"),
    el("button", {}, label),
  ]);
  if (hidden) node.attrs.hidden = "";
  return node;
}
function conversation({ company = "测试公司", recruiter = "李女士", disabledResume = false,
                        extraSend = false, draft = "", resumeLabel = "resume.pdf" } = {}) {
  const header = el("header", { class: "chat-header" }, "", [
    el("a", { href: jobURL }, "当前职位"),
    el("span", { class: "company-name" }, company),
    el("span", { class: "boss-name" }, recruiter),
  ]);
  const editor = new Node("textarea", { attrs: { class: "chat-input" }, value: draft });
  const send = el("button", { class: "btn-send" }, "发送");
  const resume = el("button", disabledResume ? { disabled: "" } : {}, "发简历");
  const controls = el("div", { class: "message-controls" }, "", [
    editor, send, resume, el("span", { "data-career-boss-resume-name": "" }, resumeLabel),
  ]);
  if (extraSend) controls.append(el("button", {}, "发送"));
  const history = el("div", { class: "message-history" });
  Object.defineProperty(history, "textContent", { get() { throw Error("chat history read"); } });
  const root = el("section", { class: "chat-container" }, "", [header, controls, history]);
  return { root, header, controls, editor, send, resume };
}
function load(doc) {
  const source = readFileSync(new URL("../browser/boss-dom.js", import.meta.url), "utf8");
  const context = vm.createContext({ document: doc, URL, TextEncoder, crypto: webcrypto });
  Object.defineProperty(context, "localStorage", { get() { throw Error("storage read"); } });
  Object.defineProperty(context, "sessionStorage", { get() { throw Error("storage read"); } });
  vm.runInContext(source, context, { timeout: 1000 });
  return context.CareerBossDOM;
}
const plain = (value) => JSON.parse(JSON.stringify(value));
const mutationCount = (node) => node.mutations + node.children.reduce((n, child) => n + mutationCount(child), 0);

test("exports only the three read-only methods", () => {
  const api = load(new Document());
  assert.deepEqual(Object.keys(api).sort(), ["extractJobs", "inspectConversation", "prepareAction"]);
  assert.equal(api.executeBoundAction, undefined);
});
test("extracts visible jobs and strips URL tracking data", () => {
  const doc = new Document([card()]);
  const jobs = plain(load(doc).extractJobs(doc, doc.location.href));
  assert.equal(jobs.length, 1);
  assert.equal(jobs[0].url, jobURL);
  assert.equal(jobs[0].job_id, "abc12345");
  assert.equal(jobs[0].communication_state, "NOT_CONTACTED");
  assert.equal(jobs[0].company, "测试公司");
  assert.equal(JSON.stringify(jobs).includes("securityId"), false);
});
test("hidden cards and foreign origins are not imported", () => {
  const doc = new Document([card({ hidden: true })]);
  assert.deepEqual(plain(load(doc).extractJobs(doc, doc.location.href)), []);
  assert.deepEqual(plain(load(doc).extractJobs(doc, "https://www.zhipin.com.evil.test/")), []);
});
test("communicated state is distinct from a resume being sent", () => {
  const doc = new Document([card({ label: "继续沟通" })]);
  const job = load(doc).extractJobs(doc, doc.location.href)[0];
  assert.equal(job.communication_state, "ALREADY_CONTACTED");
  assert.equal(Object.hasOwn(job, "resume_sent"), false);
});
test("duplicate job identities with conflicting companies are omitted", () => {
  const doc = new Document([card(), card({ company: "另一家公司" })]);
  assert.deepEqual(plain(load(doc).extractJobs(doc, doc.location.href)), []);
});
test("verifies a single matching conversation without reading chat history", async () => {
  const view = conversation();
  const doc = new Document([view.root]);
  const result = await load(doc).inspectConversation(doc, expected);
  assert.equal(result.status, "READY");
  assert.equal(result.identity.job_id, expected.job_id);
  assert.equal(result.editor.present, true);
  assert.equal(result.resume_button.enabled, true);
  assert.match(result.snapshot_fingerprint, /^[a-f0-9]{64}$/);
  assert.equal(mutationCount(doc), 0);
});
test("wrong recruiter and wrong company require the user", async () => {
  for (const options of [{ recruiter: "王先生" }, { company: "另一家公司" }]) {
    const view = conversation(options);
    const doc = new Document([view.root]);
    assert.equal((await load(doc).inspectConversation(doc, expected)).status, "NEEDS_USER");
    assert.equal(mutationCount(doc), 0);
  }
});
test("multiple visible conversation panels never select one silently", async () => {
  const doc = new Document([conversation().root, conversation().root]);
  assert.equal((await load(doc).inspectConversation(doc, expected)).status, "NEEDS_USER");
});
test("a visible security check blocks planning", async () => {
  const doc = new Document([conversation().root, el("div", { class: "boss-verify" })]);
  const result = await load(doc).inspectConversation(doc, expected);
  assert.equal(result.status, "NEEDS_USER");
  assert.equal(result.reason_code, "SITE_REQUIRES_USER");
});
test("missing target identity is never inferred from text or row order", async () => {
  const view = conversation();
  view.header.children.splice(0, 1);
  const doc = new Document([view.root]);
  assert.equal((await load(doc).inspectConversation(doc, expected)).status, "NEEDS_USER");
});
test("snapshot changes when a target resume button becomes disabled", async () => {
  const view = conversation();
  const doc = new Document([view.root]);
  const api = load(doc);
  const first = await api.inspectConversation(doc, expected);
  view.resume.attrs.disabled = "";
  const second = await api.inspectConversation(doc, expected);
  assert.notEqual(first.snapshot_fingerprint, second.snapshot_fingerprint);
});
test("prepare greeting always requires manual site review and never clicks", async () => {
  const doc = new Document([card()]);
  const plan = await load(doc).prepareAction(expected, "greet", "您好", "");
  assert.equal(plan.status, "NEEDS_USER");
  assert.equal(plan.reason_code, "GREETING_MAY_SEND_DEFAULT_TEXT");
  assert.equal(plan.execution_supported, false);
  assert.equal(mutationCount(doc), 0);
});
test("prepare message produces a review-only exact-target plan", async () => {
  const view = conversation();
  const doc = new Document([view.root]);
  const plan = await load(doc).prepareAction(expected, "message", "公开测试文字", "");
  assert.equal(plan.status, "READY");
  assert.equal(plan.text, "公开测试文字");
  assert.equal(plan.target.company, expected.company);
  assert.equal(plan.execution_supported, false);
  assert.equal(plan.approval_required, true);
  assert.match(plan.snapshot_fingerprint, /^[a-f0-9]{64}$/);
  assert.equal(view.editor.value, "");
  assert.equal(mutationCount(doc), 0);
});
test("existing unsaved editor input is preserved and requires user review", async () => {
  const view = conversation({ draft: "本机未保存文字" });
  const doc = new Document([view.root]);
  const plan = await load(doc).prepareAction(expected, "message", "新的测试文字", "");
  assert.equal(plan.status, "NEEDS_USER");
  assert.equal(plan.reason_code, "EDITOR_HAS_UNSAVED_INPUT");
  assert.equal(view.editor.value, "本机未保存文字");
  assert.equal(mutationCount(doc), 0);
});
test("ambiguous send buttons block message plans", async () => {
  const view = conversation({ extraSend: true });
  const doc = new Document([view.root]);
  const plan = await load(doc).prepareAction(expected, "message", "公开测试文字", "");
  assert.equal(plan.status, "NEEDS_USER");
  assert.equal(mutationCount(doc), 0);
});
test("disabled resume action waits for a reply, rather than claiming delivery", async () => {
  const view = conversation({ disabledResume: true });
  const doc = new Document([view.root]);
  const plan = await load(doc).prepareAction(expected, "send_resume", "", "resume.pdf");
  assert.equal(plan.status, "WAITING_REPLY");
  assert.equal(plan.execution_supported, false);
  assert.equal(mutationCount(doc), 0);
});
test("a resume plan binds the visible selected resume label", async () => {
  const view = conversation();
  const doc = new Document([view.root]);
  const api = load(doc);
  const plan = await api.prepareAction(expected, "send_resume", "", "resume.pdf");
  assert.equal(plan.status, "READY");
  assert.equal(plan.resume_label, "resume.pdf");
  assert.equal((await api.prepareAction(expected, "send_resume", "", "different.pdf")).status, "NEEDS_USER");
  assert.equal(mutationCount(doc), 0);
});
test("unsupported action kinds and unsafe identity are rejected", async () => {
  const doc = new Document([conversation().root]);
  const api = load(doc);
  assert.equal((await api.prepareAction(expected, "pay", "x", "")).status, "NEEDS_USER");
  const invalid = { ...expected, url: "https://attacker.test/job_detail/abc12345.html" };
  assert.equal((await api.inspectConversation(doc, invalid)).status, "NEEDS_USER");
});

test("hidden ancestor containers do not supply jobs or a conversation identity", async () => {
  const view = conversation();
  const wrapper = el("div", {}, "", [view.root, card()]);
  wrapper.style.display = "none";
  const doc = new Document([wrapper]);
  const api = load(doc);
  assert.deepEqual(plain(api.extractJobs(doc, doc.location.href)), []);
  assert.equal((await api.inspectConversation(doc, expected)).status, "NEEDS_USER");
});
test("a hidden second conversation does not make the visible target ambiguous", async () => {
  const hidden = conversation();
  hidden.root.attrs.hidden = "";
  const doc = new Document([conversation().root, hidden.root]);
  assert.equal((await load(doc).inspectConversation(doc, expected)).status, "READY");
});
test("distinct recruiters in one header block identity verification", async () => {
  const view = conversation();
  view.header.append(el("span", { class: "boss-name" }, "另一位招聘者"));
  const doc = new Document([view.root]);
  assert.equal((await load(doc).inspectConversation(doc, expected)).status, "NEEDS_USER");
});
test("user-info URLs and non-default ports cannot become job targets", () => {
  const item = card();
  item.children[0].attrs.href = "https://person:secret@www.zhipin.com/job_detail/abc12345.html";
  const doc = new Document([item]);
  const api = load(doc);
  assert.deepEqual(plain(api.extractJobs(doc, doc.location.href)), []);
  item.children[0].attrs.href = "https://www.zhipin.com:444/job_detail/abc12345.html";
  assert.deepEqual(plain(api.extractJobs(doc, doc.location.href)), []);
});
test("tracking query changes do not change a verified conversation identity", async () => {
  const view = conversation();
  const doc = new Document([view.root]);
  const api = load(doc);
  const first = await api.inspectConversation(doc, expected);
  view.header.children[0].attrs.href = jobURL + "?tracking=other#anchor";
  const second = await api.inspectConversation(doc, expected);
  assert.equal(second.status, "READY");
  assert.equal(second.snapshot_fingerprint, first.snapshot_fingerprint);
  assert.equal(mutationCount(doc), 0);
});
