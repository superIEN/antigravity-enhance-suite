# Changelog

本项目的所有重要版本更新与架构演进均记录于此文件。

---

## [v3.6.2] - 2026-11-10

### 新增与优化 (Added & Changed)
- **官方 Antigravity `v2.30.0`（Electron `41.2.1` / Chromium `146.0.7680.197`）全栈适配**：
  - **`dist/storage.js` 原生 `writeChain` 队列与 `getItemsSync()` 接口兼容（`src/build_asar.py`）**：
    - 官方 `v2.30.0` 在 `FileStorage` 中原生实装了 `this.writeChain = Promise.resolve()` 串行写入队列、`.corrupt-${Date.now()}.bak` 损坏备份恢复以及主进程启动时调用的 `getItemsSync(keys)` 同步批量读取方法；
    - `patch_storage_js` 新增对 `v2.30.0+` 原生 `writeChain` 的自动识别：在注入 `'contextUsageIndicatorEnabled': 'true'` 默认开关后直接保留官方完整 `FileStorage` 类体与 `getItemsSync()` 实现，消除因旧版正则替换覆盖 `FileStorage` 导致的 `storage.getItemsSync is not a function` 启动异常，同时对 `v2.24.0–v2.29.0` 保持向下兼容。
  - **`language_server_windows_x64.exe` `v2.30.0` 结构体与栈帧编译偏移适配（`src/patch_sites.py` · `src/build_ls.py`）**：
    - **`Site 16 ((*runningCommand).waitInBackground)`**：适配 `v2.30.0` 中 `*RunCommandTool`（`rsi = [rcx]`）特性标志偏移从 `+0x28`（`807e2800`）迁移至 `+0x50`（`807e5000`）的编译变化，同时保留 `*CommandSpec`（`rbx = [rcx+0x8]`）中 `IsDaemon`（`[rbx+0x30]`）与 `NotificationTimeoutSeconds`（`[rbx+0x38]`）的 60s 默认唤醒原地 78 字节块补丁；
    - **`Site 19 ((*AgentExecutor).transitionFromRunningToIdleOrClosed)`**：适配 `v2.30.0` 栈帧扩容 `+0x10`（`0x190 -> 0x1a0`）引起的局部变量偏移变化（`[rsp+0xc9] -> [rsp+0xca]`），新增 `440fb68424ca0000004584c00f84cb000000 -> 440fb68424ca0000004584c0e9cc00000090` 候选模式；
    - **`Site 17 (codeassistclient.RefreshingTokenSource)`**：新增 `total_imm_hits == 3`（`1x movabs r8, imm64; mov [rdx+0x60], r8` + `2x movabs rcx, imm64; mov [rax+0x20], rcx`）与 `total_imm_hits == 1`（`movabs rax, imm64`）精确指令级唯一跨度校验，并在 `src/build_ls.py` 中统一调用 `locate_oauth_token_sites` 替代整函数块替换。
  - **新增 23 条 `v2.30.0` Web UI 汉化词条（`src/agy-enhance-loader.js`）**：
    - 覆盖 `v2.30.0` 新增的 `Side Question`（`侧边追问`）、`Side Questions`（`侧边追问列表`）、`Peek References`（`速览引用位置`）、`Progress Updates`（`进度动态时间线`）、`Suggested Actions`（`推荐后续操作`）及配套设置说明，保持 `REVERSE_TO_EN` 双向字典一一对应。

---

## [v3.6.1] - 2026-11-06

### 新增与优化 (Added & Changed)
- **新增 `Site 23 ((*TruncationDecorator).truncateIfTooLarge)` 单会话轨迹物理体积上限扩容（`25 MB -> 256 MB`）**：
  - 经对多模态重度会话（含大量高清 PNG/JPEG 素材与多段完整 `.mp3` 音频母带）的底层 SQLite（`steps` / `gen_metadata`）与 Go 二进制逆向取证，定位到 `google3/third_party/jetski/cortex/trajectory/decorators/decorators.(*TruncationDecorator).truncateIfTooLarge` 在 `983,040` Token 阈值之外另设有一道硬编码的 **`25 MB`（`0x01900000 = 26,214,400` Bytes）** 单会话物理体积上限：一旦未清除步骤的总字节体积突破 `25 MB`，该函数即从第 0 步起循环调用 `utils.ClearTrajectoryStep` 将旧步骤状态抹为 `CORTEX_STEP_STATUS_CLEARED (5)`，进而导致 `TrajectoryToChatMessagesToTokenLimitOrStepIndex` 遇到 `CLEARED` 断层而在 ~400k Tokens 处提前触发 `CHECKPOINT` 自动压缩；
  - 在 `src/patch_sites.py`、`src/build_ls.py` 与 `src/patch_sites_macos_v36.py` 中新增 **`Site 23`**：将 Windows `x86_64`（`cmp rax, 0x01900000`、`cmp rdx, 0x01900000`、`mov eax, 0x01900000`）与 macOS `ARM64`（`movz xN/wN, #0x190, lsl #16`）下 `(*TruncationDecorator).truncateIfTooLarge` 内的全部 `25 MB`（`0x01900000`）立即数原地升级为 **`256 MB`（`0x10000000 = 268,435,456` Bytes）**，支持混合补丁态幂等归一化与 `movabs` 指令边界防护，兼容 `v2.28.x`（4 处立即数）与 `v2.29.0+`（6 处立即数），确保多媒体重度会话与普通代码会话一样完整跑满 `983,040` Tokens 上下文窗口。

---

## [v3.6.0] - 2026-11-05

### 新增与优化 (Added & Changed)
- **官方 Antigravity `v2.29.0` 全栈深度适配与架构升级（经 Grok、Kimi、Codex、AGY 两轮多智能体交叉会诊）**：
  - **`language_server(.exe)` 二进制位点精准对齐与官方原生媒体压缩切流**：
    - 适配 Windows `x86_64` `v2.29.0` 编译偏移漂移位点：`Site 1 (net/http.(*Transport).protocols)`（`0x3688aa`）、`Site 19 ((*AgentExecutor).transitionFromRunningToIdleOrClosed)`（`0x25848ba`）、`Site 20 ((*AgentExecutor).startMessageWatcherLocked.func2)`（`0x2593fc5`）；
    - 适配 macOS Apple Silicon (`darwin-arm64`) `v2.29.0` Mach-O 全部 28 处二进制位点：对齐 `Site 1 (net/http.(*Transport).protocols)` `[x0, #0x78]` 偏移（`0x2fe768`）、`Sites 4–5 (utils.GetCheckpointConfig)` `x4` 寄存器 `256K -> 1M`（`0x13aca84` / `0x13aca90`）、`Site 13 (utils.IsRetryableAPIError)` `adrp + add` 页内偏移自适应解析（`0x13b491c`）、`Site 14` 15 槽位小写断流重试表扩展（`0xa0ab2a8`）、`Site 15 (runTaskListeners)`（`0x1f0f788`）与 `Site 16 (waitInBackground)`（`0x1f46e00`）非守护命令 60s 默认超时唤醒、`Site 21 (startMessageWatcherLocked.func2.1-range1)` `[sp, #0x48]` 栈偏移与相对 `bl` 动态还原（`0x1f96784`），以及 `Site 22 (ClampCheckpointConfigForModelInfo)` `[x0, #0xc]` 字段偏移（`0x13acffc`）；
    - 自动识别 `v2.29.0` 新增的官方原生 `media.smallerAsJPEG` / `CompressConfigFromProto` / `local.(*Backend).mediaCompressConfig` 图片重编码管线，在 `v2.29.0+` 上优雅退役旧二进制 `Sites 7–8`（保留 `v2.24–v2.28` 向下兼容与 `src/compress_tempmedia.py` 会话级历史目录防爆守护）；
    - 完整保留全部 1M（`983,040` Tokens）上下文扩窗位点（`Sites 3, 4, 5, 6, 22`），并在 `src/build_asar.py` 中使用 `write_bytes` 兼容 macOS 系统自带 Python 3.9。
  - **Electron 宿主 `BaseWindow` + `WebContentsView` (`hubWcv`) 架构适配与首屏同步预注入（`src/build_asar.py`）**：
    - 全面适配 `v2.29.0` 新增的 `getHubWindows()`、`getHubWebContents(win)` 与 `resolveHubWindowFromWebContents(wc)` 视图层封装；
    - **`patch_preload_js` 新端口首屏同步预注入**：针对每次重启后 `language_server` 切换随机端口导致 Chromium `window.localStorage` 按 Origin 归零的问题，在 `preload.js` 顶层（早于页面 `/main.js` 执行）同步写入 `REQUIRED_FLAGS`（含 `'enable-context-usage-indicator': true`、`'context-window-selector': true`）并通过 `storageAPI` 同步 `app_storage.json`；
    - **`patch_storage_js` 默认开启指示器并尊重用户开关**：在主进程 `StorageManager` 构造函数的 `this.defaults` Map 中注入 `'contextUsageIndicatorEnabled': 'true'`；
    - **`patch_utils_js` 解除 `verifyLoaderIntegrity` 自指哈希死锁**：移除因 `EXPECTED_LOADER_SHA256` 内嵌于 `utils.js` 导致的 `selfUtilsSha` 自指死锁，改为校验应用版本及回执中的 `loader_sha256` / `loader_bytes`。
  - **Windows 独占文件锁后台 WMI 自动换包守护（`agy_suite.py` · `deferred_asar_swapper.py`）**：
    - 解决在已打开的 Antigravity 窗口内执行 `install --hot-inject` 时 `resources\app.asar` 被 `Antigravity.exe` 独占锁死（`[WinError 32]`）导致重启后前端增强丢失的顽疾；
    - 通过 Windows WMI `Win32_Process.Create` 派生脱离进程树 Job Object 的无窗口守护进程，预先在 `resources\` 同目录完成候选包写入与 `fsync`，并在检测到客户端关闭释放文件锁的瞬间（`<100ms`）校验原包 SHA256 防版本漂移、执行 NTFS 元数据级原子替换 `os.replace`、清理历史 `.old.*` 残余文件并原子更新回执。
  - **官方 `v2.29.0` 原生上下文与配额指示器（`context-usage-indicator` / `context-usage-panel`）联动与 React 拆分节点零破坏汉化（`src/agy-enhance-loader.js`）**：
    - 接入原厂 `window.nativeStorage` 桥接对象并按 `Array.isArray(cf)` 规范热刷新 React `FeatureManager (Rka)` 的 `runtimeEnvironment.customFlags` 数组与 `getCache`，使官方底栏指示器在热注入与冷启动下均即时挂载；
    - 通过 CSS `body:has([data-testid="context-usage-indicator"])` 与 JS 可见性双重检测，使自建 `#agy-ctx-ring-pill` 在官方指示器挂载时零闪烁自动退让；
    - 放行 `[data-testid="context-usage-panel"]` 等面板内的 `font-mono` 文本节点翻译，并针对 React `dqc`（5 节点拆分 `120K of 983K tokens used`）与 `Spc`（2 节点拆分 `85% left`）实现单节点原地中文映射，100% 保持 React Fiber 引用零破坏。
  - **修复数据库升级器 `src/upgrade_db_context_limit.py` 的行级 200K 判定**：将 `executor_metadata` 的 `db_has_any_200k` 判定从跨行跨字段 `any(v <= 262_144)` 修正为单行全字段 `all(v <= 262_144)`，消除子配置默认值 `200,000` 对 1M 会话 `gen_metadata` 升级的误拦截。
- **社区 PR 审评与双轨可选分支归档**：新增 [《PR #4 (`v3.5.0`) 综合技术审核报告》](./docs/reviews/PR4_v3.5.0_Review_Report.md) 与 `optional/*` 分支拉取索引。

---

## [v3.4.0] - 2026-11-01

### 新增与优化 (Added & Changed)
- **macOS Apple Silicon (`darwin-arm64`) 全栈原生适配与 1M 上下文真实解锁（Sites 1–22，共 29 处二进制补丁）**：
  - **新增 `Site 22 (utils.ClampCheckpointConfigForModelInfo)`**：根治桌面端因 `applyFeatureProviderCheckpointOverrideConfig`（`0x1014a0cb8`）覆写 `MaxTokenLimit = 256,000` 导致每 ~250k 自动触发有损压缩（`<CONTEXT_SUMMARY>`）的底层顽疾；通过 9 指令 `csel w4, w5, w4, gt` 原地补丁，对 `1,048,576` 上下文模型（如 `gemini-4-argon-high`）精确解锁为 **`983,040` Tokens**（`1,048,576 - 65,536`），同时在 `w3 == 0` 与 `w3 == 256,000` 双路径下对 `200,000` 上下文模型（Claude 系列）100% 保持 **`200,000` Tokens** 零回退；
  - **macOS ARM64 `Sites 19–21` 响应式唤醒竞态根治**：移植并验证 `(*AgentExecutor).transitionFromRunningToIdleOrClosed`、`(*AgentExecutor).startMessageWatcherLocked.func2` 与 `(*AgentExecutor).startMessageWatcherLocked.func2.1-range1`（`(*AgentState).SubagentsAndTasksIdle -> 1`）三处控制流补丁；
  - **Hardened Runtime 与 `allow-jit` 签名保全**：在 `build_ls.py`、`agy_suite.py` 与 `patch_sites_macos_v36.py` 中强制携带 `--identifier language_server --options runtime --entitlements`（`com.apple.security.cs.allow-jit = true`）签名，并与 macOS `.app` 离线克隆 Google Team（`EQHXZ8M8AV`）库加载约束签名及新 inode 原子替换闭环；
  - **新增结构化 Protobuf 会话数据库无损升级器（`src/upgrade_db_context_limit.py`）**：采用递归 Protobuf Wire 语法树解析精确匹配 `executor_metadata`（`$.10.2.5`）与 `gen_metadata`（`$.1.9.10.4`），利用 `256,000`（`80 d0 0f`）与 `983,040`（`80 80 3c`）同为 3 字节 Varint 的等长性质，在 `BEGIN IMMEDIATE` 事务与 `sqlite3.backup()` 在线备份下无损将存量会话升级至 `983,040`。
- **桌面渲染增强套件（`src/agy-enhance-loader.js` · `3.6.0-macos-merged`）架构升级（经 Grok 4.7 `xhigh` `500k` 4 轮极限并发对抗审计）**：
  - **按 ID 集合差分（Set-Diff）+ 双源 1:1 归一的响应式看门狗**：以 `rec.activeBgIds` 集合差分替代纯整数计数（解决同周期一退一进 `{bg-a, bg-b} -> {bg-b, bg-c}` 漏检），通过 `rec.taskToStepMap` 将 Provider 后台任务行与运行中 `invokeSubagent` 步骤 1:1 归一为 `step:${stepIndex}`（消除 Provider 行提前消失引发的假下降沿），折叠 `backgroundTasksUpdate`/`pendingAgentMessagesUpdate` 与 `globalThis.__asu` 稀疏补丁，并在 `SendAgentMessage` 返回时核对 `rec.epoch === reqEpoch` 与 `rec.bgFinishedEdgeTs === sentEdge`；
  - **Mission HUD 看门狗可观测状态胶囊（`#m-wd`）**：在任务态势托盘顶栏右侧实时展示 `后台 N`、`等待接管`、`已接管` 三态胶囊；
  - **`WeakMap` React Fiber 缓存与 250ms HUD 合并节流**：将 DOM 节点到 Fiber 实例的查找降为 $O(1)$（含 `null` 缓存命中），并保留思考流内联单句定性摘要（含 `nextToolDesc` 上下文推导）与点击捕获展开守卫；
  - **新增会话级自适应 Base64 预算媒体守护工装（`src/compress_tempmedia.py`）**：按 `18 MB` Base64 安全天花板自动规划图片尺寸与质量，支持基于 `_bak_orig` 无损备份的二阶段自适应重编码、白底 Alpha 合成、EXIF 方向校正、`sips` 防小图放大、非图视频媒体诚实核算与 POSIX `fcntl.flock` 目录锁，根治 `.tempmediaStorage` 突破 30MB 导致的 `400 FAILED_PRECONDITION` 会话死锁。
- **AGY 防退化 Harness 四大护栏与 `ArchUnit 0.3` 强契约桥接（`guardrails/`）**：
  - **护栏 1（`read_auditor.py`）**：锁定 `transcript_full.jsonl` 唯一工具正文源，通吃三类 AGY 截断头方言，按行窗 `(L1, L2)` 独立闭合字节坐标，配合 `read_snapshots.jsonl` 实现改后旧读失效与等长改写检出，对缺失/未读 `output.txt` 执行 Fail-Closed；
  - **护栏 2（`citation_verifier.py`）**：检测 `/compact` 压缩事件并强制核验目标源码重读，执行 Python AST 与 JS 词法剥离注释/字符串后的定义级符号 + 行区间重叠核验，三维反漂移诊断 100% 捕获 `cmd_check_completion`、`ArchUnit030Tests`、`pre -> invoke`；
  - **护栏 3（`substance_verifier.py`）**：锁定 `entrypoint_registry.json`，沿 AST `Call` 与活分发注册表追踪可达性（在 Python/JS 双语种拦截 `boundSpecialistServices` 死字典旁路），通过 `run-and-attest` 生成带宿主日志 `ATTESTED_EVIDENCE_NONCE` 锚点的受控实证；
  - **护栏 4（`archunit_macos_bridge.py` & `harness_guardrails.py`）**：核验 macOS `codesign` 与规范源 SHA256，强映射读回 `objects`/`empirical_evidence`/`verdict`，反向审计独审实例自身的 `transcript_full.jsonl` 真读覆盖率，并严格分列 `technical_gate_passed` 与 `triad_complete`。

---

## [v3.3.0] - 2026-10-29

### 新增与优化 (Added & Changed)
- **官方 Antigravity `v2.27.0` 与 `v2.28.0` 全栈兼容适配**：
  - 适配 `v2.28.0` `google3/third_party/oneharness/internal/tools/tools.(*runningCommand).waitInBackground` 后台任务管理重构，新增 88 字节非守护后台命令默认 60 秒 `time.NewTimer` 超时主动唤醒补丁（Site 16），同时完整保留 `v2.24.0–v2.27.0` 的 `(*RunCommandTool).runCommand` 向下兼容分支；
  - 适配 `v2.27.0` / `v2.28.0` Electron 宿主 `dist/utils.js`、`dist/storage.js`、`dist/ipcHandlers.js` 与 `dist/main.js` 新增的 `hubWindows` 窗口管理架构（`getHubWindows()`、`getTargetHubWindow()` 与 `hubWindows.add(win)`）及 `v2.28.0` 新增的 `scriptJail` / `webviewSecurityState` / `guestDebugger` 模块（共 `1,085` 条目），向下无缝兼容 `v2.24.0–v2.26.0`；
  - 适配 `v2.27.0` 双二进制与 `v2.28.0` 单 `language_server.exe` 部署结构；
  - 将 Site 21（`startMessageWatcherLocked.func2.1-range1`）二进制匹配升级为跨版本相对 `call` 位移（`e8 <disp32>`）自适应计算，同时兼容 `v2.26.0`、`v2.27.0` 与 `v2.28.0+`。
- **长任务后台 Task / 子智能体静默唤醒失败双层根治（Sites 19–21 + 前端看门狗）**：
  - **后端 `AgentExecutor` 状态机三处补丁（Sites 19–21）**：
    - `Site 19 ((*AgentExecutor).transitionFromRunningToIdleOrClosed)`：无论上一轮是否发生瞬态错误，回落至 `Idle` 时强制重启 `messageWatcher`；
    - `Site 20 ((*AgentExecutor).startMessageWatcherLocked.func2)`：启动 `messageWatcher` 时强制立即执行首轮 `shouldWakeUp()` 检查，防止切换窗口期已入队通知漏检；
    - `Site 21 ((*AgentExecutor).startMessageWatcherLocked.func2.1-range1)`：透传全部 `Idle` 状态变更信号以唤醒 `selectgo`，彻底根治常驻监听器抢占 `NotifyCh` 导致的第 2 个及后续后台任务完成静默死锁。
  - **前端响应式唤醒看门狗（`agy-enhance-loader.js`）**：
    - 新增 `evaluateReactiveWakeupWatchdog` 与 `triggerNativeSystemWakeupRpc`，当侦测到主会话停止流式输出且存在已完成的后台任务或子智能体消息超过 `3.8s` 未唤醒时，自动通过同源 Connect-RPC 发送 `clientType: 2`（`SYSTEM_WAKEUP`）唤醒请求并附带防重入锁。

---

## [v3.2.0] - 2026-10-25

### 新增与优化 (Added & Changed)
- **流式思考呼吸提示与草稿静默收束**：
  - 保留对话流中 `思考中 · Xs`（`thinking-collapsible-trigger`）单行标题及其原生流式呼吸微光动画，隐藏右侧展开箭头与底部的长篇原模型思考草稿面板，兼顾实时呼吸反馈与纯净阅读视野。
- **工具调用明细默认收起与回合防空白保护**：
  - 将内层工具组（`tool-group-collapsible`）的底层文件读写与命令明细面板默认收起为极简灰字（`思考中…` / `正在执行…` / `查阅了文件 · 修改了文件`），仅在用户主动点击时展开；
  - 保持外层回合总容器（`worked-for-collapsible + div`）在流式运行期间完整可见，彻底消除回合运行中因外层容器被隐藏导致的整片空白现象；
  - 移除折叠行右侧拼接底层 `.py` / `.json` 文件名的冗余小尾巴。
- **底部任务态势托盘（Mission HUD）布局重构与默认单行折叠**：
  - 将挂载节点从内层输入框卡片提升至第 5 层最外框容器 `div[data-testid="agent-input-box"]` 顶部，改用原生文档流相对定位（`position: relative; margin-bottom: 8px`），彻底解决输入框粘贴截图扩展栏被遮挡或发送消息后托盘悬空的问题；
  - 将初始状态改为**默认单行折叠**（`missionHudCollapsed = true`，仅高 `30px`），平时仅展示呼吸脉冲状态灯、任务总目标概括与完成比例（`X / Y 展开 ▴`），点击后按需展开分步清单。
- **对话流异常模板净化**：
  - 针对 `[data-testid="planner-response-text"]` 增加实时净化逻辑，自动隐藏意外混入正文的子智能体通信模板与原始调试 JSON 代码块。

---

## [v3.1.0] - 2026-10-25

### 初始公开发布 (Initial Public Release)
- **Go 语言服务端（`language_server.exe`）18 项二进制补丁**：
  - HTTP/2 `PingTimeout`（`15s -> 180s`）、`KeepAlive`（`60s -> 15s`）、`ReadIdleTimeout`（`300s -> 60s`）长连接防断流；
  - 全模型 `1,000,000`（1M）上下文窗口解锁与 `GetCheckpointConfig` `200k -> 1M` 检查点截断上限提升；
  - 多模态媒体压缩阈值提升（`1MB -> 10MB`）；
  - Windows `file:///D:/...` 前导双反斜杠 `\\D:\` URI 路径修复（`fileuri.ToPath`）与终端 `CP936 -> UTF-8` 编码修正；
  - `IsRetryableAPIError` 流式断流与 15 槽位 `retryableSubstrings` 自动重试扩展（适配官方 `v2.26.0`）；
  - 非守护后台命令 60s 超时主动唤醒与 CodeAssist OAuth2 Token 提前 20 分钟刷新。
- **Electron 宿主（`app.asar`）8 模块加固**：
  - `storage.js` 串行原子写入队列、`languageServer.js` 有界进程终止与动态端口回退、`ipcHandlers.js` 单写者偏好同步服务、`utils.js` SHA-256 校验受控加载器。
- **全界面深度专业中文汉化与圆形上下文计量环**：
  - 覆盖 13 个设置分页、展开菜单 `0ms` 瞬时汉化、`设置 -> 外观` 原生语言/宽屏开关集成，以及右下角悬停/点击锁定式圆形上下文 Token 计量环。
