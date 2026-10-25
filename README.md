# Antigravity Enhance Suite

一套面向 Google **Antigravity Desktop IDE**（支持 `v2.24.0` / `v2.25.0` / `v2.26.0+`）的全栈稳定性增强、百万级（1M）上下文窗口解锁、Electron 宿主加固、流式中文任务态势托盘、圆形上下文实时计量环与深度专业中文汉化套件。

---

## 核心特性概览

1. **Go 语言服务端（`language_server.exe`）18 项二进制级增强（基于 `gopclntab` 符号精确定位）**
   - **长连接防断流与 HTTP/2 KeepAlive 优化**：将 HTTP/2 `PingTimeout` 从 15s 放宽至 180s，`KeepAlive Interval` 从 60s 缩短至 15s，`ReadIdleTimeout` 从 300s 调整至 60s，大幅降低长思维链推理期间的断流概率。
   - **全模型 1M 上下文窗口解锁与截断上限抬升**：解锁 Gemini / Claude 全系列模型的 `1,000,000`（`0x0f4240`）Token 上下文上限，并将 `GetCheckpointConfig` 硬编码的 `200k` 历史截断阈值提升至 `1M`。
   - **多模态媒体压缩阈值提升（1MB -> 10MB）**：避免高分辨率学术图像、PDF 渲染页与长截图在上传前被过度降采样。
   - **Windows 路径与控制台 UTF-8 修复**：修复 Windows 下 `file:///D:/...` 解析产生前导双反斜杠 `\\D:\...` 导致 `run_command` 报 `no such directory` 的底层缺陷（`fileuri.ToPath`），并将 `goterm.Get` 控制台代码页从 `CP936` 强制修正为 `UTF-8 (65001)`。
   - **流式传输错误自动重试扩展（`IsRetryableAPIError`）**：将 `unexpected EOF`、`wsa`、`stream `、`connection`、`unauthenticated`、`http2:` 全面纳入可自动重试子串表（适配 `v2.26.0` 的 `strings.ToLower` 15 槽位表）。
   - **非守护后台任务 60s 超时主动唤醒通知**：修复后台命令挂起不通知问题。
   - **CodeAssist OAuth2 Token 提前刷新窗口扩展（5m -> 20m）**：避免长耗时推理中途因 Token 过期引发 `UNAUTHENTICATED` 中断。

2. **Electron 宿主（`app.asar`）8 项受控生命周期加固**
   - **`storage.js`**：串行写入队列 + `.tmp.<pid>` 原子重命名，根治多窗口并发写入 `storage.json` 导致的配置文件损坏。
   - **`languageServer.js`**：子进程局部句柄追踪、启动超时有界终止（`SIGTERM -> 3s -> SIGKILL`）、`stderr` 1500ms 有界排空，以及确认端口冲突后自动回退动态端口 `0`。
   - **`ipcHandlers.js` & `preload.js`**：主框架来源校验（`https://127.0.0.1:<port>`）、乐观并发控制（`expectedRevision`）与先写盘后广播的偏好同步服务。
   - **`utils.js` & `main.js`**：基于 `SHA-256` 与字节长度双重核验的受控渲染层增强加载器（`attachControlledEnhancement`），支持端口变更无感热切换与 `DevTools` 解锁。

3. **深度专业中文汉化与交互增强（`src/agy-enhance-loader.js`）**
   - **全界面深度专业中文汉化**：覆盖全部 13 个设置子页面、快捷键面板、命令面板、下拉菜单、输入框 `placeholder` 以及 45 个内置/科学计算技能描述；搭载基于 `MutationObserver` `addedNodes` 的 **0ms 同步瞬时汉化**，展开菜单无任何英文闪烁。
   - **原生设置无缝集成**：在 `设置 -> 外观 -> 界面主题` 卡片内原生提供 **「界面语言（中文专业 / English）」** 与 **「宽屏学术排版」** 开关，无任何遮挡顶栏的悬浮窗。
   - **流式中文任务态势托盘（Mission HUD）**：在输入框上方以极简学术托盘实时展示当前会话的「总目标」「当前阶段」与任务推进清单；当会话标题仍为原始提问时，自动基于完整上下文提炼简洁中文任务概括。
   - **悬停/点击锁定式圆形上下文计量环**：在输入框右下角发送按钮旁内嵌 22×22 圆形进度环，实时计量当前上下文占用比例与 Token 数；默认仅显示进度圆环，**鼠标悬停时展开精确百分比与 Token 读数，移开即收起，点击可固定常显，点击外部区域取消固定**。
   - **工作区选择器净化与防溢出**：自动过滤 Google 内部无效占位项（`CitC Workspace` / `Cog Workspace`）及 `%TEMP%\(agy_|ocr_)` 临时目录，并限制下拉列表最大高度以保证底部 `Create` 按钮在常规窗口尺寸下始终可见。

---

## 目录结构

```text
antigravity-enhance-suite/
├── agy_suite.py                 # 统一 CLI 工具（inspect / install / rollback / hot-inject）
├── src/
│   ├── __init__.py
│   ├── patch_sites.py           # Go 二进制符号解析（gopclntab）与 18 处补丁定位器
│   ├── build_ls.py              # language_server.exe 补丁构建与校验器
│   ├── build_asar.py            # Electron app.asar 宿主补丁构建与 SHA-256 块校验器
│   ├── agy-enhance-loader.js    # 渲染层深度专业汉化、态势托盘与圆形上下文计量环运行时
│   └── hot_inject.py            # 基于 Chrome DevTools Protocol (CDP) 的在线热注入工具
├── LICENSE
└── README.md
```

---

## 快速开始

### 环境要求
- Windows 10 / 11（64-bit）
- Python 3.10+
- Node.js（用于 `node --check` 静态语法校验）

### 1. 检查当前 Antigravity 安装状态与补丁位点
```bash
python agy_suite.py inspect
```

### 2. 一键构建、校验并安装全部增强补丁
```bash
python agy_suite.py install --hot-inject
```
> 安装器会自动将官方原版 `app.asar` 与 `language_server.exe` 备份至 `~/.gemini/agy-enhance/app.asar.orig_<version>.bak` 与 `language_server.exe.orig_<version>.bak`，随后原子替换生产文件，并通过 CDP 将前端增强热注入到当前已打开的 Antigravity 窗口中。

### 3. 仅对已运行窗口执行前端热更新
```bash
python agy_suite.py hot-inject
```

### 4. 一键无损回滚至官方原版
```bash
python agy_suite.py rollback
```

---

## `language_server.exe` 二进制补丁清单（18 Sites）

| 编号 | 目标函数 / 符号 | 原始值 | 增强值 | 作用说明 |
| :--- | :--- | :--- | :--- | :--- |
| Site 1 | `http2.(*Transport).PingTimeout` | `15s` | `180s` | 防止高负载流式生成期间因 H2 Ping 超时断流 |
| Site 2 | `net.(*Dialer).KeepAlive` | `60s` | `15s` | 提高 TCP 保活探测频率，防止 NAT/代理静默掐断 |
| Site 3 | `http2.(*Transport).ReadIdleTimeout` | `300s` | `60s` | 更早发现僵尸连接并触发无感重连 |
| Site 4 | `HydrateModelEntryDefaults` | `200,000` | `1,000,000` | 默认模型上下文窗口提升至 1M Tokens |
| Site 5–6 | `defaultConversationalCascadeConfig` | `200,000` | `1,000,000` | 级联会话规划与执行上下文上限解锁至 1M |
| Site 7 | `utils.GetCheckpointConfig` | `200,000` | `1,000,000` | 解除 Claude / 通用模型 200k 检查点强制截断上限 |
| Site 8–10 | `media_compression` | `1 MB` | `10 MB` | 多模态图片/PDF 页面无损传输阈值提升至 10MB |
| Site 11 | `fileuri.ToPath` | `\\D:\...` | `D:\...` | 修复 Windows 盘符 URI 前导双反斜杠导致命令执行失败 |
| Site 12 | `goterm.Get` | `CP936 (936)` | `UTF-8 (65001)` | 修复中文 Windows 终端输出乱码 |
| Site 13 | `utils.IsRetryableAPIError` | 不含 `unexpected EOF` | 纳入重试 | 流式读取 `unexpected EOF` 自动无缝重试 |
| Site 14 | `retryableSubstrings` (15 slots) | 窄匹配 | 宽匹配 (`wsa`, `stream `, `connection`, `unauthenticated`, `http2:`) | 全面覆盖网络抖动与瞬时鉴权抖动自动重试 |
| Site 15–16 | `RunCommandHandler.runTaskListeners` / `runCommand` | 无上限等待 | `60s` 通知上限 | 防止后台命令状态监听无限挂起 |
| Site 17 | `codeassistclient.RefreshingTokenSource` | `5m` | `20m` | OAuth2 Token 提前 20 分钟主动刷新 |
| Site 18 | `oauth2.(*Token).expired` | `10s` | `10s` (核验保留) | 保持标准 OAuth2 10 秒过期缓冲不被误改 |

---

## 开源许可

本项目采用 [MIT License](./LICENSE) 开源。
