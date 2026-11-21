"use strict";
const {app, BrowserWindow, shell, session, Menu} = require("electron");
const WEB_URL = "https://risegram-risegram.up.railway.app/desktop";
const ORIGIN = new URL(WEB_URL).origin;
let mainWindow;
function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1260, height: 850, minWidth: 940, minHeight: 650,
    title: "RiseGram Desktop",
    backgroundColor: "#0c1521",
    webPreferences: {
      sandbox: true,
      nodeIntegration: false,
      contextIsolation: true,
      webSecurity: true,
      allowRunningInsecureContent: false
    }
  });
  mainWindow.webContents.setWindowOpenHandler(({url}) => {
    if (url.startsWith("https://")) shell.openExternal(url);
    return {action: "deny"};
  });
  mainWindow.webContents.on("will-navigate", (event, target) => {
    try {
      const next = new URL(target);
      if (next.origin !== ORIGIN || !next.pathname.startsWith("/desktop")) {
        event.preventDefault();
        if (next.protocol === "https:") shell.openExternal(target);
      }
    } catch (_) { event.preventDefault(); }
  });
  mainWindow.loadURL(WEB_URL);
}
app.whenReady().then(() => {
  Menu.setApplicationMenu(null);
  session.defaultSession.setPermissionRequestHandler((_wc, _permission, callback) => callback(false));
  createWindow();
  app.on("activate", () => { if (BrowserWindow.getAllWindows().length === 0) createWindow(); });
});
app.on("window-all-closed", () => { if (process.platform !== "darwin") app.quit(); });
