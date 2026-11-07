/* Read-only BOSS page observations and review plans.
 * Candidate selectors are not evidence of compatibility with the live service.
 * This module never fills, clicks, sends, calls network APIs, or accesses storage.
 */
(function installCareerBossDOM(global) {
  "use strict";
  const SELECTORS = Object.freeze({
    jobs: [".job-card-wrapper", ".job-card-box", ".job-primary"],
    title: [".job-name", ".job-title"],
    company: [".company-name", ".company-text .name"],
    recruiter: [".boss-name", ".recruiter-name"],
    location: [".job-area", ".job-location", ".area"],
    salary: [".salary"],
    description: [".job-desc", ".info-desc"],
    conversation: ["[data-career-boss-conversation]", ".chat-container", ".chat-panel",
      ".im-chat-panel", ".chat-im"],
    header: ["[data-career-boss-header]", ".chat-header", ".chat-info", ".boss-info"],
    controls: ["[data-career-boss-controls]", ".message-controls", ".chat-controls",
      ".chat-editor", ".chat-footer", ".chat-input-wrap"],
    editor: ["textarea", "input[type='text']", "[contenteditable='true']"],
    selectedResume: ["[data-career-boss-resume-name]", ".resume-name"],
    blockers: [".boss-verify", ".verify-wrap", ".captcha-wrap", ".geetest_panel",
      ".login-container", ".login-wrapper", "form[action*='/login']"],
  });
  const SEND_LABELS = new Set(["发送", "发消息"]);
  const RESUME_LABELS = new Set(["发简历", "发送简历", "发送附件简历"]);
  const START_LABELS = new Set(["立即沟通", "打招呼"]);
  const CONTACTED_LABELS = new Set(["继续沟通", "继续聊", "已沟通", "已发送"]);

  function normalized(value) {
    return typeof value === "string" ? value.normalize("NFKC").replace(/\s+/g, " ").trim() : "";
  }
  function visible(node) {
    if (!node || node.isConnected === false ||
        typeof node.getClientRects !== "function" || node.getClientRects().length === 0) return false;
    for (let current = node; current; current = current.parentElement) {
      if (current.hidden || current.getAttribute?.("aria-hidden") === "true") return false;
      const style = current.ownerDocument?.defaultView?.getComputedStyle?.(current);
      if (style && (style.display === "none" || style.visibility === "hidden" ||
          style.visibility === "collapse" || style.opacity === "0")) return false;
    }
    return true;
  }
  function enabled(node) {
    if (!visible(node)) return false;
    for (let current = node; current; current = current.parentElement) {
      if (current.disabled || current.readOnly || current.hasAttribute?.("inert") ||
          current.getAttribute?.("aria-disabled") === "true" ||
          current.getAttribute?.("aria-readonly") === "true") return false;
    }
    return true;
  }
  function collect(root, selectors) {
    if (!root || typeof root.querySelectorAll !== "function") return [];
    return [...new Set(selectors.flatMap((selector) => Array.from(root.querySelectorAll(selector))))]
      .filter(visible);
  }
  function outermost(nodes) {
    return nodes.filter((node) => !nodes.some((other) => other !== node && other.contains(node)));
  }
  function oneText(root, selectors, maximum = 300) {
    const values = new Set(collect(root, selectors).map((node) => normalized(node.textContent))
      .filter(Boolean));
    if (values.size !== 1) return null;
    const result = [...values][0];
    return result.length <= maximum ? result : null;
  }
  function bossPage(value) {
    try {
      const url = new URL(value);
      if (url.protocol !== "https:" || !["www.zhipin.com", "zhipin.com"].includes(url.hostname) ||
          url.username || url.password || url.port) return null;
      return url;
    } catch { return null; }
  }
  function canonicalJob(value, baseURL) {
    try {
      const url = new URL(value, baseURL);
      if (!bossPage(url.href)) return null;
      const match = /^\/job_detail\/([A-Za-z0-9_-]{6,128})\.html$/.exec(url.pathname);
      if (!match) return null;
      return { job_id: match[1], url: "https://www.zhipin.com/job_detail/" + match[1] + ".html" };
    } catch { return null; }
  }
  function jobLinks(root, baseURL) {
    const values = new Map();
    for (const link of collect(root, ["a[href]"])) {
      const value = canonicalJob(link.getAttribute("href"), baseURL);
      if (value) values.set(value.url, value);
    }
    return [...values.values()];
  }
  function buttons(root, labels) {
    return collect(root, ["button", "a", "[role='button']"]).filter((node) =>
      labels.has(normalized(node.textContent).replace(/[\ue000-\uf8ff]/g, "").trim()));
  }
  function buttonMetadata(nodes) {
    const node = nodes.length === 1 ? nodes[0] : null;
    return { present: Boolean(node), count: nodes.length, enabled: Boolean(node && enabled(node)),
      label: node ? normalized(node.textContent).replace(/[\ue000-\uf8ff]/g, "").trim() : "" };
  }
  function expectedIdentity(expected) {
    if (!expected || typeof expected !== "object") return null;
    const job = canonicalJob(expected.url, "https://www.zhipin.com/");
    const company = normalized(expected.company);
    const recruiter = normalized(expected.recruiter);
    if (!job || expected.job_id !== job.job_id || !company || !recruiter ||
        company.length > 300 || recruiter.length > 160) return null;
    return { ...job, company, recruiter };
  }
  function reason(code, identity = null) {
    return { status: "NEEDS_USER", reason_code: code, identity,
      editor: { present: false, count: 0, enabled: false, has_draft: false },
      send_button: buttonMetadata([]), resume_button: buttonMetadata([]),
      selected_resume_label: "", snapshot_fingerprint: null };
  }
  async function fingerprint(value) {
    if (!global.crypto?.subtle || typeof TextEncoder !== "function") return null;
    const bytes = await global.crypto.subtle.digest("SHA-256",
      new TextEncoder().encode(JSON.stringify(value)));
    return Array.from(new Uint8Array(bytes), (byte) => byte.toString(16).padStart(2, "0")).join("");
  }

  function extractJobs(doc, baseURL) {
    if (!bossPage(baseURL)) return [];
    const found = new Map();
    const ambiguous = new Set();
    for (const card of outermost(collect(doc, SELECTORS.jobs))) {
      const links = jobLinks(card, baseURL);
      const title = oneText(card, SELECTORS.title);
      const company = oneText(card, SELECTORS.company);
      if (links.length !== 1 || !title || !company) continue;
      const already = buttons(card, CONTACTED_LABELS).length > 0;
      const start = buttons(card, START_LABELS).length > 0;
      const result = { ...links[0], title, company,
        recruiter: oneText(card, SELECTORS.recruiter, 160) || "",
        location: oneText(card, SELECTORS.location) || "",
        salary_text: oneText(card, SELECTORS.salary, 160) || "",
        description: oneText(card, SELECTORS.description, 8000) || "",
        communication_state: already === start ? "UNKNOWN" :
          (already ? "ALREADY_CONTACTED" : "NOT_CONTACTED") };
      const previous = found.get(result.job_id);
      if (previous && JSON.stringify(previous) !== JSON.stringify(result)) ambiguous.add(result.job_id);
      else found.set(result.job_id, result);
    }
    return [...found.values()].filter((job) => !ambiguous.has(job.job_id));
  }

  async function inspectConversation(doc, expected) {
    const target = expectedIdentity(expected);
    const page = bossPage(doc?.location?.href);
    if (!target || !page) return reason("TARGET_IDENTITY_INVALID");
    if (collect(doc, SELECTORS.blockers).length) return reason("SITE_REQUIRES_USER");
    const roots = outermost(collect(doc, SELECTORS.conversation));
    if (roots.length !== 1) return reason("CONVERSATION_NOT_UNIQUE");
    const headers = outermost(collect(roots[0], SELECTORS.header));
    if (headers.length !== 1) return reason("CONVERSATION_HEADER_NOT_UNIQUE");
    const header = headers[0];
    const links = jobLinks(header, page.href);
    const company = oneText(header, SELECTORS.company);
    const recruiter = oneText(header, SELECTORS.recruiter, 160);
    if (links.length !== 1 || links[0].job_id !== target.job_id ||
        company !== target.company || recruiter !== target.recruiter) {
      return reason("CONVERSATION_IDENTITY_MISMATCH");
    }
    const identity = { ...links[0], company, recruiter };
    const controls = outermost(collect(roots[0], SELECTORS.controls));
    if (controls.length !== 1) return reason("CONVERSATION_CONTROLS_NOT_UNIQUE", identity);
    const control = controls[0];
    const editors = collect(control, SELECTORS.editor);
    const sends = buttons(control, SEND_LABELS);
    const resumes = buttons(control, RESUME_LABELS);
    const editor = editors.length === 1 ? editors[0] : null;
    const editorText = editor ? (["INPUT", "TEXTAREA"].includes(editor.tagName) ?
      editor.value : editor.textContent) : "";
    const snapshot = {
      status: editors.length > 1 || sends.length > 1 || resumes.length > 1 ? "NEEDS_USER" : "READY",
      reason_code: editors.length > 1 || sends.length > 1 || resumes.length > 1 ?
        "CONVERSATION_CONTROLS_AMBIGUOUS" : "IDENTITY_VERIFIED",
      page: { origin: page.origin, path: page.pathname },
      identity,
      editor: { present: Boolean(editor), count: editors.length,
        enabled: Boolean(editor && enabled(editor)), has_draft: Boolean(normalized(editorText)) },
      send_button: buttonMetadata(sends), resume_button: buttonMetadata(resumes),
      selected_resume_label: oneText(control, SELECTORS.selectedResume, 200) || "",
    };
    const digest = await fingerprint(snapshot);
    if (!digest) return reason("SNAPSHOT_DIGEST_UNAVAILABLE", identity);
    return { ...snapshot, snapshot_fingerprint: digest };
  }

  async function prepareAction(expected, kind, text = "", resumeLabel = "") {
    const target = expectedIdentity(expected);
    const base = { status: "NEEDS_USER", reason_code: "TARGET_IDENTITY_INVALID",
      kind, target, text: "", resume_label: "", snapshot_fingerprint: null,
      risk: "EXTERNAL_WRITE", approval_required: true, execution_supported: false,
      consequences: [] };
    if (!target) return base;
    if (!["greet", "message", "send_resume"].includes(kind)) {
      return { ...base, reason_code: "ACTION_KIND_UNSUPPORTED" };
    }
    if (typeof text !== "string" || text.length > 2000 ||
        /[\u0000-\u0008\u000b-\u001f\u007f]/.test(text)) {
      return { ...base, reason_code: "MESSAGE_INVALID" };
    }
    if (kind === "greet") {
      return { ...base, reason_code: "GREETING_MAY_SEND_DEFAULT_TEXT", text,
        consequences: ["点击立即沟通可能直接发送账号默认招呼；需要本人核对平台话术和目标。"] };
    }
    const snapshot = await inspectConversation(global.document, target);
    const plan = { ...base, status: snapshot.status, reason_code: snapshot.reason_code,
      snapshot_fingerprint: snapshot.snapshot_fingerprint, expected: snapshot.identity };
    if (snapshot.status !== "READY") return plan;
    if (kind === "message") {
      if (!text.trim()) return { ...plan, status: "NEEDS_USER", reason_code: "MESSAGE_EMPTY" };
      if (snapshot.editor.has_draft) {
        return { ...plan, status: "NEEDS_USER", reason_code: "EDITOR_HAS_UNSAVED_INPUT" };
      }
      if (!snapshot.editor.enabled || !snapshot.send_button.enabled) {
        return { ...plan, status: "NEEDS_USER", reason_code: "MESSAGE_CONTROLS_NOT_READY" };
      }
      return { ...plan, text, consequences: ["向此岗位绑定的公司与招聘者发送所列沟通文字。"] };
    }
    const label = normalized(resumeLabel);
    if (!label || label.length > 200 || /[\\/\u0000-\u001f\u007f]/.test(label)) {
      return { ...plan, status: "NEEDS_USER", reason_code: "RESUME_LABEL_INVALID" };
    }
    if (!snapshot.resume_button.present || !snapshot.resume_button.enabled) {
      return { ...plan, status: "WAITING_REPLY", reason_code: "RESUME_ACTION_UNAVAILABLE",
        resume_label: label, consequences: ["简历入口当前不可用，需等待平台开放或招聘者回复。"] };
    }
    if (snapshot.selected_resume_label !== label) {
      return { ...plan, status: "NEEDS_USER", reason_code: "RESUME_IDENTITY_UNVERIFIED",
        resume_label: label };
    }
    return { ...plan, resume_label: label,
      consequences: ["向此岗位绑定的公司与招聘者发送指定附件简历；当前模块仅生成计划。"] };
  }

  global.CareerBossDOM = Object.freeze({ extractJobs, inspectConversation, prepareAction });
})(globalThis);
