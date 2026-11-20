const { ipcRenderer } = require("electron");

const ACTIONS = [
  ["desktop.open", "打开测试文件"],
  ["desktop.run", "运行测试程序"],
];

window.addEventListener("DOMContentLoaded", () => {
  const bar = document.createElement("div");
  for (const [action, label] of ACTIONS) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = label;
    button.addEventListener("click", async () => {
      const offer = await ipcRenderer.invoke("desktop-propose", action);
      if (!offer || !offer.confirm_token) return;
      const confirm = document.createElement("button");
      confirm.type = "button";
      confirm.textContent = "确认";
      confirm.addEventListener("click", async () => {
        confirm.disabled = true;
        await ipcRenderer.invoke("desktop-confirm", offer);
      });
      bar.appendChild(confirm);
    });
      bar.appendChild(button);
  }
  document.body.prepend(bar);

  const armDesktopCards = () => {
    for (const block of document.querySelectorAll("[data-mode='desktop']")) {
      if (block.querySelector("button[data-desktop-confirm]")) continue;
      const action = block.getAttribute("data-action") || "";
      const deviceText = block.getAttribute("data-device-id") || "";
      if (!ACTIONS.some(([name]) => name === action) || !/^\d+$/.test(deviceText)) continue;
      let args = [];
      try {
        const parsed = JSON.parse(block.getAttribute("data-local-args") || "[]");
        args = Array.isArray(parsed) ? parsed : [];
      } catch {
        args = [];
      }
      const button = document.createElement("button");
      button.type = "button";
      button.dataset.desktopConfirm = "1";
      button.textContent = "确认";
      button.addEventListener("click", async () => {
        button.disabled = true;
        await ipcRenderer.invoke("desktop-confirm", {
          confirm_token: block.getAttribute("data-confirm-token") || "",
          action,
          local_target: block.getAttribute("data-local-target") || "",
          local_args: args,
          device_id: Number(deviceText),
        });
      });
      block.appendChild(button);
    }
  };
  armDesktopCards();
  new MutationObserver(armDesktopCards).observe(document.body, { childList: true, subtree: true });
});
