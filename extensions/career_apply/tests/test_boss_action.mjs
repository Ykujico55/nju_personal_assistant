import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import vm from "node:vm";
import {webcrypto} from "node:crypto";
import test from "node:test";

const target={url:"https://www.zhipin.com/job_detail/abc12345.html",job_id:"abc12345",company:"Fixture",recruiter:"HR"};
const envelope=()=>({local_action_id:"boss_"+"b".repeat(64),target:{...target},operation:{kind:"message",text:"public fixture",snapshot_fingerprint:"a".repeat(64)},consequences:"Reviewed message.",attachments:[]});
class Node {
  constructor(text="",attrs={}) { this.textContent=text;this.attrs=attrs;this.map={};this.value="";this.tagName="DIV";this.isConnected=true;this.hidden=false;this.disabled=false;this.readOnly=false;this.changes=0; }
  querySelectorAll(selector) { return this.map[selector]||[]; }
  getAttribute(name) { return this.attrs[name]??null; }
  hasAttribute(name) { return Object.hasOwn(this.attrs,name); }
  getClientRects() { return [{}]; }
  contains(node) { return node===this||Object.values(this.map).flat().some(child=>child===node||child.contains(node)); }
  dispatchEvent() { this.changes++;this.oninput?.(); }
  click() { this.changes++;this.onclick?.(); }
}
function fixture({draft="",ack=true,wrongHR=false,extraButton=false,delivery="sent",realObserver=false}={}) {
  const doc=new Node(); doc.location={href:"https://www.zhipin.com/web/geek/chat"};
  const root=new Node(), header=new Node(), controls=new Node(), editor=new Node(), button=new Node("发送");
  editor.tagName="TEXTAREA";editor.value=draft;button.tagName="BUTTON";
  header.map["a[href]"]=[new Node("job",{href:target.url})];
  header.map[".company-name"]=[new Node("Fixture")];
  header.map[".boss-name"]=[new Node(wrongHR?"Other":"HR")];
  root.map[".chat-header"]=[header]; root.map[".message-controls"]=[controls];
  controls.map["textarea"]=[editor];controls.map["button"]=[button];
  if(extraButton) controls.map["button"].push(new Node("发送"));
  doc.map[".chat-container"]=[root];
  const messages=[];root.map["[data-career-boss-self-message]"]=messages;
  button.onclick=()=>{if(ack){const entry=new Node("",{"data-message-id":"server-1","data-delivery-state":delivery});entry.map[".message-text"]=[new Node(editor.value)];messages.push(entry);editor.value="";}};
  const observed={status:"READY",target:{...target},text:"public fixture",snapshot_fingerprint:"a".repeat(64),consequences:["Reviewed message."]};
  const context=vm.createContext({document:doc,URL,TextEncoder,crypto:webcrypto,Event:class{},InputEvent:class{},setTimeout:fn=>{fn();return 0;},CareerBossDOM:{prepareAction:async()=>({...observed})}});
  Object.defineProperty(doc,"cookie",{get(){throw Error("cookie read");}});
  Object.defineProperty(context,"localStorage",{get(){throw Error("storage read");}});
  if(realObserver) vm.runInContext(readFileSync(new URL("../browser/boss-dom.js",import.meta.url),"utf8"),context);
  vm.runInContext(readFileSync(new URL("../browser/boss-action.js",import.meta.url),"utf8"),context);
  return {api:context.CareerBossAction,doc,root,header,controls,editor,button,messages,observed,context};
}
const plain=value=>JSON.parse(JSON.stringify(value));

test("message clicks once and requires a fresh stable delivered self-message receipt",async()=>{
 const f=fixture();const result=plain(await f.api.execute(envelope()));
 assert.equal(result.status,"SUCCEEDED");assert.equal(result.state,"MESSAGE_SENT");
 assert.equal(result.receipt.reference_id,"boss-dom:server-1");assert.equal(result.receipt.verified,true);assert.equal(f.button.changes,1);
});
test("same action is replayed without another browser click",async()=>{
 const f=fixture();const first=plain(await f.api.execute(envelope()));const second=plain(await f.api.execute(envelope()));
 assert.deepEqual(first,second);assert.equal(f.button.changes,1);
});
test("missing acknowledgement and failed delivery stay UNKNOWN without retry",async()=>{
 for(const options of [{ack:false},{delivery:"failed"}]){const f=fixture(options);const result=await f.api.execute(envelope());assert.equal(result.status,"UNKNOWN");await f.api.execute(envelope());assert.equal(f.button.changes,1);}
});
test("an existing matching message cannot confirm a new send",async()=>{
 const f=fixture({ack:false});const entry=new Node("",{"data-message-id":"old-1","data-delivery-state":"sent"});entry.map[".message-text"]=[new Node("public fixture")];f.messages.push(entry);
 assert.equal((await f.api.execute(envelope())).status,"UNKNOWN");assert.equal(f.button.changes,1);
});
test("wrong target, duplicate controls and existing input cause no mutations",async()=>{
 for(const options of [{wrongHR:true},{extraButton:true},{draft:"unsaved local input"}]){const f=fixture(options);assert.equal((await f.api.execute(envelope())).status,"NEEDS_USER");assert.equal(f.button.changes,0);assert.equal(f.editor.changes,0);}
});
test("stale snapshot and changed consequences cause zero browser actions",async()=>{
 for(const field of ["snapshot","consequences"]){const f=fixture();const args=envelope();if(field==="snapshot")args.operation.snapshot_fingerprint="c".repeat(64);else args.consequences="changed";assert.equal((await f.api.execute(args)).status,"NEEDS_USER");assert.equal(f.button.changes,0);assert.equal(f.editor.changes,0);}
});
test("target changes during input prevent the click and leave outcome UNKNOWN",async()=>{
 const f=fixture();f.editor.oninput=()=>{f.header.map[".boss-name"][0].textContent="Other";};
 assert.equal((await f.api.execute(envelope())).status,"UNKNOWN");assert.equal(f.button.changes,0);
});
test("greeting and unverified remote resume never dispatch",async()=>{
 for(const kind of ["greet","send_resume"]){const f=fixture();const args=envelope();args.operation.kind=kind;assert.equal((await f.api.execute(args)).status,"NEEDS_USER");assert.equal(f.button.changes,0);assert.equal(f.editor.changes,0);}
});
test("extra attachments and foreign origin fail closed",async()=>{
 const f=fixture();const args=envelope();args.attachments=[{name:"unverified.pdf"}];assert.equal((await f.api.execute(args)).status,"NEEDS_USER");
 f.doc.location.href="https://www.zhipin.com.evil.test/";assert.equal((await f.api.execute(envelope())).status,"NEEDS_USER");assert.equal(f.button.changes,0);
});

test("the real read-only observer and action agree independent of object key order",async()=>{
 const f=fixture({realObserver:true});
 const prepared=await f.context.CareerBossDOM.prepareAction(target,"message","public fixture","");
 assert.equal(prepared.status,"READY");
 const args=envelope();args.operation.snapshot_fingerprint=prepared.snapshot_fingerprint;
 args.consequences=prepared.consequences.join("\n");
 const result=await f.api.execute(args);
 assert.equal(result.status,"SUCCEEDED");assert.equal(f.button.changes,1);
});
