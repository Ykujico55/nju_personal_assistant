"use strict";

const captureButton = document.querySelector("#capture");
const downloadLink = document.querySelector("#download");
const resultBox = document.querySelector("#result");
const statusBox = document.querySelector("#status");
let exportURL = null;

function isBossPage(value) {
  try {
    const url = new URL(value);
    return url.protocol === "https:" && url.hostname === "www.zhipin.com"
      && !url.username && !url.password && (!url.port || url.port === "443");
  } catch {
    return false;
  }
}

captureButton.addEventListener("click", async () => {
  captureButton.disabled = true;
  downloadLink.hidden = true;
  resultBox.value = "";
  statusBox.textContent = "正在读取当前已加载的职位卡片…";
  if (exportURL) {
    URL.revokeObjectURL(exportURL);
    exportURL = null;
  }
  try {
    const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
    if (tabs.length !== 1 || !Number.isSafeInteger(tabs[0].id) || !isBossPage(tabs[0].url)) {
      throw new Error("请先打开 HTTPS 的 www.zhipin.com 职位列表。");
    }
    const target = { tabId: tabs[0].id };
    await chrome.scripting.executeScript({ target, files: ["boss-dom.js"] });
    const snapshots = await chrome.scripting.executeScript({
      target,
      func: () => {
        const url = new URL(location.href);
        if (url.protocol !== "https:" || url.hostname !== "www.zhipin.com"
          || url.username || url.password || (url.port && url.port !== "443")) return null;
        const jobs = globalThis.CareerBossDOM.extractJobs(document, location.href);
        return Array.isArray(jobs) ? jobs.slice(0, 1000) : null;
      },
    });
    const topFrame = snapshots.find((item) => item.frameId === 0);
    if (!topFrame || !Array.isArray(topFrame.result)) {
      throw new Error("页面已改变或职位结果不可识别，请重新读取。");
    }
    const payload = { source: "boss-dom-readonly", jobs: topFrame.result };
    const material = JSON.stringify(payload, null, 2) + "\n";
    if (new TextEncoder().encode(material).byteLength > 2 * 1024 * 1024) {
      throw new Error("当前采集结果超过导入器的2 MiB上限，请分批整理职位。");
    }
    resultBox.value = material;
    if (!payload.jobs.length) {
      statusBox.textContent = "当前页没有可识别的职位卡片。请先加载列表，或使用本地CSV/JSON导入。";
      return;
    }
    exportURL = URL.createObjectURL(new Blob([material], { type: "application/json;charset=utf-8" }));
    downloadLink.href = exportURL;
    downloadLink.hidden = false;
    statusBox.textContent = `已整理 ${payload.jobs.length} 条职位。可下载后导入本地规划器。`;
  } catch (error) {
    statusBox.textContent = error instanceof Error ? error.message : "读取失败，请检查当前页面。";
  } finally {
    captureButton.disabled = false;
  }
});
