# Changelog

本项目的所有重要版本更新与架构演进均记录于此文件。

---

## [v3.5.0] - 2026-11-02

### 新增与优化 (Added & Changed)
- **思考过程实时流式中文翻译与完成自动折叠（`src/agy-enhance-loader.js`）**：
  - **流式阶段自动展开与逐段增量翻译**：在模型思考流式生成期间（`Thinking for Xs` / `思考中 · Xs`），自动展开思考面板并将英文思考过程按双换行分段实时翻译为中文，挂载于独立兄弟容器 `.agy-think-zh-panel`（零侵入原生 React/Lexical TextNode）；
  - **代码/路径占位符保护与尾部节流**：自动保护反引号内联代码、多行代码块与文件/URL 路径免遭机器翻译破坏；已完成段落按哈希缓存仅翻译一次，正在生长的末尾段落采用 `700ms` 停顿防抖（Fallback 字典即时兜底）；
  - **思考完成自动收起与原文对照**：思考结束（转为 `Thought for Xs` / `思考 · Xs`）且未处于用户手动固定态（`data-agy-user-open="1"`）时自动折叠收起；支持随时点击重新展开，并通过顶栏「查看英文原稿 / 返回中文译文」按钮一键切换对照。
- **底部任务态势托盘（Mission HUD `#agy-mission-hud-host`）快慢双层架构与防走偏对齐**：
  - **快层（`#m-rt-bar` 实时执行状态条）**：即使阶段列表折叠也常驻显示，以最高优先级实时展示后台运行中命令/子智能体（`activeRuntimes`），无后台任务时实时展示当前思考推演或正在执行的工具动作；
  - **慢层（`#m-goal` / `#m-task-list` 有状态渐进演化蓝图）**：建立按会话隔离的只追加用户指令账本（`userInputLedgerByConvo`，按 `stepIndex` 去重并过滤看门狗唤醒消息），彻底解决长对话 `stepsInSlice` 滑动窗口截断导致的初始目标遗忘；仅在真正语义边界（新用户指令、思考完成且新增 $\ge 2$ 步、新增 $\ge 6$ 步里程碑或回合静默收尾）演化 2–5 个阶段（`✓ 已完成` / `● 进行中` / `○ 待推进`），并在同一用户轮次内冻结已确认 `done` 的历史阶段（`reconcileBlueprintTasks`），消除蓝图频繁跳变；
  - **方向偏离检测（`evaluateMissionDrift` -> `#m-align` / `#m-drift-bar`）**：对比用户指令账本路径/文件约束与近期写操作（`edit_file` / `write_file`）及连续工具报错，在顶栏实时呈现 `✓ 方向一致` 或 `⚠️ 疑似走偏` 胶囊及原因提示条。
- **原生 `设置 -> 外观`（`Settings -> Appearance`）双开关与全链路持久化**：
  - 新增 `translateThinking`（**思考过程实时中文翻译**）与 `missionHud`（**底部任务全局蓝图与实时看板**）两项独立开关，同步接入 `src/build_asar.py` 与 `src/asar_inject_and_rollback.py` 的 `DEFAULT_AGY_PREFS`、磁盘加载白名单与 `validateGlobalPatch` IPC 校验，关闭 `missionHud` 视图时后台响应式唤醒看门狗依旧常驻运行；
  - 新增 `tests/verify_loader_sandbox.mjs` **Gate 10** 沙箱回归测试（覆盖流式思考中译、生命周期自动折叠、用户指令账本防滑窗丢失、阶段冻结、走偏告警与双开关热切换，**10 / 10 全部通过**）。

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
