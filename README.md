# Antigravity Enhance Suite `v3.6.0`

一套面向 Google **Antigravity Desktop IDE**（支持 **macOS Apple Silicon `arm64`** 与 **Windows `x86_64`**，`v2.24.0` / `v2.25.0` / `v2.26.0` / `v2.27.0` / `v2.28.x` / `v2.29.0+`）的全栈稳定性增强、桌面端 1M（`983,040` Tokens）上下文窗口真实解锁、官方 `v2.29.0` 原生上下文与配额指示器汉化联动、Windows 独占锁后台 WMI 自动换包守护、后台任务/子智能体响应式唤醒死锁双层根治、会话媒体 `18 MB` 自适应预算守护、防退化 Harness 三大工程护栏与深度专业中文汉化套件。

> 版本演进与更新详情请参阅 [CHANGELOG.md](./CHANGELOG.md) 或仓库 [Releases](https://github.com/superIEN/antigravity-enhance-suite/releases) 页面。

---

## 分支架构与可选功能拉取索引（Branch & Edition Index）

本仓库采用**「核心稳定主线（`main`） + 独立可选增强分支（`optional/*`）」**双轨维护架构，确保核心底座保持零额外推理请求负担与极简交互体验，同时允许偏好扩展视图组件的使用者按需附加拉取：

| 分支名称 | 定位与适用场景 | 包含核心能力与差异说明 | 对应审核报告 / PR |
| :--- | :--- | :--- | :--- |
| **[`main`](https://github.com/superIEN/antigravity-enhance-suite/tree/main)**<br>*(默认推荐)* | **核心稳定主线**<br>追求零额外 RPC 请求负担、零界面布局抖动、极简一行思考摘要的日常主力环境 | Go 服务端 Sites 1–22（`983,040` 上下文真实解锁 + HTTP/2 防断流 + 响应式唤醒死锁双层根治）、Electron 宿主 8 项加固、深度专业汉化、集合差分看门狗、`18 MB` 媒体自适应守护、Harness 工程护栏 | 主线基线（**严禁直接合入高频实验性 UI 组件**） |
| **[`optional/hud-thinking-stream`](https://github.com/superIEN/antigravity-enhance-suite/tree/optional/hud-thinking-stream)** | **可选增强演进主分支**<br>供贡献者针对「思考流同传与快慢双层 Mission HUD」提交后续修订 PR 并经维护者审阅合入 | 作为实验性 UI 增强组件的独立演进靶标分支（PR 基底分支） | [PR #4](https://github.com/superIEN/antigravity-enhance-suite/pull/4) · [《PR #4 综合技术审核报告》](./docs/reviews/PR4_v3.5.0_Review_Report.md) |
| **[`optional/pr4-v3.5.0-hud-thinking`](https://github.com/superIEN/antigravity-enhance-suite/tree/optional/pr4-v3.5.0-hud-thinking)** | **PR #4 (`v3.5.0`) 完整代码快照分支**<br>供希望立即体验「思考过程流式实时中译 + 自动展开/折叠 + 快慢双层防走偏看板」的使用者直接拉取或附加合并 | 在 `main` (`v3.4.0`) 基础上完整附加 `@sorry-makerT-T` 提交的 `v3.5.0` 思考流实时中文同传、快慢双层 Mission HUD 与设置面板双开关（ Commit `fd84bc1`） | [《PR #4 (`v3.5.0`) 综合技术审核报告》](./docs/reviews/PR4_v3.5.0_Review_Report.md) |

### 拉取与选装方式

#### 方式 A：仅拉取 `main` 核心稳定主线（默认推荐，零额外负担）
```bash
git clone -b main https://github.com/superIEN/antigravity-enhance-suite.git
cd antigravity-enhance-suite
python3 agy_suite.py install --hot-inject
```

#### 方式 B：在 `main` 基础上附加拉取「思考流实时中译与快慢双层 HUD」可选分支
```bash
# 1) 直接克隆可选快照分支：
git clone -b optional/pr4-v3.5.0-hud-thinking https://github.com/superIEN/antigravity-enhance-suite.git

# 2) 或者在已有 main 分支工作区上按需附加 Cherry-Pick / Merge 这一段功能：
git fetch origin optional/pr4-v3.5.0-hud-thinking
git cherry-pick fd84bc17fb6ea0085d5981955d691f124c0886f5
python3 agy_suite.py install --hot-inject
```

> **贡献者提示**：针对「思考流实时中译」与「快慢双层 Mission HUD」的后续优化与缺陷修复（详见 [《PR #4 综合技术审核报告》](./docs/reviews/PR4_v3.5.0_Review_Report.md) 第四节整改清单），请统一将 Pull Request 的 Base 分支指向 **`optional/hud-thinking-stream`**，勿直接向 `main` 提 PR。

---

## 核心特性概览

1. **Go 语言服务端（macOS `language_server` / Windows `language_server*.exe`）22 项（29 处位点）二进制级增强**
   - **桌面端 1M（`983,040` Tokens）上下文真实解锁与 200k 模型零回退保护（`v3.4.0` 新增 Site 22）**：
     - 针对桌面端 `applyFeatureProviderCheckpointOverrideConfig` 强行注入 `MaxTokenLimit = 256,000` 导致每 ~250k 自动触发有损压缩（`<CONTEXT_SUMMARY>`）的根因，在 `utils.ClampCheckpointConfigForModelInfo`（`Site 22`）植入 9 指令 `csel` 补丁：对 1M 模型精确解锁至 **`983,040` Tokens**（`1,048,576 - 65,536`），对 `200,000` 模型（Claude 系列）在 `w3 == 0` 与 `w3 == 256,000` 下均保持 **`200,000` Tokens** 零回退。
     - 配套提供 `src/upgrade_db_context_limit.py`，基于结构化 Protobuf Wire 语法树解析、在线备份与 `BEGIN IMMEDIATE` 事务，将存量 `.db` 会话的 `executor_metadata`（`$.10.2.5`）与 `gen_metadata`（`$.1.9.10.4`）从 `256,000` 等长无损升级至 `983,040`。
   - **长连接防断流与 HTTP/2 KeepAlive 优化（Sites 1–3）**：显式开启自定义 `TLSClientConfig` 下的 HTTP/2 协议协商，配置 `SendPing=20s` / `PingTimeout=80s`（stdlib）与 `ReadIdle=25s` / `PingTimeout=50s`（`go_net/http2`），消除 120s 静默断流（`unexpected EOF`）。
   - **后台任务/子智能体完成静默唤醒失败双层根治（Sites 15–16, 19–21）**：
     - **Sites 15–16**：非守护后台命令默认 60s `NotificationTimeoutSeconds` 主动唤醒（兼容 `v2.28.0` `waitInBackground`）；
     - **Site 19 (`(*AgentExecutor).transitionFromRunningToIdleOrClosed`)**：回落至 `Idle` 时强制重查 `messageTracker` 收件箱；
     - **Site 20 (`(*AgentExecutor).startMessageWatcherLocked.func2`)**：解除 `GetCascadeAutoRun()` 外层门控，确保 `NotifyCh()` 唤醒必扫描；
     - **Site 21 (`(*AgentExecutor).startMessageWatcherLocked.func2.1-range1`)**：穿透 `SubagentsAndTasksIdle` 与 `*prevIdle` 边沿锁，消除并发后台任务完成时的唤醒信号丢失。
   - **多模态媒体压缩、流式 UTF-8 容错、15 槽位自动重试与 OAuth2 20m 预刷新（Sites 7–14, 17–18）**。

2. **Electron 宿主（`app.asar`）8 项受控生命周期加固（兼容 macOS & Windows `v2.24.0–v2.29.0+`）**
   - **`v2.29.0` `BaseWindow` + `WebContentsView` (`hubWcv`) 宿主适配**：全面兼容 `getHubWindows()`、`getHubWebContents(win)` 与 `resolveHubWindowFromWebContents(wc)`，并在Windows 在线安装遭遇 `app.asar` 独占锁（`[WinError 32]`）时自动拉起脱离 Job Object 的 **WMI 后台延迟原子换包守护（`deferred_asar_swapper.py`）**。
   - **`storage.js`**：串行写入队列 + `.tmp.<pid>` 原子重命名 + 损坏 JSON 容错恢复 + 默认注入 `contextUsageIndicatorEnabled: "true"`（尊重用户手动开关选择）。
   - **`languageServer.js`**：子进程局部句柄追踪、`timer` TDZ 修复、`child.kill('SIGKILL')` 有界终止、`stderr` 1500ms 有界排空与端口冲突自动回退动态端口 `0`。
   - **`ipcHandlers.js` & `preload.js`**：主框架来源与实时 `getLsPort()` 端口绑定校验（`isTrustedAgySender`）、新端口首屏同步预注入 `REQUIRED_FLAGS` 与 `window.nativeStorage` 桥接同步。
   - **`utils.js` & `main.js`**：基于 `SHA-256`、字节长度与版本回执绑定的受控增强加载器（`attachControlledEnhancement`，已解除 `selfUtilsSha` 自指死锁）。

3. **深度专业中文汉化、集合差分看门狗与媒体预算守护（`src/agy-enhance-loader.js` & `src/compress_tempmedia.py`）**
   - **按 ID 集合差分（Set-Diff）+ 双源 1:1 归一的响应式唤醒看门狗**：精确捕获同数量任务替换（`{bg-a, bg-b} -> {bg-b, bg-c}`），折叠 `backgroundTasksUpdate`/`pendingAgentMessagesUpdate` 与 `globalThis.__asu` 稀疏补丁，将 Provider 行与运行中 `invokeSubagent` 步骤 1:1 归一为 `step:N`，并在 Mission HUD 顶栏右侧提供 `#m-wd`（`后台 N` / `等待接管` / `已接管`）实时状态胶囊。
   - **`WeakMap` React Fiber 缓存（含空值命中）与 250ms HUD 合并节流**。
   - **`983,040` 上下文计量环（`#agy-ctx-ring-pill`）、思考流单句定性摘要（含 `nextToolDesc` 推导）与 `<subagent_message>` 裸标签净化**。
   - **会话媒体库 `18 MB` Base64 二阶段自适应预算压缩器（`src/compress_tempmedia.py`）**：基于 `_bak_orig` 无损备份执行自适应重编码，带 `fcntl.flock` 目录锁、白底 Alpha 合成、EXIF 旋转校正与非图视频媒体诚实核算。

4. **AGY 防退化 Harness 三项工程护栏（`guardrails/`）**
   - **护栏 1（`read_auditor.py`）**：唯一 `transcript_full.jsonl` 正文源、三方言截断头解析、按行窗 `(L1, L2)` 字节闭合、`read_snapshots.jsonl` 改后旧读失效与 `output.txt` Fail-Closed。
   - **护栏 2（`citation_verifier.py`）**：`/compact` 后目标源码重读核验、Python AST 与 JS 词法剥离注释/字符串后的定义级符号 + 行号区间核验、三维反漂移诊断。
   - **护栏 3（`substance_verifier.py`）**：不可变正常入口登记表（`entrypoint_registry.json`）、Python/JS 双语种 AST `Call` 与活分发注册表可达性追踪（拦截 `boundSpecialistServices` 死字典）、`run-and-attest` 宿主日志 `ATTESTED_EVIDENCE_NONCE` 交叉验签与派发信封反诱导扫描。

---

## 目录结构

```text
antigravity-enhance-suite/
├── agy_suite.py                          # 统一 CLI 工具（inspect / install / rollback / hot-inject）
├── src/
│   ├── __init__.py
│   ├── patch_sites.py                    # Go 二进制符号解析（gopclntab）与跨平台 Sites 1–22 定位器
│   ├── patch_sites_macos_v36.py          # macOS ARM64 Sites 1–22 独立检查/补丁工具
│   ├── build_ls.py                       # language_server(.exe) 补丁构建、Hardened Runtime/allow-jit 签名与校验器
│   ├── build_asar.py                     # Electron app.asar 宿主补丁构建与 SHA-256 块校验器
│   ├── asar_inject_and_rollback.py       # macOS app.asar 隔离构建、原子安装、CDP 热注入与多目标回滚工具
│   ├── agy-enhance-loader.js             # 渲染层深度汉化、集合差分看门狗、#m-wd 态势托盘与 983k 上下文环运行时
│   ├── hot_inject.py                     # 基于 Chrome DevTools Protocol (CDP) 的跨平台在线热注入工具
│   ├── upgrade_db_context_limit.py       # SQLite (.db) 结构化 Protobuf Wire 树 256k -> 983,040 无损升级器
│   └── compress_tempmedia.py             # 会话级 18MB Base64 二阶段自适应预算媒体压缩与守护工具
├── guardrails/
│   ├── harness_guardrails.py             # AGY 防退化三项护栏统一正常入口 CLI
│   ├── read_auditor.py                   # 护栏 1：会话真读、三方言截断与改后失效核验器
│   ├── citation_verifier.py              # 护栏 2：压缩后反漂移与 AST 定义级双向引文验真器
│   ├── substance_verifier.py             # 护栏 3：防旁路死字典假接线、AST 调用图与受控进程实证验收器
│   └── entrypoint_registry.json          # 不可变正常入口登记表
├── tests/
│   ├── test_harness_guardrails.py        # 护栏 1–4 全套 12 组正反例单元测试
│   ├── verify_loader_sandbox.mjs         # 桌面增强 Loader 9 大项 + Grok 10 项极限并发探针沙箱自验套件
│   └── fixtures/
│       └── real_transcript_headers_fixture.jsonl
├── docs/
│   └── reviews/
│       └── PR4_v3.5.0_Review_Report.md   # PR #4 (v3.5.0) 思考流中译与快慢双层 HUD 综合技术审核报告
├── CHANGELOG.md
├── LICENSE
└── README.md
```

---

## 快速开始

### 1. 检查当前 Antigravity 安装状态与补丁位点
```bash
python3 agy_suite.py inspect
```

### 2. 一键构建、校验并安装全部增强补丁（支持运行态热注入）
```bash
python3 agy_suite.py install --hot-inject
```

### 3. 将存量桌面端会话 `.db` 从 `256,000` 结构化无损升级至 `983,040` Tokens
```bash
python3 src/upgrade_db_context_limit.py --all --upgrade-to 983040
```

### 4. 会话媒体库体积巡检与 `18 MB` Base64 自适应预算瘦身
```bash
python3 src/compress_tempmedia.py --check
python3 src/compress_tempmedia.py
```

### 5. 运行桌面增强沙箱与防退化 Harness 护栏测试套件
```bash
node tests/verify_loader_sandbox.mjs
python3 -m unittest discover -s tests -v
```

### 6. 一键无损回滚至官方原版
```bash
python3 agy_suite.py rollback
```

---

## 开源许可

本项目采用 [MIT License](./LICENSE) 开源。
