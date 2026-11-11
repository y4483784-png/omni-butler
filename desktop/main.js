const { app, BrowserWindow, ipcMain, session } = require("electron");
const { spawn } = require("child_process");
const path = require("path");
const { LOCAL_ACTIONS } = require("./actions");

const API = process.env.DESKTOP_API_URL || "http://127.0.0.1:8001";

function createWindow() {
  const win = new BrowserWindow({
    width: 1100,
    height: 800,
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      preload: path.join(__dirname, "preload.js"),
    },
  });
  win.loadURL("http://127.0.0.1:5173");
}

async function cookieHeader() {
  const seen = new Map();
  for (const url of ["http://127.0.0.1:5173", API]) {
    const rows = await session.defaultSession.cookies.get({ url });
    for (const row of rows) seen.set(row.name, row.value);
  }
  return [...seen.entries()].map(([name, value]) => `${name}=${value}`).join("; ");
}

function runPackage(args, cookie, stdin) {
  const python = process.env.PYTHON || "python3";
  const repo = path.join(__dirname, "..");
  return new Promise((resolve) => {
    const child = spawn(python, ["-m", "desktop.confirm_local", ...args], {
      cwd: repo,
      env: {
        ...process.env,
        PYTHONPATH: repo,
        DESKTOP_PACKAGE_TOKEN: process.env.DESKTOP_PACKAGE_TOKEN || "",
        DESKTOP_API_URL: API,
        DESKTOP_COOKIE: cookie,
      },
      shell: false,
    });
    let out = "";
    child.stdout.on("data", (buf) => {
      out += buf.toString("utf8");
    });
    if (stdin) {
      child.stdin.write(stdin);
      child.stdin.end();
    }
    child.on("exit", (code) => resolve({ code: code ?? 1, out }));
    child.on("error", () => resolve({ code: 1, out: "" }));
  });
}

ipcMain.handle("desktop-propose", async (_event, action) => {
  if (!LOCAL_ACTIONS.includes(action)) return null;
  const result = await runPackage(["propose", action], await cookieHeader());
  if (result.code !== 0) return null;
  try {
    return JSON.parse(result.out);
  } catch {
    return null;
  }
});

ipcMain.handle("desktop-confirm", async (_event, offer) => {
  if (!offer || !LOCAL_ACTIONS.includes(offer.action)) return { ok: false };
  const result = await runPackage(["confirm"], await cookieHeader(), JSON.stringify(offer));
  return { ok: result.code === 0 };
});

app.whenReady().then(createWindow);
app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});
