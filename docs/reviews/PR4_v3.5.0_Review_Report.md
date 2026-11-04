# PR #4 (`v3.5.0`) 综合技术审核报告：思考过程实时流式中译、自动折叠与快慢双层防走偏 Mission HUD

- **关联 PR**：[#4 `feat(hud,thinking): v3.5.0 思考过程实时流式中译、自动折叠与快慢双层防走偏 Mission HUD`](https://github.com/superIEN/antigravity-enhance-suite/pull/4)
- **提交作者**：`@sorry-makerT-T`（石子恒）
- **原始提交 Commit**：`fd84bc17fb6ea0085d5981955d691f124c0886f5`（6 个文件变更，`+1,503 / -53` 行）
- **联合审评模型**：Claude Opus 4.6（主架构师视角） + Kimi Code CLI `k3.1-pro` `max`（独立静态代码与性能边界审计）
- **审核结论与分支处置决定**：
  1. **严禁合入 `main` 核心稳定主线**：`main` 分支定位为「零额外推理请求负担、零界面布局抖动、遵循极简单行思考摘要美学」的稳定生产底座。本 PR 引入了流式思考期间的高频 `GetModelResponse` RPC 调用、思考面板自动撑开/折叠引发的页面布局跳动（Layout Shift），以及若干启发式规则误判与宣传超出实现的问题，因此**不合入 `main` 主线**。
  2. **设立独立可选增强分支体系**：
     - 已将本 PR 原始代码完整归档至独立可选分支 [`optional/pr4-v3.5.0-hud-thinking`](https://github.com/superIEN/antigravity-enhance-suite/tree/optional/pr4-v3.5.0-hud-thinking)，供偏好实时思考同传与展开式看板的使用者按需检出或附加合并；
     - 已将 **PR #4 的目标合并基底（Base Branch）从 `main` 重定向至 [`optional/hud-thinking-stream`](https://github.com/superIEN/antigravity-enhance-suite/tree/optional/hud-thinking-stream)**。作者可针对本报告列出的 9 项代码级缺陷继续向 `optional/hud-thinking-stream` 分支提交修订 PR，经验收通过后合入该可选分支。

---

## 一、 实际效用评估（真止痛改进 vs. 鸡肋/过度设计）

| 功能模块 | 效用评级 | 审评结论与工程判断 |
| :--- | :---: | :--- |
| **1. Mission HUD 快层实时状态条（`#m-rt-bar`）** | ⭐⭐⭐⭐⭐<br>**高价值实用** | **本 PR 最具实用价值的改进。** 移除了旧版折叠 HUD 时隐藏 `.rt-bar` 的样式，直接复用现有运行态数据（`activeRuntimes`、当前思考摘要、当前工具动作）在折叠态下常驻单行展示，**零额外 LLM 调用成本**，信息密度合理。 |
| **2. 原生 `设置 -> 外观` 双开关与看门狗保活** | ⭐⭐⭐⭐<br>**实用但有漏口** | 在设置面板提供 `translateThinking` 与 `missionHud` 独立开关方向正确，且隐藏 HUD 时保留后台响应式唤醒看门狗常驻运行的设计合理。**但存在代码漏口**：`missionHud = false` 时仅隐藏了 DOM，仍会先执行 `collectLiveSessionMissionState()` 并触发后台 LLM 蓝图总结请求，未做到真正的“关面板即停 RPC”。 |
| **3. Mission HUD 慢层指令账本与阶段防篡改** | ⭐⭐⭐<br>**长对话有益，短任务过重** | `userInputLedgerByConvo` 记录首条与最近 4 条用户指令，缓解了 28 步滑动窗口（`stepsInSlice`）截断初始目标的问题；里程碑触发比旧版“每 3 步重算”降低了约 50% 请求频次。但 `reconcileBlueprintTasks` 冻结的是**小模型口头声明的 `done`** 而非经工具验证的真实完成态，一旦小模型误标 `done` 就会在同一轮内永久锁死错误状态；且对简单短任务强制划分 2–5 个阶段属于过度设计。 |
| **4. 思考过程流式自动撑开、实时中译与完成折叠** | ⭐⭐<br>**体验两极分化** | 采用独立兄弟容器 `.agy-think-zh-panel` 避免直接改写 React/Lexical `TextNode` 的做法工程上很稳妥；已完成段落缓存 + 每次最多 3 段也控制了重复请求。但**默认在流式期间自动撑开大面板、思考结束瞬间自动折叠收起**会导致对话流反复上下剧烈跳动（Layout Shift），违背极简沉浸式阅读体验；且生长尾段需满足 `>= 40` 字符并停顿 `700ms` 才触发翻译，流式期间实际仍是英中交替闪烁，并非真正的逐词同传。 |
| **5. 方向偏离检测（`evaluateMissionDrift`）** | ⭐<br>**鸡肋且易误报** | 仅靠简单关键词正则（检测用户指令是否含 `不要修改` / `先不要改` / `只读` 等词）加轻量模型 `MODEL_PLACEHOLDER_M18` 的主观猜测来判定“走偏”，极易在复杂自然语言指令下误报；且未命中时直接显示绿字 `✓ 方向一致` 属于证据不足的过度承诺。 |

---

## 二、 四维运行负担与性能副作用量化分析

1. **主模型上下文窗口（Context Window）负担：零负担（`0 Token`）**
   - 思考翻译与蓝图总结均通过前端旁路调用 `LanguageServerService/GetModelResponse`（使用轻量辅助模型 `MODEL_PLACEHOLDER_M18`），不写入会话 `.db` 轨迹，不占用主模型 `983,040` 上下文额度。
2. **本地 `language_server` 与网络并发负担：思考流同传显著增负**
   - **翻译 RPC 频次**：假设一次长思考包含 $P$ 个可见英文段落，即使全部完整封口也需发起 $\lceil P / 3 \rceil$ 次 `GetModelResponse` 请求（通常 3–6 次）；而流式生长尾段每次停顿满 `700ms` 就会将当前半截文本作为新缓存键发起一次请求，段落继续生长后该半截缓存即告作废。
   - **单飞抢占（`abort`）引发的算力浪费与翻译饥饿**：`triggerLlmMissionSummarization` 启动时会无条件调用 `activeThinkingTransAbortCtrl.abort()` 中止在途翻译请求。浏览器侧 `abort()` 并不保证本地 `language_server` 或上游推理立即终止，被中止的翻译结果未写入 `thinkingParaCache`，随后重入时又会重复发起相同请求；反之，当蓝图请求在途（最长 `12s`）时翻译被阻塞，频繁到达 6 步里程碑会导致思考翻译长期饥饿。
3. **前端 DOM 渲染与 `MutationObserver` 负担：$O(N)$ 重建与三次高度跳变**
   - 在 `renderThinkingChinesePanel`（`src/agy-enhance-loader.js:556-566`）中，签名 `sig` 拼接了全部段落与正在流式生长的 `tail` 文本。流式吐字期间 `tail` 每变化一次，`sig` 即失效，代码会执行 `contentBox.textContent = ''` **清空并重建全部已译段落的 `<p>` 节点**（复杂度 $O(N)$），不仅造成无谓的 DOM 销毁重建，还会反复触发全局 `MutationObserver`。
   - 每个思考块生命周期内经历「流式自动撑开 -> 英文段落替换为中文译文高度变化 -> 思考完成自动折叠」三次明显的容器高度跳变。
4. **内存占用与缓存生命周期：存在无淘汰 Map 累积**
   - `thinkingParaCache` 虽限制为 600 条，但以完整规范化段落文本（无单段字节上限）作为 Key；
   - `thinkingTailStateByUid`（按 DOM UID 索引）、`userInputLedgerByConvo` 与 `lastBlueprintAttemptTsByConvo`（按会话 ID 索引）在长周期运行、多会话切换或 DOM 节点卸载后**缺乏统一淘汰/清理机制**，会随使用时间持续增长。

---

## 三、 代码级具体缺陷与边界 Bug 清单（共 9 项）

### 缺陷 1：`missionHud = false` 关闭开关后仍在后台偷发 LLM 蓝图请求
- **位置**：`src/agy-enhance-loader.js:5428-5431`（`renderMissionHud`）与 `5043-5054`（`collectLiveSessionMissionState`）
- **根因**：为了保证关闭 HUD 时响应式唤醒看门狗继续工作，`renderMissionHud()` 在检查 `globalPrefs.missionHud === false` 之前先调用了 `collectLiveSessionMissionState()`。然而 `collectLiveSessionMissionState()` 内部并未判断 `globalPrefs.missionHud !== false` 就直接调用了 `triggerLlmMissionSummarization()`。
- **后果**：用户在设置中关闭「底部任务全局蓝图与实时看板」后，虽然面板隐藏了，但后台依然在每轮思考完成或满 6 步时发起 `GetModelResponse` 蓝图总结请求，还会无故 `abort` 掉正在进行的思考翻译请求。

### 缺陷 2：自动展开思考面板缺失原生 `aria-expanded` 同步，真实折叠态下无法自动展开
- **位置**：`src/agy-enhance-loader.js:1647-1655`（CSS）、`3279-3282`（`isVisibleNow`）与 `3531-3542`
- **根因**：流式思考检测仅在按钮上设置了 `tb.setAttribute('data-agy-auto-streaming', '1')`，并未修改 `aria-expanded` 属性，也未触发 React 原生展开以挂载兄弟内容节点 `bodyDiv`。而 CSS 选择器硬性要求 `button[data-testid="thinking-collapsible-trigger"][data-agy-auto-streaming="1"][aria-expanded="true"] + div`，且 `isVisibleNow` 同样要求 `tb.getAttribute('aria-expanded') === 'true'`。
- **后果**：若宿主原生思考按钮初始挂载为 `aria-expanded="false"`（或未挂载 `+ div`），自动展开与实时翻译根本不会触发。`tests/verify_loader_sandbox.mjs:1821` 手工把 Mock 按钮写死为 `aria-expanded="true"`，掩盖了该真实环境缺陷。

### 缺陷 3：`unmaskCodeAndPaths` 使用字符串 `replace` 导致 `$` 特殊替换符破坏代码内容
- **位置**：`src/agy-enhance-loader.js:217-223`（`unmaskCodeAndPaths`）
- **根因**：
  ```javascript
  out = out.replace(new RegExp(`__AGY_PH_${i}__`, 'g'), placeholders[i]);
  ```
  `String.prototype.replace` 的第二个参数为字符串时，JavaScript 引擎会解析其中的 `$$`、`$&`、`` $` ``、`$'` 特殊替换模式。
- **后果**：一旦被保护的内联代码、正则或 Shell 脚本中包含 `$&`、`$'` 或 `$$`（在前端/脚本工程中极其常见），还原占位符时会把占位符自身或前后文整段复制塞入代码框，造成译文代码错乱。
- **修复方式**：必须使用函数作为第二个参数：`out.replace(new RegExp(\`__AGY_PH_\${i}__\`, 'g'), () => placeholders[i])`。

### 缺陷 4：`maskCodeAndPathsForTranslation` 漏掩码 URL 与斜杠 Windows 路径
- **位置**：`src/agy-enhance-loader.js:200-215`
- **根因**：`isAlreadyChineseParagraph` 中移除了 `https?:\/\/\S+`，但 `maskCodeAndPathsForTranslation` 完全忘记掩码裸 URL；同时路径正则 `/[A-Za-z]:\\[^\s\`"'<>()]+|\/(?:Users|var|tmp|etc|home|opt|src|dist|tests)\/[^\s\`"'<>()]+/g` 仅匹配反斜杠 `C:\...` 和固定前缀的 Unix 路径，漏掉了 `C:/...`、`D:/...` 正斜杠路径、相对路径（`./src/...`）以及 `/Applications`、`/mnt` 等路径。

### 缺陷 5：PR 描述与 CHANGELOG 声称的功能在代码中未实现（名不副实）
- **位置**：`CHANGELOG.md:14,19`、PR Summary 与 `src/agy-enhance-loader.js`
- **具体不符项**：
  1. **“网络受限或离线时无缝回退本地学术/工程词典（Fallback 字典即时兜底）”**：代码中完全不存在针对思考段落的本地翻译词典或回退函数；当 `GetModelResponse` 失败或离线时，仅设置 `lastThinkingTransFailTs = Date.now()` 并原样保留英文段落（`isZh: false`）。
  2. **“提取用户指令账本中的路径/文件名约束，与近期写操作进行实时比对”**：`evaluateMissionDrift`（`src/agy-enhance-loader.js:872-953`）根本没有从用户指令账本中提取路径或文件名，仅做了一个写死的“只读关键词”正则匹配。
  3. **“未处于用户手动固定态（`data-agy-user-open="1"`）”**：代码实际使用的属性名为 `data-agy-user-expanded="1"`，文档属性名与实现不一致；此外版本号在文档中写为 `v3.5.0`，而 `VERSION` 常量仍为 `'3.6.0-macos-merged'`，`BUILD_STAMP` 又写为 `'20261102-v3.7-hud-think'`，三处版本标识互不统一。

### 缺陷 6：`thinkingTailTimer` 采用全局单例定时器，多思考块并发时互相覆盖
- **位置**：`src/agy-enhance-loader.js:177, 458-466`
- **根因**：`thinkingTailStateByUid` 虽然按思考块 `uid` 存了各自的 `{ text, changedAt }`，但防抖定时器 `thinkingTailTimer` 却是全局唯一下标，每次任意思考块尾段变化都会 `clearTimeout(thinkingTailTimer)`。
- **后果**：当同一视图存在多个未封口思考块或快速切换会话时，后一个思考块的尾段更新会不断重置前一个思考块的计时器。

### 缺陷 7：`evaluateMissionDrift` 只读正则极易在复合指令下误报或被绕过
- **位置**：`src/agy-enhance-loader.js:877-913`
- **根因**：
  ```javascript
  const isReadOnlyInstruction =
    /(?:只读|仅排查|只排查|不要改代码|不要修改|不修改任何文件|先不要改|先调起.*审核|只分析|只讨论)/.test(latestUserText) &&
    !/^(?:可以|认可|同意|开始施工|正式开工)/.test(latestUserText.trim());
  ```
- **后果**：
  - **高频误报**：用户输入“**先不要改** A 模块，直接修复 B 文件”、“不要修改配置文件以外的代码”、“这次**不只分析**，请直接落盘修复”均会命中该正则，导致正常修改代码时底部弹出橙色 `⚠️ 疑似走偏` 告警；
  - **轻易漏报**：若用户说“**可以**帮我先只读排查一下吗，千万不要修改代码”，因为以“可以”开头，整条只读检测直接被短路关闭；且通过 `run_command`（Shell 重定向/脚本）修改文件不会被识别为写工具。

### 缺陷 8：`computeTitleOverlapRatio` 单字集合交集算法会把反义阶段或不同动作误判为同一阶段
- **位置**：`src/agy-enhance-loader.js:756-768`
- **根因**：将两个标题拆为单字 `Set`，计算 `common / Math.min(set1.size, set2.size)`，阈值为 `0.65`（冻结判重）和 `0.58`（沿用旧标题）。
- **后果**：
  - `"完成系统架构设计"` 与 `"未完成系统架构设计"` 的重合率为 `8 / 8 = 1.0`；
  - `"读取配置文件"` 与 `"写入配置文件"` 的重合率为 `5 / 6 = 0.833 >= 0.65`；
  - `"排查前端接口缺陷"` 与 `"修复前端接口缺陷"` 的重合率为 `6 / 8 = 0.75 >= 0.65`；
  在 `reconcileBlueprintTasks` 中，一旦前序“排查前端接口缺陷”被冻结为 `done`，后续真正的“修复前端接口缺陷”阶段会因 `computeTitleOverlapRatio >= 0.65`（第 799 行 `if (dupFrozen) continue;`）**被直接当成重复阶段静默丢弃**！

### 缺陷 9：`reconcileBlueprintTasks` 冻结机制与 `slice(0, 5)` 截断会导致蓝图假死
- **位置**：`src/agy-enhance-loader.js:780-866`
- **根因**：
  1. 只要前序阶段处于非末尾位置且状态为 `done`，就会立刻进入 `frozenDone` 数组，同一用户轮次内永远排在 `merged` 最前面；
  2. 第 839 行执行 `merged = merged.slice(0, 5)`。一旦长任务在同一轮内累积了 5 个 `frozenDone` 阶段，后续所有新增的 `active`（进行中）和 `todo`（待推进）阶段都会被 `.slice(0, 5)` 全部截掉，导致模型明明还在干活，看板上却永远只显示前 5 个旧阶段；
  3. 第 857 行 `if (!isRunningNow && turnCompletedQuietly && seenActive)` 在没有 `todo` 阶段时会自动把 `active` 强制改成 `done`，但模型单轮静默停顿（如向用户提问或阶段汇报）并不等于整个阶段已完成。

---

## 四、 可选分支（`optional/hud-thinking-stream`）后续演进与整改路线图

若作者希望后续将改进版合入 `optional/hud-thinking-stream` 可选增强分支，建议按以下清单提交后续修订 PR：

1. **修复开关语义与默认策略（必改）**
   - 在 `collectLiveSessionMissionState()` 调用 `triggerLlmMissionSummarization()` 前增加 `globalPrefs.missionHud !== false` 守卫；当用户在设置面板关闭 `missionHud` 或 `translateThinking` 时，立即调用对应 `AbortController.abort()` 停止在途 RPC。
   - 将「思考过程流式自动撑开」与「思考过程点击展开时中译」解耦：建议默认保持折叠（不自动撑开造成页面跳动），仅在用户点击展开思考块、或显式开启“流式自动展开思考”子选项时才发起段落翻译。
2. **修复占位符还原与掩码边界（必改）**
   - 将 `unmaskCodeAndPaths` 中的 `.replace(regex, placeholders[i])` 改为 `.replace(regex, () => placeholders[i])`；
   - 在 `maskCodeAndPathsForTranslation` 中补齐 `https?:\/\/\S+` URL 掩码、正斜杠盘符路径 `([A-Za-z]:\/[^\s\`"'<>()]+)` 与常见相对路径掩码。
3. **修复 DOM 增量更新与原生展开同步（必改）**
   - `renderThinkingChinesePanel` 按段落索引增量更新 `<p>` 节点，尾段更新仅改写最后一个 `.tail-raw` 节点，严禁每次尾段字符变化都清空重建整个 `contentBox`；
   - 将全局单例 `thinkingTailTimer` 放入 `thinkingTailStateByUid` 每个 `uid` 条目内部独立管理，并在会话切换或节点卸载时清理过期条目。
4. **重写阶段匹配与冻结淘汰逻辑（必改）**
   - 废弃单纯按单字集合交集 `computeTitleOverlapRatio >= 0.65` 丢弃新阶段的做法，对“排查/分析/读取”与“修改/实现/验证”等不同阶段动词建立区分度，防止后续施工阶段被前序排查阶段误吞；
   - 当阶段总数超过 5 项且需要裁剪时，保留最新的 `active` / `todo` 阶段并折叠早期 `done` 阶段（而非简单 `.slice(0, 5)` 砍掉尾部进行中阶段）。
5. **诚实校准文档描述与走偏检测（必改）**
   - 移除 `CHANGELOG.md` 与文档中未实现的“本地学术/工程词典兜底”及“路径/文件约束比对”表述，统一版本号标识；
   - 将 `evaluateMissionDrift` 中的 `✓ 方向一致` 降级为中性状态或仅在触发明确连续报错阈值时提示，重构或移除易误报的粗糙只读正则。
