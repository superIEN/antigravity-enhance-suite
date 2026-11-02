#!/usr/bin/env python3
"""
Antigravity (macOS v2.25.0) 桌面增强套件 app.asar 构建、注入与一键回滚工具 (A-41 合规版)

职责边界与安全契约：
1. `status`: 只读巡检本机原厂基线 (`app.asar.orig_2.25.0.bak`)、现行装机 (`app.asar`)、候选包 (`work/app.asar.patched`) 及三处 `agy-enhance-loader.js` 的 SHA256、字节数与注入契约一致性；
2. `build`: 在 `/Users/ien/agy治理/02_desktop_enhance/work/` 隔离沙箱内基于原厂纯净基线构建 `app.asar.patched`，完成全部 `dist/*.js` 语法校验 (`node --check`) 与 ASAR 4MB 分块 SHA256 完整性校验，并备份现行原件至 `backups/`（绝不触碰 `/Users/ien/Applications/Antigravity.app` 或 `/Users/ien/.gemini/agy-enhance`）；
3. `install`: 经主持（会话 1）协同确认后执行，原子替换 `agy-enhance-loader.js` 与 `app.asar`，更新 `last_install_receipt.json`，支持重签名与 `--hot-inject` 免重启热注入；
4. `rollback`: 一键可逆回滚，支持回滚至会话 2 施工前快照 (`--target pre-session2`) 或 v2.25.0 原厂纯净基线 (`--target orig`)，并逐项回读校验 SHA256。
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import os
import re
import shutil
import struct
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

WORK_ROOT = Path("/Users/ien/agy治理/02_desktop_enhance")
WORK_DIR = WORK_ROOT / "work"
STAGING_JS_DIR = WORK_DIR / "staging_dist"
CANDIDATE_ASAR_PATH = WORK_DIR / "app.asar.patched"
BUILD_RECEIPT_PATH = WORK_DIR / "build_receipt.json"
BACKUP_DIR = WORK_ROOT / "backups"
MERGED_LOADER_PATH = WORK_ROOT / "agy-enhance-loader.js"

APP_BUNDLE_PATH = Path("/Users/ien/Applications/Antigravity.app")
LIVE_ASAR_PATH = APP_BUNDLE_PATH / "Contents" / "Resources" / "app.asar"

RUNTIME_DIR = Path.home() / ".gemini" / "agy-enhance"
ORIG_ASAR_BAK_PATH = RUNTIME_DIR / "app.asar.orig_2.25.0.bak"
LIVE_LOADER_PATH = RUNTIME_DIR / "agy-enhance-loader.js"
SUITE_LOADER_PATH = RUNTIME_DIR / "antigravity-enhance-suite" / "src" / "agy-enhance-loader.js"
INSTALL_RECEIPT_PATH = RUNTIME_DIR / "last_install_receipt.json"

BLOCK_SIZE = 4 * 1024 * 1024


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_sha256_and_size(path: Path) -> tuple[str | None, int | None]:
    if not path.is_file():
        return None, None
    raw = path.read_bytes()
    return sha256_hex(raw), len(raw)


def compute_asar_integrity(data: bytes) -> dict:
    blocks = []
    if len(data) == 0:
        blocks.append(sha256_hex(b""))
    else:
        for i in range(0, len(data), BLOCK_SIZE):
            blocks.append(sha256_hex(data[i : i + BLOCK_SIZE]))
    return {
        "algorithm": "SHA256",
        "hash": sha256_hex(data),
        "blockSize": BLOCK_SIZE,
        "blocks": blocks,
    }


def extract_file_from_asar(asar_path: Path, rel_parts: list[str]) -> bytes | None:
    if not asar_path.is_file():
        return None
    raw = asar_path.read_bytes()
    _, hdr_sz, _, json_sz = struct.unpack_from("<IIII", raw, 0)
    hdr = json.loads(raw[16 : 16 + json_sz].decode("utf-8"))
    data_start = 8 + hdr_sz
    node = hdr
    for part in rel_parts:
        node = node.get("files", {}).get(part)
        if not node:
            return None
    if "offset" not in node or "size" not in node:
        return None
    off = int(node["offset"])
    sz = int(node["size"])
    return raw[data_start + off : data_start + off + sz]


def inspect_asar_loader_contract(asar_path: Path) -> dict:
    utils_bytes = extract_file_from_asar(asar_path, ["dist", "utils.js"])
    if not utils_bytes:
        return {"patched": False}
    text = utils_bytes.decode("utf-8", errors="replace")
    m_sha = re.search(r'EXPECTED_LOADER_SHA256\s*=\s*"([0-9a-fA-F]+)"', text)
    m_len = re.search(r"EXPECTED_LOADER_BYTES\s*=\s*(\d+)", text)
    m_ver = re.search(r'EXPECTED_LOADER_VERSION\s*=\s*"([^"]+)"', text)
    return {
        "patched": bool(m_sha and m_len),
        "expected_loader_sha256": m_sha.group(1) if m_sha else None,
        "expected_loader_bytes": int(m_len.group(1)) if m_len else None,
        "expected_loader_version": m_ver.group(1) if m_ver else None,
        "has_receipt_fallback": "last_install_receipt.json" in text,
    }


def patch_storage_js(code: str) -> str:
    old_ctor = "this.emitter = new events_1.EventEmitter();"
    new_ctor = (
        "this.emitter = new events_1.EventEmitter();\n"
        "        this._writeQueue = Promise.resolve();"
    )
    assert old_ctor in code, "storage.js constructor anchor not found"
    code = code.replace(old_ctor, new_ctor, 1)

    old_update = """    async updateItems(changes) {
        try {
            const currentItems = await this.getRawItems();
            for (const [key, value] of Object.entries(changes)) {
                if (value === null) {
                    delete currentItems[key];
                }
                else {
                    currentItems[key] = value;
                }
            }
            // Ensure directory exists
            const dir = path.dirname(this.storagePath);
            if (!(0, fs_1.existsSync)(dir)) {
                await fs.mkdir(dir, { recursive: true });
            }
            await fs.writeFile(this.storagePath, JSON.stringify(currentItems, null, 2), 'utf-8');
            const windows = electron_1.BrowserWindow.getAllWindows();
            for (const win of windows) {
                win.webContents.send('storage:changed', changes);
            }
            this.emitter.emit('changed', changes);
        }
        catch (e) {
            console.error('Error updating storage items:', e);
            throw e;
        }
    }"""

    new_update = """    async updateItems(changes) {
        const op = this._writeQueue.then(() => this._doUpdateItems(changes));
        this._writeQueue = op.catch(() => {});
        return op;
    }
    async _doUpdateItems(changes) {
        try {
            let currentItems = {};
            if ((0, fs_1.existsSync)(this.storagePath)) {
                const raw = await fs.readFile(this.storagePath, 'utf-8');
                if (raw && raw.trim() !== '') {
                    try {
                        currentItems = JSON.parse(raw);
                    } catch {
                        currentItems = {};
                    }
                }
            }
            for (const [key, value] of Object.entries(changes)) {
                if (value === null) {
                    delete currentItems[key];
                }
                else {
                    currentItems[key] = value;
                }
            }
            const dir = path.dirname(this.storagePath);
            if (!(0, fs_1.existsSync)(dir)) {
                await fs.mkdir(dir, { recursive: true });
            }
            const tmpPath = this.storagePath + '.tmp.' + process.pid;
            await fs.writeFile(tmpPath, JSON.stringify(currentItems, null, 2), 'utf-8');
            await fs.rename(tmpPath, this.storagePath);
            const windows = electron_1.BrowserWindow.getAllWindows();
            for (const win of windows) {
                if (!win.isDestroyed()) {
                    win.webContents.send('storage:changed', changes);
                }
            }
            this.emitter.emit('changed', changes);
        }
        catch (e) {
            console.error('Error updating storage items:', e);
            throw e;
        }
    }"""
    assert old_update in code, "storage.js updateItems anchor not found"
    return code.replace(old_update, new_update, 1)


def patch_ipc_handlers_js(code: str) -> str:
    old_overlay = """    electron_1.ipcMain.handle('window:set-title-bar-overlay', async (_event, options) => {
        const win = electron_1.BrowserWindow.getFocusedWindow() || electron_1.BrowserWindow.getAllWindows()[0];
        if (win && process.platform === 'win32') {
            win.setTitleBarOverlay({
                color: options.color,
                symbolColor: options.symbolColor,
                height: 30,
            });
        }
    });"""

    new_overlay = """    const languageServer_agy_ipc = require('./languageServer');

    function isTrustedAgySender(_event) {
        if (!_event || !_event.sender || _event.sender.isDestroyed()) return null;
        if (_event.senderFrame !== _event.sender.mainFrame) return null;
        const win = electron_1.BrowserWindow.fromWebContents(_event.sender);
        if (!win || win.isDestroyed()) return null;
        try {
            const parsed = new URL(_event.sender.getURL() || '');
            const liveLsPort = (0, languageServer_agy_ipc.getLsPort)();
            if (
                parsed.protocol !== 'https:' ||
                parsed.hostname !== '127.0.0.1' ||
                parsed.username !== '' ||
                parsed.password !== '' ||
                !/^\\d+$/.test(parsed.port) ||
                liveLsPort <= 0 ||
                Number(parsed.port) !== liveLsPort
            ) {
                return null;
            }
            return win;
        } catch {
            return null;
        }
    }

    electron_1.ipcMain.handle('window:set-title-bar-overlay', async (_event, options) => {
        const win = isTrustedAgySender(_event);
        if (
            win &&
            process.platform === 'win32' &&
            options &&
            typeof options.color === 'string' &&
            /^#[0-9a-fA-F]{6}$/.test(options.color) &&
            typeof options.symbolColor === 'string' &&
            /^#[0-9a-fA-F]{6}$/.test(options.symbolColor)
        ) {
            win.setTitleBarOverlay({
                color: options.color,
                symbolColor: options.symbolColor,
                height: 30,
            });
        }
    });

    // Antigravity v3.1.1 Single-Writer Preference Service (Disk-First Commit + Optimistic Concurrency)
    const path = require('path');
    const os_agy = require('os');
    const fsSync = require('fs');
    const agyRuntimeDir = process.env.AGY_ENHANCE_DIR || path.join(os_agy.homedir(), '.gemini', 'agy-enhance');
    const agyPrefsPath = path.join(agyRuntimeDir, 'preferences.v3.json');
    const agyBackupsDir = path.join(agyRuntimeDir, 'backups');
    const DEFAULT_AGY_PREFS = {
        schemaVersion: 3,
        revision: 1,
        global: {
            enabled: true,
            theme: 'native',
            lang: 'zh-std',
            widescreen: true,
            translateThinking: true,
            missionHud: true,
        },
        windowDefaults: {},
    };
    let agyPrefsState = JSON.parse(JSON.stringify(DEFAULT_AGY_PREFS));
    try {
        if (fsSync.existsSync(agyPrefsPath)) {
            const loaded = JSON.parse(fsSync.readFileSync(agyPrefsPath, 'utf-8'));
            if (loaded && typeof loaded.revision === 'number' && loaded.global) {
                agyPrefsState = {
                    schemaVersion: 3,
                    revision: loaded.revision,
                    global: {
                        enabled: typeof loaded.global.enabled === 'boolean' ? loaded.global.enabled : true,
                        theme: 'native',
                        lang: loaded.global.lang === 'en' ? 'en' : 'zh-std',
                        widescreen: typeof loaded.global.widescreen === 'boolean' ? loaded.global.widescreen : true,
                        translateThinking: typeof loaded.global.translateThinking === 'boolean' ? loaded.global.translateThinking : true,
                        missionHud: typeof loaded.global.missionHud === 'boolean' ? loaded.global.missionHud : true,
                    },
                    windowDefaults: (loaded.windowDefaults && typeof loaded.windowDefaults === 'object') ? loaded.windowDefaults : {},
                };
            }
        }
    } catch (err) {
        console.error('[AGY-Prefs] Failed to read preferences.v3.json, keeping safe in-memory state:', err);
    }
    let agyPrefsWriteQueue = Promise.resolve();

    function validateGlobalPatch(patch) {
        const clean = {};
        if ('enabled' in patch && typeof patch.enabled === 'boolean') clean.enabled = patch.enabled;
        if ('theme' in patch && patch.theme === 'native') clean.theme = 'native';
        if ('lang' in patch && ['en', 'zh-std'].includes(patch.lang)) clean.lang = patch.lang;
        if ('widescreen' in patch && typeof patch.widescreen === 'boolean') clean.widescreen = patch.widescreen;
        if ('translateThinking' in patch && typeof patch.translateThinking === 'boolean') clean.translateThinking = patch.translateThinking;
        if ('missionHud' in patch && typeof patch.missionHud === 'boolean') clean.missionHud = patch.missionHud;
        return clean;
    }

    function validateWindowPatch(patch) {
        if (!patch || typeof patch !== 'object') return {};
        const clean = {};
        if ('enabled' in patch && typeof patch.enabled === 'boolean') clean.enabled = patch.enabled;
        if ('widescreen' in patch && typeof patch.widescreen === 'boolean') clean.widescreen = patch.widescreen;
        if ('hudCollapsed' in patch && typeof patch.hudCollapsed === 'boolean') clean.hudCollapsed = patch.hudCollapsed;
        if ('bossMode' in patch && typeof patch.bossMode === 'boolean') clean.bossMode = patch.bossMode;
        if ('theme' in patch && patch.theme === 'native') clean.theme = 'native';
        if ('lang' in patch && ['en', 'zh-std'].includes(patch.lang)) clean.lang = patch.lang;
        return clean;
    }

    electron_1.ipcMain.handle('agy:get-preferences', async (_event) => {
        if (!isTrustedAgySender(_event)) return null;
        return {
            schemaVersion: agyPrefsState.schemaVersion,
            revision: agyPrefsState.revision,
            lsGeneration: global.__agyLsGeneration || 1,
            global: agyPrefsState.global,
            windowDefaults: agyPrefsState.windowDefaults,
        };
    });

    electron_1.ipcMain.handle('agy:update-preferences', async (_event, payload) => {
        if (!isTrustedAgySender(_event)) return null;
        const op = agyPrefsWriteQueue.then(async () => {
            if (!payload || typeof payload !== 'object') {
                return { revision: agyPrefsState.revision };
            }
            if (payload.globalPatch && typeof payload.globalPatch === 'object') {
                if (
                    typeof payload.expectedRevision !== 'number' ||
                    !Number.isInteger(payload.expectedRevision) ||
                    payload.expectedRevision < 1 ||
                    payload.expectedRevision !== agyPrefsState.revision
                ) {
                    return {
                        conflict: true,
                        revision: agyPrefsState.revision,
                        global: agyPrefsState.global,
                        windowDefaults: agyPrefsState.windowDefaults,
                    };
                }
            }

            const nextState = JSON.parse(JSON.stringify(agyPrefsState));
            let globalChanged = false;
            let winChanged = false;

            if (payload.globalPatch && typeof payload.globalPatch === 'object') {
                const validated = validateGlobalPatch(payload.globalPatch);
                for (const [k, v] of Object.entries(validated)) {
                    if (nextState.global[k] !== v) {
                        nextState.global[k] = v;
                        globalChanged = true;
                    }
                }
            }
            if (payload.windowDefaultsPatch && typeof payload.windowDefaultsPatch === 'object') {
                const validatedWin = validateWindowPatch(payload.windowDefaultsPatch);
                for (const [k, v] of Object.entries(validatedWin)) {
                    if (JSON.stringify(nextState.windowDefaults[k]) !== JSON.stringify(v)) {
                        nextState.windowDefaults[k] = v;
                        winChanged = true;
                    }
                }
            }

            if (!globalChanged && !winChanged) {
                return { revision: agyPrefsState.revision };
            }
            if (globalChanged) {
                nextState.revision += 1;
            }

            await fs.mkdir(agyRuntimeDir, { recursive: true });
            const tmp = agyPrefsPath + '.tmp.' + process.pid;
            await fs.writeFile(tmp, JSON.stringify(nextState, null, 2), 'utf-8');
            await fs.rename(tmp, agyPrefsPath);

            agyPrefsState = nextState;
            if (globalChanged) {
                const msg = {
                    revision: agyPrefsState.revision,
                    lsGeneration: global.__agyLsGeneration || 1,
                    global: agyPrefsState.global,
                };
                for (const w of electron_1.BrowserWindow.getAllWindows()) {
                    if (!w.isDestroyed()) {
                        w.webContents.send('agy:preferences-changed', msg);
                    }
                }
            }
            return { revision: agyPrefsState.revision };
        });
        agyPrefsWriteQueue = op.catch(() => {});
        return op;
    });

    electron_1.ipcMain.handle('agy:backup-legacy-flags', async (_event, backupPayload) => {
        if (!isTrustedAgySender(_event) || !backupPayload || typeof backupPayload !== 'object') return false;
        try {
            await fs.mkdir(agyBackupsDir, { recursive: true });
            const safeOrigin = String(backupPayload.origin || 'default').replace(/[^a-zA-Z0-9_.-]/g, '_');
            const outFile = path.join(agyBackupsDir, `legacy_flags_${safeOrigin}.json`);
            if (!fsSync.existsSync(outFile)) {
                await fs.writeFile(outFile, JSON.stringify(backupPayload, null, 2), 'utf-8');
            }
            return true;
        } catch {
            return false;
        }
    });"""

    assert old_overlay in code, "ipcHandlers.js window:set-title-bar-overlay anchor not found"
    return code.replace(old_overlay, new_overlay, 1)


def patch_preload_js(code: str) -> str:
    bridge_code = """
const agyBridgeAPI = {
    getPreferences: () => electron_1.ipcRenderer.invoke('agy:get-preferences'),
    updatePreferences: (patch) => electron_1.ipcRenderer.invoke('agy:update-preferences', patch),
    backupLegacyFlags: (data) => electron_1.ipcRenderer.invoke('agy:backup-legacy-flags', data),
    onPreferencesChanged: (callback) => {
        const handler = (_event, payload) => {
            callback(payload);
        };
        electron_1.ipcRenderer.on('agy:preferences-changed', handler);
        return () => {
            electron_1.ipcRenderer.removeListener('agy:preferences-changed', handler);
        };
    },
    setTitleBarOverlay: (options) => electron_1.ipcRenderer.invoke('window:set-title-bar-overlay', options),
};
electron_1.contextBridge.exposeInMainWorld('__AGY_BRIDGE__', agyBridgeAPI);
try {
    electron_1.contextBridge.exposeInIsolatedWorld(1004, '__AGY_BRIDGE__', agyBridgeAPI);
} catch (e) {}
"""
    assert "__agy_bootstrap_v1__" not in code, "preload.js should be clean original baseline"
    return code + "\n" + bridge_code


def patch_utils_js(code: str, loader_sha256: str, loader_byte_len: int) -> str:
    code = code.replace("devTools: !electron_1.app.isPackaged,", "devTools: true,")
    code = code.replace("devTools: (0, debugConsole_1.isDebugConsoleEnabled)(),", "devTools: true,")
    code = code.replace("const maxLength = 25;", "const maxLength = 120;")

    old_create = "function createWindow(url, storageManager) {"
    new_create = f"""let _defaultStorageManager = null;
const crypto_agy = require("crypto");
const os_agy = require("os");
const AGY_LOADER_PATH = process.env.AGY_LOADER_PATH || path_1.default.join(os_agy.homedir(), ".gemini", "agy-enhance", "agy-enhance-loader.js");
const EXPECTED_LOADER_SHA256 = "{loader_sha256}";
const EXPECTED_LOADER_BYTES = {loader_byte_len};
const EXPECTED_LOADER_VERSION = "3.6.0-macos-merged";
const languageServer_agy = require("./languageServer");

function verifyLoaderIntegrity(rawBytes, actualSha) {{
    if (rawBytes.length === EXPECTED_LOADER_BYTES && actualSha === EXPECTED_LOADER_SHA256) {{
        return true;
    }}
    try {{
        const receiptPath = path_1.default.join(path_1.default.dirname(AGY_LOADER_PATH), "last_install_receipt.json");
        if (fs.existsSync(receiptPath)) {{
            const receipt = JSON.parse(fs.readFileSync(receiptPath, "utf-8"));
            const selfUtilsSha = crypto_agy.createHash("sha256").update(fs.readFileSync(__filename)).digest("hex");
            if (
                receipt &&
                receipt.desktop_enhance_version === EXPECTED_LOADER_VERSION &&
                receipt.utils_js_sha256 === selfUtilsSha &&
                receipt.loader_bytes === rawBytes.length &&
                receipt.loader_sha256 === actualSha
            ) {{
                return true;
            }}
        }}
    }} catch {{}}
    return false;
}}

function attachControlledEnhancement(win) {{
    const wc = win.webContents;
    let generation = 0;
    let disposed = false;

    function onNavigation(_event, _url, isInPlace, isMainFrame) {{
        if (isMainFrame && !isInPlace) generation++;
    }}

    async function onDomReady() {{
        const expectedGeneration = generation;
        try {{
            if (disposed || wc.isDestroyed()) return;
            const currentUrl = wc.getURL() || '';
            const livePort = (0, languageServer_agy.getLsPort)();
            const expectedOrigin = livePort > 0 ? `https://127.0.0.1:${{livePort}}` : '';
            if (!expectedOrigin || new URL(currentUrl).origin !== expectedOrigin) return;
            if (!fs.existsSync(AGY_LOADER_PATH)) return;
            const rawBytes = await fs.promises.readFile(AGY_LOADER_PATH);
            const actualSha = crypto_agy.createHash('sha256').update(rawBytes).digest('hex');
            if (!verifyLoaderIntegrity(rawBytes, actualSha)) {{
                throw new Error(`Loader integrity mismatch: len=${{rawBytes.length}} (expected ${{EXPECTED_LOADER_BYTES}}), sha=${{actualSha}} (expected ${{EXPECTED_LOADER_SHA256}})`);
            }}
            if (disposed || wc.isDestroyed() || expectedGeneration !== generation) return;
            const loaderSource = rawBytes.toString('utf-8');
            const wrapped = `(() => {{
                ${{loaderSource}}
                return globalThis.__AGY_V3__?.readyPromise ?? Promise.resolve({{ status: 'failed' }});
            }})()`;
            const receipt = await wc.executeJavaScript(wrapped, false);
            if (disposed || wc.isDestroyed() || expectedGeneration !== generation) return;
            if (!receipt || receipt.status !== 'ready' || receipt.version !== EXPECTED_LOADER_VERSION) {{
                throw new Error('Loader initialization receipt invalid: ' + JSON.stringify(receipt));
            }}
        }} catch (err) {{
            console.error('[AGY-v3.6.0-macos-merged] Enhancement load skipped or failed:', err);
        }}
    }}

    function disposeBinding() {{
        disposed = true;
        generation++;
        wc.removeListener('did-start-navigation', onNavigation);
        wc.removeListener('dom-ready', onDomReady);
        wc.removeListener('destroyed', disposeBinding);
    }}

    wc.on('did-start-navigation', onNavigation);
    wc.on('dom-ready', onDomReady);
    wc.once('destroyed', disposeBinding);
}}

function createWindow(url, storageManager) {{
    if (storageManager) {{
        _defaultStorageManager = storageManager;
    }} else if (_defaultStorageManager) {{
        storageManager = _defaultStorageManager;
    }}
    try {{
        const livePort = (0, languageServer_agy.getLsPort)();
        if (livePort > 0 && typeof url === 'string' && url.startsWith('https://127.0.0.1:')) {{
            const parsedUrl = new URL(url);
            parsedUrl.port = String(livePort);
            url = parsedUrl.toString();
        }}
    }} catch {{}}"""

    assert old_create in code, "utils.js createWindow anchor not found"
    code = code.replace(old_create, new_create, 1)

    old_load = "    void win.loadURL(url);\n    return win;"
    new_load = "    attachControlledEnhancement(win);\n    void win.loadURL(url);\n    return win;"
    assert old_load in code, "utils.js win.loadURL anchor not found"
    return code.replace(old_load, new_load, 1)


def patch_menu_js(code: str) -> str:
    code = code.replace("hideDevTools(menu);", "/*hideDevTools*/")
    code = code.replace("(0, debugConsole_1.configureDebugConsoleMenu)(menu);", "/*configureDebugConsoleMenu*/")
    return code


def patch_debug_console_js(code: str) -> str:
    return code.replace("return !electron_1.app.isPackaged;", "return true;")


def patch_language_server_js(code: str) -> str:
    old_state = "let _lsStdinIsLiveness = false;"
    new_state = """let _lsStdinIsLiveness = false;
let _spawnAttemptSeq = 0;

function terminateChildBounded(child, graceMs = 3000) {
    return new Promise((resolve) => {
        if (!child || child.exitCode !== null || child.signalCode !== null) {
            resolve(true);
            return;
        }
        let settled = false;
        const finish = (exited) => {
            if (!settled) {
                settled = true;
                resolve(exited);
            }
        };
        child.once('exit', () => finish(true));
        try {
            child.kill('SIGTERM');
        } catch {}
        setTimeout(() => {
            if (settled) return;
            try {
                child.kill('SIGKILL');
            } catch {
                try {
                    if (child.pid !== undefined) process.kill(child.pid, 'SIGKILL');
                } catch {}
            }
            setTimeout(() => {
                finish(child.exitCode !== null || child.signalCode !== null);
            }, 1000);
        }, graceMs);
    });
}"""
    assert old_state in code, "languageServer.js _lsStdinIsLiveness anchor not found"
    code = code.replace(old_state, new_state, 1)

    old_clear = """function clearLsProcess() {
    _lsProcess = null;
}"""
    new_clear = """function clearLsProcess(expectedChild) {
    if (!expectedChild || _lsProcess === expectedChild) {
        _lsProcess = null;
    }
}"""
    assert old_clear in code, "languageServer.js clearLsProcess anchor not found"
    code = code.replace(old_clear, new_clear, 1)

    old_stream = "const logStream = fs.createWriteStream((0, paths_1.getLsLogPath)(), { flags: 'w' });"
    new_stream = (
        "try { fs.copyFileSync((0, paths_1.getLsLogPath)(), (0, paths_1.getLsLogPath)() + '.prev'); } catch {}\n"
        "        const logStream = fs.createWriteStream((0, paths_1.getLsLogPath)(), { flags: 'w' });"
    )
    assert old_stream in code, "languageServer.js logStream anchor not found"
    code = code.replace(old_stream, new_stream, 1)

    old_spawn_block = """        _lsProcess = (0, child_process_1.spawn)(spawnCmd, spawnArgs, {
            stdio: ['pipe', 'pipe', 'pipe'],
            env,
        });
        _lsStdinIsLiveness = !!wsl;
        if (!headless && !wsl) {
            // Close stdin immediately — the LS may block waiting for metadata on stdin.
            // In WSL mode stdin must stay open: it is the LS's liveness signal.
            _lsProcess.stdin.end();
        }
        const combined = new stream_1.PassThrough();
        _lsProcess.stdout.pipe(combined, { end: false });
        _lsProcess.stderr.pipe(combined, { end: false });
        // Buffer stderr for crash log extraction (ring buffer)
        const stderrChunks = [];
        let stderrLength = 0;
        _lsProcess.stderr.on('data', (data) => {
            const str = data.toString();
            stderrChunks.push(str);
            stderrLength += str.length;
            while (stderrChunks.length > 0 && stderrLength > MAX_STDERR_BUFFER) {
                stderrLength -= stderrChunks.shift().length;
            }
        });
        let resolved = false;
        let logStreamEnded = false;
        const timer = setTimeout(() => {
            if (!resolved) {
                resolved = true;
                reject(new Error(`Timeout: language server did not report its port within ${LS_STARTUP_TIMEOUT_MS / 1000}s`));
            }
        }, LS_STARTUP_TIMEOUT_MS);
        const rl = readline.createInterface({ input: combined, crlfDelay: Infinity });
        rl.on('close', () => {
            if (!logStreamEnded) {
                logStreamEnded = true;
                logStream.end();
            }
        });
        rl.on('line', (line) => {
            if (!logStreamEnded) {
                logStream.write(line + '\\n');
            }
            if (!resolved) {
                const m = PORT_PATTERN.exec(line);
                if (m) {
                    resolved = true;
                    clearTimeout(timer);
                    const actualPort = parseInt(m[1], 10);
                    _lsPort = actualPort;
                    resolve({
                        port: actualPort,
                        process: _lsProcess,
                        exitPromise,
                    });
                }
            }
            const authMatch = AUTH_URL_PATTERN.exec(line);
            if (authMatch) {
                console.log('\\n' + '='.repeat(60));
                console.log('  Please visit the following URL to authorize.');
                console.log('  After authorizing, paste the authorization code below.');
                console.log(`  ${authMatch[0]}`);
                console.log('='.repeat(60) + '\\n');
            }
            const openUrl = extractOpenUrl(line);
            if (openUrl) {
                console.log(`Opening URL requested by the language server: ${openUrl}`);
                void electron_1.shell.openExternal(openUrl);
            }
        });
        // Exit promise — resolves whenever the process exits (whether during
        // startup or after). Includes crash stack trace extraction.
        const exitPromise = new Promise((exitResolve) => {
            _lsProcess.on('exit', (code, signal) => {
                if (!logStreamEnded) {
                    logStreamEnded = true;
                    logStream.end();
                }
                const fullStderr = stderrChunks.join('');
                const crashStackTrace = extractCrashStackTrace(fullStderr);
                // If we haven't resolved the startup promise yet, reject it.
                if (!resolved) {
                    resolved = true;
                    clearTimeout(timer);
                    reject(new Error(`Language server exited unexpectedly (code=${code}, signal=${signal})`));
                }
                exitResolve({ code, signal, crashStackTrace });
            });
        });"""

    new_spawn_block = """        const attemptId = ++_spawnAttemptSeq;
        const child = (0, child_process_1.spawn)(spawnCmd, spawnArgs, {
            stdio: ['pipe', 'pipe', 'pipe'],
            env,
        });
        _lsProcess = child;
        _lsStdinIsLiveness = !!wsl;
        if (!headless && !wsl) {
            child.stdin.end();
        }
        const combined = new stream_1.PassThrough();
        child.stdout.pipe(combined, { end: false });
        child.stderr.pipe(combined, { end: false });
        const stderrChunks = [];
        let stderrLength = 0;
        let stderrClosed = false;
        const stderrClosePromise = new Promise((res) => {
            child.stderr.once('close', () => {
                stderrClosed = true;
                res();
            });
        });
        child.stderr.on('data', (data) => {
            const str = data.toString();
            stderrChunks.push(str);
            stderrLength += str.length;
            while (stderrChunks.length > 0 && stderrLength > MAX_STDERR_BUFFER) {
                stderrLength -= stderrChunks.shift().length;
            }
        });
        let resolved = false;
        let logStreamEnded = false;
        let timer = null;
        const endLogStreamSafe = () => {
            if (!logStreamEnded) {
                logStreamEnded = true;
                logStream.end();
            }
        };
        child.once('error', (spawnErr) => {
            const exited = child.exitCode !== null || child.signalCode !== null || child.pid === undefined;
            if (exited) {
                clearLsProcess(child);
            }
            endLogStreamSafe();
            if (!resolved) {
                resolved = true;
                if (timer !== null) clearTimeout(timer);
                spawnErr.isSpawnError = child.pid === undefined;
                spawnErr.childConfirmedExited = exited;
                reject(spawnErr);
            }
        });
        timer = setTimeout(() => {
            if (!resolved) {
                resolved = true;
                void terminateChildBounded(child, 3000).then((exited) => {
                    if (exited) {
                        clearLsProcess(child);
                    }
                    endLogStreamSafe();
                    const err = new Error(`Timeout: language server (attempt ${attemptId}) did not report its port within ${LS_STARTUP_TIMEOUT_MS / 1000}s`);
                    err.isStartupTimeout = true;
                    err.childConfirmedExited = exited;
                    err.fatalUnkillableChild = !exited;
                    reject(err);
                });
            }
        }, LS_STARTUP_TIMEOUT_MS);
        const rl = readline.createInterface({ input: combined, crlfDelay: Infinity });
        rl.on('close', endLogStreamSafe);
        rl.on('line', (line) => {
            if (!logStreamEnded) {
                logStream.write(line + '\\n');
            }
            if (!resolved) {
                const m = PORT_PATTERN.exec(line);
                if (m) {
                    resolved = true;
                    clearTimeout(timer);
                    const actualPort = parseInt(m[1], 10);
                    _lsPort = actualPort;
                    global.__agyLsGeneration = (global.__agyLsGeneration || 0) + 1;
                    resolve({
                        port: actualPort,
                        process: child,
                        exitPromise,
                    });
                }
            }
            const authMatch = AUTH_URL_PATTERN.exec(line);
            if (authMatch) {
                console.log('\\n' + '='.repeat(60));
                console.log('  Please visit the following URL to authorize.');
                console.log('  After authorizing, paste the authorization code below.');
                console.log(`  ${authMatch[0]}`);
                console.log('='.repeat(60) + '\\n');
            }
            const openUrl = extractOpenUrl(line);
            if (openUrl) {
                console.log(`Opening URL requested by the language server: ${openUrl}`);
                void electron_1.shell.openExternal(openUrl);
            }
        });
        const exitPromise = new Promise((exitResolve) => {
            child.once('exit', (code, signal) => {
                clearLsProcess(child);
                void Promise.race([
                    stderrClosePromise,
                    new Promise((r) => setTimeout(r, stderrClosed ? 0 : 1500)),
                ]).then(() => {
                    try { rl.close(); } catch {}
                    try { combined.destroy(); } catch {}
                    try { child.stderr.removeAllListeners(); } catch {}
                    try { child.stdout.removeAllListeners(); } catch {}
                    endLogStreamSafe();
                    const fullStderr = stderrChunks.join('');
                    const crashStackTrace = extractCrashStackTrace(fullStderr);
                    if (!resolved) {
                        resolved = true;
                        clearTimeout(timer);
                        const err = new Error(`Language server exited unexpectedly (code=${code}, signal=${signal})`);
                        err.childConfirmedExited = true;
                        err.stderrText = fullStderr;
                        err.isPortConflict = /EADDRINUSE|address already in use|Only one usage of each socket address/i.test(fullStderr);
                        reject(err);
                    }
                    exitResolve({ code, signal, crashStackTrace });
                });
            });
        });"""

    assert old_spawn_block in code, "languageServer.js spawn block anchor not found"
    code = code.replace(old_spawn_block, new_spawn_block, 1)

    old_mon1 = "monitorLsCrashInternal(handle, port, csrf, options);"
    new_mon1 = "monitorLsCrashInternal(handle, handle.port, csrf, options);"
    assert old_mon1 in code, "languageServer.js monitorLsCrashInternal(handle) anchor not found"
    code = code.replace(old_mon1, new_mon1, 1)

    old_crash = """    void handle.exitPromise.then(async (exitInfo) => {
        clearLsProcess();"""
    new_crash = """    void handle.exitPromise.then(async (exitInfo) => {
        clearLsProcess(handle.process);"""
    assert old_crash in code, "languageServer.js clearLsProcess in monitorLsCrashInternal anchor not found"
    code = code.replace(old_crash, new_crash, 1)

    old_restart = """            const newHandle = await startLanguageServer(port, csrf, options);
            _lsPort = newHandle.port;
            if (options.onPortChanged) {
                options.onPortChanged(_lsPort);
            }
            // Recurse
            monitorLsCrashInternal(newHandle, port, csrf, options);"""

    new_restart = """            let newHandle;
            try {
                newHandle = await startLanguageServer(port, csrf, options);
            } catch (firstErr) {
                const canFallbackPort0 = (
                    !_intentionalTermination &&
                    port !== 0 &&
                    !firstErr.isSpawnError &&
                    !firstErr.fatalUnkillableChild &&
                    firstErr.childConfirmedExited === true &&
                    firstErr.isPortConflict === true &&
                    _lsProcess === null
                );
                if (!canFallbackPort0) {
                    throw firstErr;
                }
                console.warn('[LS Supervisor] Port ' + port + ' bind conflict confirmed and previous child exited; falling back to port 0');
                newHandle = await startLanguageServer(0, csrf, options);
            }
            _lsPort = newHandle.port;
            if (options.onPortChanged) {
                options.onPortChanged(_lsPort);
            }
            monitorLsCrashInternal(newHandle, newHandle.port, csrf, options);"""

    assert old_restart in code, "languageServer.js restart block anchor not found"
    return code.replace(old_restart, new_restart, 1)


def patch_main_js(code: str) -> str:
    old_port_cb = """            onPortChanged: (newPort) => {
                const newUrl = `${constants_1.WINDOW_ORIGIN}:${newPort}/`;
                console.log(`[Auto-Restart] Port changed! Reloading all windows with URL: ${newUrl}`);
                // Apply cert trust
                (0, languageServer_1.setupLocalCertTrust)();
                if (!HEADLESS) {
                    const windows = electron_1.BrowserWindow.getAllWindows();
                    for (const win of windows) {
                        void win.loadURL(newUrl);
                    }
                }
            },"""

    new_port_cb = """            onPortChanged: (newPort) => {
                (0, languageServer_1.setupLocalCertTrust)();
                const lsGen = global.__agyLsGeneration || 1;
                if (!HEADLESS) {
                    for (const w of electron_1.BrowserWindow.getAllWindows()) {
                        if (!w.isDestroyed()) {
                            w.webContents.send('agy:preferences-changed', { lsGeneration: lsGen });
                        }
                    }
                }
                if (global.__agyLastPort === newPort) {
                    console.log(`[Auto-Restart] Reused same port ${newPort} (gen=${lsGen}), skipping window reload`);
                    return;
                }
                global.__agyLastPort = newPort;
                console.log(`[Auto-Restart] Port changed to ${newPort}! Updating registered AGY windows...`);
                if (!HEADLESS) {
                    const windows = electron_1.BrowserWindow.getAllWindows();
                    for (const win of windows) {
                        if (win.isDestroyed()) continue;
                        try {
                            const cur = win.webContents.getURL() || '';
                            const parsed = new URL(cur);
                            if (
                                parsed.protocol === 'https:' &&
                                parsed.hostname === '127.0.0.1' &&
                                parsed.username === '' &&
                                parsed.password === ''
                            ) {
                                parsed.port = String(newPort);
                                void win.loadURL(parsed.toString());
                            }
                        } catch {}
                    }
                }
            },"""

    assert old_port_cb in code, "main.js onPortChanged anchor not found"
    return code.replace(old_port_cb, new_port_cb, 1)


def verify_asar_archive(asar_path: Path) -> int:
    raw = asar_path.read_bytes()
    p0, hdr_sz, inner_sz, json_sz = struct.unpack_from("<IIII", raw, 0)
    assert p0 == 4 and hdr_sz == inner_sz + 4, "Invalid ASAR header pickle framing"
    hdr = json.loads(raw[16 : 16 + json_sz].decode("utf-8"))
    data_start = 8 + hdr_sz
    verified_count = 0

    def verify_node(node: dict, path_str: str) -> None:
        nonlocal verified_count
        if "files" in node:
            for k, v in node["files"].items():
                verify_node(v, f"{path_str}/{k}" if path_str else k)
            return
        if node.get("unpacked"):
            return
        off = int(node["offset"])
        sz = int(node["size"])
        chunk = raw[data_start + off : data_start + off + sz]
        assert len(chunk) == sz, f"Truncated chunk at {path_str}"
        expected_hash = node["integrity"]["hash"]
        actual_hash = sha256_hex(chunk)
        assert actual_hash == expected_hash, f"Hash mismatch at {path_str}: {actual_hash} != {expected_hash}"
        verified_count += 1

    verify_node(hdr, "")
    return verified_count


def ensure_pre_session2_backups() -> dict:
    """Idempotently preserve factory orig_2.25.0 and pre-Session-2 live artifacts inside 02_desktop_enhance/backups/."""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    summary: dict[str, dict] = {}

    orig_copy = BACKUP_DIR / "app.asar.orig_2.25.0.bak"
    if ORIG_ASAR_BAK_PATH.is_file() and not orig_copy.is_file():
        shutil.copy2(ORIG_ASAR_BAK_PATH, orig_copy)

    pre_asar = BACKUP_DIR / "app.asar.pre_session2.bak"
    if LIVE_ASAR_PATH.is_file() and not pre_asar.is_file():
        shutil.copy2(LIVE_ASAR_PATH, pre_asar)

    pre_loader = BACKUP_DIR / "agy-enhance-loader.pre_session2.js"
    if LIVE_LOADER_PATH.is_file() and not pre_loader.is_file():
        shutil.copy2(LIVE_LOADER_PATH, pre_loader)

    for label, p in (
        ("orig_2.25.0_asar", orig_copy),
        ("pre_session2_asar", pre_asar),
        ("pre_session2_loader", pre_loader),
    ):
        sha, sz = file_sha256_and_size(p)
        summary[label] = {"path": str(p), "sha256": sha, "bytes": sz}

    return summary


def cmd_status() -> int:
    print("=== [Antigravity Desktop Enhance · 状态与 SHA256 巡检] ===")
    items = [
        ("原厂纯净基线 (app.asar.orig_2.25.0.bak)", ORIG_ASAR_BAK_PATH),
        ("现行装机包 (Contents/Resources/app.asar)", LIVE_ASAR_PATH),
        ("会话2候选包 (work/app.asar.patched)", CANDIDATE_ASAR_PATH),
        ("会话2三方合并 Loader (02_desktop_enhance/agy-enhance-loader.js)", MERGED_LOADER_PATH),
        ("现行运行态 Loader (~/.gemini/agy-enhance/agy-enhance-loader.js)", LIVE_LOADER_PATH),
        ("套件源目录 Loader (antigravity-enhance-suite/src/agy-enhance-loader.js)", SUITE_LOADER_PATH),
    ]
    for label, path in items:
        sha, sz = file_sha256_and_size(path)
        if sha is None:
            print(f"  - {label}:\n      状态: [未生成/不存在] ({path})")
        else:
            print(f"  - {label}:\n      路径: {path}\n      大小: {sz:,} bytes | SHA256: {sha}")

    if LIVE_ASAR_PATH.is_file():
        c_live = inspect_asar_loader_contract(LIVE_ASAR_PATH)
        live_l_sha, live_l_sz = file_sha256_and_size(LIVE_LOADER_PATH)
        match_live = (
            c_live.get("expected_loader_sha256") == live_l_sha
            and c_live.get("expected_loader_bytes") == live_l_sz
        )
        print(
            f"\n  [现行 app.asar 注入契约]: expected_bytes={c_live.get('expected_loader_bytes')}, "
            f"expected_sha256={c_live.get('expected_loader_sha256')}\n"
            f"  [与现行磁盘 Loader 一致性]: {'✅ 一致' if match_live else '⚠️ 不一致 (e93889f8 修改了磁盘 Loader 但尚未重打包 app.asar)'}"
        )

    if CANDIDATE_ASAR_PATH.is_file():
        c_cand = inspect_asar_loader_contract(CANDIDATE_ASAR_PATH)
        m_sha, m_sz = file_sha256_and_size(MERGED_LOADER_PATH)
        match_cand = (
            c_cand.get("expected_loader_sha256") == m_sha
            and c_cand.get("expected_loader_bytes") == m_sz
        )
        print(
            f"\n  [候选 work/app.asar.patched 注入契约]: expected_bytes={c_cand.get('expected_loader_bytes')}, "
            f"expected_sha256={c_cand.get('expected_loader_sha256')}, "
            f"receipt_fallback={c_cand.get('has_receipt_fallback')}\n"
            f"  [与三方合并 Loader 一致性]: {'✅ 100% 匹配' if match_cand else '❌ 不匹配'}"
        )
    return 0


def cmd_build() -> int:
    print("=== [Step 1/4] 备份原厂基线与会话2施工前快照 (A-41) ===")
    if not ORIG_ASAR_BAK_PATH.is_file():
        raise FileNotFoundError(f"Missing clean factory baseline: {ORIG_ASAR_BAK_PATH}")
    if not MERGED_LOADER_PATH.is_file():
        raise FileNotFoundError(f"Missing merged loader: {MERGED_LOADER_PATH}")

    backups = ensure_pre_session2_backups()
    for k, v in backups.items():
        print(f"  [Backup] {k}: {v['bytes']:,} bytes | sha256={v['sha256']}")

    print("\n=== [Step 2/4] 校验三方合并 Loader 语法 (node --check) ===")
    search_path = os.environ.get("PATH", "") + os.pathsep + "/opt/homebrew/bin:/usr/local/bin"
    node_bin = shutil.which("node", path=search_path) or "node"
    chk_loader = subprocess.run(
        [node_bin, "--check", str(MERGED_LOADER_PATH)], capture_output=True, text=True
    )
    if chk_loader.returncode != 0:
        raise RuntimeError(f"Syntax error in {MERGED_LOADER_PATH}:\n{chk_loader.stderr}")

    loader_raw = MERGED_LOADER_PATH.read_bytes()
    loader_sha = sha256_hex(loader_raw)
    loader_len = len(loader_raw)
    print(f"  [OK] {MERGED_LOADER_PATH.name}: {loader_len:,} bytes | sha256={loader_sha}")

    print("\n=== [Step 3/4] 从原厂基线解包并补丁 7 个 Electron 主进程/预加载模块 ===")
    raw_orig = ORIG_ASAR_BAK_PATH.read_bytes()
    orig_sha = sha256_hex(raw_orig)
    _, hdr_sz, _, json_sz = struct.unpack_from("<IIII", raw_orig, 0)
    orig_header = json.loads(raw_orig[16 : 16 + json_sz].decode("utf-8"))
    orig_data_start = 8 + hdr_sz

    pkg_info = orig_header["files"]["package.json"]
    pkg_bytes = raw_orig[
        orig_data_start + int(pkg_info["offset"]) : orig_data_start
        + int(pkg_info["offset"])
        + int(pkg_info["size"])
    ]
    pkg_json = json.loads(pkg_bytes.decode("utf-8"))
    app_version = pkg_json.get("version", "unknown")

    patchers = {
        "storage.js": patch_storage_js,
        "ipcHandlers.js": patch_ipc_handlers_js,
        "preload.js": patch_preload_js,
        "utils.js": lambda code: patch_utils_js(code, loader_sha, loader_len),
        "menu.js": patch_menu_js,
        "debugConsole.js": patch_debug_console_js,
        "languageServer.js": patch_language_server_js,
        "main.js": patch_main_js,
    }

    STAGING_JS_DIR.mkdir(parents=True, exist_ok=True)
    patched_dist_bytes: dict[str, bytes] = {}
    dist_entries = orig_header["files"]["dist"]["files"]
    for fname, patch_fn in patchers.items():
        if fname not in dist_entries:
            continue
        finfo = dist_entries[fname]
        off, sz = int(finfo["offset"]), int(finfo["size"])
        orig_code = raw_orig[orig_data_start + off : orig_data_start + off + sz].decode("utf-8")
        new_code = patch_fn(orig_code)
        staged_file = STAGING_JS_DIR / fname
        staged_file.write_bytes(new_code.replace("\r\n", "\n").encode("utf-8"))

        chk = subprocess.run([node_bin, "--check", str(staged_file)], capture_output=True, text=True)
        if chk.returncode != 0:
            raise RuntimeError(f"Syntax error in patched dist/{fname}:\n{chk.stderr}")

        new_bytes = staged_file.read_bytes()
        patched_dist_bytes[fname] = new_bytes
        extracted_dist_dir = WORK_DIR / "asar_extracted" / "dist"
        if extracted_dist_dir.is_dir():
            (extracted_dist_dir / fname).write_bytes(new_bytes)
        print(
            f"  [OK] dist/{fname:<18} | {sz:6d} -> {len(new_bytes):6d} bytes | node --check PASS | sha256={sha256_hex(new_bytes)[:16]}..."
        )

    print("\n=== [Step 4/4] 封装 work/app.asar.patched 并执行全量 SHA256 块完整性校验 ===")
    new_header = copy.deepcopy(orig_header)
    new_payload_chunks: list[bytes] = []
    current_offset = 0
    packed_count = 0
    unpacked_count = 0

    def walk_and_repack(node: dict, rel_parts: list[str]) -> None:
        nonlocal current_offset, packed_count, unpacked_count
        if "files" in node:
            for child_name, child_node in node["files"].items():
                walk_and_repack(child_node, rel_parts + [child_name])
            return
        if node.get("unpacked"):
            unpacked_count += 1
            return
        packed_count += 1
        if len(rel_parts) == 2 and rel_parts[0] == "dist" and rel_parts[1] in patched_dist_bytes:
            file_bytes = patched_dist_bytes[rel_parts[1]]
        else:
            old_off = int(node["offset"])
            old_sz = int(node["size"])
            file_bytes = raw_orig[orig_data_start + old_off : orig_data_start + old_off + old_sz]

        node["offset"] = str(current_offset)
        node["size"] = len(file_bytes)
        node["integrity"] = compute_asar_integrity(file_bytes)
        new_payload_chunks.append(file_bytes)
        current_offset += len(file_bytes)

    walk_and_repack(new_header, [])

    header_json_bytes = json.dumps(new_header, separators=(",", ":")).encode("utf-8")
    header_sha256 = sha256_hex(header_json_bytes)
    json_len = len(header_json_bytes)
    align_pad = (4 - (json_len % 4)) % 4
    inner_size = 4 + json_len + align_pad
    outer_size = 4 + inner_size

    asar_prefix = (
        struct.pack("<IIII", 4, outer_size, inner_size, json_len)
        + header_json_bytes
        + (b"\x00" * align_pad)
    )
    full_candidate = asar_prefix + b"".join(new_payload_chunks)

    CANDIDATE_ASAR_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_asar = CANDIDATE_ASAR_PATH.parent / f"app.asar.tmp.{os.getpid()}"
    tmp_asar.write_bytes(full_candidate)
    verified_files = verify_asar_archive(tmp_asar)
    os.replace(tmp_asar, CANDIDATE_ASAR_PATH)
    cand_sha = sha256_hex(full_candidate)

    receipt = {
        "built_at": datetime.now(timezone.utc).isoformat(),
        "app_version": app_version,
        "orig_asar_path": str(ORIG_ASAR_BAK_PATH),
        "orig_asar_sha256": orig_sha,
        "orig_asar_bytes": len(raw_orig),
        "candidate_asar_path": str(CANDIDATE_ASAR_PATH),
        "candidate_asar_sha256": cand_sha,
        "candidate_asar_bytes": len(full_candidate),
        "header_sha256": header_sha256,
        "utils_js_sha256": sha256_hex(patched_dist_bytes["utils.js"]),
        "loader_path": str(MERGED_LOADER_PATH),
        "loader_sha256": loader_sha,
        "loader_bytes": loader_len,
        "patched_modules": list(patched_dist_bytes.keys()),
        "packed_files": packed_count,
        "unpacked_files": unpacked_count,
        "verified_packed_files": verified_files,
        "backups": backups,
    }
    BUILD_RECEIPT_PATH.write_text(json.dumps(receipt, indent=2, ensure_ascii=False), encoding="utf-8")
    print(
        f"  [OK] 候选包已生成: {CANDIDATE_ASAR_PATH}\n"
        f"       大小: {len(full_candidate):,} bytes | SHA256: {cand_sha}\n"
        f"       校验通过文件数: {verified_files} packed ({unpacked_count} unpacked)\n"
        f"       构建回执: {BUILD_RECEIPT_PATH}"
    )
    return 0


def atomic_copy_verified(src: Path, dst: Path) -> tuple[str, int]:
    src_bytes = src.read_bytes()
    expected_sha = sha256_hex(src_bytes)
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp_dst = dst.parent / f".{dst.name}.tmp.{os.getpid()}"
    with open(tmp_dst, "wb") as f:
        f.write(src_bytes)
        f.flush()
        os.fsync(f.fileno())
    shutil.copymode(src, tmp_dst)
    tmp_sha, tmp_sz = file_sha256_and_size(tmp_dst)
    if tmp_sha != expected_sha or tmp_sz != len(src_bytes):
        try:
            tmp_dst.unlink(missing_ok=True)
        except OSError:
            pass
        raise RuntimeError(f"Temp file pre-replace verification failed for {tmp_dst}")
    os.replace(tmp_dst, dst)
    if hasattr(os, "O_RDONLY"):
        try:
            dir_fd = os.open(str(dst.parent), os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError:
            pass
    actual_sha, actual_sz = file_sha256_and_size(dst)
    if actual_sha != expected_sha or actual_sz != len(src_bytes):
        raise RuntimeError(f"Atomic copy verification failed for {dst}")
    return expected_sha, len(src_bytes)


def _find_antigravity_pids() -> list[str]:
    if sys.platform != "darwin":
        return []
    res = subprocess.run(["ps", "-ax", "-o", "pid=,args="], capture_output=True, text=True)
    pids: list[str] = []
    for line in res.stdout.splitlines():
        s = line.strip()
        if not s:
            continue
        parts = s.split(None, 1)
        if len(parts) == 2 and "--type=" not in parts[1]:
            exe_tok = parts[1].split()[0] if parts[1].split() else ""
            if "Antigravity.app/Contents/MacOS/" in exe_tok:
                pids.append(parts[0])
    return pids


def check_antigravity_running(allow_running: bool) -> None:
    if sys.platform != "darwin":
        return
    pids = _find_antigravity_pids()
    if pids and not allow_running:
        raise RuntimeError(
            f"Antigravity.app is currently running (PID={','.join(pids)}). "
            "Replacing app.asar while Electron holds open file descriptors or performing deep codesign "
            "requires either quitting Antigravity first or passing --allow-running during coordinated Session 1 handoff."
        )


def run_verified_cdp_hot_inject(loader_path: Path, expected_version: str = "3.6.0-macos-merged") -> list:
    if sys.platform == "darwin":
        pids = _find_antigravity_pids()
        if not pids:
            print("  [热注入] Antigravity 当前未运行，磁盘装机已完成，新版本将在下次启动时自动生效。")
            return []
    hot_script = RUNTIME_DIR / "antigravity-enhance-suite" / "src" / "hot_inject.py"
    if not hot_script.is_file():
        raise FileNotFoundError(f"Hot-inject module not found: {hot_script}")
    spec = importlib.util.spec_from_file_location("agy_hot_inject", str(hot_script))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Failed to load spec from {hot_script}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    if not hasattr(mod, "hot_inject_running_windows"):
        raise RuntimeError(f"hot_inject_running_windows() missing in {hot_script}")
    results = mod.hot_inject_running_windows(loader_path)
    if not results:
        raise RuntimeError(
            "CDP hot-inject found 0 active Antigravity page targets (check DevToolsActivePort or running window)."
        )
    for item in results:
        rcpt = item.get("receipt") or {}
        if rcpt.get("status") != "ready" or rcpt.get("version") != expected_version:
            raise RuntimeError(
                f"CDP hot-inject receipt verification failed on {item.get('url')}: {rcpt}"
            )
    return results


def cmd_install(
    skip_codesign: bool = False, hot_inject: bool = False, allow_running: bool = False
) -> int:
    print("=== [Install] 同步桌面增强 Loader 与候选 app.asar.patched ===")
    check_antigravity_running(allow_running=allow_running)
    if not CANDIDATE_ASAR_PATH.is_file():
        print("  候选包不存在，先自动执行 build...")
        cmd_build()

    ensure_pre_session2_backups()
    verified_files = verify_asar_archive(CANDIDATE_ASAR_PATH)
    print(f"  [校验] 候选包 ASAR 完整性验证通过 ({verified_files} files)")

    utils_bytes = extract_file_from_asar(CANDIDATE_ASAR_PATH, ["dist", "utils.js"])
    if not utils_bytes:
        raise RuntimeError("dist/utils.js missing inside candidate ASAR")
    utils_js_sha = sha256_hex(utils_bytes)

    loader_sha, loader_sz = atomic_copy_verified(MERGED_LOADER_PATH, LIVE_LOADER_PATH)
    print(f"  [已同步] {LIVE_LOADER_PATH} ({loader_sz:,} bytes, sha256={loader_sha[:16]}...)")

    if SUITE_LOADER_PATH.parent.is_dir():
        atomic_copy_verified(MERGED_LOADER_PATH, SUITE_LOADER_PATH)
        print(f"  [已同步] {SUITE_LOADER_PATH}")

    asar_sha, asar_sz = atomic_copy_verified(CANDIDATE_ASAR_PATH, LIVE_ASAR_PATH)
    print(f"  [已替换] {LIVE_ASAR_PATH} ({asar_sz:,} bytes, sha256={asar_sha[:16]}...)")

    # Update last_install_receipt.json while preserving existing LS fields if present
    receipt_data: dict = {}
    if INSTALL_RECEIPT_PATH.is_file():
        try:
            receipt_data = json.loads(INSTALL_RECEIPT_PATH.read_text(encoding="utf-8"))
        except Exception:
            receipt_data = {}

    orig_sha, _ = file_sha256_and_size(ORIG_ASAR_BAK_PATH)
    receipt_data.update(
        {
            "app_path": str(APP_BUNDLE_PATH),
            "runtime_dir": str(RUNTIME_DIR),
            "orig_sha256": orig_sha,
            "candidate_sha256": asar_sha,
            "utils_js_sha256": utils_js_sha,
            "loader_sha256": loader_sha,
            "loader_bytes": loader_sz,
            "desktop_enhance_updated_at": datetime.now(timezone.utc).isoformat(),
            "desktop_enhance_version": "3.6.0-macos-merged",
        }
    )
    INSTALL_RECEIPT_PATH.write_text(
        json.dumps(receipt_data, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"  [已更新] 安装回执 {INSTALL_RECEIPT_PATH}")

    if not skip_codesign and sys.platform == "darwin":
        print("  [签名] 正在对 Antigravity.app 执行 ad-hoc 重签名与严格校验...")
        subprocess.run(
            ["codesign", "--force", "--deep", "--sign", "-", str(APP_BUNDLE_PATH)],
            check=True,
        )
        subprocess.run(
            ["codesign", "--verify", "--deep", "--strict", str(APP_BUNDLE_PATH)],
            check=True,
        )
        print("  [签名] codesign --verify --deep --strict PASS")

    if hot_inject:
        print("  [热注入] 正在通过 CDP 向已打开窗口热注入最新 Loader 并校验返回版本...")
        inj_results = run_verified_cdp_hot_inject(LIVE_LOADER_PATH, "3.6.0-macos-merged")
        print(f"  [热注入] 成功注入并校验 {len(inj_results)} 个运行窗口: {inj_results}")

    return 0


def cmd_rollback(
    target: str = "pre-session2", skip_codesign: bool = False, allow_running: bool = False
) -> int:
    print(f"=== [Rollback] 一键回滚桌面增强组件 (target={target}) ===")
    check_antigravity_running(allow_running=allow_running)
    if target == "orig":
        asar_src = ORIG_ASAR_BAK_PATH
        loader_src = BACKUP_DIR / "agy-enhance-loader.pre_session2.js"
    elif target == "pre-session2":
        asar_src = BACKUP_DIR / "app.asar.pre_session2.bak"
        loader_src = BACKUP_DIR / "agy-enhance-loader.pre_session2.js"
    else:
        raise ValueError(f"Unsupported rollback target: {target}")

    if not asar_src.is_file():
        raise FileNotFoundError(f"Rollback source ASAR not found: {asar_src}")

    verify_asar_archive(asar_src)
    asar_sha, asar_sz = atomic_copy_verified(asar_src, LIVE_ASAR_PATH)
    print(f"  [已恢复] {LIVE_ASAR_PATH} <- {asar_src.name} ({asar_sz:,} bytes, sha256={asar_sha})")

    restored_loader_sha = None
    restored_loader_sz = None
    if loader_src and loader_src.is_file():
        restored_loader_sha, restored_loader_sz = atomic_copy_verified(loader_src, LIVE_LOADER_PATH)
        print(
            f"  [已恢复] {LIVE_LOADER_PATH} <- {loader_src.name} ({restored_loader_sz:,} bytes, sha256={restored_loader_sha})"
        )
        if SUITE_LOADER_PATH.parent.is_dir():
            atomic_copy_verified(loader_src, SUITE_LOADER_PATH)
            print(f"  [已恢复] {SUITE_LOADER_PATH}")

    # Synchronize last_install_receipt.json so receipt fallback matches the rolled-back state
    if INSTALL_RECEIPT_PATH.is_file():
        try:
            receipt_data = json.loads(INSTALL_RECEIPT_PATH.read_text(encoding="utf-8"))
        except Exception:
            receipt_data = {}
        orig_sha, _ = file_sha256_and_size(ORIG_ASAR_BAK_PATH)
        if target == "pre-session2":
            receipt_data.pop("utils_js_sha256", None)
            receipt_data.update(
                {
                    "orig_sha256": orig_sha,
                    "candidate_sha256": asar_sha,
                    "loader_sha256": restored_loader_sha,
                    "loader_bytes": restored_loader_sz,
                    "desktop_enhance_version": "3.1.1",
                    "rolled_back_at": datetime.now(timezone.utc).isoformat(),
                    "rolled_back_target": target,
                }
            )
        else:
            for k in (
                "candidate_sha256",
                "utils_js_sha256",
                "loader_sha256",
                "loader_bytes",
                "desktop_enhance_version",
                "desktop_enhance_updated_at",
            ):
                receipt_data.pop(k, None)
            receipt_data.update(
                {
                    "orig_sha256": orig_sha,
                    "rolled_back_at": datetime.now(timezone.utc).isoformat(),
                    "rolled_back_target": "orig",
                }
            )
        INSTALL_RECEIPT_PATH.write_text(
            json.dumps(receipt_data, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(f"  [已同步回执] {INSTALL_RECEIPT_PATH} (target={target})")

    if not skip_codesign and sys.platform == "darwin":
        print("  [签名] 正在对 Antigravity.app 执行 ad-hoc 重签名与严格校验...")
        subprocess.run(
            ["codesign", "--force", "--deep", "--sign", "-", str(APP_BUNDLE_PATH)],
            check=True,
        )
        subprocess.run(
            ["codesign", "--verify", "--deep", "--strict", str(APP_BUNDLE_PATH)],
            check=True,
        )
        print("  [签名] codesign --verify --deep --strict PASS")

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Antigravity macOS app.asar 桌面增强构建、安装与一键回滚工具"
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="只读巡检各基线、候选包与 Loader 的 SHA256 及契约状态")
    sub.add_parser("build", help="在 work/ 目录构建并验证候选 app.asar.patched（不改动正式 App Bundle）")

    p_inst = sub.add_parser("install", help="安装合并后的 Loader 与候选 app.asar.patched")
    p_inst.add_argument("--skip-codesign", action="store_true", help="跳过 codesign（由主持统一签名时使用）")
    p_inst.add_argument("--hot-inject", action="store_true", help="安装后通过 CDP 热注入当前运行窗口")
    p_inst.add_argument("--allow-running", action="store_true", help="允许在 Antigravity 运行中执行替换（协同模式）")

    p_rb = sub.add_parser("rollback", help="一键原子回滚至指定基线")
    p_rb.add_argument(
        "--target",
        choices=["pre-session2", "orig"],
        default="pre-session2",
        help="回滚目标：pre-session2（会话2施工前状态，含 e93889f8 改动）或 orig（v2.25.0 原厂纯净基线）",
    )
    p_rb.add_argument("--skip-codesign", action="store_true", help="跳过 codesign")
    p_rb.add_argument("--allow-running", action="store_true", help="允许在 Antigravity 运行中执行回滚")

    args = parser.parse_args()
    if args.cmd == "status":
        return cmd_status()
    if args.cmd == "build":
        return cmd_build()
    if args.cmd == "install":
        return cmd_install(
            skip_codesign=args.skip_codesign,
            hot_inject=args.hot_inject,
            allow_running=args.allow_running,
        )
    if args.cmd == "rollback":
        return cmd_rollback(
            target=args.target,
            skip_codesign=args.skip_codesign,
            allow_running=args.allow_running,
        )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
