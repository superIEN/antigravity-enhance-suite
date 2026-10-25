# Changelog

本项目的所有重要版本更新与架构演进均记录于此文件。

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
