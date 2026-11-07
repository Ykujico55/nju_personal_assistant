/* Explicit, bound BOSS message actions. This entry point is called only after
 * the host Gateway consumes the matching one-time R2 approval. No hidden APIs,
 * credential access, navigation, automatic retry, or resume-button dispatch.
 */
(function installCareerBossAction(global) {
  "use strict";
  const attempts = new Map();
  const ROOTS = ["[data-career-boss-conversation]", ".chat-container", ".chat-panel", ".im-chat-panel", ".chat-im"];
  const HEADERS = ["[data-career-boss-header]", ".chat-header", ".chat-info", ".boss-info"];
  const CONTROLS = ["[data-career-boss-controls]", ".message-controls", ".chat-controls", ".chat-editor", ".chat-footer", ".chat-input-wrap"];
  const SELF = ["[data-career-boss-self-message]", ".message-item.item-myself", ".chat-message.is-self", ".item-myself"];
  const TEXT = ["[data-career-boss-message-text]", ".message-text", ".message-body", ".text-message", ".text"];
  const normalize = value => typeof value === "string" ? value.normalize("NFKC").replace(/\s+/g, " ").trim() : "";
  const clone = value => JSON.parse(JSON.stringify(value));
  function visible(node) {
    if (!node || node.isConnected === false || !node.getClientRects?.().length) return false;
    for (let current = node; current; current = current.parentElement) {
      if (current.hidden || current.getAttribute?.("aria-hidden") === "true") return false;
      const style = current.ownerDocument?.defaultView?.getComputedStyle?.(current);
      if (style && (style.display === "none" || style.visibility === "hidden" || style.opacity === "0")) return false;
    }
    return true;
  }
  function enabled(node) {
    if (!visible(node)) return false;
    for (let current = node; current; current = current.parentElement) {
      if (current.disabled || current.readOnly || current.hasAttribute?.("inert") || current.getAttribute?.("aria-disabled") === "true") return false;
    }
    return true;
  }
  function collect(root, selectors) {
    return [...new Set(selectors.flatMap(selector => Array.from(root?.querySelectorAll?.(selector) || [])))].filter(visible);
  }
  function outermost(nodes) { return nodes.filter(node => !nodes.some(other => other !== node && other.contains(node))); }
  function oneText(root, selectors) {
    const values = new Set(collect(root, selectors).map(node => normalize(node.textContent)).filter(Boolean));
    return values.size === 1 ? [...values][0] : null;
  }
  function canonicalJob(value) {
    try {
      const url = new URL(value, global.document.location.href);
      if (url.protocol !== "https:" || url.hostname !== "www.zhipin.com" || url.port || url.username || url.password) return null;
      const match = /^\/job_detail\/([A-Za-z0-9_-]{1,128})\.html$/.exec(url.pathname);
      return match ? {job_id:match[1],url:"https://www.zhipin.com" + url.pathname} : null;
    } catch { return null; }
  }
  function conversation(target) {
    const page = new URL(global.document.location.href);
    if (page.protocol !== "https:" || page.hostname !== "www.zhipin.com" || page.port || page.username || page.password) return null;
    const roots = outermost(collect(global.document, ROOTS));
    if (roots.length !== 1) return null;
    const root = roots[0], headers = outermost(collect(root, HEADERS));
    if (headers.length !== 1) return null;
    const header = headers[0];
    const links = new Map();
    for (const link of collect(header, ["a[href]"])) {
      const job = canonicalJob(link.getAttribute("href"));
      if (job) links.set(job.url, job);
    }
    if (links.size !== 1 || [...links.values()][0].url !== target.url ||
        [...links.values()][0].job_id !== target.job_id ||
        oneText(header,[".company-name", ".company-text .name"]) !== target.company ||
        oneText(header,[".boss-name", ".recruiter-name"]) !== target.recruiter) return null;
    const controls = outermost(collect(root, CONTROLS));
    if (controls.length !== 1) return null;
    return {root,control:controls[0]};
  }
  function locate(target, allowDraft = false, expectedText = "") {
    const bound = conversation(target);
    if (!bound) return null;
    const {root,control} = bound;
    const editors = collect(control,["textarea", "input[type='text']", "[contenteditable='true']"]);
    const buttons = collect(control,["button", "a", "[role='button']"]).filter(node =>
      ["发送", "发消息"].includes(normalize(node.textContent).replace(/[\ue000-\uf8ff]/g,"")));
    if (editors.length !== 1 || buttons.length !== 1 || !enabled(editors[0]) || !enabled(buttons[0])) return null;
    const editor = editors[0], button = buttons[0];
    const linkTarget = button.getAttribute?.("href");
    if (linkTarget && linkTarget !== "#") return null;
    const text = ["INPUT", "TEXTAREA"].includes(editor.tagName) ? editor.value : editor.textContent;
    if (allowDraft ? text !== expectedText : Boolean(normalize(text))) return null;
    return {root,editor,button};
  }
  function messages(root) {
    const values = collect(root, SELF);
    if (values.length > 200) throw Error("bounded message receipt scan exceeded");
    return values;
  }
  function messageId(node) {
    const value = node.getAttribute("data-message-id") || node.getAttribute("data-mid") || node.getAttribute("id");
    return typeof value === "string" && /^[A-Za-z0-9_-]{1,128}$/.test(value) ? value : null;
  }
  function acknowledged(node) {
    const state = node.getAttribute("data-delivery-state") || node.getAttribute("data-status");
    if (["sent", "delivered", "success"].includes(state)) return true;
    return ["已发送", "发送成功", "已送达"].includes(oneText(node,[".message-status", ".delivery-status"]));
  }
  function freshReceipt(root, previous, text) {
    const found = [];
    for (const node of messages(root)) {
      const id = messageId(node);
      if (!id || previous.has(id) || !acknowledged(node)) continue;
      const fragments = collect(node, TEXT);
      if (fragments.length === 1 && fragments[0].textContent === text) found.push(id);
    }
    return found.length === 1 ? found[0] : null;
  }
  const unknown = () => ({status:"UNKNOWN",state:"UNKNOWN",diagnostic_code:"BOSS_RESULT_UNCONFIRMED"});
  const needs = () => ({status:"NEEDS_USER",state:"NEEDS_USER",diagnostic_code:"BOSS_PAGE_REQUIRES_USER"});
  async function execute(args) {
    let mutated = false, reserved = false, id = null;
    try {
      if (!args || Object.keys(args).sort().join(",") !== "attachments,consequences,local_action_id,operation,target" ||
          typeof args.local_action_id !== "string" || !/^boss_[a-f0-9]{64}$/.test(args.local_action_id) ||
          !Array.isArray(args.attachments) || args.attachments.length ||
          !args.target || !args.operation || args.operation.kind !== "message" ||
          Object.keys(args.operation).sort().join(",") !== "kind,snapshot_fingerprint,text" ||
          typeof args.operation.text !== "string" || !args.operation.text.trim() || args.operation.text.length > 2000 ||
          /[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/.test(args.operation.text)) return needs();
      id = args.local_action_id;
      if (attempts.has(id)) return clone(attempts.get(id));
      const prepared = await global.CareerBossDOM.prepareAction(args.target,"message",args.operation.text,"");
      if (prepared.status !== "READY" || prepared.snapshot_fingerprint !== args.operation.snapshot_fingerprint ||
          Object.keys(args.target).length !== 4 || !["url","job_id","company","recruiter"].every(key => prepared.target?.[key] === args.target[key]) || prepared.text !== args.operation.text ||
          prepared.consequences.join("\n") !== args.consequences) return needs();
      if (attempts.has(id)) return clone(attempts.get(id));
      const before = locate(args.target);
      if (!before || attempts.size >= 100) return needs();
      const previous = new Set(messages(before.root).map(messageId).filter(Boolean));
      attempts.set(id, unknown()); reserved = true;
      const editor = before.editor;
      mutated = true;
      if (["INPUT", "TEXTAREA"].includes(editor.tagName)) {
        const prototype = editor.tagName === "TEXTAREA" ? global.HTMLTextAreaElement?.prototype : global.HTMLInputElement?.prototype;
        const setter = prototype && Object.getOwnPropertyDescriptor(prototype,"value")?.set;
        if (setter) setter.call(editor,args.operation.text); else editor.value = args.operation.text;
      } else editor.textContent = args.operation.text;
      editor.dispatchEvent(new global.Event("input", {bubbles:true}));
      editor.dispatchEvent(new global.Event("change", {bubbles:true}));
      // No await is permitted between this final identity check and the single click.
      const bound = locate(args.target,true,args.operation.text);
      if (!bound || bound.editor !== editor || bound.button !== before.button) return unknown();
      bound.button.click();
      for (let step = 0; step < 30; step++) {
        const current = locate(args.target,true,"") || locate(args.target,true,args.operation.text);
        if (!current) return unknown();
        const receipt = freshReceipt(current.root,previous,args.operation.text);
        if (receipt) {
          const result = {status:"SUCCEEDED",state:"MESSAGE_SENT",receipt:{
            reference_id:"boss-dom:"+receipt,verified:true,
            snapshot_fingerprint:args.operation.snapshot_fingerprint,
          }};
          attempts.set(id,result); return clone(result);
        }
        await new Promise(resolve => global.setTimeout(resolve,100));
      }
      return unknown();
    } catch {
      const result = mutated ? unknown() : needs();
      if (reserved && id) attempts.set(id,result);
      return result;
    }
  }
  const uploads = new Map();
  function resumeControls(target, selected = false) {
    const bound = conversation(target);
    if (!bound) return null;
    const areas = outermost(collect(bound.control,["[data-career-boss-upload-area]", ".resume-upload"]));
    if (areas.length !== 1) return null;
    const inputs = Array.from(areas[0].querySelectorAll("input[type='file']"))
      .filter(node => node.isConnected !== false && !node.disabled && !node.multiple);
    const buttons = collect(bound.control,["button","a","[role='button']"]).filter(node =>
      ["发简历","发送简历","发送附件简历"].includes(normalize(node.textContent)));
    if (inputs.length !== 1 || buttons.length !== 1 || !enabled(buttons[0]) ||
        (!selected && inputs[0].files?.length)) return null;
    const href = buttons[0].getAttribute("href");
    if (href && href !== "#") return null;
    return {...bound,input:inputs[0],button:buttons[0]};
  }
  async function hash(value) {
    const data = typeof value === "string" ? new TextEncoder().encode(value) : value;
    const digest = await global.crypto.subtle.digest("SHA-256",data);
    return [...new Uint8Array(digest)].map(byte=>byte.toString(16).padStart(2,"0")).join("");
  }
  async function prepareResume(target, resume) {
    const controls = resumeControls(target);
    const snapshot = await global.CareerBossDOM.inspectConversation(global.document,target);
    const base = {status:"NEEDS_USER",target,text:"",consequences:[],snapshot_fingerprint:null};
    if (!controls || snapshot.status !== "READY" || !resume ||
        typeof resume.filename !== "string" || !/^[a-f0-9]{64}$/.test(resume.sha256 || "") ||
        !Number.isSafeInteger(resume.size_bytes) || resume.size_bytes < 1 || resume.size_bytes > 524288) return base;
    const fingerprint = await hash(JSON.stringify({page:snapshot.snapshot_fingerprint,
      upload:{accept:controls.input.getAttribute("accept") || "",multiple:false},resume}));
    return {...base,status:"READY",snapshot_fingerprint:fingerprint,
      consequences:["先将所列新附件简历上传至BOSS，再发送给当前核对的岗位招聘者。"]};
  }
  const uploadNodes = control => collect(control,["[data-career-boss-upload]", ".resume-upload-item"]);
  const resumeMessages = root => collect(root,["[data-career-boss-resume-message]", ".resume-message.item-myself"]);
  function uploadId(node) {
    const id = node.getAttribute("data-upload-id");
    return typeof id === "string" && /^[A-Za-z0-9_-]{1,128}$/.test(id) ? id : null;
  }
  function fileMetadata(node, attachment) {
    return oneText(node,["[data-career-boss-filename]", ".resume-name"]) === attachment.name &&
      Number(node.getAttribute("data-file-size")) === attachment.size_bytes;
  }
  async function uploadInput(args) {
    if (!args?.operation || args.operation.kind !== "send_resume" || args.operation.text !== "" ||
        !/^boss_[a-f0-9]{64}$/.test(args.local_action_id || "") ||
        !Array.isArray(args.attachments) || args.attachments.length !== 1) return null;
    const file = args.attachments[0], name = file.name;
    const media = name?.toLowerCase().endsWith(".pdf") ? "application/pdf" :
      name?.toLowerCase().endsWith(".txt") ? "text/plain" :
      "application/vnd.openxmlformats-officedocument.wordprocessingml.document";
    const prepared = await prepareResume(args.target,{filename:name,sha256:file.sha256,
      size_bytes:file.size_bytes,media_type:media});
    if (prepared.status !== "READY" || prepared.snapshot_fingerprint !== args.operation.snapshot_fingerprint ||
        prepared.consequences.join("\n") !== args.consequences || args.operation.resume_label !== name) return null;
    const controls = resumeControls(args.target);
    if (!controls || uploads.has(args.local_action_id) || uploads.size >= 100) return null;
    const beforeUploads = uploadNodes(controls.control), beforeMessages = resumeMessages(controls.root);
    if (beforeUploads.length > 30 || beforeMessages.length > 200) return null;
    uploads.set(args.local_action_id,{input:controls.input,
      previous:new Set(beforeUploads.map(uploadId).filter(Boolean)),
      messages:new Set(beforeMessages.map(messageId).filter(Boolean))});
    return controls.input;
  }
  async function finishUpload(args) {
    try {
      const session = uploads.get(args.local_action_id), file = args.attachments[0];
      if (!session || session.completed) return unknown();
      session.completed = true;
      const selected = session.input.files;
      const checkedFile = selected?.[0];
      if (!selected || selected.length !== 1 || checkedFile.name !== file.name ||
          checkedFile.size !== file.size_bytes || await hash(await checkedFile.arrayBuffer()) !== file.sha256) return unknown();
      let fresh = null;
      for (let step=0;step<30;step++) {
        const bound = resumeControls(args.target,true);
        if (!bound || bound.input !== session.input) return unknown();
        const candidates = uploadNodes(bound.control).filter(node => uploadId(node) &&
          !session.previous.has(uploadId(node)) && fileMetadata(node,file) &&
          ["uploaded","ready"].includes(node.getAttribute("data-upload-state")));
        if (candidates.length > 1) return unknown();
        if (candidates.length === 1) {fresh=uploadId(candidates[0]);break;}
        await new Promise(resolve=>global.setTimeout(resolve,100));
      }
      if (!fresh) return unknown();
      const bound = resumeControls(args.target,true);
      const selections = collect(bound?.control,["[data-career-boss-selected-upload]", ".selected-resume"]);
      if (!bound || selections.length !== 1 || uploadId(selections[0]) !== fresh ||
          !fileMetadata(selections[0],file)) return unknown();
      // Final synchronous target/selected-file checks and one send only.
      const last = resumeControls(args.target,true);
      if (!last || last.button !== bound.button || last.input !== session.input ||
          session.input.files?.length !== 1 || session.input.files[0] !== checkedFile ||
          session.input.files[0].name !== file.name ||
          session.input.files[0].size !== file.size_bytes) return unknown();
      last.button.click();
      for (let step=0;step<30;step++) {
        const current = resumeControls(args.target,true);
        if (!current) return unknown();
        const candidates = resumeMessages(current.root).filter(node => messageId(node) &&
          !session.messages.has(messageId(node)) && uploadId(node) === fresh &&
          acknowledged(node) && fileMetadata(node,file));
        if (candidates.length > 1) return unknown();
        if (candidates.length === 1) return {status:"SUCCEEDED",state:"RESUME_SENT",receipt:{
          reference_id:"boss-dom:"+messageId(candidates[0]),verified:true,
          snapshot_fingerprint:args.operation.snapshot_fingerprint}};
        await new Promise(resolve=>global.setTimeout(resolve,100));
      }
      return unknown();
    } catch {return unknown();}
  }
  global.CareerBossAction = Object.freeze({execute,prepareResume,uploadInput,finishUpload});
})(globalThis);
