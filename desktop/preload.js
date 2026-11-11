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
});
