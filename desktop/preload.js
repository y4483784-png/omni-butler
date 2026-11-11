const { ipcRenderer } = require("electron");
const { LOCAL_ACTIONS } = require("./actions");

const LABELS = {
  "desktop.open": "打开测试文件",
  "desktop.run": "运行测试程序",
};

window.addEventListener("DOMContentLoaded", () => {
  const bar = document.createElement("div");
  for (const action of LOCAL_ACTIONS) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = LABELS[action];
    button.addEventListener("click", () => {
      void ipcRenderer.invoke("desktop-local", action);
    });
    bar.appendChild(button);
  }
  document.body.prepend(bar);
});
