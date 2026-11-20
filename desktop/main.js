const { app, BrowserWindow, ipcMain, session } = require("electron");
const { spawn } = require("child_process");
const fs = require("fs");
const os = require("os");
const path = require("path");
const { LOCAL_ACTIONS } = require("./actions");

const API = process.env.DESKTOP_API_URL || "http://127.0.0.1:8001";
const SESSION_COOKIE = "omni_session";

let packageToken = "";
let packageDeviceId = "";
let enrolling = null;

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

function tokenFile() {
  return path.join(app.getPath("userData"), "package-token");
}

function deviceFile() {
  return path.join(app.getPath("userData"), "package-device");
}

function readTokenFile() {
  try {
    return fs.readFileSync(tokenFile(), "utf8").trim();
  } catch {
    return "";
  }
}

function writeTokenFile(token) {
  const file = tokenFile();
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, token, { encoding: "utf8", mode: 0o600 });
  fs.chmodSync(file, 0o600);
}

function writeDeviceFile(id) {
  const file = deviceFile();
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, String(id), { encoding: "utf8", mode: 0o600 });
  fs.chmodSync(file, 0o600);
}

function readDeviceFile() {
  try {
    return fs.readFileSync(deviceFile(), "utf8").trim();
  } catch {
    return "";
  }
}

function loadToken() {
  if (!packageToken) packageToken = readTokenFile();
  return packageToken;
}

function loadDeviceId() {
  if (!packageDeviceId) packageDeviceId = readDeviceFile();
  return packageDeviceId;
}

async function cookieHeader() {
  const seen = new Map();
  for (const url of ["http://127.0.0.1:5173", API]) {
    const rows = await session.defaultSession.cookies.get({ url });
    for (const row of rows) seen.set(row.name, row.value);
  }
  return [...seen.entries()].map(([name, value]) => `${name}=${value}`).join("; ");
}

async function sessionCookieValue() {
  for (const url of ["http://127.0.0.1:5173", API]) {
    const rows = await session.defaultSession.cookies.get({ url, name: SESSION_COOKIE });
    if (rows.length > 0 && rows[0].value) return rows[0].value;
  }
  return "";
}

async function enrollOnce() {
  if (loadToken()) return;
  const cookie = await sessionCookieValue();
  if (!cookie) return;
  let response;
  try {
    response = await fetch(`${API}/api/desktop/devices`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Cookie: `${SESSION_COOKIE}=${cookie}`,
      },
      body: JSON.stringify({ label: os.hostname() }),
    });
  } catch {
    return;
  }
  if (!response.ok) return;
  const body = await response.json();
  if (!body || typeof body.token !== "string" || !body.token) return;
  if (typeof body.id !== "number" || !Number.isInteger(body.id)) return;
  packageDeviceId = String(body.id);
  writeDeviceFile(body.id);
  packageToken = body.token;
  writeTokenFile(body.token);
}

function enrollIfNeeded() {
  if (loadToken()) return Promise.resolve();
  if (enrolling) return enrolling;
  enrolling = enrollOnce().finally(() => {
    enrolling = null;
  });
  return enrolling;
}

function runPackage(args, cookie, stdin) {
  const python = process.env.PYTHON || "python3";
  const repo = path.join(__dirname, "..");
  const token = loadToken();
  return new Promise((resolve) => {
    const child = spawn(python, ["-m", "desktop.confirm_local", ...args], {
      cwd: repo,
      env: {
        ...process.env,
        PYTHONPATH: repo,
        DESKTOP_PACKAGE_TOKEN: token,
        DESKTOP_DEVICE_ID: loadDeviceId(),
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
  await enrollIfNeeded();
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

app.whenReady().then(() => {
  loadToken();
  loadDeviceId();
  session.defaultSession.cookies.on("changed", () => {
    enrollIfNeeded();
  });
  createWindow();
  enrollIfNeeded();
});
app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});
