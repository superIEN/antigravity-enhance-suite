"""Portable Electron Host (`app.asar`) Patcher & Integrity Verifier for Antigravity IDE."""
import copy
import hashlib
import json
import os
import shutil
import struct
import subprocess
from pathlib import Path

DEFAULT_RUNTIME_DIR = Path.home() / ".gemini" / "agy-enhance"

BLOCK_SIZE = 4 * 1024 * 1024


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


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
                    currentItems = JSON.parse(raw);
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

    new_overlay = """    function isTrustedAgySender(_event) {
        if (!_event || !_event.sender || _event.sender.isDestroyed()) return null;
        if (_event.senderFrame !== _event.sender.mainFrame) return null;
        const win = electron_1.BrowserWindow.fromWebContents(_event.sender);
        if (!win || win.isDestroyed()) return null;
        try {
            const parsed = new URL(_event.sender.getURL() || '');
            if (
                parsed.protocol !== 'https:' ||
                parsed.hostname !== '127.0.0.1' ||
                parsed.username !== '' ||
                parsed.password !== '' ||
                !/^\d+$/.test(parsed.port)
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
    const os_agy = require('os');
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
        },
        windowDefaults: {},
    };
    let agyPrefsState = JSON.parse(JSON.stringify(DEFAULT_AGY_PREFS));
    try {
        const fsSync = require('fs');
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
                    },
                    windowDefaults: {},
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
        return clean;
    }

    function validateWindowPatch(_patch) {
        return {};
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

            // Construct candidate snapshot in memory WITHOUT mutating authoritative agyPrefsState yet!
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

            // Persist to disk FIRST; only commit memory & broadcast if disk write succeeds!
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
            if (!(0, fs_1.existsSync)(outFile)) {
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
const AGY_LOADER_PATH = process.env.AGY_LOADER_PATH || path.join(os_agy.homedir(), ".gemini", "agy-enhance", "agy-enhance-loader.js");
const EXPECTED_LOADER_SHA256 = "{loader_sha256}";
const EXPECTED_LOADER_BYTES = {loader_byte_len};
const EXPECTED_LOADER_VERSION = "3.1.1";
const languageServer_agy = require("./languageServer");

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
            if (rawBytes.length !== EXPECTED_LOADER_BYTES) {{
                throw new Error(`Loader size mismatch: ${{rawBytes.length}} != ${{EXPECTED_LOADER_BYTES}}`);
            }}
            const actualSha = crypto_agy.createHash('sha256').update(rawBytes).digest('hex');
            if (actualSha !== EXPECTED_LOADER_SHA256) {{
                throw new Error(`Loader SHA256 mismatch: ${{actualSha}} != ${{EXPECTED_LOADER_SHA256}}`);
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
            console.error('[AGY-v3.1.1] Enhancement load skipped or failed:', err);
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
    """Fix Round 4 Section III.1:
    - Local `child` & `attemptId` tracking; clear `_lsProcess` only if `_lsProcess === child`.
    - Startup timeout terminates `child` (SIGTERM -> 3s wait -> SIGKILL -> 1s wait) before rejecting.
    - Separate process `exit` from bounded 1500ms `stderr` pipe drain.
    - Fallback to port 0 ONLY on confirmed port-bind failure when `childConfirmedExited === true`.
    """
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
                if (child.pid !== undefined) process.kill(child.pid, 'SIGKILL');
            } catch {}
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
                clearTimeout(timer);
                spawnErr.isSpawnError = child.pid === undefined;
                spawnErr.childConfirmedExited = exited;
                reject(spawnErr);
            }
        });
        const timer = setTimeout(() => {
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
                // Bounded 1500ms stderr drain so inherited stdio pipes never hang exitPromise
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
    """Fix Round 4 Section III.8: Only reload windows matching WINDOW_ORIGIN; skip non-LS windows."""
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


def build_candidate_asar(orig_asar_path: Path, loader_path: Path, candidate_asar_path: Path, staging_js_dir: Path) -> dict:
    raw_orig = orig_asar_path.read_bytes()
    orig_sha = sha256_hex(raw_orig)

    _, hdr_sz, _, json_sz = struct.unpack_from("<IIII", raw_orig, 0)
    orig_header = json.loads(raw_orig[16 : 16 + json_sz].decode("utf-8"))
    orig_data_start = 8 + hdr_sz

    pkg_info = orig_header["files"]["package.json"]
    pkg_bytes = raw_orig[orig_data_start + int(pkg_info["offset"]) : orig_data_start + int(pkg_info["offset"]) + int(pkg_info["size"])]
    pkg_json = json.loads(pkg_bytes.decode("utf-8"))
    app_version = pkg_json.get("version", "unknown")

    node_bin = shutil.which("node") or "node"
    chk_loader = subprocess.run([node_bin, "--check", str(loader_path)], capture_output=True, text=True)
    if chk_loader.returncode != 0:
        raise RuntimeError(f"Syntax error in {loader_path.name}:\n{chk_loader.stderr}")

    loader_raw = loader_path.read_bytes()
    loader_sha = sha256_hex(loader_raw)
    loader_len = len(loader_raw)

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

    staging_js_dir.mkdir(parents=True, exist_ok=True)
    patched_dist_bytes: dict[str, bytes] = {}
    dist_entries = orig_header["files"]["dist"]["files"]
    for fname, patch_fn in patchers.items():
        if fname not in dist_entries:
            continue
        finfo = dist_entries[fname]
        off, sz = int(finfo["offset"]), int(finfo["size"])
        orig_code = raw_orig[orig_data_start + off : orig_data_start + off + sz].decode("utf-8")
        new_code = patch_fn(orig_code)
        staged_file = staging_js_dir / fname
        staged_file.write_text(new_code, encoding="utf-8", newline="\n")

        chk = subprocess.run([node_bin, "--check", str(staged_file)], capture_output=True, text=True)
        if chk.returncode != 0:
            raise RuntimeError(f"Syntax error in patched dist/{fname}:\n{chk.stderr}")

        new_bytes = staged_file.read_bytes()
        patched_dist_bytes[fname] = new_bytes

    new_header = copy.deepcopy(orig_header)
    new_payload_chunks: list[bytes] = []
    current_offset = 0
    packed_count = 0
    unpacked_count = 0

    def walk_and_repack(node: dict, rel_parts: list[str]):
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
    json_len = len(header_json_bytes)
    align_pad = (4 - (json_len % 4)) % 4
    inner_size = 4 + json_len + align_pad
    outer_size = 4 + inner_size

    asar_prefix = struct.pack("<IIII", 4, outer_size, inner_size, json_len) + header_json_bytes + (b"\x00" * align_pad)
    full_candidate = asar_prefix + b"".join(new_payload_chunks)

    candidate_asar_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_asar = candidate_asar_path.parent / f"app.asar.tmp.{os.getpid()}"
    tmp_asar.write_bytes(full_candidate)
    verify_asar_archive(tmp_asar)
    os.replace(tmp_asar, candidate_asar_path)
    cand_sha = sha256_hex(full_candidate)
    return {
        "app_version": app_version,
        "orig_sha256": orig_sha,
        "candidate_sha256": cand_sha,
        "loader_sha256": loader_sha,
        "loader_bytes": loader_len,
        "patched_modules": list(patched_dist_bytes.keys()),
        "packed_files": packed_count,
        "unpacked_files": unpacked_count,
    }


def verify_asar_archive(asar_path: Path):
    raw = asar_path.read_bytes()
    p0, hdr_sz, inner_sz, json_sz = struct.unpack_from("<IIII", raw, 0)
    assert p0 == 4 and hdr_sz == inner_sz + 4
    hdr = json.loads(raw[16 : 16 + json_sz].decode("utf-8"))
    data_start = 8 + hdr_sz

    def verify_node(node: dict, path_str: str):
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
        assert actual_hash == expected_hash, f"Hash mismatch at {path_str}"

    verify_node(hdr, "")
