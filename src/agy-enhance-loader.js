/**
 * Antigravity Controlled Host Enhancement, Deep Chinese Localization & Streaming Mission HUD Runtime (v3.1.1)
 * - True async readyPromise & same-version idempotent reuse.
 * - Full Mode C consumer suspension (disconnects observer, timers, and removes non-wakeup DOM hosts).
 * - MutationObserver strictly filters out all [id^="agy-"] self-mutations (zero ring self-trigger loop).
 * - True multi-page generatorMetadata pagination (reachedTail only on batch.length === 0) + global concurrency cap.
 * - Native CortexStepStatus enum mapping (DONE=3, INVALID=4, CANCELED=6, ERROR=7, INTERRUPTED=12) via stepsUpdate.steps.
 * - Native Settings integration (Settings -> Appearance: Interface Language & Widescreen Academic Typography).
 */
(function () {
  const VERSION = '3.1.1';

  if (
    globalThis.__AGY_V3__ &&
    globalThis.__AGY_V3__.version === VERSION &&
    typeof globalThis.__AGY_V3__.isDisposed === 'function' &&
    !globalThis.__AGY_V3__.isDisposed() &&
    globalThis.__AGY_V3__.readyPromise
  ) {
    return globalThis.__AGY_V3__.readyPromise;
  }

  if (globalThis.__AGY_V3__ && typeof globalThis.__AGY_V3__.dispose === 'function') {
    try {
      globalThis.__AGY_V3__.dispose();
    } catch (e) {}
  }

  let disposed = false;
  let consumersActive = false;
  const instanceId = Date.now() + '-' + Math.random().toString(36).slice(2, 8);
  let currentLsGeneration = 1;

  // ---------------------------------------------------------------------------
  // 1. Bridge & Preferences State
  // ---------------------------------------------------------------------------
  const bridge = globalThis.__AGY_BRIDGE__ || null;

  const DEFAULT_GLOBAL_PREFS = {
    enabled: true,
    theme: 'native',
    lang: 'zh-std',
    widescreen: true
  };

  let localRevision = 0;
  let globalPrefs = Object.assign({}, DEFAULT_GLOBAL_PREFS);

  const OFFICIAL_HIDDEN_FLAGS = {
    'enable-trajectory-debug-tools': true,
    'enableRawErrorDetails': true,
    'enableCompactionNotification': true,
    'enable-developer-mode': true,
    'enable-conversation-selection-quoting': true,
    'enable-conversation-forking': true,
    'enable-fork-at-historical-step': true,
    'enable-fork-in-new-worktree': true,
    'enable-conversation-navigator': true,
    'enable-input-draft-persistence': true,
    'enable-input-autocomplete': true,
    'enable-sidebar-unread-dot': true,
    'enable-conversation-groups': true,
    'enable-split-view': true,
    'enable-thought-editing': true,
    'enable-thought-steering-from-selection': true,
    'markdown-table-v2': true,
    'configure-artifact-width': true,
    'enable-pdf-viewer': true,
    'enable-notebook-rendering': true,
    'enable-file-explorer-tab': true,
    'enable-code-search-palette': true,
    'enable-git-vcs-ui': true,
    'enable-permissions-selector': true,
    'enable-auto-permission-preset': true,
    'enable-resume-conversations-from-restart': true,
    'enable-conversation-search-v2': true,
    'context-window-selector': true
  };

  async function runOneTimeLegacyFlagsReset() {
    try {
      if (window.__APP_CONFIG__) {
        window.__APP_CONFIG__.devMode = true;
      }
      const rawFlags = window.localStorage.getItem('jetski.developer.customFlagOverrides');
      let existing = {};
      if (rawFlags) {
        try { existing = JSON.parse(rawFlags) || {}; } catch (e) { existing = {}; }
      }
      const merged = Object.assign({}, OFFICIAL_HIDDEN_FLAGS, existing, {
        'context-window-selector': true
      });
      const mergedStr = JSON.stringify(merged);
      if (rawFlags !== mergedStr) {
        window.localStorage.setItem('jetski.developer.customFlagOverrides', mergedStr);
        try {
          window.dispatchEvent(new StorageEvent('storage', {
            key: 'jetski.developer.customFlagOverrides',
            newValue: mergedStr,
            oldValue: rawFlags,
            storageArea: window.localStorage
          }));
        } catch (e) {}
      }

      const rawEnv = window.localStorage.getItem('jetski.developer.featureEnvironmentOverride');
      let needEnv = true;
      if (rawEnv) {
        try {
          const parsedEnv = JSON.parse(rawEnv);
          if (parsedEnv && parsedEnv.enabled === true && parsedEnv.userCohort === 'google') {
            needEnv = false;
          }
        } catch (e) {}
      }
      if (needEnv) {
        const envStr = JSON.stringify({ enabled: true, userCohort: 'google' });
        window.localStorage.setItem('jetski.developer.featureEnvironmentOverride', envStr);
        try {
          window.dispatchEvent(new StorageEvent('storage', {
            key: 'jetski.developer.featureEnvironmentOverride',
            newValue: envStr,
            oldValue: rawEnv,
            storageArea: window.localStorage
          }));
        } catch (e) {}
      }
      if (window.localStorage.getItem('jetski.projectV5Migration.regroupCardDismissed.v1') !== 'true') {
        window.localStorage.setItem('jetski.projectV5Migration.regroupCardDismissed.v1', 'true');
      }
    } catch (e) {}
  }

  function sanitizeWorkspaceAndGoogleCorpClutter() {
    try {
      if (window.localStorage.getItem('jetski.projectV5Migration.regroupCardDismissed.v1') !== 'true') {
        window.localStorage.setItem('jetski.projectV5Migration.regroupCardDismissed.v1', 'true');
      }
      const rootEl = document.getElementById('root') || document.body?.firstElementChild;
      if (!rootEl) return;
      const rk = Object.keys(rootEl).find((k) => k.startsWith('__reactContainer') || k.startsWith('__reactFiber'));
      if (!rk) return;

      let fm = null;
      let reduxStore = null;
      const q = [rootEl[rk]];
      const seen = new Set();
      while (q.length > 0 && (!fm || !reduxStore)) {
        const cur = q.shift();
        if (!cur || seen.has(cur)) continue;
        seen.add(cur);
        const val = cur.memoizedProps?.value;
        if (val && typeof val === 'object') {
          if (!fm && typeof val.get === 'function' && val.config && val.runtimeEnvironment) {
            fm = val;
          }
          if (!reduxStore) {
            if (val.store && typeof val.store.getState === 'function' && typeof val.store.dispatch === 'function') {
              reduxStore = val.store;
            } else if (typeof val.getState === 'function' && typeof val.dispatch === 'function') {
              reduxStore = val;
            }
          }
        }
        if (cur.child) q.push(cur.child);
        if (cur.sibling) q.push(cur.sibling);
      }

      if (fm) {
        const pc = fm.get('projectsCustomizations');
        if (pc && typeof pc === 'object') {
          pc.magicWorkspaceEnabled = false;
          pc.showHeadCustomizationsInMainScreen = false;
          pc.enableGoogleSettingsTab = false;
          pc.openNewConversation = ({ goToNewConversation: a, sectionId: c }) => {
            if (typeof a === 'function') a(c);
          };
          if (typeof pc.sidebarDropdownEntrypoints === 'function' && !pc.sidebarDropdownEntrypoints.__agyCleaned) {
            const origEntrypoints = pc.sidebarDropdownEntrypoints;
            const cleanedFn = function (handlers) {
              const items = origEntrypoints(handlers) || [];
              return items.filter(
                (it) =>
                  it &&
                  !it.createsVcsWorkspace &&
                  !['New Workspace', 'CitC Workspace', 'Cog Workspace', 'ABFS Workspace (Alpha)', '新建工作区', '新建云工作区'].includes(
                    it.label
                  )
              );
            };
            cleanedFn.__agyCleaned = true;
            pc.sidebarDropdownEntrypoints = cleanedFn;
          }
        }
        const vcs = fm.get('vcsWorkspaceCreation');
        if (vcs && typeof vcs === 'object') {
          vcs.entrypoints = false;
          vcs.worktrees = false;
        }
        const mig = fm.get('projectV5Migration');
        if (mig && typeof mig === 'object') {
          mig.enabled = false;
          mig.regroupCard = null;
        }
        const pmf = fm.get('projectManagementFeature');
        if (pmf) {
          const prov = pmf.projectsStateProvider || pmf._projectsStateProvider;
          const listProv = pmf.projectListStateProvider || pmf._projectListStateProvider;
          const allProjs = prov?.getState?.() || [];
          const tempIds = new Set();
          const keptProjs = allProjs.filter((entry) => {
            const nm = String(entry?.project?.name || '');
            const isTemp = /AppData[\\/]Local[\\/]Temp[\\/](?:agy_|ocr_)/i.test(nm);
            if (isTemp && entry?.project?.id) tempIds.add(entry.project.id);
            return !isTemp;
          });
          if (tempIds.size > 0) {
            for (const tid of tempIds) {
              try {
                if (typeof pmf.deleteProject === 'function') {
                  pmf.deleteProject(tid).catch?.(() => {});
                }
              } catch (e) {}
            }
            try {
              prov?.setState?.(keptProjs);
              const curList = listProv?.getState?.();
              if (curList && Array.isArray(curList.projectIds)) {
                listProv?.setState?.({
                  ...curList,
                  projectIds: curList.projectIds.filter((id) => !tempIds.has(id))
                });
              }
            } catch (e) {}
          }
        }
      }

      if (reduxStore) {
        const st = reduxStore.getState();
        if (st?.newConvoResourceSelector?.isMagicWorkspaceSelected === true) {
          reduxStore.dispatch({
            type: 'newConvoResourceSelector/setIsMagicWorkspaceSelected',
            payload: false
          });
        }
      }
    } catch (e) {}
  }

  function loadLocalFallbackPrefs() {
    try {
      const gRaw = window.localStorage.getItem('agy.v3.globalPrefs');
      if (gRaw) Object.assign(globalPrefs, JSON.parse(gRaw));
    } catch (e) {}
  }

  let prefUpdateChain = Promise.resolve();

  function patchGlobalPrefs(patch) {
    Object.assign(globalPrefs, patch);
    try {
      window.localStorage.setItem('agy.v3.globalPrefs', JSON.stringify(globalPrefs));
    } catch (e) {}
    if (bridge && typeof bridge.updatePreferences === 'function') {
      prefUpdateChain = prefUpdateChain
        .then(() => bridge.updatePreferences({ globalPatch: patch, expectedRevision: localRevision }))
        .then((res) => {
          if (!res) return;
          if (res.conflict && typeof res.revision === 'number') {
            if (res.revision > localRevision) {
              localRevision = res.revision;
              if (res.global) {
                Object.assign(globalPrefs, res.global);
                applyAllStates();
              }
            }
          } else if (typeof res.revision === 'number' && res.revision > localRevision) {
            localRevision = res.revision;
          }
        })
        .catch(() => {});
    }
  }

  // ---------------------------------------------------------------------------
  // 2. Enumerable & Reversible Localization Engine (#2, #9, #29)
  // ---------------------------------------------------------------------------
  const DICT_STD = {
    'File': '文件',
    'Edit': '编辑',
    'Selection': '选区',
    'View': '视图',
    'Go': '前往',
    'Run': '运行',
    'Terminal': '终端',
    'Window': '窗口',
    'Help': '帮助',
    'New Conversation': '新建会话',
    'New Chat': '新建对话',
    'Conversation History': '历史会话检索',
    'Automations': '自动化任务',
    'Pinned Conversations': '置顶核心会话',
    'Pinned': '已置顶',
    'Projects': '工作区项目',
    'Conversations': '会话列表',
    'Workspaces': '工作区',
    'Recent Workspaces': '最近工作区',
    'Open Workspace': '打开工作区',
    'Add Workspace': '添加工作区',
    'Review': '审阅变更',
    'Review Changes': '审阅代码变更',
    'Settings': '设置',
    'General': '常规',
    'Appearance': '外观',
    'Permissions': '权限控制',
    'Shortcuts': '快捷键',
    'Notifications': '通知提醒',
    'Customizations': '扩展与规则',
    'Developer': '开发者工具',
    'Account': '账户信息',
    'Browser': '内置浏览器',
    'Editor': '编辑器',
    'App': '应用设置',
    'Provide Feedback': '问题反馈',
    'Search conversations...': '搜索历史会话...',
    'Search conversations': '搜索会话',
    'Search flags': '搜索隐藏特性开关',
    'Custom Flags': '特性开关 (Custom Flags)',
    'Feature Environment Override': '运行环境模拟覆盖',
    'Workspace URI Switcher': '工作区 URI 切换',
    'Reset overrides': '重置覆盖项',
    'Subagents': '子智能体 (Subagents)',
    'Child Subagents': '派生子智能体',
    'Parent Conversation': '父级会话',
    'Root Conversation': '根会话',
    'Background Tasks': '后台任务',
    'Running': '运行中',
    'Idle': '空闲待命',
    'Waiting': '等待确认',
    'Completed': '已完成',
    'Canceled': '已取消',
    'Error': '异常报错',
    'Retry': '重试',
    'Continue': '继续执行',
    'Stop': '停止',
    'Cancel': '取消',
    'Approve': '批准执行',
    'Reject': '拒绝',
    'Proceed': '批准继续',
    'Submit': '提交',
    'Skip': '跳过',
    'Copy': '复制',
    'Copied': '已复制',
    'Copy debug info': '复制调试信息',
    'Reload Window': '重载窗口',
    'Toggle DevTools': '开发者控制台',
    'Checkpoints': '检查点',
    'New Group': '新建分组',
    'Move to Group': '移至分组',
    'Split View': '分屏视图',
    'File Explorer': '文件资源树',
    'Archive': '归档会话',
    'Pin': '置顶会话',
    'Unpin': '取消置顶',
    'Rename': '重命名',
    'Delete': '删除',
    'Thinking': '思考中',
    'Thought Process': '思考脉络',
    'Analyzing': '正在研读',
    'Analyzed': '已研读',
    'Analyzing Task Log': '正在核查任务日志',
    'Analyzed Task Log': '已核查任务日志',
    'Editing': '正在修改',
    'Edited': '已修改',
    'Canceled edit to': '已取消修改',
    'Creating': '正在创建',
    'Created': '已创建',
    'Canceled creation of': '已取消创建',
    'Deleting': '正在删除',
    'Deleted': '已删除',
    'Canceled deletion of': '已取消删除',
    'Searching': '正在检索',
    'Searched': '已检索',
    'Semantic searching': '正在语义检索',
    'Semantic searched': '已语义检索',
    'Working': '正在执行',
    'Done': '已完成',
    'Starting': '正在启动',
    'Started': '已启动',
    'Output': '输出',
    'Logs': '日志',
    'Errors': '错误',
    'Sources': '参考来源',
    'Page contents': '页面内容',
    'Show more': '展开更多',
    'No background tasks': '暂无后台挂起任务',
    'No artifacts generated': '暂无生成产物',
    'Checking command status': '正在核查命令状态',
    'Checked command status': '已核查命令状态',
    'Reading page': '正在读取网页',
    'Read page': '已读取网页',
    'Created outline': '已提炼页面大纲',
    'Opening URL in Browser': '正在浏览器中打开链接',
    'Opened URL in Browser': '已在浏览器中打开链接',
    'Reading Browser Page in Browser': '正在读取浏览器页面',
    'Read Browser Page in Browser': '已读取浏览器页面',
    'Agent Stopped': '已停止执行',
    'Agent is analyzing videos': '正在分析视频内容',
    // --- AGY 深度汉化词库 (Project / Workspace / Sidebar / Composer / Settings / Customizations / Git / AuxPane) ---
    'New Workspace': '选择工作区',
    'Select Workspace': '选择工作区',
    'Select Project': '选择项目',
    'Search Recent Workspaces': '搜索最近工作区...',
    'Search Projects': '搜索项目...',
    'Search workspaces...': '搜索工作区...',
    'Search projects...': '搜索项目...',
    'Search for conversations...': '搜索会话...',
    'Search for commands...': '搜索命令...',
    'Search file contents...': '搜索文件内容...',
    'Search customizations...': '搜索扩展、技能或规则...',
    'Search MCP servers by name': '按名称搜索 MCP 服务...',
    'Create Project': '创建新项目 (打开文件夹)',
    'New Project': '新建项目',
    'New Remote Project': '新建远程项目',
    'No Project': '无项目 (临时会话)',
    'Outside of Project': '临时会话',
    'Standalone': '临时会话',
    'Project Settings': '项目设置',
    'No matching workspaces': '无匹配的工作区',
    'No matching projects': '无匹配的项目',
    'No matching flags': '无匹配的特性开关',
    'No workspaces found': '未找到工作区',
    'No projects found': '未找到项目',
    'No conversations yet': '暂无会话记录',
    'Create project from scratch': '从零创建项目',
    'Set Project Name': '设置项目名称',
    'Project already exists': '项目已存在',
    'Agent Security Settings': '智能体安全设置',
    'Select a folder.': '选择本地文件夹',
    'Main Agent': '主智能体',
    'Turbo': '极速自动 (Turbo)',
    'Turbo mode': '极速模式 (Turbo)',
    'Learn more about ': '进一步了解 ',
    'Configure allowed commands outside the sandbox.': '配置允许智能体在沙箱外执行的终端命令。',
    'Email': '电子邮箱',
    'Auto (Preview)': '智能自动 (Auto)',
    'Auto (detected)': '自动检测',
    'Default': '默认',
    'Always Ask': '每次询问',
    'Always Proceed': '始终批准',
    'Always Allow': '始终允许',
    'Allow Once': '允许一次',
    'Ask first': '先询问',
    'Always run': '始终运行',
    'Allow once': '仅本次允许',
    'Request Review': '请求审阅',
    'Run all commands without restrictions.': '无限制自动执行所有命令',
    'Automatically decide if commands run.': '自动判断命令是否可直接运行',
    'Ask before sensitive operations.': '执行敏感操作前先询问',
    'Every command requires approval.': '每条命令均需手动批准',
    'Agent always asks for review.': '智能体始终请求审阅',
    'Display Options': '显示与分组设置',
    'Display': '显示设置',
    'Group By': '分组方式',
    'Group By Project': '按项目分组',
    'Group By Workspace': '按工作区分组',
    'Sort Projects': '项目排序',
    'Sort Workspaces': '工作区排序',
    'Sort Conversations': '会话排序',
    'Custom Order': '自定义顺序',
    'Alphabetical': '按名称排序',
    'Date Added': '按创建时间',
    'Recent': '最近活跃',
    'Status': '状态',
    'None': '不分组',
    'Filter': '筛选',
    'Show Subtitles': '显示副标题',
    'Only Unread': '仅看未读',
    'Archived': '已归档',
    'In Progress': '进行中',
    'Needs attention': '需关注',
    'Input required': '等待输入',
    'Ready': '就绪',
    'Blocked': '受阻',
    'Pending': '等待中',
    'Stopped': '已停止',
    'Disabled': '已禁用',
    'Installed': '已安装',
    'Currently installed': '当前已安装',
    'Built In': '系统内置',
    'Global': '全局',
    'Workspace': '工作区',
    'Local': '本地',
    'Custom': '自定义',
    'Unknown': '未知',
    'Pin This Conversation': '置顶此会话',
    'Unpin This Conversation': '取消置顶此会话',
    'Archive This Conversation': '归档此会话',
    'Rename This Conversation': '重命名此会话',
    'Rename Conversation': '重命名会话',
    'Delete Conversation': '删除会话',
    'Mark as Read': '标为已读',
    'Mark as Unread': '标为未读',
    'Mark This Conversation as Read': '将此会话标为已读',
    'Mark This Conversation as Unread': '将此会话标为未读',
    'Mark All as Read': '全部标为已读',
    'Move to New Group': '移至新分组',
    'Remove from Group': '从分组移除',
    'Copy Conversation ID': '复制会话 ID',
    'Copy Trajectory ID': '复制轨迹 ID',
    'Copy Conversation Markdown': '复制会话 Markdown',
    'Open Trajectory Debug View': '打开轨迹调试视图',
    'Fork conversation in this folder': '在此文件夹分叉会话',
    'Fork in Shared Workspace': '在共享工作区分叉会话',
    'Fork in New Workspace': '在新工作区分叉会话',
    'Fork': '分叉会话',
    'Share Conversation (Preview)': '分享会话 (预览)',
    'Share': '分享',
    'Export as PDF': '导出为 PDF',
    'Find in Conversation': '在会话中查找',
    'Find in Pane': '在面板中查找',
    'More Actions': '更多操作',
    'More': '更多',
    'See all': '查看全部',
    'See more results': '查看更多结果',
    'Plugins': '插件',
    'Skills': '技能',
    'Rules': '规则',
    'Skills & Rules': '技能与规则',
    'Custom Agents': '自定义智能体',
    'Agents': '智能体',
    'MCP Servers': 'MCP 服务',
    'MCP servers': 'MCP 服务',
    'MCP Tools': 'MCP 工具',
    'MCP tools': 'MCP 工具',
    'Hooks': '钩子 (Hooks)',
    'Manage Hooks': '管理钩子',
    'Marketplace': '插件市场',
    'UI Extensions': '界面扩展',
    'UI extensions': '界面扩展',
    'Context Providers': '上下文提供器',
    'Add MCP Server': '添加 MCP 服务',
    'Delete MCP Server': '删除 MCP 服务',
    'Delete Skill': '删除技能',
    'Delete Task': '删除任务',
    'Create a Plugin with the Agent': '让智能体协助创建插件',
    'Explore the plugin marketplace': '浏览插件市场',
    'Copy Path': '复制路径',
    'Copy Config File Path': '复制配置文件路径',
    'Default Customizations': '默认扩展与技能',
    'Customize Global Skills': '配置全局技能',
    'Include default customizations, such as default skills.': '包含系统默认扩展与内置技能',
    'Include Jetski Default Customizations': '启用内置默认扩展与技能',
    'Build With Google Plugins': '官方精选插件',
    'Loading customizations...': '正在加载扩展与规则...',
    'Loading installed plugins...': '正在加载已安装插件...',
    'Loading events...': '正在加载事件...',
    'Loading trajectory...': '正在加载轨迹...',
    'Loading apps...': '正在加载应用...',
    'Loading...': '正在加载...',
    'Refreshing...': '正在刷新...',
    'Creating...': '正在创建...',
    'Getting scripts...': '正在获取脚本...',
    'Open Settings': '打开设置',
    'Editor Settings': '编辑器设置',
    'Chat Settings': '对话设置',
    'Advanced': '高级',
    'Advanced Settings': '高级设置',
    'Application': '应用程序',
    'Navigation': '导航',
    'Execution': '执行策略',
    'Environment': '运行环境',
    'Overview': '总览',
    'Tool Permissions': '工具权限',
    'Network Permissions': '网络权限',
    'Global Permissions': '全局权限',
    'Permission Preset': '权限预设',
    'Access rules': '访问规则',
    'Access grants': '授权范围',
    'Terminal Commands': '终端命令',
    'Commands Outside Sandbox': '沙箱外命令',
    'Unsandboxed commands': '非沙箱命令',
    'Read Files': '读取文件',
    'Read files': '读取文件',
    'Write Files': '写入文件',
    'Write files': '写入文件',
    'Read URLs': '读取网页链接',
    'Execute URLs': '访问执行链接',
    'Browser actions': '浏览器操作',
    'Admin escalation': '管理员提权 (UAC)',
    'Administrator access (UAC)': '管理员权限 (UAC)',
    'Agent Non-Workspace File Access': '允许智能体访问工作区外文件',
    'Allows the agent to access files outside of your current workspace.': '允许智能体读写当前工作区目录以外的本机文件。',
    'Terminal Command Auto Execution': '终端命令自动执行策略',
    'Controls whether terminal commands require your approval before running.': '控制终端命令在执行前是否需要经过您的批准。',
    'Controls the actions the agent can take.': '控制智能体可执行的操作与工具权限边界。',
    'Enable Sandbox Mode (Preview)': '启用沙箱隔离模式 (预览)',
    'Restricts agent tools to a secure, isolated local sandbox.': '将智能体工具限制在安全隔离的本地沙箱内运行。',
    'Enable Terminal Sandbox': '启用终端沙箱',
    'Run terminal commands with sandbox restrictions.': '在沙箱安全限制下运行终端命令。',
    'Sandbox Allow Network': '允许沙箱内访问网络',
    'Allow sandboxed commands to make network requests.': '允许沙箱内的命令发起网络请求。',
    'Allow List Terminal Commands': '终端命令白名单',
    'The agent auto-executes commands matched by an allow list entry.': '匹配白名单的终端命令将由智能体自动执行。',
    'Deny List Terminal Commands': '终端命令黑名单',
    'The agent asks for permission before executing commands matched by a deny list entry.': '匹配黑名单的终端命令在执行前必须请求您的批准。',
    'Agent Auto-Fix Lints': '自动修复代码检查 (Lint) 错误',
    'Give the agent awareness of lint errors created by its edits so it can fix them without explicit prompting.': '让智能体自动感知修改引入的语法/Lint错误并主动修复。',
    'Queued Messages': '排队消息发送时机',
    'Configure when follow-up messages are sent.': '配置您在执行中途追加的消息何时发送给智能体。',
    'Explain and Fix in Current Conversation': '在当前会话中解释并修复',
    'Command Setup Script': '命令前置初始化脚本',
    'A shell setup script run before every command the agent executes.': '在智能体执行每条命令前自动运行的初始化脚本。',
    'Enable Shell Integration': '启用终端 Shell 集成',
    'Enable Sounds for Agent': '启用智能体提示音',
    'Play a sound when your action is needed or the agent finishes.': '当需要您确认或智能体完成任务时播放提示音。',
    'Volume': '提示音音量',
    'Enable Notifications for Agent': '启用智能体系统通知',
    'Enable Notifications': '启用系统通知',
    'Show system notifications when your action is needed or the agent finishes.': '当需要您确认或智能体完成任务时弹出系统通知。',
    'Auto-Expand Changes Overview': '自动展开代码变更总览栏',
    'Automatically expand the Changes Overview toolbar when the agent finishes generating a response.': '当智能体完成回复时自动展开文件变更总览工具栏。',
    'Auto-Open Edited Files': '后台自动打开被修改文件',
    'Open files in the background if the agent creates or edits them': '当智能体创建或修改文件时在后台自动打开预览。',
    'Open Agent on Reload': '重载窗口时打开智能体面板',
    'Open the agent panel on window reload': '窗口重新加载后自动打开智能体主面板。',
    'Verbose Agent Chat': '详尽对话模式',
    'Display and preserve intermediate thinking steps.': '在对话流中完整显示并保留中间思考步骤。',
    'Custom Context Menus': '自定义快捷右键菜单',
    'Replace the default browser right-click menu with quick actions.': '使用快捷操作菜单替代默认右键菜单。',
    'Conversation Width': '会话正文宽度',
    'Configure the maximum width of the conversation panel.': '配置会话阅读区的最大显示宽度。',
    'Markdown Artifact Width': 'Markdown 产物默认宽度',
    'Configure the default width of markdown artifacts.': '配置右侧 Markdown 产物面板的默认宽度。',
    'Suggestions in Editor': '编辑器内联建议',
    'Show suggestions when typing in the editor': '在代码编辑器中输入时显示智能补全建议。',
    'Tab to Jump': 'Tab 键预测跳转',
    'Predict the location of your next edit and navigate you there with a tab keypress.': '预测您下一处修改位置，按 Tab 键一键跳转。',
    'Tab to Import': 'Tab 键快捷导入依赖',
    'Quickly add and update imports with a tab keypress.': '按 Tab 键快速补全或更新模块导入语句。',
    'Tab Speed': 'Tab 补全响应速度',
    'Set the speed of tab suggestions': '设置 Tab 智能补全建议的触发速度。',
    'Highlight After Accept': '采纳补全后高亮文本',
    'Highlight newly inserted text after accepting a Tab completion.': '按 Tab 采纳补全后短暂高亮新插入的代码。',
    'Tab Gitignore Access': '允许 Tab 补全访问 Gitignore 文件',
    'Enable Browser Tools': '启用内置浏览器工具',
    'Browser Javascript Execution Policy': '浏览器 JavaScript 执行策略',
    'Controls whether the agent can run custom JavaScript to automate complex browser actions.': '控制智能体能否在内置浏览器中执行自定义 JS 脚本。',
    'Chrome Binary Path': 'Chrome 浏览器程序路径',
    'Browser User Profile Path': '浏览器用户配置目录',
    'Browser CDP Port': '浏览器 CDP 调试端口',
    'Show Selection Actions': '显示选区快捷操作浮条',
    'Prevent Sleep': '任务运行期间阻止电脑休眠',
    'Prevent the computer from sleeping while the app is running.': '在应用运行期间阻止系统进入休眠状态。',
    'Keep In Menu Bar': '关闭窗口后驻留托盘菜单栏',
    'Keep the app accessible from the menu bar and running in the background when all windows are closed.': '所有窗口关闭后仍驻留系统托盘并在后台保持运行。',
    'Automatic Check for Updates': '自动检查更新',
    'Confirm Window Reload': '重载窗口前弹窗确认',
    'Enable Demo Mode (Beta)': '启用演示模式 (Beta)',
    'Strict Mode': '严格安全模式',
    'Remote Control': '远程控制',
    'Enable Remote Control': '启用远程控制',
    'Open in Remote Control': '在远程控制中打开',
    'Device Name': '设备名称',
    'Nickname': '本机昵称',
    'Model Quota': '模型额度',
    'Quota': '额度',
    'No quota information available.': '暂无模型额度信息',
    'Enable Telemetry': '启用遥测统计',
    'Telemetry': '遥测统计',
    'App version': '应用版本',
    'Version': '版本',
    'Check for Updates': '检查更新',
    'Update Available': '发现新版本',
    'Command Palette': '命令面板',
    'Open Command Palette': '打开命令面板',
    'Toggle Fullscreen': '切换全屏',
    'Zoom In': '放大视图',
    'Zoom Out': '缩小视图',
    'Reset Zoom': '重置缩放',
    'Toggle Sidebar': '切换侧边栏',
    'Toggle Terminal': '切换终端面板',
    'Toggle Auxiliary Pane': '切换右侧辅助栏',
    'Split Right': '向右分屏',
    'Split Down': '向下分屏',
    'Split Conversation Vertically': '左右分屏会话',
    'Split Conversation Horizontally': '上下分屏会话',
    'Remove from Split': '退出分屏',
    'Replace with New': '替换为新会话',
    'Agent Edits': '智能体修改',
    'Modified Files': '已修改文件',
    'Files modified by the agent in this conversation': '本轮会话中由智能体修改的文件',
    'Files Changed': '变更文件',
    'Staged Changes': '已暂存变更',
    'Branch Changes': '分支变更',
    'Uncommitted': '未提交变更',
    'Changes': '变更',
    'Files': '文件',
    'Artifacts': '生成产物',
    'Documents': '文档',
    'Uploads': '上传文件',
    'No documents': '暂无文档',
    'No uploads': '暂无上传文件',
    'Code Search': '代码检索',
    'Open in Code Search': '在代码检索中打开',
    'Recent Files': '最近文件',
    'Recent files': '最近文件',
    'Open in Preview': '在预览中打开',
    'Open in Preview Tab': '在预览标签页打开',
    'Open in Built-in Browser': '在内置浏览器中打开',
    'Open in External Browser': '在外部浏览器中打开',
    'Open in external browser': '在外部浏览器中打开',
    'Open in side pane': '在侧边栏打开',
    'Open File': '打开文件',
    'Open Folder': '打开文件夹',
    'Collapse All Folders': '折叠全部文件夹',
    'Expand All Folders': '展开全部文件夹',
    'Preview': '预览',
    'Preview unavailable': '预览暂不可用',
    'Accept Step': '接受此步',
    'Reject Step': '拒绝此步',
    'Undo to Here': '撤回至此处',
    'Confirm Undo': '确认撤回',
    'Revert file changes': '同时还原文件修改',
    'Copy Message': '复制消息',
    'Copy command': '复制命令',
    'Click to copy full command': '点击复制完整命令',
    'Copy error to clipboard': '复制错误信息',
    'Capture Screenshot': '截取网页快照',
    'Capture Console Logs': '抓取控制台日志',
    'Add to Chat': '添加到对话',
    'Quote Selection': '引用选中内容',
    'Quote': '引用',
    'Comment on Selection': '批注选中内容',
    'Add inline comment': '添加行内批注',
    'Comments': '批注',
    'Commit': '提交 (Commit)',
    'Amend': '追加提交 (Amend)',
    'Push': '推送 (Push)',
    'Stage change': '暂存变更',
    'Unstage change': '取消暂存',
    'Discard': '放弃修改',
    'Discard unstaged changes': '放弃未暂存的修改',
    'Include unstaged changes': '包含未暂存修改',
    'Select branch': '选择分支',
    'New Worktree': '新建工作树',
    'Configure Branches': '配置分支',
    'Back': '返回',
    'Close': '关闭',
    'Dismiss': '忽略',
    'Create': '创建',
    'New': '新建',
    'Open': '打开',
    'Refresh': '刷新',
    'Reload': '重新加载',
    'Restore': '恢复',
    'Delete Permanently': '永久删除',
    'Undo': '撤销',
    'Cut': '剪切',
    'Paste': '粘贴',
    'Select All': '全选',
    'OK': '确定',
    'Deny': '拒绝',
    'Connect': '连接',
    'Disconnect': '断开连接',
    'Scheduled Tasks': '定时任务',
    'Hourly': '每小时',
    'Daily': '每天',
    'Weekly': '每周',
    'Runs on this computer': '在本机运行',
    'Runs in the cloud': '在云端运行',
    'On this computer': '本机环境',
    'Local Windows environment': '本地 Windows 环境',
    // --- 设置全分页、按键展开栏与内置技能深度汉化扩展 ---
    'Models': '模型与额度',
    'Models & Usage': '模型与额度用量',
    'Manage your model quota and credits.': '管理您的模型调用额度与 AI 算力积分。',
    'Plan': '当前订阅方案',
    'Your Plan:': '当前订阅方案：',
    'You are subscribed to the best Google AI plan.': '您已订阅最高级别的 Google AI 旗舰方案。',
    'Model Credits': '模型算力积分',
    'Enable AI Credit Overages': '启用 AI 积分超额续用',
    "When toggled on, Antigravity will use your AI credits to fulfill model requests once you're out of model quota. Antigravity will always use your model quota first before using AI credits.": '开启后，当常规模型额度用尽时将自动消耗 AI 积分继续响应请求（系统始终优先消耗常规额度）。',
    'Gemini Models': 'Gemini 系列模型',
    'Claude and GPT models': 'Claude 与 GPT 系列模型',
    'Weekly Limit Remaining': '本周剩余可用额度',
    'Five Hour Limit Remaining': '5 小时窗口剩余额度',
    'Give Feedback': '问题反馈',
    'Show All': '显示全部',
    'Not in Project': '未归属项目 (临时会话)',
    'Sign Out': '退出登录',
    'Not Signed In': '未登录',
    'Manage your plan, credentials, and general preferences.': '管理您的订阅方案、登录凭据与全局偏好设置。',
    'When toggled on, Antigravity collects usage data to help Google enhance performance and features.': '开启后将收集匿名使用数据以协助优化性能与功能体验。',
    'Marketing Emails': '产品资讯邮件',
    'Receive product updates, tips, and promotions from Google Antigravity via email.': '通过电子邮件接收版本更新、使用技巧与动态通知。',
    'Configure agent execution, queued message delivery, and permissions.': '配置智能体执行策略、排队消息发送时机与安全权限。',
    'Configure global allowed and denied resource permissions.': '配置全局允许与禁止访问的文件、命令及网络资源规则。',
    'Learn more': '了解更多',
    'Modified in': '已自定义覆盖于',
    'projects': '个项目',
    'Modify permissions for file, terminal, and MCP tools.': '配置文件读写、终端命令与 MCP 工具的细粒度访问权限。',
    'Network Access Rules': '网络访问规则',
    'File Access Rules': '文件访问规则',
    'Configure allowed and denied URLs for reading.': '配置允许或禁止智能体读取的网页链接与域名规则。',
    'Agent Behavior': '智能体行为',
    'Plan Review Policy': '方案审阅策略',
    'Review Policy': '产物审阅策略',
    'Whether the agent asks you to review its documents.': '控制智能体在生成规划方案与文档产物时是否请求您审阅。',
    'Specifies the agent\'s behavior when asking for review on artifacts, which are documents it creates to enable a richer conversation experience.': '控制智能体在生成规划方案与文档产物时是否请求您审阅。',
    'Type': '在输入框键入',
    'and select': '并选择',
    'to have the agent generate a plan.': '即可让智能体先行生成实施方案。',
    'Queue': '排队等待本轮结束',
    'Send Immediately': '立即插队发送',
    'Interrupt the agent and send immediately.': '立即中断当前生成并发送新消息。',
    'Queue until after the current turn.': '排队等待当前回合执行完毕后自动发送。',
    'Keyboard shortcuts': '键盘快捷键',
    'Configure the browser subagent. It requires': '配置内置浏览器子智能体（需要本机已安装',
    'to be installed.': '浏览器）。',
    'The browser subagent can be invoked by typing /browser in the conversation input box.': '在对话输入框中输入 /browser 即可调起浏览器子智能体。',
    'Browser Actuation Rules': '浏览器交互操作规则',
    'Configure allowed and denied URLs for browser actuation.': '配置允许或禁止浏览器子智能体执行点击、输入等交互操作的网址。',
    'Block all browser JavaScript execution.': '禁止在内置浏览器中执行任何自定义 JavaScript 脚本。',
    'Prompt for approval before running browser scripts.': '执行内置浏览器脚本前先请求您的批准。',
    'Allow full browser script execution without prompting.': '允许直接执行内置浏览器脚本而无需每次询问。',
    'Labs': '实验性功能 (Labs)',
    'Try out early-stage features before they ship. These may be changed or removed at any time. These features are experimental, and we make no guarantees about performance or functionality.': '抢先体验尚未正式发布的实验特性（可能随时调整或移除，不作稳定性承诺）。',
    'Inline Actions (Experimental)': '跨会话行内悬浮通知 (实验)',
    'Inline Actions': '跨会话行内悬浮通知',
    'Show a floating notification card when background conversations need your input. Answer questions, approve commands, and grant permissions without leaving your current conversation. Share feedback at go/inline-actions-feedback.': '当后台会话需要您输入或审批命令时弹出悬浮通知卡，无需离开当前会话即可直接处理。',
    'Autonomous Mode (Experimental)': '全自主无人值守模式 (实验)',
    'Autonomous Mode': '全自主无人值守模式',
    'Adds an Autonomous Mode switch to the modes dropdown below the input box. Turns sent with it on run non-interactively: the agent does not ask questions or request new permissions.': '在输入框下方模式菜单中增加全自主开关；开启后智能体全程非交互运行，不再提问或请求新权限。',
    'Chief of Staff': '幕僚长总控 (Chief of Staff)',
    'A permanent Chief of Staff conversation in the sidebar that manages your other conversations: delegate tasks, check status, and read results from one place.': '在侧边栏常驻幕僚长总控会话，集中管理、委派和查看其他所有会话的进度与结果。',
    "Configure the agent's visual theme and display preferences.": '配置界面视觉主题、对比度与对话版面宽度。',
    'Theme': '界面主题',
    'Themes': '主题外观',
    'Contrast': '视觉对比度',
    'Strong': '高对比度',
    'Light Theme': '浅色主题',
    'Dark Theme': '深色主题',
    'Preset': '配色预设',
    'Default Light': '默认浅色',
    'Default Dark': '默认深色',
    'Background': '背景色',
    'Foreground': '前景色',
    'Accent': '强调色',
    'Narrow': '紧凑窄栏',
    'Wide': '宽屏排版',
    'Fill': '铺满全宽',
    'Project 4K': '精简模式 (Project 4K)',
    'Choose the experience that fits how you work.': '选择适合您工作流的主界面模式。',
    'Choose between the Default and Project 4K experience.': '在完整开发者体验与精简界面模式之间切换。',
    'Configure editor-specific behaviors and shortcuts.': '配置代码编辑器交互行为与选区快捷操作。',
    'Selection Actions': '选区快捷操作',
    'Show "Edit" and "Chat" buttons when selecting text in the editor.': '在编辑器中选中文本时浮现「编辑」与「对话」快捷按钮。',
    'UI Actions': '界面确认行为',
    'Toggle if a confirmation is shown when using the "Reload Window" button.': '点击「重载窗口」前是否弹出二次确认框。',
    'To modify editor settings, open Settings within the editor window.': '如需修改底层代码编辑器设置，请在编辑器窗口内打开设置。',
    'Open Editor Settings': '打开编辑器详细设置',
    'Tab': 'Tab 智能补全',
    'Configure tab completion, suggestions, and navigation behavior.': '配置 Tab 键代码补全、预测跳转与自动导入行为。',
    'Fast': '极速',
    'Slow': '平稳',
    'Jetski Chat': 'Google Chat 机器人联动',
    'Configure a chat bot so you can use Jetski directly from Google Chat.': '配置聊天机器人以在 Google Chat 中直接调用智能体。',
    'For help, visit': '如需帮助，请访问',
    'or join the': '或加入',
    'chat space': '讨论空间',
    'Setup': '配置',
    'Bot Name': '机器人名称',
    'Avatar URL': '头像链接',
    'Setup Jetski Chat': '配置聊天联动',
    'Manage your notification preferences.': '管理系统通知与完成提示音偏好。',
    'Sound': '提示音效',
    'Set the volume of agent sounds.': '调节智能体事件提示音的音量大小。',
    'Configure default behaviors, skills, and MCP servers.': '管理系统默认扩展、全局技能与外部 MCP 服务。',
    'Token Usage': '上下文 Token 占用明细',
    'Loading token usage...': '正在统计 Token 占用...',
    'Loading workspace customizations...': '正在加载工作区扩展与规则...',
    'Loading available plugins...': '正在加载可用插件列表...',
    'The breakdown below shows token usage from customizations like rules, skills, and MCP. If a budget is exceeded, large rules are demoted to path pointers and large customizations are excluded automatically.': '下方列出常驻规则、技能与 MCP 工具占用的上下文 Token 数。超出预算时，大体积规则将自动转为路径指针。',
    'Other Customizations': '其他扩展项',
    'Mcp Tools': 'MCP 工具',
    'Plugin:': '所属插件：',
    'Browse and enable plugins from the Build With Google catalog.': '浏览并启用官方精选插件目录中的扩展组件。',
    'Customize': '前往配置',
    'Manage Antigravity app settings.': '管理桌面客户端系统级运行与更新设置。',
    'Work with local agents from another device.': '通过移动设备或其他电脑远程连接并操控本机智能体。',
    'Notification Settings': '操作系统通知权限',
    "To modify notification settings, open your operating system's system preferences.": '如需调整系统级通知权限，请打开操作系统的通知设置面板。',
    'Open System Preferences': '打开系统通知设置',
    'Windows Subsystem for Linux': 'Windows Linux 子系统 (WSL)',
    'Run the app against a Linux environment. Connecting relaunches the app into the selected WSL distro.': '连接至 WSL Linux 发行版环境运行（点击连接将重启进入所选发行版）。',
    'Feedback Type': '反馈问题类型',
    'Bug Report': '故障报告 (Bug)',
    'Feature Request': '功能建议',
    'Auth and Billing': '账户与订阅计费',
    'Remote Control Issue': '远程控制问题',
    'General Feedback': '常规综合反馈',
    'Description': '详细描述',
    'Please describe the issue in detail. The more actionable your feedback, the quicker our team can address your request. Some helpful information includes:': '请详细描述您遇到的问题或建议，提供以下信息有助于团队更快定位处理：',
    'Steps to reproduce the issue': '复现该问题的具体操作步骤',
    'Expected behavior': '您期望的正常表现',
    'Actual behavior': '实际发生的异常表现',
    'Any error messages': '相关报错提示信息',
    'Any relevant information': '其他有助于排查的上下文信息',
    'Steps to Reproduce': '复现步骤',
    'Attach a screenshot (optional)': '附带屏幕截图 (可选)',
    'Attach Antigravity server logs': '附带语言服务运行日志',
    'Visit': '前往',
    'Legal Help': '法律与合规帮助页',
    'to ask for content changes for legal reasons.': '提交法务相关内容申诉。',
    'Keyboard shortcuts for quick navigation and control.': '用于快速导航、面板切换与会话控制的全局键盘快捷键。',
    'Recommended': '常用推荐',
    'Open Conversation Picker': '打开会话快速切换器',
    'Open File Search': '打开文件快速检索',
    'Focus Input': '聚焦底部输入框',
    'Go Back': '后退',
    'Go Forward': '前进',
    'Go Back in Pane': '在面板内后退',
    'Go Forward in Pane': '在面板内前进',
    'File Picker': '添加文件附件',
    'New Tab': '新建标签页',
    'Close Tab': '关闭标签页',
    'New Window': '新建窗口',
    'New Editor Window': '新建编辑器窗口',
    'Select Previous Conversation': '切换至上一个会话',
    'Select Next Conversation': '切换至下一个会话',
    'Previous Pane Tab': '上一个面板标签页',
    'Next Pane Tab': '下一个面板标签页',
    'Previous Aux Pane Tab': '上一个辅助栏标签',
    'Next Aux Pane Tab': '下一个辅助栏标签',
    'Conversation': '当前会话操作',
    'Toggle Model Selector': '展开/收起模型选择器',
    'Toggle Voice Recording': '开启/停止语音输入',
    'Start Voice Recording': '开始语音输入',
    'Stop Voice Recording': '停止语音输入',
    'Add to Chat/Quote': '引用选中内容至对话',
    'Layout Controls': '窗口与面板布局',
    'Toggle Maximize Auxiliary Pane': '最大化/还原右侧辅助栏',
    'Toggle Overview Panel': '展开/收起变更总览面板',
    'Toggle File Viewer': '切换文件预览面板',
    'Toggle Editor': '切换代码编辑器',
    'Toggle Project Selector': '展开/收起工作区选择器',
    'Toggle Environment Selector': '展开/收起运行环境选择器',
    'Open Workspace Selector': '打开工作区选择器',
    'Open Conversation History': '打开历史会话检索',
    'Open Keyboard Shortcuts': '打开键盘快捷键列表',
    'Open Commit Graph': '打开 Git 提交图谱',
    'Open Launchpad': '打开启动面板',
    'Split Terminal': '分屏终端',
    'New Terminal Tab': '新建终端标签页',
    'Close Terminal Tab': '关闭终端标签页',
    'Equalize Split Panes': '均分分屏面板宽度',
    'Continue Response': '继续生成回复',
    'Cancel Generation': '取消生成',
    'Restart Main Language Server': '重启主语言服务 (Language Server)',
    'Download Diagnostics': '下载诊断日志包',
    'Developer-only tools. These settings are stored locally in this browser and do not affect other users.': '开发者专属调试工具（仅保存在本机窗口本地存储中）。',
    'Override feature environment': '强制覆盖运行环境特征',
    'When enabled, the values below replace the auto-detected OS, host, binary, and user type used for feature gating.': '开启后，下方所选参数将覆盖系统自动检测的操作系统、宿主类型与用户分组以解锁内部特性。',
    'Operating System': '操作系统模拟',
    'Simulate running on a different OS.': '模拟在不同操作系统下运行。',
    'Host Environment': '宿主环境模拟',
    'Simulate a different host (Electron, desktop or mobile Web, Extension, remote control, or installed PWA).': '模拟在不同宿主环境（桌面端、Web 端、IDE 插件等）下运行。',
    'Binary': '客户端形态 (Binary)',
    'Simulate running as a different binary (Hub or the IDE/editor).': '模拟以不同客户端形态（Hub 主台或 IDE 编辑器）运行。',
    'User Type': '用户组别 (Cohort)',
    'Simulate a different user cohort (Google, External, Enterprise).': '模拟不同用户群体（Google 内部、外部用户、企业版）。',
    'Google (internal)': 'Google (内部研发组)',
    'External': '外部正式用户',
    'Enterprise': '企业版用户',
    'forced on': '已强制开启',
    'Add': '添加',
    'Flags come from Mendel experiments and from the host app. Overrides are local to this browser and change how every feature gated on the flag resolves. Some features are only read at startup — reload the window if a change does not take effect.': '特性开关源自实验配置与宿主程序。本机覆盖项将改变所有关联功能的启用状态（部分开关仅在启动时读取，修改后请重载窗口生效）。',
    'Regroup Google3 Chats': '归整 Google3 历史会话',
    'Google3 chats will be regrouped into their workspaces in the sidebar.': '将旧版 Google3 会话按工作区重新归类。',
    'This migration may mess up your settings, chats, and sidebar.': '此迁移仅面向 Google 内部旧数据，外部用户无需操作。',
    'Follow the guide at': '详见内部指南',
    'to back up your data and run the migration.': '备份数据后执行。',
    'Model': '模型选择',
    'High': '高推理',
    'Medium': '中推理',
    'Low': '低推理',
    'Notice': '提示说明',
    'View Usage': '查看额度用量',
    'Last Updated': '按最近更新',
    'Last Prompt': '按最近提问',
    'Auto Pin': '自动置顶活跃会话',
    'Archived Only': '仅显示已归档',
    'Create with Prompt': '通过自然语言创建',
    'No automations configured.': '暂未配置自动化定时任务',
    'Search automations...': '搜索自动化任务...',
    'Ask anything, @ to mention, / for actions': '输入任何问题，键入 @ 引用文件或上下文，键入 / 调用技能指令',
    'Message subagent, @ to mention, / for actions': '向子智能体发送指令，键入 @ 引用上下文，键入 / 调用技能',
    'Load older messages': '加载更早的历史消息',
    'Conversation Log': '会话执行日志',
    'now': '刚刚',
    'Enter bot name (optional)': '输入机器人名称 (可选)',
    'Enter avatar URL (optional)': '输入头像图片链接 (可选)',
    'Describe the bug you encountered...': '请详细描述您遇到的故障现象或改进建议...',
    'Please list the steps to reproduce the issue': '请按顺序列出复现该问题的操作步骤',
    "Turn on a flag you don't have": '输入要强制开启的隐藏特性开关名称...',
    'Enter file or directory path...': '输入文件或目录路径规则...',
    'Enter command (e.g., git, blaze)...': '输入终端命令前缀 (如 git, python)...',
    'Enter tool name or server...': '输入 MCP 工具或服务名称...',
    'Enter URL pattern...': '输入网址或域名匹配规则...',
    'Enter device name...': '输入本机设备显示名称...',
    'Enter authorization code...': '输入授权验证码...',
    'Type absolute path or navigate folders...': '输入绝对路径或浏览选择文件夹...',
    'Outside of folders file access policy': '工作区目录外文件访问策略',
    'Security Preset': '安全策略预设',
    'File Reads': '文件读取规则',
    'File Writes': '文件写入规则',
    'Allow/deny agent read access to specific files or directories.': '允许或禁止智能体读取指定文件或目录。',
    'Allow/deny agent write access to specific files or directories.': '允许或禁止智能体写入修改指定文件或目录。',
    'Allow/deny agent read access to specific URLs or domains.': '允许或禁止智能体读取指定网址或域名。',
    'Allow/deny agent browser actuation access to specific URLs.': '允许或禁止智能体在指定网址上执行浏览器交互操作。',
    'Allow/deny specific terminal commands.': '允许或禁止智能体执行指定终端命令。',
    'Allow/deny agent command execution outside the sandbox.': '允许或禁止智能体在沙箱外执行终端命令。',
    'External tools the agent can call via Model Context Protocol.': '智能体可通过模型上下文协议 (MCP) 调用的外部工具。',
    'Paths the agent can read inside this workspace.': '智能体在当前工作区内可读取的路径。',
    'Paths the agent can modify inside this workspace.': '智能体在当前工作区内可修改的路径。',
    'Terminal commands the agent can execute in this workspace.': '智能体在当前工作区内可执行的终端命令。',
    'Commands the agent can run outside the sandbox in this workspace.': '智能体在当前工作区内可在沙箱外运行的命令。',
    'URLs the agent can read or open in this workspace.': '智能体在当前工作区内可读取或打开的网址。',
    'URLs the agent can actuate on in this workspace.': '智能体在当前工作区内可执行交互操作的网址。',
    // --- 内置与科学计算插件 (Built-in & Science Skills) 专业中文简介 ---
    'Comprehensive guide and reference for the Antigravity Customization System. Use to explain how customizations work, their loading priority, discovery mechanisms, and to guide the creation of skills, rules, plugins, hooks, and MCP servers.': 'Antigravity 扩展定制系统完整指南（涵盖规则、技能、插件、生命周期钩子与 MCP 服务的发现优先级与编写规范）',
    'Provides a comprehensive guide, quick reference, and sitemap for Google Antigravity (AGY), including the Antigravity CLI (agy), Antigravity 2.0, Antigravity IDE, Python SDK, slash commands, keybindings, and customizations (skills, rules, MCP, sidecars). Activate this skill when the user asks questions about how to use, configure, or customize Antigravity, AGY, the agy CLI, the Antigravity IDE, or Antigravity 2.0.': 'Google Antigravity (AGY) 桌面端、CLI、快捷键、斜杠命令与扩展系统综合参考指南',
    'Interactive guide to design and create a scheduled background automation. Use this skill when the user wants to create an automated or recurring scheduled task (e.g. "summarize my emails every morning", "every Monday send me a to-do list"). Also triggered by the /automation slash command.': '设计并创建后台定时自动化任务的交互式指南（支持通过 /automation 指令调起）',
    'How to render rich interactive HTML widgets inline in the chat or as standalone artifacts. Use this skill when you want to show the user diagrams, data visualizations, interactive controls, educational walkthroughs, or any rich visual content beyond plain text and markdown.': '在对话流内或独立产物中渲染交互式 HTML 可视化组件与动态图表',
    'Automatically migrate legacy workflows to modern skills across global and workspace configurations. Scans for existing workflows, creates target SKILL.md files, and safely archives old workflow files.': '将旧版工作流配置自动迁移并归档转换为标准 SKILL.md 技能文件',
    'How to manage and create plugins — namespaced bundles of skills, agents, rules, MCP servers and hooks that install, enable and disable as a single unit. Use this skill when the user wants to enable, disable, install or uninstall a plugin, when they want to create a new plugin, or when a new customization should be packaged into a plugin rather than left loose. Also triggered by the /plugin slash command. Don\'t use for the underlying customization system itself — discovery roots, loading priority, or authoring a standalone skill, agent, rule, hook or MCP server outside a plugin; see the customizations guide skill for those.': '管理、安装、启停与打包 Antigravity 复合插件包（支持通过 /plugin 指令调起）',
    'Discover UI plugin panels relevant to the current task and surface a one-click pill in chat to open (toggle) them in the side pane. Use when a running UI plugin\'s panel would help with what the user is doing, or right after the user enables a new UI plugin pane and a shortcut to open it is handy.': '发现当前任务相关的侧边栏可视化插件面板并在对话中生成一键打开胶囊链接',
    'Guidelines for interacting with GitHub and request permissions from the user when commands fail due to restrictions in the agent environment.': 'GitHub 仓库交互与受限环境下的精细化权限申请指南',
    'Retrieve and analyze AlphaFold predicted structures for a protein. Use when the user provides a specific UniProt Accession ID and wants structural confidence metrics (pLDDT), domain boundary analysis, or disorder assessment. Do not use if the user only has a protein name, gene name, or amino acid sequence — ask for a UniProt ID first.': '按 UniProt ID 检索并分析 AlphaFold 蛋白质预测三维结构与置信度 (pLDDT)',
    'Constructs deep-links and URLs for the AlphaGenome Atlas website. Supports generating single-variant exploration links (1-based VCF-style coordinates), genomic locus views (1-based closed chr:start-end), candidate summary tables, and AlphaGenome reference vs. alternate predictions. Use when visualizing, exploring, charting, or linking genetic variants and genomic loci on the AlphaGenome Atlas, or when asked to inspect, view, or link predictions for a genomic variant. Don\'t use for computing raw AlphaGenome variant predictions directly without Atlas URLs (use alphagenome-single-variant-analysis or alphagenome-variant-impact-score).': '构建 AlphaGenome Atlas 基因组位点与单变异可视化深度链接',
    'Analyzes genetic variant effects on gene expression (RNA-seq), chromatin accessibility (DNASE), histone marks (ChIP), and transcription factors using the AlphaGenome API. Use when the user asks about non-coding variant effects, pathogenicity, clinical significance, disease associations, functional effects, gene expression changes, splicing disruption, or regulatory effects in promoters and enhancers. Also use for resolving biological terms to tissue/cell-type ontologies (UBERON/CL) or analyzing variants in chr:pos:ref>alt format.': '调用 AlphaGenome API 分析基因变异对转录表达、染色质可及性与调控元件的影响',
    'Score, annotate, and analyze the functional impact of genetic variants using AlphaGenome Variant Impact (AVI) scores. Query variants in chr:pos:ref>alt format, annotate VCF/tabular callsets, perform saturation mutagenesis window scans (1-based closed chr:start-end), and extract GENCODE v46 GTF gene/exon/junction coordinates all via the AlphaGenome Atlas API.': '基于 AlphaGenome 变异影响评分 (AVI) 注释分析基因变异功能效应与饱和突变扫描',
    'Provides instructions for installing and using the `android` CLI. The `android` command-line tool is a critical tool for Android development and helps you create new Android projects, run Android apps on devices, manage and interact with Android virtual devices (including screenshots and UI inspection), manage Android SDK components, look up official Android documentation, and discover and install official Android skills.': 'Android CLI 命令行工具安装使用、虚拟设备管理与 SDK 组件开发指南',
    'Interactive 3D biomolecular structure viewer via Mol*. Renders 3D molecular structures from local workspace files (.cif, .mmcif, .pdb) or conversation artifacts, and manages user 3D annotations and highlighted residues. Use when the user asks to visualise a protein or nucleic acid, display a 3D structure, or asks about 3D biomolecular structure annotations and highlighted residues.': '基于 Mol* 的交互式三维生物大分子结构侧边栏查看器与残基高亮批注工具',
    'Query the ChEMBL database for bioactive molecules, drug targets, bioactivity data, approved drugs, and chemical structures. Use when the user asks about compounds, targets, IC50/Ki values, drug mechanisms, or structure searches.': '检索 ChEMBL 生物活性分子、药物靶点、IC50/Ki 活性数据与化学结构数据库',
    'Query ClinicalTrials.gov via APIv2. Use when you want to search for trials by condition, drug, location, status, or phase; retrieve trial details by NCT ID; check eligibility/inclusion criteria; count trials across conditions or time periods; identify a sponsor\'s trial portfolio; find recruiting trials for patient matching.': '通过 ClinicalTrials.gov APIv2 检索全球临床试验方案、入组标准与状态',
    'Use when needing clinical significance, pathogenicity classifications (e.g., Pathogenic, Benign, VUS), clinical evidence rationales, or finding "hard positive" benchmark controls for human genomic variants.': '检索 NCBI ClinVar 人类基因组变异临床致病性分类与证据解读',
    'Instructions for handling API keys and credentials safely, verifying their presence, and prompting the user to add them if missing using a safe protocol.': '外部科学数据库与服务 API 密钥的安全核验与凭据配置规程',
    'Use when you want to look up, map, and search for short genetic variants (SNPs, indels) in NCBI\'s dbSNP database. Resolves between rsIDs, genomic coordinates in VCF format, and HGVS strings. For an rsID, returns variant type, gene associations, clinical significance, allele frequencies, and genomic coordinates (GRCh38).': '检索 NCBI dbSNP 短遗传变异 (SNP/Indel) 并转换 rsID、VCF 坐标与等位基因频率',
    'Query and search the EMBL-EBI Ontology Lookup Service (OLS) for biomedical ontology terms, definitions, and hierarchies across 250+ ontologies (e.g., GO, DOID, HP). Use when the user asks to search for terms, retrieve details, navigate hierarchies (parents, children, ancestors), look up properties and individuals, get autocomplete suggestions, or access ontology metadata and statistics.': '检索 EMBL-EBI 生物医学本体查询服务 (OLS) 的术语定义与层级关系',
    'Query the ENCODE Registry of cis-Regulatory Elements (cCREs) via the SCREEN GraphQL API, or make custom queries to the ENCODE Portal REST API for experiments and files (ChIP-seq peaks, etc.). Use when you want to query regulatory annotations or raw experimental data across human cell types.': '检索 ENCODE 顺式调控元件注册库 (cCREs) 与表观遗传实验数据',
    'Query the Ensembl database to resolve gene, transcript, and protein IDs, fetch genomic or protein sequences, retrieve gene structures (exons), and get variant consequence and effect predictions (VEP). Use this skill as a primary ID translator, genomic sequence database and variant effect prediction tool.': '检索 Ensembl 基因组数据库以转换基因/转录本 ID、获取序列及预测变异效应 (VEP)',
    'Performs 3D structural searches of proteins against various databases (PDB, AlphaFold, CATH, MGnify, etc.) using the Foldseek API. Use ONLY when the user provides a physical 3D coordinate file (.cif, .mmcif, or .pdb) and wants to find structurally similar proteins. Do NOT use if the user only provides a protein sequence, gene name, or UniProt ID.': '调用 Foldseek API 基于三维坐标文件检索结构同源蛋白质',
    'Query the Genome Aggregation Database (gnomAD). Use when determining the rarity or allele frequency of specific genetic variants, retrieving gene constraint metrics (pLI, LOEUF) to assess loss-of-function intolerance, finding variants in a genomic region or gene, or querying structural variants. Don\'t use for analyzing individual patient genomes, tracking somatic mutations in cancer (use COSMIC), or requesting raw sequencing reads (use ENA).': '检索 gnomAD 基因组聚合数据库的群体等位基因频率与基因功能缺失约束指标',
    'Use when you want to retrieve quantitative RNA expression data and variant eQTL information from the GTEx (Genotype-Tissue Expression) Project across 54 non-diseased tissue sites.': '检索 GTEx 数据库在 54 种人体正常组织中的定量 RNA 表达谱与 eQTL 数据',
    'Use when you want to retrieve semi-quantitative protein expression and spatial localisation data from the Human Protein Atlas (HPA).': '检索人类蛋白质图谱 (HPA) 的组织与亚细胞空间定位及表达数据',
    'Identify domains, families, and sites in proteins; find all proteins in a family or sharing a domain; explore species distribution for a domain; annotate genomes with protein families and GO terms. InterPro combines 14 databases (e.g., Pfam, CDD) into one searchable resource. InterPro-N significantly expands annotation and sequence coverage with deep learning. Includes domain architecture (IDA) search.': '检索 InterPro 蛋白质结构域、家族分类、功能位点与 GO 注释',
    'Query the JASPAR database for Transcription Factor (TF) binding profiles. Use when retrieving Position Frequency Matrices (PFMs) or Position Weight Matrices (PWMs) for specific TFs, resolving gene symbols to JASPAR Matrix IDs, or getting TF metadata. Supports multiple output formats (MEME, TRANSFAC, PFM, JASPAR, YAML).': '检索 JASPAR 转录因子结合位点位置频率矩阵 (PFM/PWM)',
    'Search for scientific papers, preprints, and publications on arXiv. Extract metadata, abstracts, and download full-text PDFs or HTML versions of papers. Use when the user asks to find research papers, literature, or specific arXiv IDs.': '检索 arXiv 科学预印本论文元数据、摘要并下载全文 PDF',
    'Browse, filter, and download life sciences, biology, and medical preprints from bioRxiv and medRxiv. Supports fetching paper metadata by DOI, and browsing by date range with category and keyword filters. Keyword filtering is local, so date ranges MUST be narrow (1-4 weeks) with a category to prevent timeouts.': '检索并下载 bioRxiv 与 medRxiv 生命科学及医学预印本论文',
    'Search Europe PMC for scientific literature and download open-access full texts and PDFs. Retrieve full-text XML/plain text by PMCID, get citation lists and bibliography.': '检索 Europe PMC 生物医学文献并获取开放获取全文与引文网络',
    'Query the OpenAlex scholarly database for research papers, authors, institutions, topics, sources, publishers, funders, geo-locations, and keywords. Use when searching academic papers, resolving DOIs, downloading open-access PDFs, finding an author\'s publications, aggregating bibliometric data (citation counts, h-index, impact factor), exploring the research taxonomies, or performing DOI lookups.': '检索 OpenAlex 全球学术文献、学者机构、DOI 与文献计量指标',
    'Retrieve protein and nucleotide sequences from NCBI databases using E-utilities. Supports direct accession lookup, CDS translation, gene+organism search, locus lookup, PubMed-linked sequences, patent protein extraction, and organism+length fallback search. Use when you need to fetch biological sequences by accession, gene name, locus tag, PubMed ID, or patent number.': '通过 NCBI E-utilities 检索并提取蛋白质与核酸序列',
    'Query, search, and download data from the openFDA API for drugs, devices, foods, tobacco, cosmetics, animal and veterinary products, substances, and transparency data. Use for FDA adverse events, recalls, labeling, approvals, shortages, 510(k) clearances, NDC lookups, and any FDA safety or regulatory data query across all 28 API endpoints.': '检索 openFDA 药品审批、不良反应事件、说明书与医疗器械监管数据库',
    'Query Open Targets Platform for target-disease associations, drug target discovery, tractability/safety data, genetics/omics evidence, known drugs, for therapeutic target identification.': '检索 Open Targets 药物靶点与疾病关联证据及成药性评估平台',
    'Use when you want to search for or download experimentally-determined 3D structures for biomolecules (proteins, nucleic acids, bound ligands). Supports searching by sequence similarity, structure similarity, chemical and other attributes. Also use to get metadata about biomolecular structure experiments.': '检索并下载 RCSB PDB 实验测定的生物大分子三维晶体/冷冻电镜结构',
    'Ancient text restoration, attribution, dating, contextualization, and embedding via Aeneas (Latin) / Ithaca (Ancient Greek). Use when asked to "restore", "attribute", "date", "contextualize", "find parallels", "where was it written", "when was it written", "embed", or "analyze" an ancient text, inscription, or epigraphic document, or when the user mentions "Aeneas", or "Ithaca".': '调用 Aeneas (拉丁文) 与 Ithaca (古希腊文) 模型进行古代铭文残卷缀合、断代与归属地分析',
    'Performs multiple sequence alignment of proteins with EBI Clustal Omega. Use when you need to align multiple sequences to assess similarity, domain conservation, or key residue conservation. Supports up to 4000 sequences and a maximum file size of 4 MB. Do not use to search for homologous proteins in a database (use MMseqs2, BLAST), align non-protein sequences (DNA, RNA), perform structural alignment (use Foldseek, PyMOL), or if you only have a single sequence.': '调用 EBI Clustal Omega 执行蛋白质多序列比对与保守位点分析',
    'Searches for homologous protein sequences using MMseqs2 (fast, default) or BLAST (comprehensive, fallback). Trigger this whenever the user provides a protein sequence or FASTA file and asks to find homologues, sequence matches, or wants to infer protein function based on sequence similarity, but not when the user wants to infer protein function based on structural similarity.': '使用 MMseqs2 或 BLAST 检索同源蛋白质序列并推断功能',
    'Query PubChem, search by name/CID/SMILES, retrieve properties, similarity/substructure searches, bioactivity, for cheminformatics. Use when a user asks about a specific chemical, drug, or molecule.': '检索 PubChem 化合物属性、SMILES 结构、子结构相似性与生物活性数据',
    'Search PubMed for scientific literature, including published clinical trials. Fetch abstracts and full text. Link published research to biological databases (gene, protein, nucleotide, PubChem) to discover associations between papers and specific compounds or genes. Verify medical spelling, match raw citations, and cache result sets for bulk processing. Interfaces NCBI E-utilities and PMC BioC APIs.': '检索 PubMed 生物医学文献摘要、PMC 全文及关联基因/化合物记录',
    'Visualize, analyze, and render protein and molecular structures using PyMOL. Use when the user wants to create images of protein structures, perform structural alignments or superposition, measure distances or contacts, highlight binding sites or active site residues, color by B-factor/pLDDT, or analyze protein-ligand interactions. Do not use for docking, molecular dynamics, or sequence-only analysis.': '使用 PyMOL 分析、叠合与渲染蛋白质及配体三维分子结构图像',
    'Query the QuickGO and Evidence & Conclusion Ontology (ECO) REST API. Use this when you need to map genes to biological processes, molecular functions, or cellular components, find genes associated with a specific pathway/GO term, or explore the Gene Ontology hierarchy. Do not use for querying drug targets (use OpenTargets) or mechanistic signaling pathway diagrams (use KEGG).': '检索 QuickGO 基因本体论 (Gene Ontology) 功能注释与层级图谱',
    'Query the Reactome database (Analysis and Content Services). Use when the user asks about pathway analysis, gene list enrichment, retrieving results by token, finding unmapped or not-found identifiers, mapping identifiers, reaction participants (inputs, outputs), pathway hierarchy (including top-level pathways), diagram export, cross-reference mapping, or searching the knowledgebase.': '检索 Reactome 生物信号通路富集分析与反应网络知识库',
    'Query the STRING database for protein-protein interactions (PPIs), functional enrichment, and homology. Use when the user asks about interactions between specific proteins, interaction evidence, confidence scores, protein interaction partners, or pathway enrichments.': '检索 STRING 蛋白质相互作用网络 (PPI) 与功能富集度',
    'Fetch Evolutionary Conservation scores (phyloP, phastCons) and Transcription Factor Binding Sites (TFBS) from the UCSC Genome Browser. Use when analyzing whether genomic variants or regions are evolutionarily conserved, functionally important, or bounded by TF regulators across major projects (ENCODE, JASPAR, ReMap).': '检索 UCSC 基因组浏览器进化保守性评分 (phyloP/phastCons) 与转录因子结合位点',
    'Queries the UniBind database for experimentally validated transcription factor (TF) binding sites. Use when retrieving direct TF-DNA interaction datasets, downloading binding site coordinates (BED/FASTA) for local analysis, or listing available datasets by species, cell line, or TF name. Don\'t use to query specific intervals, locations, genes, motif models or expression data.': '检索 UniBind 实验验证的转录因子直接结合位点 (TFBS) 数据集',
    'Access protein metadata, function, taxonomy, and sequences across UniProtKB, UniParc, and UniRef. Use when searching for proteins, mapping identifiers, or retrieving functional annotations and publications. Don\'t use for sequence alignment, protein folding, or sequence similarity search (use specialized skills for those tasks).': '检索 UniProt 蛋白质知识库的序列、功能注释、分类学与文献记录',
    'Checks whether the uv Python package manager is installed and installs it if missing. Ensures uv is on PATH. Use when another skill requires uv as a prerequisite.': '检查并配置 uv Python 包管理器运行环境',
    'Distills a completed user workflow or interaction into a reusable agent skill. Use when the user asks to turn their workflow, interaction, or multi-step process into a skill, or when they say "make this a skill", "create a skill from what we just did", "package this workflow" or similar. Do not use for creating skills from scratch without an existing workflow (use a generic skill-creator for that).': '将已完成的交互式多步工作流提炼封装为可复用的 SKILL.md 技能包'
  };

  const REVERSE_TO_EN = {};
  for (const [en, zh] of Object.entries(DICT_STD)) REVERSE_TO_EN[zh] = en;

  function stripBackendTags(str) {
    if (!str || typeof str !== 'string') return '';
    return str
      .replace(/^\s*(?:【[^】]{1,28}】|\[[^\]]{1,28}\]|〔[^〕]{1,28}〕)\s*/g, '')
      .replace(/(?:【(?:定性判断|实时推演|本步意图|全局图景|阶段成果|核心判断|架构侦察|阶段施工|打样渲染|打样展示|基线封存|核心施工|任务清单|下一步动作)[^】]*】)/g, '')
      .replace(/\s+/g, ' ')
      .trim();
  }

  const STREAM_DYNAMIC_PATTERNS = [
    {
      re: /^Thinking for (\d+)s$/i,
      zh: (_, s) => `思考中 · ${s}s`
    },
    {
      re: /^Thought for (\d+)s$/i,
      zh: (_, s) => `思考 · ${s}s`
    },
    {
      re: /^Worked for (.+)$/i,
      zh: (_, dur) => `本阶段执行耗时 ${dur}`
    },
    {
      re: /^Waiting for command completion \(up to (\d+) seconds\)$/i,
      zh: (_, s) => `等待命令执行完成 (上限 ${s}s)`
    },
    {
      re: /^Executing task:\s*(.+)$/i,
      zh: (_, t) => `正在执行：${stripBackendTags(t)}`
    },
    {
      re: /^(\d+)\s+results?$/i,
      zh: (_, n) => `${n} 条结果`
    },
    {
      re: /^(\d+)\s+subagents?$/i,
      zh: (_, n) => `${n} 个子智能体`
    },
    {
      re: /^Show\s+(\d+)\s+more$/i,
      zh: (_, n) => `展开其余 ${n} 项`
    },
    {
      re: /^Version\s+([0-9A-Za-z._-]+)$/i,
      zh: (_, v) => `版本 ${v}`
    },
    {
      re: /^(\d+)\s*s(?:\s+ago)?$/i,
      zh: (_, n) => `${n}秒前`
    },
    {
      re: /^(\d+)\s*m(?:\s+ago)?$/i,
      zh: (_, n) => `${n}分钟前`
    },
    {
      re: /^(\d+)\s*h(?:\s+ago)?$/i,
      zh: (_, n) => `${n}小时前`
    },
    {
      re: /^(\d+)\s*d(?:\s+ago)?$/i,
      zh: (_, n) => `${n}天前`
    },
    {
      re: /^(\d+)\s*w(?:\s+ago)?$/i,
      zh: (_, n) => `${n}周前`
    },
    {
      re: /^Just now$/i,
      zh: () => '刚刚'
    },
    {
      re: /^Updated\s+(.+)$/i,
      zh: (_, t) => `更新于 ${t}`
    },
    {
      re: /^Message from\s+(.+)$/i,
      zh: (_, who) => `来自子智能体的汇报：${who.replace(/\s*\((?:research|self)\)\s*$/i, '')}`
    },
    {
      re: /^Show\s+(\d+)\s+breakdowns?$/i,
      zh: (_, n) => `展开 ${n} 项明细`
    },
    {
      re: /^Hide\s+(\d+)\s+breakdowns?$/i,
      zh: (_, n) => `收起 ${n} 项明细`
    },
    {
      re: /^Reset\s+(\d+)\s+overrides?$/i,
      zh: (_, n) => `重置 ${n} 项自定义覆盖`
    },
    {
      re: /^gates\s+(.+)$/i,
      zh: (_, mod) => `控制模块：${mod}`
    },
    {
      re: /^Not referenced by any feature\s*\($/i,
      zh: () => '未关联前端模块 ('
    },
    {
      re: /^Send feedback as\s+(.+)$/i,
      zh: (_, email) => `以 ${email} 身份提交反馈`
    },
    {
      re: /^(Low|Medium|High)\s*·\s*(\d+\s*[KM])$/i,
      zh: (_, eff, cap) => {
        const map = { low: '低', medium: '中', high: '高' };
        return `${map[eff.toLowerCase()] || eff} · ${cap}`;
      }
    },
    {
      re: /^You have used some of your weekly limit, it will fully refresh in\s+(.+)\.$/i,
      zh: (_, t) => `您已使用部分本周额度，将在 ${t.replace(/days?/i, '天').replace(/hours?/i, '小时').replace(/minutes?/i, '分钟')} 后完全重置。`
    },
    {
      re: /^You have used some of your 5-hour limit, it will fully refresh in\s+(.+)\.$/i,
      zh: (_, t) => `您已使用部分 5 小时窗口额度，将在 ${t.replace(/hours?/i, '小时').replace(/minutes?/i, '分钟')} 后完全重置。`
    },
    {
      re: /^You have hit your weekly limit, it refreshes in\s+([^.]+)\.(.*)$/i,
      zh: (_, t) => `您已达到本周额度上限，将在 ${t.replace(/days?/i, '天').replace(/hours?/i, '小时').replace(/minutes?/i, '分钟')} 后重置（期间可使用 AI 算力积分或升级订阅）。`
    },
    {
      re: /^You have hit your weekly limit, the 5-hour limit does not currently apply\. Your weekly limit will fully refresh in\s+(.+)\.$/i,
      zh: (_, t) => `您已达到本周额度上限（当前不适用 5 小时窗口额度），本周额度将在 ${t.replace(/days?/i, '天').replace(/hours?/i, '小时').replace(/minutes?/i, '分钟')} 后完全重置。`
    },
    {
      re: /^(?:Explored|Ran|Edited)(?:[\s\S]*)$/i,
      zh: (full) => {
        if (!/^(Explored|Ran command|Ran \d+ command|Edited )/i.test(full)) return null;
        const parts = [];
        const mExp = full.match(/Explored(?:\s+([^,]+))?/i);
        if (mExp) {
          parts.push(mExp[1] ? `研读了 ${mExp[1].trim().replace(/(\d+)\s+files?/i, '$1 个文件')}` : '已完成代码与现场勘探');
        }
        const mRan = full.match(/\bran\s+(\d+\s+)?commands?/i);
        if (mRan) {
          const cnt = mRan[1] ? mRan[1].trim() : '1';
          parts.push(`执行了 ${cnt} 项命令核验`);
        }
        const mEd = full.match(/\bedited\s+(.+)$/i);
        if (mEd) {
          parts.push(`修改了 ${mEd[1].trim().replace(/(\d+)\s+files?/i, '$1 个文件')}`);
        }
        return parts.length ? parts.join('，') : null;
      }
    }
  ];

  function translateDynamicEnCore(core) {
    for (const pat of STREAM_DYNAMIC_PATTERNS) {
      const m = core.match(pat.re);
      if (m) {
        const out = pat.zh(...m);
        if (out) return out;
      }
    }
    if (/^\s*【[^】]{1,24}】\s*\S+/.test(core)) {
      const cleaned = stripBackendTags(core);
      if (cleaned && cleaned !== core) return cleaned;
    }
    return null;
  }

  const trackedTextNodes = new Map();
  const trackedPlaceholders = new Map();

  function splitWhitespace(str) {
    const m = String(str || '').match(/^(\s*)([\s\S]*?)(\s*)$/);
    return m ? { leading: m[1], core: m[2], trailing: m[3] } : { leading: '', core: '', trailing: '' };
  }

  function isElementInForbiddenZone(el, allowSearchInputPlaceholder = false) {
    if (el && el.textContent) {
      const t = el.textContent.trim();
      if (t.startsWith('Ask anything, @ to mention') || t.startsWith('Message subagent, @ to mention')) {
        return false;
      }
    }
    let cur = el;
    while (cur) {
      if (!cur.tagName) {
        cur = cur.parentElement;
        continue;
      }
      const tag = cur.tagName.toUpperCase();
      if (['SCRIPT', 'STYLE', 'CODE', 'PRE', 'SVG', 'PATH'].includes(tag)) return true;
      if ((tag === 'INPUT' || tag === 'TEXTAREA') && !allowSearchInputPlaceholder) return true;
      if (cur.isContentEditable) return true;
      if (cur.id && cur.id.startsWith('agy-')) return true;

      const cls = typeof cur.className === 'string' ? cur.className : '';
      if (
        cls.includes('prose') ||
        cls.includes('monaco') ||
        cls.includes('prism') ||
        cls.includes('font-mono')
      ) {
        return true;
      }
      cur = cur.parentElement;
    }
    return false;
  }

  function targetStringForEn(origEn) {
    if (!globalPrefs.enabled || globalPrefs.lang === 'en' || globalPrefs.bossMode) {
      return origEn;
    }
    if (Object.prototype.hasOwnProperty.call(DICT_STD, origEn)) {
      return DICT_STD[origEn];
    }
    const dyn = translateDynamicEnCore(origEn);
    return dyn || origEn;
  }

  function processTextNode(textNode) {
    if (!textNode || textNode.nodeType !== Node.TEXT_NODE) return;
    const parent = textNode.parentElement;
    if (!parent || isElementInForbiddenZone(parent, false)) return;

    const currentRaw = textNode.nodeValue || '';
    const { leading, core, trailing } = splitWhitespace(currentRaw);
    if (!core) return;

    let rec = trackedTextNodes.get(textNode);
    if (rec) {
      if (currentRaw !== rec.lastWritten) {
        if (Object.prototype.hasOwnProperty.call(DICT_STD, core) || translateDynamicEnCore(core)) {
          rec.origEn = core;
        } else {
          trackedTextNodes.delete(textNode);
          return;
        }
      }
    } else {
      let origEn = null;
      if (Object.prototype.hasOwnProperty.call(DICT_STD, core) || translateDynamicEnCore(core)) {
        origEn = core;
      } else if (Object.prototype.hasOwnProperty.call(REVERSE_TO_EN, core)) {
        origEn = REVERSE_TO_EN[core];
      }
      if (!origEn) return;
      rec = { origEn, lastWritten: currentRaw };
      trackedTextNodes.set(textNode, rec);
    }

    const translatedCore = targetStringForEn(rec.origEn);
    const nextRaw = leading + translatedCore + trailing;
    rec.lastWritten = nextRaw;
    if (textNode.nodeValue !== nextRaw) {
      textNode.nodeValue = nextRaw;
    }
  }

  function processInputPlaceholder(inputEl) {
    if (!inputEl || (inputEl.tagName !== 'INPUT' && inputEl.tagName !== 'TEXTAREA')) return;
    if (isElementInForbiddenZone(inputEl, true)) return;
    const currentPh = inputEl.getAttribute('placeholder');
    if (!currentPh) return;
    const { leading, core, trailing } = splitWhitespace(currentPh);
    if (!core) return;

    let rec = trackedPlaceholders.get(inputEl);
    if (rec) {
      if (currentPh !== rec.lastWritten) {
        if (Object.prototype.hasOwnProperty.call(DICT_STD, core)) {
          rec.origEn = core;
        } else {
          trackedPlaceholders.delete(inputEl);
          return;
        }
      }
    } else {
      let origEn = null;
      if (Object.prototype.hasOwnProperty.call(DICT_STD, core)) {
        origEn = core;
      } else if (Object.prototype.hasOwnProperty.call(REVERSE_TO_EN, core)) {
        origEn = REVERSE_TO_EN[core];
      }
      if (!origEn) return;
      rec = { origEn, lastWritten: currentPh };
      trackedPlaceholders.set(inputEl, rec);
    }

    const translatedCore = targetStringForEn(rec.origEn);
    const nextPh = leading + translatedCore + trailing;
    rec.lastWritten = nextPh;
    if (inputEl.getAttribute('placeholder') !== nextPh) {
      inputEl.setAttribute('placeholder', nextPh);
    }
  }

  function pruneAndRefreshTrackedLocalization() {
    for (const [node, rec] of Array.from(trackedTextNodes.entries())) {
      if (!node.isConnected) {
        if (node.nodeValue === rec.lastWritten) {
          const { leading, trailing } = splitWhitespace(node.nodeValue || '');
          node.nodeValue = leading + rec.origEn + trailing;
        }
        trackedTextNodes.delete(node);
      } else {
        processTextNode(node);
      }
    }
    for (const [inputEl, rec] of Array.from(trackedPlaceholders.entries())) {
      if (!inputEl.isConnected) {
        if (inputEl.getAttribute('placeholder') === rec.lastWritten) {
          inputEl.setAttribute('placeholder', rec.origEn);
        }
        trackedPlaceholders.delete(inputEl);
      } else {
        processInputPlaceholder(inputEl);
      }
    }
  }

  function applyLocalizationToSubtree(root) {
    if (!root || disposed) return;
    pruneAndRefreshTrackedLocalization();
    if (!globalPrefs.enabled) return;

    if (root.nodeType === Node.TEXT_NODE) {
      processTextNode(root);
      return;
    }
    if (root.nodeType !== Node.ELEMENT_NODE && root.nodeType !== Node.DOCUMENT_NODE) return;
    if (root.nodeType === Node.ELEMENT_NODE && root.id && root.id.startsWith('agy-')) return;

    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, null);
    while (walker.nextNode()) {
      processTextNode(walker.currentNode);
    }
    if (root.querySelectorAll) {
      const inputs = root.querySelectorAll('input[placeholder], textarea[placeholder]');
      for (const inp of inputs) {
        processInputPlaceholder(inp);
      }
      // Hide useless Google-internal workspace items & temp diagnostic folders inside dropdowns/popovers
      const menuItems = root.querySelectorAll(
        '[data-testid="project-selector-new-workspace"], [role="option"], [role="menuitem"], [cmdk-item], [id^="base-ui-"] button'
      );
      for (const item of menuItems) {
        if (item.getAttribute && item.getAttribute('data-testid') === 'project-selector-trigger') continue;
        const txt = (item.textContent || '').trim();
        const titleEl = item.querySelector ? item.querySelector('[title]') : null;
        const titleAttr = (item.getAttribute && item.getAttribute('title')) || (titleEl && titleEl.getAttribute('title')) || '';
        if (
          txt === 'New Workspace' ||
          txt === '选择工作区' && item.getAttribute('data-testid') === 'project-selector-new-workspace' ||
          txt === 'CitC Workspace' ||
          txt === 'Cog Workspace' ||
          txt === 'ABFS Workspace (Alpha)' ||
          /Work in a (?:CitC|Cog) workspace/i.test(titleAttr) ||
          /Create a new Fig workspace/i.test(titleAttr) ||
          /\\AppData\\Local\\Temp\\(?:agy_|ocr_)/i.test(txt) ||
          /\\AppData\\Local\\Temp\\(?:agy_|ocr_)/i.test(titleAttr)
        ) {
          item.style.setProperty('display', 'none', 'important');
        }
      }
    }
  }

  function restoreAllLocalizationOnDispose() {
    for (const [node, rec] of trackedTextNodes.entries()) {
      if (node.nodeValue === rec.lastWritten) {
        const { leading, trailing } = splitWhitespace(node.nodeValue || '');
        node.nodeValue = leading + rec.origEn + trailing;
      }
    }
    trackedTextNodes.clear();

    for (const [inputEl, rec] of trackedPlaceholders.entries()) {
      if (inputEl.getAttribute('placeholder') === rec.lastWritten) {
        inputEl.setAttribute('placeholder', rec.origEn);
      }
    }
    trackedPlaceholders.clear();

    if (document.querySelectorAll) {
      const injected = document.querySelectorAll('[id^="agy-stream-"]');
      for (const el of injected) {
        el.remove();
      }
    }
  }

  // ---------------------------------------------------------------------------
  // 3. Exact Native Titlebar Color Algorithm (Zub/Yub from /main.js:4834607)
  // ---------------------------------------------------------------------------
  function rgbCssToHex(cssColor) {
    const nums = String(cssColor || '').match(/[\d.]+/g)?.map(Number);
    if (!nums || nums.length < 3) return '';
    const [r, g, b, a = 1] = nums;
    if (a < 1) return '';
    const scale = String(cssColor).startsWith('color(') ? 255 : 1;
    return (
      '#' +
      [r, g, b]
        .map((c) => Math.max(0, Math.min(255, Math.round(c * scale))).toString(16).padStart(2, '0'))
        .join('')
    );
  }

  function resolveNativeCssVarHex(varName) {
    try {
      if (!getComputedStyle(document.documentElement).getPropertyValue(varName).trim()) return '';
      const probe = document.createElement('div');
      probe.style.color = `var(${varName})`;
      probe.style.display = 'none';
      document.body.appendChild(probe);
      const computed = getComputedStyle(probe).color;
      probe.remove();
      return rgbCssToHex(computed);
    } catch (e) {
      return '';
    }
  }

  function setWindowTitleBarColors(color, symbolColor) {
    if (bridge && typeof bridge.setTitleBarOverlay === 'function') {
      bridge.setTitleBarOverlay({ color, symbolColor }).catch(() => {});
    } else if (window.electronNative && typeof window.electronNative.setTitleBarOverlay === 'function') {
      try {
        window.electronNative.setTitleBarOverlay({ color, symbolColor });
      } catch (e) {}
    }
  }

  function restoreNativeTitleBarOverlay() {
    const isLight = document.body.classList.contains('theme-light');
    const isDark = !isLight;
    const sidebarHex = resolveNativeCssVarHex('--sidebar');
    const fgHex = resolveNativeCssVarHex('--foreground');
    setWindowTitleBarColors(
      sidebarHex || (isDark ? '#131313' : '#FFFFFF'),
      fgHex || (isDark ? '#FAFAFA' : '#000000')
    );
  }

  // ---------------------------------------------------------------------------
  // 4. Scoped Theme CSS & Background Layer (#7, #8)
  // ---------------------------------------------------------------------------
  function ensureScopedThemeStyle() {
    let styleEl = document.getElementById('agy-enhance-scoped-style');
    if (!globalPrefs.enabled) {
      if (styleEl) styleEl.remove();
      return;
    }
    if (!styleEl) {
      styleEl = document.createElement('style');
      styleEl.id = 'agy-enhance-scoped-style';
      document.head.appendChild(styleEl);
    }
    styleEl.textContent = `
      html[data-agy-widescreen="1"] {
        --max-conversation-width: min(92vw, 1480px) !important;
      }
      html[data-agy-widescreen="1"] .max-w-3xl,
      html[data-agy-widescreen="1"] .max-w-2xl,
      html[data-agy-widescreen="1"] .max-w-4xl {
        max-width: min(92vw, 1480px) !important;
      }
      html[data-agy-widescreen="1"] .prose {
        max-width: 100% !important;
        line-height: 1.78 !important;
        letter-spacing: 0.012em !important;
      }
      html[data-agy-widescreen="1"] .prose table {
        display: table !important;
        width: 100% !important;
      }

      #agy-ctx-ring-pill {
        display: none;
        position: fixed;
        z-index: 40;
        align-items: center;
        justify-content: center;
        gap: 0;
        width: 24px;
        height: 24px;
        padding: 0;
        border-radius: 999px;
        font-size: 11px;
        font-weight: 600;
        font-family: "JetBrains Mono", "Microsoft YaHei", monospace, system-ui;
        line-height: 1;
        user-select: none;
        white-space: nowrap;
        cursor: pointer;
        background: rgba(36, 36, 40, 0.90);
        border: 1px solid rgba(255, 255, 255, 0.20);
        color: #e4e4e7;
        box-shadow: 0 2px 6px rgba(0, 0, 0, 0.20);
        transition: padding 0.14s ease, background-color 0.14s ease;
      }
      #agy-ctx-ring-pill.show-num {
        width: auto;
        gap: 5px;
        padding: 0 8px 0 6px;
      }
      #agy-ctx-ring-pill #agy-ctx-ring-text {
        display: none;
      }
      #agy-ctx-ring-pill.show-num #agy-ctx-ring-text {
        display: inline;
      }
      html[data-agy-color-mode="light"] #agy-ctx-ring-pill,
      body.theme-light #agy-ctx-ring-pill {
        background: #f4f4f5;
        border: 1px solid rgba(0, 0, 0, 0.10);
        color: #52525b;
        box-shadow: none;
      }
      .agy-ctx-ring-svg {
        width: 14px;
        height: 14px;
        transform: rotate(-90deg);
        flex-shrink: 0;
      }

      /* Stream Qualitative UX Inline Enhancements (Minimalist Native Style) */
      .agy-think-inline-summary {
        display: inline-flex;
        align-items: center;
        margin-left: 6px;
        color: var(--muted-foreground, #6b7280);
        font-size: 12.5px;
        font-weight: 400;
        max-width: min(56vw, 600px);
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
        pointer-events: none;
      }
      .agy-think-inline-summary::before {
        content: "·";
        margin-right: 6px;
        opacity: 0.65;
      }
      .agy-think-digest-card {
        margin: 4px 0 8px 0;
        padding: 6px 10px;
        border-radius: 6px;
        background: var(--muted, rgba(128, 128, 128, 0.06));
        border: 1px solid var(--border, rgba(128, 128, 128, 0.14));
        font-size: 12px;
        line-height: 1.5;
        color: var(--foreground, #374151);
        display: flex;
        flex-direction: column;
        gap: 2px;
      }
      .agy-think-digest-row {
        display: flex;
        align-items: baseline;
        gap: 6px;
      }
      .agy-think-digest-label {
        color: var(--muted-foreground, #6b7280);
        font-weight: 600;
        white-space: nowrap;
        flex-shrink: 0;
      }
      .agy-think-digest-val {
        color: var(--foreground, #374151);
      }
      .agy-stage-qualitative-sub {
        display: inline-flex;
        align-items: center;
        margin-left: 6px;
        color: var(--muted-foreground, #6b7280);
        font-size: 12.5px;
        font-weight: 400;
        max-width: min(52vw, 540px);
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
        pointer-events: none;
      }
      .agy-stage-qualitative-sub::before {
        content: "·";
        margin-right: 6px;
        opacity: 0.65;
      }
      html[data-agy-hud-pad="expanded"] [data-testid="autoscroll-viewport"] {
        padding-bottom: 148px !important;
      }
      html[data-agy-hud-pad="collapsed"] [data-testid="autoscroll-viewport"] {
        padding-bottom: 42px !important;
      }
      [data-testid="project-selector-new-workspace"],
      [role="option"]:has(span[title*="\\AppData\\Local\\Temp\\agy_"]),
      [role="option"]:has(span[title*="\\AppData\\Local\\Temp\\ocr_"]),
      [role="menuitem"]:has(span[title*="\\AppData\\Local\\Temp\\agy_"]),
      [role="menuitem"]:has(span[title*="\\AppData\\Local\\Temp\\ocr_"]) {
        display: none !important;
      }
      [data-testid="project-selector-search"] + div {
        max-height: min(35vh, 220px) !important;
        overflow-y: auto !important;
        overscroll-behavior: contain !important;
      }
    `;
  }

  // ---------------------------------------------------------------------------
  // 5. Active Conversation Detection & Real Agent State Tracking
  // ---------------------------------------------------------------------------
  let realAgentState = 'IDLE';
  let lastRunningExecKey = null;

  function getActiveConversationView() {
    if (!document.querySelectorAll) return null;
    const cvs = document.querySelectorAll('[data-testid="conversation-view"]');
    for (const cv of cvs) {
      if (cv.closest && cv.closest('[hidden]')) continue;
      if (cv.offsetWidth > 0 && cv.offsetHeight > 0) return cv;
    }
    return cvs[0] || null;
  }

  function getActiveConversationId() {
    const activeCv = getActiveConversationView();
    if (activeCv && activeCv.getAttribute) {
      const domCid = activeCv.getAttribute('data-cascade-id') || '';
      if (/^[0-9a-f-]{36}$/i.test(domCid)) return domCid;
    }
    const m = window.location.pathname.match(/\/c\/([0-9a-f-]{36})/i);
    if (m) return m[1];
    const sp = new URLSearchParams(window.location.search);
    return sp.get('conversationId') || sp.get('cascadeId') || '';
  }

  // ---------------------------------------------------------------------------
  // 6. Verified Unary Token Adapter + Multi-Page Tail Verification (#10, #11)
  // ---------------------------------------------------------------------------
  let ctxRingPill = null;
  let globalMetadataInFlight = false;
  let activeMetadataAbortCtrl = null;
  const tokenCacheByConvo = new Map();
  let trailingFetchTimer = null;

  function normalizeUsage(s, active) {
    const unknown = {
      kind: 'unknown',
      usedTokens: null,
      percent: null
    };

    if (
      !s?.latestVerified ||
      !s.requestId ||
      s.conversationId !== active.conversationId ||
      s.documentGeneration !== active.documentGeneration ||
      s.lsGeneration !== active.lsGeneration ||
      s.modelId !== active.modelId
    ) {
      return unknown;
    }

    const count = (value) => {
      if (
        !(
          typeof value === 'number' ||
          typeof value === 'bigint' ||
          (typeof value === 'string' && /^\d+$/.test(value))
        )
      ) {
        return null;
      }
      const n = Number(value);
      return Number.isSafeInteger(n) && n >= 0 ? n : null;
    };

    const h = s.metadata;
    let usage = h?.usage;
    const retries = h?.retryInfos ?? [];

    for (let i = retries.length - 1; i >= 0; --i) {
      if (retries[i]?.usage) {
        usage = retries[i].usage;
        break;
      }
    }

    const parts = [
      usage?.inputTokens,
      usage?.cacheReadTokens,
      usage?.outputTokens
    ].map(count);

    if (parts.some((n) => n === null) || parts[0] + parts[1] <= 0) return unknown;

    const usedTokens = parts.reduce((a, b) => a + b, 0);
    if (!Number.isSafeInteger(usedTokens)) return unknown;

    const maxTokens = count(
      h?.chatStartMetadata?.contextWindowMetadata?.maxContextTokens
    );
    const percent = maxTokens > 0 ? (usedTokens / maxTokens) * 100 : null;

    return {
      kind: 'last-request',
      usedTokens,
      maxTokens,
      percent,
      arcFraction: percent === null ? null : Math.min(1, percent / 100),
      measuredAt: s.measuredAt,
      requestId: s.requestId
    };
  }

  function fillProto3UsageZeros(rawUsage) {
    if (!rawUsage || typeof rawUsage !== 'object') return null;
    return Object.assign(
      {
        inputTokens: '0',
        cacheReadTokens: '0',
        outputTokens: '0'
      },
      rawUsage
    );
  }

  function prepareChatModelMetadataForNormalize(chatModel) {
    if (!chatModel || typeof chatModel !== 'object') return null;
    const cloned = Object.assign({}, chatModel);
    if (cloned.usage) {
      cloned.usage = fillProto3UsageZeros(cloned.usage);
    }
    if (Array.isArray(cloned.retryInfos)) {
      cloned.retryInfos = cloned.retryInfos.map((r) => {
        if (r && r.usage) {
          return Object.assign({}, r, { usage: fillProto3UsageZeros(r.usage) });
        }
        return r;
      });
    }
    return cloned;
  }

  async function requestMetadataPage(cascadeId, offset, externalSignal) {
    if (bridge && typeof bridge.fetchGeneratorMetadata === 'function') {
      return await bridge.fetchGeneratorMetadata({
        cascadeId,
        generatorMetadataOffset: offset,
        includeMessages: false
      });
    }
    const csrf =
      window.__APP_CONFIG__?.csrfToken ||
      document.cookie.split('; ').find((a) => a.startsWith('csrfToken='))?.split('=')[1] ||
      '';
    if (!csrf) return null;
    const ctrl = new AbortController();
    const onExtAbort = () => ctrl.abort();
    if (externalSignal) {
      if (externalSignal.aborted) return null;
      externalSignal.addEventListener('abort', onExtAbort, { once: true });
    }
    const timer = setTimeout(() => ctrl.abort(), 2500);
    try {
      const resp = await window.fetch(
        `${window.location.origin}/exa.language_server_pb.LanguageServerService/GetCascadeTrajectoryGeneratorMetadata`,
        {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'Connect-Protocol-Version': '1',
            'x-codeium-csrf-token': csrf
          },
          body: JSON.stringify({
            cascadeId,
            generatorMetadataOffset: offset,
            includeMessages: false
          }),
          signal: ctrl.signal
        }
      );
      if (!resp.ok) return null;
      const contentLen = Number(resp.headers?.get?.('content-length') || 0);
      if (contentLen > 2 * 1024 * 1024) return null;
      const rawText = await resp.text();
      if (rawText.length > 2 * 1024 * 1024) return null;
      return JSON.parse(rawText);
    } finally {
      clearTimeout(timer);
      if (externalSignal) {
        externalSignal.removeEventListener('abort', onExtAbort);
      }
    }
  }

  function scheduleTokenMetadataRefresh(forceImmediate = false) {
    if (disposed || !globalPrefs.enabled || document.hidden) return;
    const cid = getActiveConversationId();
    if (!cid) return;

    let state = tokenCacheByConvo.get(cid);
    if (!state) {
      state = {
        knownLength: 0,
        entriesByIndex: new Map(),
        normalized: { kind: 'unknown', usedTokens: null, percent: null },
        lastFetchTs: 0,
        reachedTail: false,
        stale: false
      };
      tokenCacheByConvo.set(cid, state);
    }

    const now = Date.now();
    const elapsed = now - state.lastFetchTs;
    if (globalMetadataInFlight) {
      if (!trailingFetchTimer) {
        trailingFetchTimer = setTimeout(() => {
          trailingFetchTimer = null;
          scheduleTokenMetadataRefresh(true);
        }, 1200);
      }
      return;
    }
    if (!forceImmediate && elapsed < 3000) {
      if (!trailingFetchTimer) {
        trailingFetchTimer = setTimeout(() => {
          trailingFetchTimer = null;
          scheduleTokenMetadataRefresh(true);
        }, 3000 - elapsed + 50);
      }
      return;
    }

    globalMetadataInFlight = true;
    state.lastFetchTs = now;
    const reqLsGen = currentLsGeneration;
    const reqOrigin = window.location.origin;
    const reqCtrl = new AbortController();
    activeMetadataAbortCtrl = reqCtrl;

    (async () => {
      try {
        let offset = Math.max(0, state.knownLength - 1);
        let pagesFetched = 0;
        const MAX_PAGES_PER_CYCLE = 4;
        let hitError = false;

        while (pagesFetched < MAX_PAGES_PER_CYCLE) {
          if (disposed || !globalPrefs.enabled || reqCtrl.signal.aborted || currentLsGeneration !== reqLsGen) {
            hitError = true;
            break;
          }
          const data = await requestMetadataPage(cid, offset, reqCtrl.signal);
          if (disposed || !globalPrefs.enabled || reqCtrl.signal.aborted || currentLsGeneration !== reqLsGen) {
            hitError = true;
            break;
          }
          if (!data || typeof data !== 'object' || Array.isArray(data) || 'error' in data || 'code' in data) {
            hitError = true;
            break;
          }
          // ProtoJSON omits empty repeated fields: `{}` means generatorMetadata is `[]` (EOF!)
          let batch = null;
          if (data.generatorMetadata === undefined) {
            batch = [];
          } else if (Array.isArray(data.generatorMetadata)) {
            batch = data.generatorMetadata;
          } else {
            hitError = true;
            break;
          }

          pagesFetched++;
          if (batch.length === 0) {
            state.reachedTail = true;
            break;
          }
          for (let i = 0; i < batch.length; i++) {
            state.entriesByIndex.set(offset + i, batch[i]);
          }
          offset += batch.length;
          if (offset > state.knownLength) {
            state.knownLength = offset;
          }
          state.reachedTail = false;
        }

        if (hitError) {
          state.stale = true;
        } else {
          state.stale = false;
        }

        if (!hitError && state.reachedTail && state.knownLength > 0) {
          let latestWithUsage = null;
          for (let idx = state.knownLength - 1; idx >= 0; idx--) {
            const item = state.entriesByIndex.get(idx);
            if (item && item.chatModel && (item.chatModel.usage || item.chatModel.retryInfos?.length)) {
              latestWithUsage = item;
              break;
            }
          }
          const currentActiveCid = getActiveConversationId();
          if (
            latestWithUsage &&
            latestWithUsage.chatModel &&
            currentActiveCid === cid &&
            currentLsGeneration === reqLsGen &&
            window.location.origin === reqOrigin
          ) {
            const preparedMeta = prepareChatModelMetadataForNormalize(latestWithUsage.chatModel);
            const metaModelId = preparedMeta?.usage?.model || preparedMeta?.model || 'unknown';
            const reqModelId = latestWithUsage.chatModel?.model || metaModelId;
            const reqId =
              latestWithUsage.executionId ||
              preparedMeta?.usage?.messageId ||
              preparedMeta?.usage?.responseId ||
              `step-${state.knownLength}`;
            const reqLsGenKey = `${reqOrigin}#gen${reqLsGen}`;
            const curLsGenKey = `${window.location.origin}#gen${currentLsGeneration}`;
            const activeCtx = {
              conversationId: currentActiveCid,
              documentGeneration: instanceId,
              lsGeneration: curLsGenKey,
              modelId: reqModelId
            };
            const sample = {
              latestVerified: state.reachedTail && !state.stale,
              requestId: reqId,
              conversationId: cid,
              documentGeneration: instanceId,
              lsGeneration: reqLsGenKey,
              modelId: metaModelId,
              metadata: preparedMeta,
              measuredAt: new Date().toLocaleTimeString()
            };
            state.normalized = normalizeUsage(sample, activeCtx);
          }
        } else if (!state.reachedTail && !hitError && globalPrefs.enabled && !disposed) {
          if (!trailingFetchTimer) {
            trailingFetchTimer = setTimeout(() => {
              trailingFetchTimer = null;
              scheduleTokenMetadataRefresh(true);
            }, 800);
          }
        }
      } catch (e) {
        state.stale = true;
      } finally {
        if (activeMetadataAbortCtrl === reqCtrl) {
          activeMetadataAbortCtrl = null;
        }
        globalMetadataInFlight = false;
        updateFloatingContextRing();
      }
    })();
  }

  function hasVisibleOverlappingPopup(targetRect) {
    const candidates = document.querySelectorAll(
      '[role="menu"], [role="listbox"], [role="dialog"], [data-radix-popper-content-wrapper]'
    );
    for (const el of candidates) {
      if (el.getAttribute('aria-hidden') === 'true') continue;
      const st = getComputedStyle(el);
      if (st.display === 'none' || st.visibility === 'hidden' || parseFloat(st.opacity) <= 0.05) continue;
      const r = el.getBoundingClientRect();
      if (r.width <= 4 || r.height <= 4) continue;
      if (el.getAttribute('role') === 'dialog' || r.bottom >= targetRect.top - 12) {
        return true;
      }
    }
    return false;
  }

  function findActiveComposerTurboButton() {
    const allTurbo = document.querySelectorAll('button[data-testid="permissions-selector-trigger"]');
    for (const btn of allTurbo) {
      const r = btn.getBoundingClientRect();
      if (r.width > 0 && r.height > 0 && r.bottom > 0 && r.top < window.innerHeight) {
        return btn;
      }
    }
    return null;
  }

  let ctxRingHovered = false;
  let ctxRingPinned = false;

  function onDocumentMouseDownForRing(e) {
    if (!ctxRingPinned || !ctxRingPill) return;
    if (e && e.target && (e.target === ctxRingPill || (ctxRingPill.contains && ctxRingPill.contains(e.target)))) {
      return;
    }
    ctxRingPinned = false;
    updateFloatingContextRing();
  }

  function extractRealtimeFiberUsage(cid) {
    if (!cid || !document.querySelectorAll) return null;
    try {
      const activeCv = getActiveConversationView();
      if (!activeCv) return null;

      let lastUsage = null;
      let maxTokens = null;

      const av = activeCv.querySelector ? activeCv.querySelector('[data-testid="autoscroll-viewport"]') : null;
      if (av) {
        const ctxHolder = findAncestorFiberProps(av, (p) => Boolean(p.value?.cascadeContext?.state?.agentStateProvider), 20);
        const provider = ctxHolder?.value?.cascadeContext?.state?.agentStateProvider;
        if (provider && typeof provider.getState === 'function') {
          const st = provider.getState();
          const steps = st?.trajectorySlice?.stepsInSlice || [];
          for (let i = steps.length - 1; i >= 0; i--) {
            if (steps[i]?.metadata?.modelUsage) {
              lastUsage = steps[i].metadata.modelUsage;
              break;
            }
          }
          const gm = st?.trajectorySlice?.generatorMetadatasInSlice?.[0];
          if (!lastUsage && gm?.chatModel?.usage) {
            lastUsage = gm.chatModel.usage;
          }
          const gmMax = Number(gm?.chatModel?.chatStartMetadata?.contextWindowMetadata?.maxContextTokens || 0);
          if (gmMax > 0) maxTokens = gmMax;
        }
      }

      if (!lastUsage && activeCv.querySelectorAll) {
        const gbs = activeCv.querySelectorAll('button[data-testid="tool-group-collapsible"], button[data-testid="worked-for-collapsible"]');
        for (let gi = gbs.length - 1; gi >= 0 && !lastUsage; gi--) {
          const fp = findAncestorFiberProps(gbs[gi], (p) => Array.isArray(p.steps) || Array.isArray(p.segment?.steps));
          const steps = fp?.steps || fp?.segment?.steps || [];
          for (let si = steps.length - 1; si >= 0; si--) {
            if (steps[si]?.metadata?.modelUsage) {
              lastUsage = steps[si].metadata.modelUsage;
              break;
            }
          }
        }
      }

      if (!lastUsage) return null;

      const inTok = Number(lastUsage.inputTokens || 0);
      const cacheTok = Number(lastUsage.cacheReadTokens || 0);
      const outTok = Number(lastUsage.outputTokens || 0);
      const usedTokens = inTok + cacheTok + outTok;
      if (!Number.isSafeInteger(usedTokens) || usedTokens <= 0) return null;

      if (!maxTokens) {
        const modelBtns = document.querySelectorAll('[data-testid="model-selector-trigger"]');
        for (const mb of modelBtns) {
          if (mb.offsetWidth > 0) {
            const txt = mb.textContent || '';
            const mK = txt.match(/(\d+)\s*K\b/i);
            const mM = txt.match(/(\d+(?:\.\d+)?)\s*M\b/i);
            if (mK) {
              maxTokens = parseInt(mK[1], 10) * 1000;
              break;
            } else if (mM) {
              maxTokens = Math.round(parseFloat(mM[1]) * 1000000);
              break;
            }
          }
        }
      }
      if (!maxTokens || maxTokens <= 0) maxTokens = 900000;

      const percent = (usedTokens / maxTokens) * 100;
      return {
        kind: 'last-request',
        usedTokens,
        maxTokens,
        percent,
        arcFraction: Math.min(1, percent / 100),
        measuredAt: new Date().toLocaleTimeString(),
        requestId: lastUsage.responseId || lastUsage.messageId || 'fiber-live'
      };
    } catch (e) {
      return null;
    }
  }

  function updateFloatingContextRing() {
    if (disposed || !globalPrefs.enabled) {
      if (ctxRingPill) ctxRingPill.style.display = 'none';
      return;
    }

    const turboBtn = findActiveComposerTurboButton();
    if (!turboBtn) {
      if (ctxRingPill) ctxRingPill.style.display = 'none';
      return;
    }

    if (!ctxRingPill || ctxRingPill.parentElement !== document.body) {
      const existing = document.getElementById('agy-ctx-ring-pill');
      if (existing) existing.remove();
      ctxRingPill = document.createElement('div');
      ctxRingPill.id = 'agy-ctx-ring-pill';
      ctxRingPill.innerHTML = `
        <svg class="agy-ctx-ring-svg" viewBox="0 0 18 18">
          <circle cx="9" cy="9" r="6.8" fill="none" stroke="rgba(148,163,184,0.28)" stroke-width="2.3"></circle>
          <circle id="agy-ctx-ring-arc" cx="9" cy="9" r="6.8" fill="none" stroke="#4ade80" stroke-width="2.3"
                  stroke-dasharray="42.72" stroke-dashoffset="42.72" stroke-linecap="round"></circle>
        </svg>
        <span id="agy-ctx-ring-text">--%</span>
      `;
      ctxRingPill.addEventListener('mouseenter', () => {
        ctxRingHovered = true;
        updateFloatingContextRing();
      });
      ctxRingPill.addEventListener('mouseleave', () => {
        ctxRingHovered = false;
        updateFloatingContextRing();
      });
      ctxRingPill.addEventListener('click', (e) => {
        e.stopPropagation();
        ctxRingPinned = !ctxRingPinned;
        updateFloatingContextRing();
      });
      document.body.appendChild(ctxRingPill);
    }

    const tRect = turboBtn.getBoundingClientRect();
    if (hasVisibleOverlappingPopup(tRect)) {
      ctxRingPill.style.display = 'none';
      return;
    }

    // Full sibling collision check across all controls inside the same @container/input-footer
    const footer = turboBtn.closest('[class*="@container/input-footer"]') || turboBtn.parentElement?.parentElement;
    let maxLeftControlRight = footer ? footer.getBoundingClientRect().left : 0;
    if (footer) {
      const leftGroup = footer.firstElementChild;
      if (leftGroup && leftGroup !== turboBtn.parentElement) {
        const leftButtons = leftGroup.querySelectorAll('button, [role="button"]');
        for (const lb of leftButtons) {
          const lr = lb.getBoundingClientRect();
          if (lr.width > 0 && lr.height > 0) {
            if (Math.abs(lr.top - tRect.top) > 14) {
              ctxRingPill.style.display = 'none';
              return;
            }
            maxLeftControlRight = Math.max(maxLeftControlRight, lr.right);
          }
        }
      }
      if (turboBtn.parentElement) {
        for (const sib of turboBtn.parentElement.children) {
          if (sib === turboBtn || sib.id === 'agy-ctx-ring-pill') continue;
          const sr = sib.getBoundingClientRect();
          if (sr.width > 0 && sr.right <= tRect.left + 2) {
            maxLeftControlRight = Math.max(maxLeftControlRight, sr.right);
          }
        }
      }
    }

    const availableGap = tRect.left - maxLeftControlRight;
    if (availableGap < 30) {
      ctxRingPill.style.display = 'none';
      return;
    }

    const cid = getActiveConversationId();
    const state = cid ? tokenCacheByConvo.get(cid) : null;
    let norm = state?.normalized || { kind: 'unknown', usedTokens: null, percent: null };
    const liveNorm = extractRealtimeFiberUsage(cid);
    if (liveNorm && liveNorm.usedTokens !== null) {
      norm = liveNorm;
    }

    const showNum = Boolean(ctxRingHovered || ctxRingPinned);
    ctxRingPill.classList.toggle('show-num', showNum);
    ctxRingPill.title = '';

    const arc = ctxRingPill.querySelector('#agy-ctx-ring-arc');
    const txt = ctxRingPill.querySelector('#agy-ctx-ring-text');
    const circumference = 42.72;
    const compactMode = availableGap < 112;

    if (norm.kind === 'last-request' && norm.usedTokens !== null) {
      const pctNum = norm.percent !== null ? Math.round(norm.percent) : null;
      const arcFrac = norm.arcFraction !== null ? norm.arcFraction : 0;
      const offset = (circumference * (1 - arcFrac)).toFixed(2);
      const strokeColor =
        pctNum !== null && pctNum >= 80 ? '#ef4444' : pctNum !== null && pctNum >= 55 ? '#f59e0b' : '#10b981';

      if (arc) {
        arc.setAttribute('stroke-dashoffset', String(offset));
        arc.setAttribute('stroke', strokeColor);
      }
      const usedK = norm.usedTokens / 1000;
      const kFormatted = usedK >= 100 ? `${Math.round(usedK)}k` : `${usedK.toFixed(1)}k`;
      const pctStr = pctNum !== null ? `${pctNum}%` : '--%';
      if (txt) {
        txt.textContent = compactMode ? pctStr : `${pctStr} · ${kFormatted}`;
      }
    } else {
      if (arc) {
        arc.setAttribute('stroke-dashoffset', String(circumference));
        arc.setAttribute('stroke', '#94a3b8');
      }
      if (txt) {
        txt.textContent = '--%';
      }
    }

    ctxRingPill.style.display = 'inline-flex';
    const actualPillWidth = ctxRingPill.getBoundingClientRect().width || (showNum ? (compactMode ? 52 : 92) : 24);
    if (actualPillWidth + 6 > availableGap) {
      ctxRingPill.style.display = 'none';
      return;
    }
    ctxRingPill.style.left = `${Math.round(tRect.left - actualPillWidth - 6)}px`;
    ctxRingPill.style.top = `${Math.round(tRect.top + (tRect.height - 24) / 2)}px`;
  }

  // ---------------------------------------------------------------------------
  // 7. Authoritative Agent State Observation via stepsUpdate.steps (#28, Round 4 III.5)
  // ---------------------------------------------------------------------------
  const STEP_STATUS_DONE = 3;
  const STEP_STATUS_CANCELED = 6;
  const STEP_STATUS_ERROR = 7;
  const STEP_STATUS_INTERRUPTED = 12;

  function mergeBufferedConversationState(cid) {
    const records = globalThis.__asu?.conversations?.get?.(cid);
    if (!Array.isArray(records) || records.length === 0) return null;

    const stepsByIndex = new Map();
    let maxTotalLength = 0;
    let latestStatus = null;
    let latestFullyIdle = null;
    let latestTrajectoryId = '';
    let hasTerminalError = false;

    for (const rec of records) {
      const u = rec?.update;
      if (!u) continue;
      if (u.trajectoryId) latestTrajectoryId = u.trajectoryId;
      if (u.status !== undefined) latestStatus = u.status;
      if (u.fullyIdle !== undefined) latestFullyIdle = u.fullyIdle;
      if (u.lastStepError || u.error || u.mainTrajectoryUpdate?.lastStepError) {
        hasTerminalError = true;
      }
      const su = u.mainTrajectoryUpdate?.stepsUpdate;
      if (su) {
        if (typeof su.totalLength === 'number' && su.totalLength > maxTotalLength) {
          maxTotalLength = su.totalLength;
        }
        const steps = Array.isArray(su.steps) ? su.steps : [];
        const indices = Array.isArray(su.indices) ? su.indices : [];
        for (let i = 0; i < steps.length; i++) {
          const idx = typeof indices[i] === 'number' ? indices[i] : (steps[i]?.metadata?.stepIndex ?? i);
          stepsByIndex.set(idx, steps[i]);
          if (idx + 1 > maxTotalLength) maxTotalLength = idx + 1;
        }
      }
    }

    const tailIndex = maxTotalLength > 0 ? maxTotalLength - 1 : -1;
    const tailStep = tailIndex >= 0 ? (stepsByIndex.get(tailIndex) || null) : null;
    return {
      trajectoryId: latestTrajectoryId,
      status: latestStatus,
      fullyIdle: latestFullyIdle,
      hasTerminalError,
      tailIndex,
      tailStep
    };
  }

  function pollLiveAgentState() {
    if (disposed || !globalPrefs.enabled) return;
    const cid = getActiveConversationId();

    let nextState = 'IDLE';
    try {
      const mainPane =
        document.querySelector('[data-pane-focused="true"]') ||
        document.querySelector('main') ||
        document.getElementById('root') ||
        document.body;
      const buttons = mainPane.querySelectorAll('button');
      for (const b of buttons) {
        if (b.offsetWidth === 0 || b.offsetHeight === 0) continue;
        const t = (b.textContent || '').trim();
        if (
          t === 'Approve' ||
          t === '批准执行' ||
          t === 'Proceed' ||
          t === '批准继续'
        ) {
          nextState = 'WAITING';
          break;
        }
      }

      if (nextState !== 'WAITING' && cid) {
        const merged = mergeBufferedConversationState(cid);
        if (merged) {
          const tailStep = merged.tailStep;
          const tailStatus = tailStep?.status ?? tailStep?.step?.status ?? null;
          const execKey = `${cid}:${merged.trajectoryId}`;

          if (merged.fullyIdle === false || merged.status === 2) {
            nextState = 'RUNNING';
            lastRunningExecKey = execKey;
          } else if (
            merged.hasTerminalError ||
            tailStatus === STEP_STATUS_ERROR ||
            tailStatus === 'CORTEX_STEP_STATUS_ERROR' ||
            tailStep?.type === 'CORTEX_STEP_TYPE_ERROR_MESSAGE' ||
            tailStep?.errorMessage
          ) {
            nextState = 'ERROR';
          } else if (
            tailStatus === STEP_STATUS_CANCELED ||
            tailStatus === STEP_STATUS_INTERRUPTED ||
            tailStatus === 'CORTEX_STEP_STATUS_CANCELED' ||
            tailStatus === 'CORTEX_STEP_STATUS_INTERRUPTED'
          ) {
            nextState = 'IDLE';
          } else if (
            merged.fullyIdle === true &&
            lastRunningExecKey === execKey &&
            tailStep !== null &&
            (tailStatus === STEP_STATUS_DONE || tailStatus === 'CORTEX_STEP_STATUS_DONE')
          ) {
            nextState = 'DONE';
          }
        }
      }
    } catch (e) {}

    if (nextState !== realAgentState) {
      const prev = realAgentState;
      realAgentState = nextState;
      if (prev === 'RUNNING' && nextState !== 'RUNNING') {
        scheduleTokenMetadataRefresh(true);
      }
    }

    if (globalPrefs.enabled && globalPrefs.lang !== 'en') {
      enhanceConversationStreamDOM();
      updateMissionHud();
    } else {
      hideMissionHud();
    }
  }

  // ---------------------------------------------------------------------------
  // 7.5 Stream Qualitative UX & Live Mission HUD Engine (AGY-01)
  // ---------------------------------------------------------------------------
  let missionHudHost = null;
  let missionHudShadow = null;
  let missionHudRefs = {};
  let missionHudCollapsed = false;
  let inlineSeqCounter = 0;
  const domElementUidMap = new WeakMap();

  function getStableDomUid(el) {
    let id = domElementUidMap.get(el);
    if (!id) {
      id = 's' + (++inlineSeqCounter);
      domElementUidMap.set(el, id);
    }
    return id;
  }

  function getReactFiber(el) {
    if (!el || typeof el !== 'object') return null;
    try {
      const keys = Object.keys(el);
      for (let i = 0; i < keys.length; i++) {
        const k = keys[i];
        if (k.startsWith('__reactFiber$') || k.startsWith('__reactInternalInstance$')) {
          return el[k];
        }
      }
    } catch (e) {}
    return null;
  }

  function findAncestorFiberProps(el, predicate, maxDepth = 18) {
    let cur = getReactFiber(el);
    let depth = 0;
    while (cur && depth < maxDepth) {
      const props = cur.memoizedProps;
      if (props && typeof props === 'object') {
        try {
          if (predicate(props, cur)) return props;
        } catch (e) {}
      }
      cur = cur.return;
      depth++;
    }
    return null;
  }

  function cleanNaturalSentence(raw) {
    if (!raw || typeof raw !== 'string') return '';
    let s = stripBackendTags(raw);
    s = s
      .replace(/^[-*+•>\d.)\s]+/, '')
      .replace(/\*\*([^*]+)\*\*/g, '$1')
      .replace(/`([^`]+)`/g, '$1')
      .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')
      .replace(/^\s*(?:本步意图|核心判断|下一步动作|下一步|阶段成果|结论|判断|目标|全局图景|总目标)[：:]\s*/i, '')
      .replace(/\s+/g, ' ')
      .trim();
    return s;
  }

  function summarizeEnglishThinkingToZh(rawEn, nextToolDesc = '') {
    const t = String(rawEn || '');
    const mFiles = Array.from(
      new Set((t.match(/\b([a-zA-Z0-9_-]+\.(?:py|js|ts|tsx|json|md|html|css|go|asar|exe))\b/gi) || []).slice(0, 2))
    );
    const mCode = Array.from(new Set((t.match(/`([a-zA-Z0-9_$.]{3,32})`/g) || []).map((s) => s.slice(1, -1)).slice(0, 2)));
    const subject = mFiles.length > 0 ? mFiles.join('、') : mCode.length > 0 ? mCode.join('、') : '';

    let reasonPart = '';
    if (/\b(BigInt|TypeError|serialize|JSON\.stringify)\b/i.test(t)) {
      reasonPart = '定位到 BigInt 序列化兼容问题';
    } else if (/\b(escape|escaping|quote|syntax|SyntaxError)\b/i.test(t)) {
      reasonPart = '规避命令行引号转义干扰';
    } else if (/\b(hash|SHA256|verify|integrity|preflight)\b/i.test(t)) {
      reasonPart = subject ? `核对 ${subject} 的完整性与哈希` : '核对构建产物的哈希与完整性';
    } else if (/\b(bug|error|fail|crash|hang|timeout|fix)\b/i.test(t)) {
      reasonPart = subject ? `排查 ${subject} 的异常根因` : '分析故障现象与触发根因';
    } else if (/\b(design|plan|propose|architecture|structure|prototype)\b/i.test(t)) {
      reasonPart = subject ? `梳理 ${subject} 的设计结构` : '推演整体设计方案与实现脉络';
    } else if (/\b(test|assert|validate|suite)\b/i.test(t)) {
      reasonPart = subject ? `验证 ${subject} 的运行表现` : '核验各项实现结果是否达标';
    } else if (/\b(edit|write|patch|update|implement|build|inject)\b/i.test(t)) {
      reasonPart = subject ? `准备更新 ${subject} 的核心实现` : '组织代码实现与配置更新';
    } else if (subject) {
      reasonPart = `分析 ${subject} 的上下文与实现细节`;
    }

    if (nextToolDesc && reasonPart) {
      if (nextToolDesc.includes(subject) && subject) {
        return `${reasonPart}，${nextToolDesc}`;
      }
      return `${reasonPart}，进而${nextToolDesc}`;
    }
    if (nextToolDesc) {
      return `结合上下文推演方案，准备${nextToolDesc}`;
    }
    return reasonPart || '正在结合当前上下文推演下一步执行方案';
  }

  function parseThinkingQualitativeDigest(rawThinking, nextToolDesc = '') {
    const text = String(rawThinking || '').replace(/```[\s\S]*?```/g, ' ').trim();
    if (!text) {
      return {
        headline: nextToolDesc || '',
        intent: nextToolDesc || '',
        judgment: '',
        nextStep: nextToolDesc || '',
        globalGoal: '',
        explicitTasks: []
      };
    }

    let globalGoal = '';
    const goalMatch = text.match(/(?:【?全局图景】?|【?总目标】?)[：:\s]*([^\n]+)/);
    if (goalMatch && goalMatch[1]) {
      globalGoal = cleanNaturalSentence(goalMatch[1]);
    }

    const explicitTasks = [];
    const lines = text.split(/\r?\n/);
    for (const rawLine of lines) {
      const mTask = rawLine.match(/^\s*(?:[-*+]|\d+\.)\s*\[([ xX/>-])\]\s*(.+)$/);
      if (mTask) {
        const mark = mTask[1].toLowerCase();
        const body = mTask[2].trim();
        let titlePart = body;
        let outcomePart = '';
        const splitMatch = body.match(/^(.+?)(?:\s+(?:—|--|->|→|✓|✔)\s*(.+))$/);
        if (splitMatch) {
          titlePart = splitMatch[1];
          outcomePart = cleanNaturalSentence(splitMatch[2]);
        }
        const cleanedTitle = cleanNaturalSentence(titlePart);
        if (cleanedTitle) {
          explicitTasks.push({
            status: mark === 'x' ? 'done' : (mark === '/' || mark === '>' || mark === '-') ? 'active' : 'todo',
            title: cleanedTitle,
            outcome: outcomePart
          });
        }
      }
    }

    let intent = '';
    let judgment = '';
    let nextStep = '';

    const mIntent = text.match(/(?:【?本步意图】?|意图)[：:\s]*([^\n]+)/);
    if (mIntent && mIntent[1]) intent = cleanNaturalSentence(mIntent[1]);

    const mJudg = text.match(/(?:【?核心判断】?|【?定性判断】?|【?阶段成果】?|结论)[：:\s]*([^\n]+)/);
    if (mJudg && mJudg[1]) judgment = cleanNaturalSentence(mJudg[1]);

    const mNext = text.match(/(?:【?下一步(?:动作)?】?)[：:\s]*([^\n]+)/);
    if (mNext && mNext[1]) nextStep = cleanNaturalSentence(mNext[1]);

    const zhSentences = [];
    const rawChunks = text.split(/[\r\n。！？；!?;]+/);
    for (const ch of rawChunks) {
      const cleaned = cleanNaturalSentence(ch);
      if (
        cleaned.length >= 6 &&
        cleaned.length <= 96 &&
        /[\u4e00-\u9fa5]{3,}/.test(cleaned) &&
        !/^(https?:|file:\/\/|[A-Za-z]:\\|import\s|def\s|function\s|const\s|var\s|let\s)/.test(cleaned)
      ) {
        zhSentences.push(cleaned);
      }
    }

    if (!intent && zhSentences.length > 0) {
      intent = zhSentences[0];
    }
    if (!judgment && zhSentences.length > 1) {
      judgment = zhSentences[zhSentences.length - 1];
    }

    let headline = '';
    if (zhSentences.length > 0) {
      headline = zhSentences[zhSentences.length - 1];
    } else {
      headline = summarizeEnglishThinkingToZh(text, nextToolDesc);
      if (!intent) intent = headline;
      if (!nextStep && nextToolDesc) nextStep = nextToolDesc;
    }
    if (headline.length > 66) {
      headline = headline.slice(0, 64) + '…';
    }

    return {
      headline,
      intent,
      judgment,
      nextStep,
      globalGoal,
      explicitTasks
    };
  }

  function basenameFromUriOrPath(uriOrPath) {
    if (!uriOrPath || typeof uriOrPath !== 'string') return '';
    const cleaned = decodeURIComponent(uriOrPath).replace(/\\/g, '/').replace(/\/+$/, '');
    const parts = cleaned.split('/');
    return parts[parts.length - 1] || '';
  }

  function translateEnglishPhrasePartsToZh(enBody) {
    let b = String(enBody || '').trim();
    if (!b) return '';
    b = b
      .replace(/\band\s+run\s+both\s+real\s+probes\b/gi, '并运行双路实机探针核验')
      .replace(/\band\s+run\s+([^\s,]+)\s+probes?\b/gi, '并运行 $1 探针核验')
      .replace(/\band\s+verify\s+via\s+(.+)$/i, '并通过 $1 核验')
      .replace(/\band\s+verify\b/gi, '并核验结果')
      .replace(/\band\s+test\s+all\s+slices\b/gi, '并检验全表切片')
      .replace(/\bblock\s+paragraphs\b/gi, '段落块文本')
      .replace(/\bremaining\s+Wikisource\s+texts\s+via\s+direct\s+paths\b/gi, '其余维基文库原文')
      .replace(/\bexact\s+Wikisource\s+subpage\s+paths\b/gi, '维基文库子页面精确路径')
      .replace(/\brendered\s+screenshot\s+of\s+updated\s+HTML\b/gi, '更新后 HTML 的渲染预览图')
      .replace(/\bCSV\s+labels\s+in\s+([A-Za-z0-9_]+)\s+and\s+([A-Za-z0-9_]+)/gi, '$1 与 $2 中的 CSV 标签')
      .replace(/\bin\s+document\s+and\s+footnotes\b/gi, '正文与脚注中')
      .replace(/\bTrack\s+Changes\b/gi, '审阅修订痕迹')
      .replace(/\bcompletion\b/gi, '阶段收口')
      .replace(/\bfinal\s+delivery\b/gi, '最终交付')
      .replace(/\bwrapper\s+shortcuts\b/gi, '包装器捷径')
      .replace(/\s+and\s+/gi, ' 与 ');
    return b;
  }

  function translateEnglishToolSummaryToZh(enStr) {
    const s = cleanNaturalSentence(enStr);
    if (!s) return '';
    if (/[\u4e00-\u9fa5]{4,}/.test(s) && !/[a-zA-Z]{4,}\s+[a-zA-Z]{4,}/.test(s)) return s;

    let m = s.match(/^Run\s+(?:resident\s+)?ArchUnit\s+(L\d+)\s+(?:gate\s+)?review\s+on\s+(.+)$/i);
    if (m) return `执行 ${translateEnglishPhrasePartsToZh(m[2])} 阶段 ArchUnit ${m[1]} 关口审读`;

    m = s.match(/^Commit\s+([A-Za-z0-9_-]+)\s+checkpoint\s+(r\d+)(?:\s+revoking\s+(.+))?(?:\s+for\s+(.+))?$/i);
    if (m) {
      if (m[3]) return `提交 ${m[1]} 检查点 ${m[2]}（撤回 ${translateEnglishPhrasePartsToZh(m[3])}）`;
      if (m[4]) return `提交 ${m[1]} 检查点 ${m[2]}（${translateEnglishPhrasePartsToZh(m[4])}）`;
      return `提交 ${m[1]} 检查点 ${m[2]} 工程存档`;
    }

    m = s.match(/^Run\s+(?:updated\s+)?([a-zA-Z0-9_.-]+)\s+and\s+verify\s+via\s+(.+)$/i);
    if (m) return `运行 ${m[1]} 并通过 ${m[2]} 核验结果`;

    m = s.match(/^Read\s+back\s+and\s+verify\s+all\s+(.+?)\s+Track\s+Changes\s+in\s+document\s+and\s+footnotes$/i);
    if (m) return `读回并核验 ${m[1].replace(/^Step\s+(\d+)$/i, '第 $1 步')} 正文与脚注全部修订痕迹`;

    m = s.match(/^Print\s+markup\s+readback\s+of\s+all\s+(.+?)\s+Track\s+Changes$/i);
    if (m) return `输出并核对 ${m[1].replace(/^Step\s+(\d+)$/i, '第 $1 步')} 修订标记读回清单`;

    const rules = [
      [/^Git\s+status\s+check$/i, '检查 Git 仓库工作树状态'],
      [/^Unit\s+test\s+execution$/i, '运行单元测试套件'],
      [/^Package\s+build$/i, '执行工程打包与构建'],
      [/^Dev\s+server\s+startup$/i, '启动本地开发服务'],
      [/^Re-extract\s+(.+)$/i, (_, a) => `重新提取${translateEnglishPhrasePartsToZh(a)}`],
      [/^Capture\s+(.+)$/i, (_, a) => `生成${translateEnglishPhrasePartsToZh(a)}`],
      [/^Fetch\s+(.+)$/i, (_, a) => `拉取${translateEnglishPhrasePartsToZh(a)}`],
      [/^Discover\s+(.+)$/i, (_, a) => `探测${translateEnglishPhrasePartsToZh(a)}`],
      [/^Read\s+back\s+and\s+verify\s+(.+)$/i, (_, a) => `读回并核验 ${translateEnglishPhrasePartsToZh(a)}`],
      [/^(?:Run|Execute)\s+(.+?)\s+tests?$/i, (_, a) => `运行 ${translateEnglishPhrasePartsToZh(a)} 测试验证`],
      [/^(?:Run|Execute)\s+(.+)$/i, (_, a) => `运行 ${translateEnglishPhrasePartsToZh(a)}`],
      [/^Verify\s+(.+)$/i, (_, a) => `核验 ${translateEnglishPhrasePartsToZh(a)}`],
      [/^Check\s+(.+)$/i, (_, a) => `核查 ${translateEnglishPhrasePartsToZh(a)}`],
      [/^(?:Inspect|Analyze|Read)\s+(.+)$/i, (_, a) => `分析 ${translateEnglishPhrasePartsToZh(a)}`],
      [/^(?:Build|Compile)\s+(.+)$/i, (_, a) => `构建 ${translateEnglishPhrasePartsToZh(a)}`],
      [/^(?:Update|Modify|Patch|Edit)\s+(.+)$/i, (_, a) => `更新 ${translateEnglishPhrasePartsToZh(a)}`],
      [/^(?:Fix|Resolve)\s+(.+)$/i, (_, a) => `修复 ${translateEnglishPhrasePartsToZh(a)}`],
      [/^(?:Create|Generate|Write)\s+(.+)$/i, (_, a) => `生成 ${translateEnglishPhrasePartsToZh(a)}`],
      [/^(?:Search|Find|Grep)\s+(?:for\s+)?(.+)$/i, (_, a) => `检索 ${translateEnglishPhrasePartsToZh(a)}`]
    ];
    for (const [re, rep] of rules) {
      const rm = s.match(re);
      if (rm) {
        return typeof rep === 'function' ? rep(...rm) : rep;
      }
    }
    return '';
  }

  function describeStepQualitatively(stepWrapper) {
    if (!stepWrapper || typeof stepWrapper !== 'object') return '';
    const meta = stepWrapper.metadata || {};
    const stepCase = stepWrapper.step?.case || '';
    const val = stepWrapper.step?.value || {};
    const args = (stepCase === 'generic' && val.args && typeof val.args === 'object') ? val.args : {};
    const toolName = meta.toolCall?.name || stepCase || '';

    let summary =
      args.toolSummary ||
      args.toolAction ||
      meta.toolSummary ||
      meta.toolAction ||
      '';
    if (!summary && meta.toolCall?.argumentsJson) {
      try {
        const parsed = JSON.parse(meta.toolCall.argumentsJson);
        summary = parsed.toolSummary || parsed.toolAction || '';
      } catch (e) {}
    }
    if (summary) {
      const zhSummary = translateEnglishToolSummaryToZh(summary);
      if (zhSummary) {
        return zhSummary;
      }
    }

    if (toolName === 'read_file' || stepCase === 'viewFile') {
      const bname = basenameFromUriOrPath(args.Path || args.AbsolutePath || val.absolutePathUri || '');
      return bname ? `核阅 ${bname} 内容与实现逻辑` : '核阅工程文件内容';
    }
    if (toolName === 'edit_file' || stepCase === 'codeAction') {
      const uri =
        args.Path ||
        args.TargetFile ||
        val.fileUri ||
        val.targetFileUri ||
        val.actionSpec?.spec?.value?.fileUri ||
        val.actionSpec?.spec?.value?.path ||
        '';
      const bname = basenameFromUriOrPath(uri);
      return bname ? `修改并更新 ${bname} 实现逻辑` : '更新工程代码实现';
    }
    if (toolName === 'write_file' || stepCase === 'writeToFile') {
      const bname = basenameFromUriOrPath(args.Path || args.TargetFile || val.fileUri || '');
      return bname ? `写入并生成 ${bname}` : '创建并写入工程文件';
    }
    if (toolName === 'run_command' || stepCase === 'runCommand') {
      const cmd = String(args.CommandLine || val.commandLine || val.command || '').trim();
      const scriptMatch = cmd.match(/([a-zA-Z0-9_.-]+\.(?:py|mjs|js|ts|ps1|bat|sh))/i);
      const sName = scriptMatch ? scriptMatch[1] : '';
      if (/verify|test|check/i.test(cmd)) return sName ? `运行 ${sName} 校验测试` : '运行自动化验证脚本核查结果';
      if (/build|asar|upgrade|patch/i.test(cmd)) return sName ? `运行 ${sName} 构建与打包` : '运行构建与补丁打包脚本';
      if (/msedge|screenshot/i.test(cmd)) return '生成页面渲染预览截图';
      if (sName) return `执行 ${sName} 脚本任务`;
      return '在工作区执行终端核验命令';
    }
    if (toolName === 'invoke_subagent' || stepCase === 'invokeSubagent') {
      const role = args.Role || (Array.isArray(val.subagents) && val.subagents[0]?.role) || '';
      if (role) {
        return `派生子智能体（${cleanNaturalSentence(role)}）并行处理专项任务`;
      }
      return '派生子智能体并行执行专项任务';
    }
    if (toolName === 'ask_question' || stepCase === 'askQuestion') {
      return '向您请示设计方案与关键取舍';
    }
    if (toolName === 'grep_search' || toolName === 'find_by_name' || stepCase === 'grepSearch' || stepCase === 'find' || stepCase === 'codeSearch') {
      const q = args.Query || args.Pattern || val.query || val.pattern || '';
      return q ? `在工程中检索 ${q} 相关定义与引用` : '在工程中检索相关代码与文件';
    }
    if (toolName === 'list_dir' || stepCase === 'listDirectory') {
      const bname = basenameFromUriOrPath(args.DirectoryPath || val.directoryPathUri || '');
      return bname ? `盘点 ${bname} 目录结构` : '盘点工作区目录结构';
    }
    return '';
  }

  function enhanceConversationStreamDOM() {
    if (!document.querySelectorAll) return;

    // Build a lookup from thinking text -> next tool step qualitative description across all tool groups
    const nextToolDescByThinking = new Map();
    const groupButtons = document.querySelectorAll('button[data-testid="tool-group-collapsible"]');
    for (const gbtn of groupButtons) {
      const ibProps = findAncestorFiberProps(gbtn, (p) => Array.isArray(p.steps));
      const steps = ibProps?.steps || [];
      for (let i = 0; i < steps.length; i++) {
        const st = steps[i];
        if (st.step?.case === 'plannerResponse') {
          const th = st.step.value?.rawThinking || st.step.value?.thinking || '';
          if (th) {
            for (let j = i + 1; j < Math.min(steps.length, i + 4); j++) {
              const nxt = steps[j];
              if (nxt.step?.case !== 'plannerResponse') {
                const d = describeStepQualitatively(nxt);
                if (d) {
                  nextToolDescByThinking.set(th, d);
                  break;
                }
              }
            }
          }
        }
      }
    }

    // 1. Enhance Thinking Collapsible Triggers & Expanded Bodies
    const thinkButtons = document.querySelectorAll('button[data-testid="thinking-collapsible-trigger"]');
    for (const btn of thinkButtons) {
      const uid = getStableDomUid(btn);
      const inlineId = `agy-stream-think-inline-${uid}`;
      const digestId = `agy-stream-think-digest-${uid}`;

      const vebProps = findAncestorFiberProps(btn, (p) => typeof p.thinking === 'string');
      let rawThinking = vebProps?.thinking || '';
      const expanded = btn.getAttribute('aria-expanded') === 'true';
      const wrapper = btn.parentElement;

      if (!rawThinking && expanded && wrapper) {
        rawThinking = wrapper.textContent || '';
      }

      const nextToolDesc = nextToolDescByThinking.get(rawThinking) || '';
      const digest = parseThinkingQualitativeDigest(rawThinking, nextToolDesc);
      let inlineEl = document.getElementById(inlineId);
      if (digest.headline) {
        if (!inlineEl) {
          inlineEl = document.createElement('span');
          inlineEl.id = inlineId;
          inlineEl.className = 'agy-think-inline-summary';
          btn.appendChild(inlineEl);
        }
        if (inlineEl.textContent !== digest.headline) {
          inlineEl.textContent = digest.headline;
        }
      } else if (inlineEl) {
        inlineEl.remove();
      }

      if (expanded && wrapper && (digest.intent || digest.judgment)) {
        const contentPane = btn.nextElementSibling;
        if (contentPane) {
          let cardEl = document.getElementById(digestId);
          if (!cardEl) {
            cardEl = document.createElement('div');
            cardEl.id = digestId;
            cardEl.className = 'agy-think-digest-card';
            contentPane.prepend(cardEl);
          }
          const rowsHtml = [];
          if (digest.intent) {
            rowsHtml.push(
              `<div class="agy-think-digest-row"><span class="agy-think-digest-label">本步意图：</span><span class="agy-think-digest-val">${escapeHtml(digest.intent)}</span></div>`
            );
          }
          if (digest.judgment && digest.judgment !== digest.intent) {
            rowsHtml.push(
              `<div class="agy-think-digest-row"><span class="agy-think-digest-label">核心判断：</span><span class="agy-think-digest-val">${escapeHtml(digest.judgment)}</span></div>`
            );
          }
          if (digest.nextStep && digest.nextStep !== digest.judgment && digest.nextStep !== digest.intent) {
            rowsHtml.push(
              `<div class="agy-think-digest-row"><span class="agy-think-digest-label">下一步：</span><span class="agy-think-digest-val">${escapeHtml(digest.nextStep)}</span></div>`
            );
          }
          const nextHtml = rowsHtml.join('');
          if (cardEl.getAttribute('data-sig') !== nextHtml) {
            cardEl.setAttribute('data-sig', nextHtml);
            cardEl.innerHTML = nextHtml;
          }
        }
      }
    }

    // 2. Enhance Tool Group & Worked-For Collapsible Headers with Qualitative Purpose
    const summaryGroupBtns = document.querySelectorAll(
      'button[data-testid="tool-group-collapsible"], button[data-testid="worked-for-collapsible"]'
    );
    for (const gbtn of summaryGroupBtns) {
      const uid = getStableDomUid(gbtn);
      const subId = `agy-stream-stage-sub-${uid}`;
      const fiberProps = findAncestorFiberProps(
        gbtn,
        (p) => Array.isArray(p.steps) || Array.isArray(p.segment?.steps)
      );
      const steps = fiberProps?.steps || fiberProps?.segment?.steps || [];

      const descs = [];
      for (let i = steps.length - 1; i >= 0; i--) {
        const d = describeStepQualitatively(steps[i]);
        if (d && !descs.includes(d)) {
          descs.unshift(d);
          if (descs.length >= 2) break;
        }
      }

      let qualitativeText = '';
      if (descs.length > 0) {
        qualitativeText = descs.join(' · ');
      }

      let subEl = document.getElementById(subId);
      if (qualitativeText) {
        if (!subEl) {
          subEl = document.createElement('span');
          subEl.id = subId;
          subEl.className = 'agy-stage-qualitative-sub';
          gbtn.appendChild(subEl);
        }
        if (subEl.textContent !== qualitativeText) {
          subEl.textContent = qualitativeText;
        }
      } else if (subEl) {
        subEl.remove();
      }
    }
  }

  function escapeHtml(str) {
    return String(str || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function detectHostLightMode() {
    try {
      if (document.body) {
        if (document.body.classList.contains('theme-light') || document.body.classList.contains('light')) return true;
        if (document.body.classList.contains('theme-dark') || document.body.classList.contains('dark')) return false;
      }
      if (document.documentElement) {
        if (document.documentElement.classList.contains('light')) return true;
        if (document.documentElement.classList.contains('dark')) return false;
      }
      const bgHex = resolveNativeCssVarHex('--background') || resolveNativeCssVarHex('--card');
      if (bgHex && /^#[0-9a-f]{6}$/i.test(bgHex)) {
        const r = parseInt(bgHex.slice(1, 3), 16);
        const g = parseInt(bgHex.slice(3, 5), 16);
        const b = parseInt(bgHex.slice(5, 7), 16);
        const luma = 0.299 * r + 0.587 * g + 0.114 * b;
        return luma > 160;
      }
    } catch (e) {}
    return true;
  }

  function isDiagnosticNoiseStep(st, desc) {
    if (!desc) return true;
    if (
      /^(清理临时|探测|核查DOM|查看.*字段|使用BigInt|读取构建产物|打印|盘点.*目录|在工程中检索|核阅.*内容|在工作区执行终端核验命令|核阅工程文件内容|更新工程代码实现|创建并写入工程文件)/.test(
        desc
      )
    ) {
      return true;
    }
    const sCase = st?.step?.case || '';
    const sVal = st?.step?.value || {};
    const args = (sCase === 'generic' && sVal.args && typeof sVal.args === 'object') ? sVal.args : {};
    const pathStr = String(args.Path || args.TargetFile || args.CommandLine || '');
    if (/probe_fiber_live|probe_ctx_live|hot_inject_live/i.test(pathStr)) {
      return true;
    }
    return false;
  }

  const LLM_SUMMARY_STORAGE_KEY = 'agy.v3.llmMissionCache.v1';
  const llmSummaryCacheByConvo = new Map();
  let llmSummaryInFlight = false;
  let activeLlmSummaryAbortCtrl = null;

  function loadPersistedLlmSummaryCache() {
    try {
      const raw = window.localStorage.getItem(LLM_SUMMARY_STORAGE_KEY);
      if (!raw) return;
      const parsed = JSON.parse(raw);
      if (parsed && typeof parsed === 'object') {
        for (const [k, v] of Object.entries(parsed)) {
          if (v && typeof v.title === 'string' && Array.isArray(v.tasks)) {
            llmSummaryCacheByConvo.set(k, v);
          }
        }
      }
    } catch (e) {}
  }

  function savePersistedLlmSummaryCache() {
    try {
      const obj = {};
      const entries = Array.from(llmSummaryCacheByConvo.entries()).slice(-24);
      for (const [k, v] of entries) {
        obj[k] = {
          sig: v.sig,
          title: v.title,
          tasks: v.tasks,
          updatedAt: v.updatedAt
        };
      }
      window.localStorage.setItem(LLM_SUMMARY_STORAGE_KEY, JSON.stringify(obj));
    } catch (e) {}
  }

  function buildTraceTextForLlm(allSteps) {
    const traceLines = [];
    const startIdx = Math.max(0, allSteps.length - 26);
    for (let i = startIdx; i < allSteps.length; i++) {
      const s = allSteps[i];
      if (!s) continue;
      const sc = s.step?.case || '';
      const sv = s.step?.value || {};
      const st = s.status;
      if (sc === 'userInput') {
        let uTxt = '';
        try {
          uTxt = JSON.stringify(sv, (k, v) => (typeof v === 'bigint' ? v.toString() : v));
        } catch (e) {}
        if (uTxt) traceLines.push(`[用户请求 step=${i}] ${uTxt.slice(0, 180)}`);
      } else if (sc === 'plannerResponse') {
        if (sv.thinking) traceLines.push(`[思考 step=${i}] ${String(sv.thinking).slice(0, 160)}`);
        if (sv.response) traceLines.push(`[模型回复 step=${i}] ${String(sv.response).slice(0, 180)}`);
      } else if (sc === 'generic') {
        const tname = s.metadata?.toolCall?.name || '';
        const args = sv.args || {};
        const target = args.toolSummary || args.Path || args.TargetFile || args.Role || args.CommandLine || '';
        if (/probe_fiber_live|probe_ctx_live|hot_inject_live|list_ls_rpcs/i.test(String(target))) continue;
        traceLines.push(`[步骤 step=${i} status=${st} tool=${tname}] ${String(target).slice(0, 140)}`);
      }
    }
    return traceLines;
  }

  function triggerLlmMissionSummarization(cid, sig, allSteps, isRunningNow) {
    if (disposed || !globalPrefs.enabled || !cid || llmSummaryInFlight) return;
    if (typeof window.fetch !== 'function') return;

    const csrf =
      window.__APP_CONFIG__?.csrfToken ||
      (document.cookie ? document.cookie.split('; ').find((a) => a.startsWith('csrfToken='))?.split('=')[1] : '') ||
      '';
    if (!csrf) return;

    const traceLines = buildTraceTextForLlm(allSteps);
    if (traceLines.length === 0) return;

    const existing = llmSummaryCacheByConvo.get(cid);
    const now = Date.now();
    if (existing && existing.lastAttemptTs && now - existing.lastAttemptTs < 3500) {
      return;
    }
    if (existing) {
      existing.lastAttemptTs = now;
    }

    llmSummaryInFlight = true;
    const ctrl = new AbortController();
    activeLlmSummaryAbortCtrl = ctrl;
    const timer = setTimeout(() => ctrl.abort(), 12000);

    const prompt = [
      '你是一个研发与学术工作台的实时态势总结器。请阅读以下当前会话最近的任务执行轨迹，从整体目的、设计与施工进展出发，用简练纯正的中文总结理解当前这一轮任务到底在干什么（严禁照抄会话标题，严禁出现英文单词或半中半英，严禁带【】或[]括号标签）。',
      isRunningNow
        ? '注意：当前任务仍在推进中，请将最后一个正在进行的阶段 status 标为 "active"，已完成的前序阶段标为 "done"。'
        : '注意：当前各阶段已执行就绪，请将已完成阶段 status 标为 "done"。',
      '请仅输出严格合法的 JSON 对象（不要 markdown 代码块标记），格式如下：',
      '{"title":"用12-24个纯中文汉字概括当前这一轮任务在干什么","tasks":[{"status":"done或active","title":"宏观阶段名称（纯中文，10-26字，共3到4项）","outcome":"定性结果或进展（纯中文，4-12字）"}]}',
      '当前会话轨迹：',
      traceLines.join('\n')
    ].join('\n');

    (async () => {
      try {
        const resp = await window.fetch(
          `${window.location.origin}/exa.language_server_pb.LanguageServerService/GetModelResponse`,
          {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
              'Connect-Protocol-Version': '1',
              'x-codeium-csrf-token': csrf
            },
            body: JSON.stringify({
              prompt,
              model: 'MODEL_PLACEHOLDER_M18'
            }),
            signal: ctrl.signal
          }
        );
        if (!resp.ok) return;
        const data = await resp.json();
        let rawResp = String(data?.response || '').trim();
        rawResp = rawResp.replace(/^```(?:json)?\s*/i, '').replace(/\s*```$/, '').trim();
        const mJson = rawResp.match(/\{[\s\S]*\}/);
        if (!mJson) return;
        const parsed = JSON.parse(mJson[0]);
        if (parsed && typeof parsed.title === 'string' && Array.isArray(parsed.tasks) && parsed.tasks.length > 0) {
          const cleanTitle = cleanNaturalSentence(parsed.title).replace(/[【】\[\]]/g, '');
          const cleanTasks = parsed.tasks
            .slice(0, 4)
            .map((t) => ({
              status: t.status === 'active' ? 'active' : t.status === 'todo' ? 'todo' : 'done',
              title: cleanNaturalSentence(t.title || '').replace(/[【】\[\]]/g, ''),
              outcome: cleanNaturalSentence(t.outcome || '').replace(/[【】\[\]]/g, '')
            }))
            .filter((t) => t.title.length >= 4);

          if (cleanTitle && cleanTasks.length > 0) {
            llmSummaryCacheByConvo.set(cid, {
              sig,
              title: cleanTitle,
              tasks: cleanTasks,
              updatedAt: Date.now(),
              lastAttemptTs: Date.now()
            });
            savePersistedLlmSummaryCache();
            if (!disposed && globalPrefs.enabled) {
              updateMissionHud();
            }
          }
        }
      } catch (e) {
      } finally {
        clearTimeout(timer);
        if (activeLlmSummaryAbortCtrl === ctrl) {
          activeLlmSummaryAbortCtrl = null;
        }
        llmSummaryInFlight = false;
      }
    })();
  }

  function summarizeFallbackFromSteps(allSteps, isRunningNow) {
    if (!Array.isArray(allSteps) || allSteps.length === 0) {
      return { missionTitle: '', phases: [] };
    }

    const editedFiles = [];
    const substantiveSteps = [];
    let latestModelHeading = '';

    for (let i = 0; i < allSteps.length; i++) {
      const st = allSteps[i];
      if (!st) continue;
      const sCase = st.step?.case || '';
      const sVal = st.step?.value || {};

      if (sCase === 'plannerResponse') {
        const resp = String(sVal.response || '');
        const firstLine = cleanNaturalSentence(resp.split(/[\r\n。；;！!]+/)[0] || '').replace(/（[^）]*）/g, '');
        if (firstLine && /[\u4e00-\u9fa5]{6,}/.test(firstLine)) {
          let condensed = firstLine
            .replace(/^(?:主人[，,：:\s]*)?(?:我已经?|我已为您|已经?|目前已|现已|本次已)?(?:按照?您的要求|按刚才对齐的[^，,]*[，,]?)?\s*/, '')
            .replace(/^将(.+?)全部(.+?)(?:到了|至)(.+?)中$/, '$2$1至$3')
            .replace(/^将(.+?)(?:落实|更新|写入)(?:到了|至)(.+?)中$/, '落实$1至$2')
            .trim();
          if (condensed.length > 38) {
            condensed = condensed.split(/[，,]/)[0].trim();
          }
          if (condensed.length >= 8 && condensed.length <= 42) {
            latestModelHeading = condensed;
          }
        }
        continue;
      }

      const args = (sCase === 'generic' && sVal.args && typeof sVal.args === 'object') ? sVal.args : {};
      const toolName = st.metadata?.toolCall?.name || sCase || '';

      if (toolName === 'edit_file' || toolName === 'write_file' || sCase === 'codeAction') {
        const bname = basenameFromUriOrPath(args.Path || args.TargetFile || valUri(sVal) || '');
        if (bname && !/^probe_|^tmp_|hot_inject_live|list_ls_rpcs/i.test(bname) && !editedFiles.includes(bname)) {
          editedFiles.push(bname);
        }
      }

      const d = describeStepQualitatively(st);
      if (d && !isDiagnosticNoiseStep(st, d) && !/[a-zA-Z]{4,}\s+[a-zA-Z]{4,}/.test(d)) {
        substantiveSteps.push({ st, desc: d, editedFile: basenameFromUriOrPath(args.Path || args.TargetFile || '') });
      }
    }

    let buckets = [];
    if (substantiveSteps.length > 0) {
      const targetCount = Math.min(4, substantiveSteps.length);
      const chunkSz = Math.max(1, Math.ceil(substantiveSteps.length / targetCount));
      for (let i = 0; i < substantiveSteps.length; i += chunkSz) {
        buckets.push(substantiveSteps.slice(i, i + chunkSz));
      }
      buckets = buckets.slice(-4);
    }

    const phases = [];
    const seenTitles = new Set();
    for (let bIdx = 0; bIdx < buckets.length; bIdx++) {
      const grp = buckets[bIdx];
      const isLast = bIdx === buckets.length - 1;
      let grpRunning = false;
      let chosenTitle = grp[grp.length - 1].desc;
      const grpFiles = [];

      for (const item of grp) {
        const sStatus = item.st.status;
        if (
          sStatus === 1 ||
          sStatus === 2 ||
          sStatus === 9 ||
          sStatus === 'CORTEX_STEP_STATUS_RUNNING' ||
          sStatus === 'CORTEX_STEP_STATUS_GENERATING' ||
          sStatus === 'CORTEX_STEP_STATUS_WAITING'
        ) {
          grpRunning = true;
        }
        if (item.editedFile && !/^probe_|^tmp_|list_ls_rpcs/i.test(item.editedFile) && !grpFiles.includes(item.editedFile)) {
          grpFiles.push(item.editedFile);
        }
        if (!/^修改并更新|^写入并生成/.test(item.desc)) {
          chosenTitle = item.desc;
        }
      }

      if (seenTitles.has(chosenTitle) && grpFiles.length > 0) {
        chosenTitle = `更新 ${grpFiles.slice(0, 2).join('、')} 核心实现`;
      }
      seenTitles.add(chosenTitle);

      const active = grpRunning || (isLast && isRunningNow);
      phases.push({
        status: active ? 'active' : 'done',
        title: chosenTitle,
        outcome: active ? '正在推进' : (grpFiles.length > 0 ? `已更新 ${grpFiles.slice(0, 2).join('、')}` : '已完成')
      });
    }

    const missionTitle = latestModelHeading || (phases.length > 0 ? phases[phases.length - 1].title : '');
    return { missionTitle, phases };
  }

  function valUri(sVal) {
    if (!sVal || typeof sVal !== 'object') return '';
    return sVal.fileUri || sVal.targetFileUri || sVal.absolutePathUri || '';
  }

  function collectLiveSessionMissionState() {
    const cid = getActiveConversationId();
    let globalGoal = '';
    const explicitTasks = [];
    const activeRuntimes = [];

    if (!document.querySelectorAll) {
      return { hasData: false, globalGoal, tasks: [], activeRuntimes: [], isRunning: false };
    }

    if (llmSummaryCacheByConvo.size === 0) {
      loadPersistedLlmSummaryCache();
    }

    const activeCv = getActiveConversationView() || document;

    // 1. Check explicit mission plan registered on globalThis.__AGY_MISSION_PLAN__
    const customPlan = globalThis.__AGY_MISSION_PLAN__ && (globalThis.__AGY_MISSION_PLAN__[cid] || globalThis.__AGY_MISSION_PLAN__.default);
    if (customPlan) {
      if (customPlan.title) globalGoal = cleanNaturalSentence(customPlan.title);
      if (Array.isArray(customPlan.tasks) && customPlan.tasks.length > 0) {
        for (const ct of customPlan.tasks) {
          explicitTasks.push({
            status: ct.status || 'todo',
            title: cleanNaturalSentence(ct.title || ''),
            outcome: cleanNaturalSentence(ct.outcome || '')
          });
        }
      }
    }

    // 2. Collect full stepsInSlice from activeCv's agentStateProvider (plus visible groups fallback)
    const allSteps = [];
    const seenStepObjs = new Set();
    const seenRuntimeKeys = new Set();
    let totalStepsCount = 0;

    const av = activeCv.querySelector ? activeCv.querySelector('[data-testid="autoscroll-viewport"]') : null;
    if (av) {
      const ctxHolder = findAncestorFiberProps(av, (p) => Boolean(p.value?.cascadeContext?.state?.agentStateProvider), 20);
      const provider = ctxHolder?.value?.cascadeContext?.state?.agentStateProvider;
      if (provider && typeof provider.getState === 'function') {
        const st = provider.getState();
        totalStepsCount = Number(st?.trajectorySlice?.totalStepsLength || 0);
        const sliceSteps = st?.trajectorySlice?.stepsInSlice || [];
        for (const s of sliceSteps) {
          if (s && !seenStepObjs.has(s)) {
            seenStepObjs.add(s);
            allSteps.push(s);
          }
        }
      }
    }

    const allGroupBtns = activeCv.querySelectorAll
      ? Array.from(activeCv.querySelectorAll('button[data-testid="tool-group-collapsible"], button[data-testid="worked-for-collapsible"]'))
      : [];
    for (const gb of allGroupBtns) {
      const fiberProps = findAncestorFiberProps(
        gb,
        (p) => Array.isArray(p.steps) || Array.isArray(p.segment?.steps)
      );
      const steps = fiberProps?.steps || fiberProps?.segment?.steps || [];
      for (const s of steps) {
        if (s && !seenStepObjs.has(s)) {
          seenStepObjs.add(s);
          allSteps.push(s);
        }
      }
    }

    for (let i = 0; i < allSteps.length; i++) {
      const st = allSteps[i];
      const sCase = st.step?.case || '';
      const sVal = st.step?.value || {};
      const args = (sCase === 'generic' && sVal.args && typeof sVal.args === 'object') ? sVal.args : {};
      const toolName = st.metadata?.toolCall?.name || sCase || '';
      const sStatus = st.status;
      const isRunning =
        sStatus === 1 ||
        sStatus === 2 ||
        sStatus === 9 ||
        sStatus === 'CORTEX_STEP_STATUS_RUNNING' ||
        sStatus === 'CORTEX_STEP_STATUS_GENERATING' ||
        sStatus === 'CORTEX_STEP_STATUS_WAITING';

      if (sCase === 'plannerResponse' || !isRunning) continue;

      const desc = describeStepQualitatively(st);
      if (toolName === 'run_command' || sCase === 'runCommand') {
        const key = `cmd:${st.metadata?.sourceTrajectoryStepInfo?.stepIndex ?? i}`;
        if (!seenRuntimeKeys.has(key)) {
          seenRuntimeKeys.add(key);
          activeRuntimes.push({
            kind: 'cmd',
            role: desc || '正在执行终端核验任务',
            statusText: '运行中'
          });
        }
      } else if (toolName === 'invoke_subagent' || sCase === 'invokeSubagent') {
        const roleName = cleanNaturalSentence(args.Role || (Array.isArray(sVal.subagents) && sVal.subagents[0]?.role) || '专项子智能体');
        const key = `sub:${roleName}:${i}`;
        if (!seenRuntimeKeys.has(key)) {
          seenRuntimeKeys.add(key);
          activeRuntimes.push({
            kind: 'subagent',
            role: `子智能体（${roleName}）正在独立执行专项核查`,
            statusText: '独立运行中'
          });
        }
      } else if (desc) {
        const key = `act:${desc}`;
        if (!seenRuntimeKeys.has(key)) {
          seenRuntimeKeys.add(key);
          activeRuntimes.push({
            kind: 'tool',
            role: desc,
            statusText: '执行中'
          });
        }
      }
    }

    const isRunningNow =
      realAgentState === 'RUNNING' ||
      realAgentState === 'THINKING' ||
      activeRuntimes.length > 0;

    // 3. Use real LLM mission summarization (via GetModelResponse MODEL_PLACEHOLDER_M18) with instant cache
    const sig = `${Math.floor((totalStepsCount || allSteps.length) / 3)}:${isRunningNow ? 'run' : 'idle'}`;
    const llmCached = cid ? llmSummaryCacheByConvo.get(cid) : null;
    if (cid && allSteps.length > 0 && (!llmCached || llmCached.sig !== sig)) {
      triggerLlmMissionSummarization(cid, sig, allSteps, isRunningNow);
    }

    let finalTasks = [];
    if (explicitTasks.length > 0) {
      finalTasks = explicitTasks;
    } else if (llmCached && Array.isArray(llmCached.tasks) && llmCached.tasks.length > 0) {
      finalTasks = llmCached.tasks;
      if (!globalGoal && llmCached.title) {
        globalGoal = llmCached.title;
      }
    } else if (allSteps.length > 0) {
      globalGoal = '正在理解并概括当前任务脉络…';
      finalTasks = [];
    }

    const hasData = Boolean(cid && (finalTasks.length > 0 || allSteps.length > 0));
    return {
      hasData,
      globalGoal: globalGoal || '正在理解并概括当前任务脉络…',
      tasks: finalTasks,
      activeRuntimes: activeRuntimes.slice(-2),
      isRunning: isRunningNow
    };
  }

  function hideMissionHud() {
    if (missionHudHost) {
      missionHudHost.style.display = 'none';
    }
    if (document.documentElement) {
      document.documentElement.removeAttribute('data-agy-hud-pad');
    }
  }

  function ensureMissionHudMounted() {
    if (missionHudHost && missionHudHost.parentElement === document.body && missionHudShadow) {
      return;
    }
    const existing = document.getElementById('agy-mission-hud-host');
    if (existing) existing.remove();

    missionHudHost = document.createElement('div');
    missionHudHost.id = 'agy-mission-hud-host';
    missionHudHost.style.position = 'fixed';
    missionHudHost.style.zIndex = '38';
    missionHudHost.style.display = 'none';
    document.body.appendChild(missionHudHost);

    missionHudShadow = missionHudHost.attachShadow({ mode: 'open' });
    missionHudShadow.innerHTML = `
      <style>
        :host {
          all: initial;
          font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", system-ui, sans-serif;
          user-select: none;
        }
        .mission-box {
          background: #fafafa;
          border: 1px solid rgba(0, 0, 0, 0.08);
          border-radius: 10px;
          box-shadow: 0 1px 4px rgba(0, 0, 0, 0.03);
          overflow: hidden;
          color: #1f2937;
          transition: border-color 0.15s ease;
        }
        .mission-box.dark {
          background: #1a1a1e;
          border-color: rgba(255, 255, 255, 0.09);
          box-shadow: 0 2px 8px rgba(0, 0, 0, 0.25);
          color: #e4e4e7;
        }
        .mission-top {
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 10px;
          padding: 6px 12px;
          border-bottom: 1px solid rgba(0, 0, 0, 0.05);
          cursor: pointer;
        }
        .mission-box.dark .mission-top {
          border-bottom-color: rgba(255, 255, 255, 0.06);
        }
        .mission-box.collapsed .mission-top {
          border-bottom: none;
        }
        .goal-left {
          display: flex;
          align-items: center;
          gap: 8px;
          min-width: 0;
          flex: 1;
        }
        .status-dot {
          width: 6px;
          height: 6px;
          border-radius: 50%;
          background: #10b981;
          flex-shrink: 0;
        }
        .status-dot.running {
          background: #2563eb;
        }
        .goal-text {
          font-size: 12px;
          font-weight: 600;
          color: #374151;
          white-space: nowrap;
          overflow: hidden;
          text-overflow: ellipsis;
        }
        .mission-box.dark .goal-text {
          color: #e4e4e7;
        }
        .top-right {
          display: flex;
          align-items: center;
          gap: 8px;
          flex-shrink: 0;
          font-size: 11.5px;
          color: #6b7280;
          font-variant-numeric: tabular-nums;
        }
        .mission-box.dark .top-right {
          color: #9ca3af;
        }
        .mission-body {
          padding: 5px 12px 6px;
          display: flex;
          flex-direction: column;
          gap: 2px;
        }
        .mission-box.collapsed .mission-body,
        .mission-box.collapsed .rt-bar {
          display: none !important;
        }
        .t-row {
          display: flex;
          align-items: center;
          gap: 7px;
          height: 22px;
          font-size: 12px;
          white-space: nowrap;
          overflow: hidden;
        }
        .t-chk {
          width: 12px;
          text-align: center;
          font-size: 11px;
          flex-shrink: 0;
          color: #9ca3af;
        }
        .t-row.active .t-chk {
          color: #2563eb;
          font-size: 9px;
        }
        .mission-box.dark .t-row.active .t-chk {
          color: #60a5fa;
        }
        .t-title {
          overflow: hidden;
          text-overflow: ellipsis;
          color: #4b5563;
        }
        .mission-box.dark .t-title {
          color: #d4d4d8;
        }
        .t-row.done .t-title {
          color: #9ca3af;
          text-decoration: line-through;
          text-decoration-color: #d1d5db;
          text-decoration-thickness: 1px;
        }
        .mission-box.dark .t-row.done .t-title {
          color: #71717a;
          text-decoration-color: #52525b;
        }
        .t-row.active .t-title {
          color: #111827;
          font-weight: 600;
        }
        .mission-box.dark .t-row.active .t-title {
          color: #fafafa;
        }
        .t-out {
          margin-left: auto;
          padding-left: 10px;
          font-size: 11px;
          color: #9ca3af;
          flex-shrink: 0;
        }
        .mission-box.dark .t-out {
          color: #71717a;
        }
        .t-row.active .t-out {
          color: #2563eb;
        }
        .mission-box.dark .t-row.active .t-out {
          color: #60a5fa;
        }
        .rt-bar {
          display: none;
          align-items: center;
          justify-content: space-between;
          gap: 8px;
          padding: 4px 12px;
          background: rgba(0, 0, 0, 0.025);
          border-top: 1px solid rgba(0, 0, 0, 0.05);
          font-size: 11.5px;
          color: #4b5563;
          white-space: nowrap;
          overflow: hidden;
        }
        .mission-box.dark .rt-bar {
          background: rgba(255, 255, 255, 0.03);
          border-top-color: rgba(255, 255, 255, 0.06);
          color: #a1a1aa;
        }
        .rt-bar.visible {
          display: flex;
        }
        .rt-text {
          overflow: hidden;
          text-overflow: ellipsis;
        }
        .rt-status {
          color: #2563eb;
          font-weight: 500;
          flex-shrink: 0;
        }
        .mission-box.dark .rt-status {
          color: #60a5fa;
        }
      </style>
      <div class="mission-box" id="m-box">
        <div class="mission-top" id="m-top" title="点击收起或展开阶段任务清单">
          <div class="goal-left">
            <span class="status-dot" id="m-dot"></span>
            <span class="goal-text" id="m-goal"></span>
          </div>
          <div class="top-right">
            <span id="m-phase"></span>
            <span id="m-fold">▾</span>
          </div>
        </div>
        <div class="mission-body" id="m-task-list"></div>
        <div class="rt-bar" id="m-rt-bar">
          <span class="rt-text" id="m-rt-text"></span>
          <span class="rt-status" id="m-rt-status"></span>
        </div>
      </div>
    `;

    missionHudRefs = {
      box: missionHudShadow.getElementById('m-box'),
      top: missionHudShadow.getElementById('m-top'),
      dot: missionHudShadow.getElementById('m-dot'),
      goal: missionHudShadow.getElementById('m-goal'),
      phase: missionHudShadow.getElementById('m-phase'),
      fold: missionHudShadow.getElementById('m-fold'),
      taskList: missionHudShadow.getElementById('m-task-list'),
      rtBar: missionHudShadow.getElementById('m-rt-bar'),
      rtText: missionHudShadow.getElementById('m-rt-text'),
      rtStatus: missionHudShadow.getElementById('m-rt-status')
    };

    if (missionHudRefs.top) {
      missionHudRefs.top.addEventListener('click', () => {
        missionHudCollapsed = !missionHudCollapsed;
        updateMissionHud();
      });
    }
  }

  function updateMissionHud() {
    const isLight = detectHostLightMode();
    if (document.documentElement) {
      document.documentElement.setAttribute('data-agy-color-mode', isLight ? 'light' : 'dark');
    }

    if (disposed || !globalPrefs.enabled || globalPrefs.lang === 'en' || globalPrefs.bossMode) {
      hideMissionHud();
      return;
    }

    const turboBtn = findActiveComposerTurboButton();
    if (!turboBtn) {
      hideMissionHud();
      return;
    }

    const footer = turboBtn.closest('[class*="@container/input-footer"]') || turboBtn.parentElement?.parentElement;
    const composerBox = footer?.parentElement || footer;
    if (!composerBox || !composerBox.getBoundingClientRect) {
      hideMissionHud();
      return;
    }

    const cRect = composerBox.getBoundingClientRect();
    if (cRect.width < 260 || cRect.top < 80 || hasVisibleOverlappingPopup(cRect)) {
      hideMissionHud();
      return;
    }

    const state = collectLiveSessionMissionState();
    if (!state.hasData) {
      hideMissionHud();
      return;
    }

    ensureMissionHudMounted();
    if (!missionHudRefs.box) return;

    const totalTasks = state.tasks.length;
    const doneTasks = state.tasks.filter((t) => t.status === 'done').length;
    const effectiveCollapsed = missionHudCollapsed || totalTasks === 0;

    missionHudRefs.box.classList.toggle('dark', !isLight);
    missionHudRefs.box.classList.toggle('collapsed', effectiveCollapsed);
    if (missionHudRefs.dot) {
      missionHudRefs.dot.classList.toggle('running', state.isRunning || totalTasks === 0);
    }
    if (missionHudRefs.fold) {
      missionHudRefs.fold.textContent = totalTasks === 0 ? '' : (missionHudCollapsed ? '展开 ▴' : '收起 ▾');
    }
    if (missionHudRefs.goal) {
      missionHudRefs.goal.textContent = state.globalGoal;
    }

    if (missionHudRefs.phase) {
      missionHudRefs.phase.textContent = totalTasks > 0 ? `${doneTasks} / ${totalTasks}` : '';
    }

    if (missionHudRefs.taskList) {
      const taskHtml = state.tasks
        .map((t, idx) => {
          const chk = t.status === 'done' ? '✓' : t.status === 'active' ? '●' : '○';
          const outSpan = t.outcome ? `<span class="t-out">${escapeHtml(t.outcome)}</span>` : '';
          const numPrefix = /^\d+\./.test(t.title) ? '' : `${idx + 1}. `;
          return `<div class="t-row ${t.status}"><span class="t-chk">${chk}</span><span class="t-title">${escapeHtml(numPrefix + t.title)}</span>${outSpan}</div>`;
        })
        .join('');
      if (missionHudRefs.taskList.getAttribute('data-sig') !== taskHtml) {
        missionHudRefs.taskList.setAttribute('data-sig', taskHtml);
        missionHudRefs.taskList.innerHTML = taskHtml;
      }
    }

    if (missionHudRefs.rtBar) {
      const hasActiveRt = state.activeRuntimes.length > 0;
      missionHudRefs.rtBar.classList.toggle('visible', hasActiveRt);
      if (hasActiveRt) {
        const latestRt = state.activeRuntimes[state.activeRuntimes.length - 1];
        if (missionHudRefs.rtText) {
          missionHudRefs.rtText.textContent = `正在运行：${latestRt.role}`;
        }
        if (missionHudRefs.rtStatus) {
          missionHudRefs.rtStatus.textContent = latestRt.statusText;
        }
      }
    }

    missionHudHost.style.display = 'block';
    missionHudHost.style.left = `${Math.round(cRect.left)}px`;
    missionHudHost.style.width = `${Math.round(cRect.width)}px`;
    const hudHeight = missionHudHost.getBoundingClientRect().height || (missionHudCollapsed ? 30 : 118);
    missionHudHost.style.top = `${Math.max(42, Math.round(cRect.top - hudHeight - 6))}px`;
    if (document.documentElement) {
      document.documentElement.setAttribute('data-agy-hud-pad', missionHudCollapsed ? 'collapsed' : 'expanded');
    }
  }

  // ---------------------------------------------------------------------------
  // 8. Native Settings Modal Integration (Language & Widescreen Controls)
  // ---------------------------------------------------------------------------
  function syncSettingsEnhancementRowsState() {
    const zhBtn = document.getElementById('agy-settings-lang-zh');
    const enBtn = document.getElementById('agy-settings-lang-en');
    const isEn = globalPrefs.lang === 'en';
    if (zhBtn && enBtn) {
      if (!isEn) {
        zhBtn.setAttribute('data-pressed', '');
        zhBtn.setAttribute('aria-pressed', 'true');
        enBtn.removeAttribute('data-pressed');
        enBtn.setAttribute('aria-pressed', 'false');
      } else {
        enBtn.setAttribute('data-pressed', '');
        enBtn.setAttribute('aria-pressed', 'true');
        zhBtn.removeAttribute('data-pressed');
        zhBtn.setAttribute('aria-pressed', 'false');
      }
    }

    const wideSwitch = document.getElementById('agy-settings-wide-switch');
    const wideKnob = document.getElementById('agy-settings-wide-knob');
    const isWide = Boolean(globalPrefs.widescreen);
    if (wideSwitch && wideKnob) {
      wideSwitch.setAttribute('aria-checked', isWide ? 'true' : 'false');
      wideSwitch.classList.toggle('bg-primary', isWide);
      wideSwitch.classList.toggle('bg-secondary', !isWide);
      wideKnob.style.transform = isWide ? 'translateX(24px)' : 'translateX(4px)';
    }
  }

  function ensureSettingsEnhancementRowsMounted() {
    if (disposed || !globalPrefs.enabled || !document.querySelector) return;
    const modal = document.querySelector('.settings-modal-container');
    if (!modal) return;

    // Locate the Appearance -> Theme card (contains button[aria-label="System"] and button[aria-label="Light"])
    const sysBtn = modal.querySelector('button[aria-label="System"]');
    const themeCard = sysBtn ? sysBtn.closest('.divide-y.divide-border') : null;
    if (!themeCard) return;

    let langRow = document.getElementById('agy-settings-lang-row');
    let wideRow = document.getElementById('agy-settings-wide-row');

    if (!langRow || !themeCard.contains(langRow)) {
      if (langRow) langRow.remove();
      langRow = document.createElement('div');
      langRow.id = 'agy-settings-lang-row';
      langRow.className = 'py-2 px-3 w-full bg-transparent flex justify-between gap-4 items-center';
      langRow.innerHTML = `
        <div class="flex-1 flex flex-col gap-0.5">
          <div class="flex items-center gap-1.5">
            <span class="text-sm">界面语言 (Interface Language)</span>
          </div>
          <span class="text-xs text-muted-foreground whitespace-pre-wrap">切换客户端全界面显示语言（中文专业汉化 / English 原生英文，快捷键 Alt+L）。</span>
        </div>
        <div class="shrink-0">
          <div data-orientation="horizontal" aria-orientation="horizontal" role="group" class="inline-flex items-center rounded-lg border border-border bg-secondary/20 p-0.5 gap-0.5 select-none h-8">
            <button type="button" id="agy-settings-lang-zh" class="inline-flex items-center justify-center transition-colors text-secondary-foreground outline-none cursor-pointer border-none shrink-0 select-none h-full hover:text-foreground data-[pressed]:bg-secondary data-[pressed]:text-foreground text-sm px-2.5 rounded-md gap-1.5">中文专业</button>
            <button type="button" id="agy-settings-lang-en" class="inline-flex items-center justify-center transition-colors text-secondary-foreground outline-none cursor-pointer border-none shrink-0 select-none h-full hover:text-foreground data-[pressed]:bg-secondary data-[pressed]:text-foreground text-sm px-2.5 rounded-md gap-1.5">English</button>
          </div>
        </div>
      `;
      themeCard.appendChild(langRow);

      const zhBtn = langRow.querySelector('#agy-settings-lang-zh');
      const enBtn = langRow.querySelector('#agy-settings-lang-en');
      if (zhBtn) {
        zhBtn.addEventListener('click', () => {
          patchGlobalPrefs({ lang: 'zh-std' });
          applyAllStates();
        });
      }
      if (enBtn) {
        enBtn.addEventListener('click', () => {
          patchGlobalPrefs({ lang: 'en' });
          applyAllStates();
        });
      }
    }

    if (!wideRow || !themeCard.contains(wideRow)) {
      if (wideRow) wideRow.remove();
      wideRow = document.createElement('div');
      wideRow.id = 'agy-settings-wide-row';
      wideRow.className = 'py-2 px-3 w-full bg-transparent flex justify-between gap-4 items-center';
      wideRow.innerHTML = `
        <div class="flex-1 flex flex-col gap-0.5">
          <div class="flex items-center gap-1.5">
            <span class="text-sm">宽屏学术排版</span>
          </div>
          <span class="text-xs text-muted-foreground whitespace-pre-wrap">突破默认宽度限制，将会话阅读区与表格扩展至 92% 视口宽度（上限 1480px）。</span>
        </div>
        <div class="shrink-0">
          <button type="button" role="switch" id="agy-settings-wide-switch" aria-checked="false" class="relative inline-flex items-center rounded-full transition-colors duration-200 ease-in-out shrink-0 h-6 w-11 bg-secondary cursor-pointer">
            <span id="agy-settings-wide-knob" class="inline-block rounded-full bg-white transition-transform duration-200 ease-in-out shadow-sm h-4 w-4" style="transform: translateX(4px);"></span>
          </button>
        </div>
      `;
      themeCard.appendChild(wideRow);

      const wideSwitch = wideRow.querySelector('#agy-settings-wide-switch');
      if (wideSwitch) {
        wideSwitch.addEventListener('click', () => {
          patchGlobalPrefs({ widescreen: !globalPrefs.widescreen });
          applyAllStates();
        });
      }
    }

    syncSettingsEnhancementRowsState();
  }

  // ---------------------------------------------------------------------------
  // 9. Consumer Suspension (Mode C) & Non-Self-Triggering Observers
  // ---------------------------------------------------------------------------
  let mutObserver = null;
  let ringTimer = null;
  let locThrottleTimer = null;

  function removeLegacyFloatingHosts() {
    const el = document.getElementById('agy-hud-host');
    if (el) el.remove();
  }

  function suspendAllActiveConsumers() {
    consumersActive = false;
    if (activeMetadataAbortCtrl) {
      try {
        activeMetadataAbortCtrl.abort();
      } catch (e) {}
      activeMetadataAbortCtrl = null;
    }
    if (activeLlmSummaryAbortCtrl) {
      try {
        activeLlmSummaryAbortCtrl.abort();
      } catch (e) {}
      activeLlmSummaryAbortCtrl = null;
    }
    llmSummaryInFlight = false;
    if (ringTimer) {
      clearInterval(ringTimer);
      ringTimer = null;
    }
    if (locThrottleTimer) {
      clearTimeout(locThrottleTimer);
      locThrottleTimer = null;
    }
    if (trailingFetchTimer) {
      clearTimeout(trailingFetchTimer);
      trailingFetchTimer = null;
    }
    if (mutObserver) {
      mutObserver.disconnect();
      mutObserver = null;
    }
    window.removeEventListener('resize', onWindowResize);
    document.removeEventListener('visibilitychange', onVisibilityChange);
    document.removeEventListener('mousedown', onDocumentMouseDownForRing, true);
    ctxRingHovered = false;
    ctxRingPinned = false;

    document.documentElement.removeAttribute('data-agy-widescreen');
    restoreAllLocalizationOnDispose();
    restoreNativeTitleBarOverlay();
    removeLegacyFloatingHosts();

    for (const id of [
      'agy-enhance-scoped-style',
      'agy-ctx-ring-pill',
      'agy-mission-hud-host',
      'agy-settings-lang-row',
      'agy-settings-wide-row'
    ]) {
      const el = document.getElementById(id);
      if (el) el.remove();
    }
    ctxRingPill = null;
    missionHudHost = null;
    missionHudShadow = null;
    missionHudRefs = {};
  }

  function ensureActiveConsumersMounted() {
    if (disposed || !globalPrefs.enabled) return;
    removeLegacyFloatingHosts();
    if (!consumersActive) {
      consumersActive = true;
      window.addEventListener('resize', onWindowResize, { passive: true });
      document.addEventListener('visibilitychange', onVisibilityChange);
      document.addEventListener('mousedown', onDocumentMouseDownForRing, true);

      ringTimer = setInterval(() => {
        if (disposed || !globalPrefs.enabled || document.hidden) return;
        updateFloatingContextRing();
        pollLiveAgentState();
      }, 600);

      mutObserver = new MutationObserver((mutations) => {
        if (disposed || !globalPrefs.enabled) return;
        let hasExternalMutation = false;
        for (const m of mutations) {
          const t = m.target;
          const el = t.nodeType === Node.ELEMENT_NODE ? t : t.parentElement;
          // Strictly filter out our own #agy-* host/ring updates so ringTimer never self-triggers mutObserver!
          if (el && ((el.id && el.id.startsWith('agy-')) || (el.closest && el.closest('[id^="agy-"]')))) {
            continue;
          }
          hasExternalMutation = true;
          if (m.type === 'characterData') {
            processTextNode(t);
          } else if (m.type === 'attributes' && m.attributeName === 'placeholder') {
            processInputPlaceholder(t);
          } else if (m.type === 'childList' && m.addedNodes && m.addedNodes.length > 0) {
            for (const added of m.addedNodes) {
              if (!added) continue;
              if (added.nodeType === Node.TEXT_NODE) {
                processTextNode(added);
              } else if (added.nodeType === Node.ELEMENT_NODE && (!added.id || !added.id.startsWith('agy-'))) {
                const w = document.createTreeWalker(added, NodeFilter.SHOW_TEXT, null);
                while (w.nextNode()) {
                  processTextNode(w.currentNode);
                }
                if (added.querySelectorAll) {
                  for (const inp of added.querySelectorAll('input[placeholder], textarea[placeholder]')) {
                    processInputPlaceholder(inp);
                  }
                }
              }
            }
          }
        }
        if (!hasExternalMutation) return;

        if (!locThrottleTimer) {
          locThrottleTimer = setTimeout(() => {
            locThrottleTimer = null;
            if (!disposed && globalPrefs.enabled) {
              sanitizeWorkspaceAndGoogleCorpClutter();
              applyLocalizationToSubtree(document.body);
              ensureSettingsEnhancementRowsMounted();
              pollLiveAgentState();
              scheduleTokenMetadataRefresh(false);
            }
          }, 60);
        }
      });

      mutObserver.observe(document.body, {
        childList: true,
        subtree: true,
        characterData: true,
        attributes: true,
        attributeFilter: ['placeholder']
      });
    }
  }

  function applyAllStates() {
    if (disposed) return;

    globalPrefs.theme = 'native';
    if (globalPrefs.lang !== 'en') {
      globalPrefs.lang = 'zh-std';
    }

    if (!globalPrefs.enabled) {
      suspendAllActiveConsumers();
      return;
    }

    ensureActiveConsumersMounted();
    ensureScopedThemeStyle();
    sanitizeWorkspaceAndGoogleCorpClutter();
    restoreNativeTitleBarOverlay();

    if (globalPrefs.widescreen) {
      document.documentElement.setAttribute('data-agy-widescreen', '1');
    } else {
      document.documentElement.removeAttribute('data-agy-widescreen');
    }

    applyLocalizationToSubtree(document.body);
    ensureSettingsEnhancementRowsMounted();
    if (globalPrefs.lang === 'en') {
      if (document.querySelectorAll) {
        const injected = document.querySelectorAll('[id^="agy-stream-"]');
        for (const el of injected) el.remove();
      }
      hideMissionHud();
    } else {
      enhanceConversationStreamDOM();
      updateMissionHud();
    }
    scheduleTokenMetadataRefresh(false);
    updateFloatingContextRing();
  }

  function onKeyDown(e) {
    if (disposed || !globalPrefs.enabled) return;
    if (e.altKey && (e.key === 'l' || e.key === 'L' || e.code === 'KeyL')) {
      e.preventDefault();
      patchGlobalPrefs({ lang: globalPrefs.lang === 'en' ? 'zh-std' : 'en' });
      applyAllStates();
    } else if (e.altKey && (e.key === 'w' || e.key === 'W' || e.code === 'KeyW')) {
      e.preventDefault();
      patchGlobalPrefs({ widescreen: !globalPrefs.widescreen });
      applyAllStates();
    }
  }

  function onVisibilityChange() {
    if (!document.hidden && globalPrefs.enabled) {
      scheduleTokenMetadataRefresh(false);
      updateFloatingContextRing();
      updateMissionHud();
    }
  }

  function onWindowResize() {
    if (!disposed && globalPrefs.enabled) {
      updateFloatingContextRing();
      updateMissionHud();
    }
  }

  let unsubscribeBridgePrefs = null;

  function setupTopLevelListeners() {
    window.addEventListener('keydown', onKeyDown, true);

    if (bridge && typeof bridge.onPreferencesChanged === 'function') {
      unsubscribeBridgePrefs = bridge.onPreferencesChanged((payload) => {
        if (disposed || !payload) return;
        if (typeof payload.lsGeneration === 'number' && payload.lsGeneration !== currentLsGeneration) {
          currentLsGeneration = payload.lsGeneration;
        }
        if (typeof payload.revision === 'number' && payload.revision > localRevision) {
          localRevision = payload.revision;
          if (payload.global) {
            Object.assign(globalPrefs, payload.global);
            applyAllStates();
          }
        }
      });
    }
  }

  function dispose() {
    if (disposed) return;
    disposed = true;

    suspendAllActiveConsumers();

    if (typeof unsubscribeBridgePrefs === 'function') {
      try {
        unsubscribeBridgePrefs();
      } catch (e) {}
      unsubscribeBridgePrefs = null;
    }

    window.removeEventListener('keydown', onKeyDown, true);
    window.removeEventListener('resize', onWindowResize);
    document.removeEventListener('visibilitychange', onVisibilityChange);
    removeLegacyFloatingHosts();
  }

  async function init() {
    if (!document.body) {
      await new Promise((resolve) => {
        window.addEventListener('DOMContentLoaded', resolve, { once: true });
      });
    }
    if (disposed) return { status: 'disposed', version: VERSION, instanceId };

    await runOneTimeLegacyFlagsReset();
    loadLocalFallbackPrefs();

    if (bridge && typeof bridge.getPreferences === 'function') {
      try {
        const snap = await bridge.getPreferences();
        if (snap && !disposed) {
          if (typeof snap.revision === 'number') localRevision = snap.revision;
          if (typeof snap.lsGeneration === 'number') currentLsGeneration = snap.lsGeneration;
          if (snap.global) Object.assign(globalPrefs, snap.global);
        }
      } catch (e) {}
    }

    if (disposed) return { status: 'disposed', version: VERSION, instanceId };

    removeLegacyFloatingHosts();
    setupTopLevelListeners();
    applyAllStates();
    if (globalPrefs.enabled) {
      scheduleTokenMetadataRefresh(true);
    }

    const readyReceipt = { status: 'ready', version: VERSION, instanceId };
    if (globalThis.__AGY_V3__) {
      globalThis.__AGY_V3__.status = 'ready';
      globalThis.__AGY_V3__.ready = readyReceipt;
    }
    return readyReceipt;
  }

  const initPromise = init();

  globalThis.__AGY_V3__ = {
    status: 'initializing',
    version: VERSION,
    instanceId,
    ready: null,
    readyPromise: initPromise,
    isDisposed: () => disposed,
    getAgentState: () => realAgentState,
    dispose
  };
  window.__AGY_ENHANCE_CLEANUP__ = dispose;
})();
