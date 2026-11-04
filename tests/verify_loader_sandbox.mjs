#!/usr/bin/env node
/**
 * Antigravity Desktop Enhance Loader (v3.6.0-macos-merged) 真实沙箱与 DOM 自验脚本
 *
 * 完整实测验证以下 8 大核心验收条件：
 * 1. 界面中文化与动态流状态翻译（含 trackedTextNodes / lastWritten 防 MutationObserver 死循环守卫 + Alt+L 无损还原）；
 * 2. 宽屏排版（html[data-agy-widescreen="1"]）与阅读舒适度样式（#agy-enhance-scoped-style）；
 * 3. 真实步骤轨迹 HUD（#agy-mission-hud-host Shadow DOM，基于实际 stepsInSlice 轨迹与 GetModelResponse）；
 * 4. <subagent_message> / Ahmad 等子 Agent 唤醒裸标签与内部包装文本清洗；
 * 5. 前端响应式唤醒看门狗（evaluateReactiveWakeupWatchdog / triggerNativeSystemWakeupRpc / getWakeupWatchdogState 三大触发条件与 45s 冷却守卫）；
 * 6. 1M 上下文环（#agy-ctx-ring-pill 实时 Fiber Token 计算与 hover/pin 展示）及配额重置动态提示翻译；
 * 7. 过滤无用 Google 内部工作区菜单项（CitC / Cog / ABFS）与 macOS (/var/folders/.../T/agy_, /tmp/agy_) + Windows (\AppData\Local\Temp\agy_) 临时诊断目录；
 * 8. 保全本机并发会话 e93889f8 的思考与工具流折叠展开修复（onCollapsibleClickCapture、.agy-think-inline-summary）及 macOS setTitleBarOverlay 平台守卫。
 */

import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const LOADER_PATH = fs.existsSync(path.join(__dirname, 'agy-enhance-loader.js'))
  ? path.join(__dirname, 'agy-enhance-loader.js')
  : path.resolve(__dirname, '../src/agy-enhance-loader.js');

// ---------------------------------------------------------------------------
// Minimal Spec-Compliant DOM + TreeWalker + ShadowRoot + MutationObserver Harness
// ---------------------------------------------------------------------------
const Node = {
  ELEMENT_NODE: 1,
  TEXT_NODE: 3,
  DOCUMENT_NODE: 9
};

const NodeFilter = {
  SHOW_ELEMENT: 1,
  SHOW_TEXT: 4
};

class MockClassList {
  constructor(el) {
    this._el = el;
    this._set = new Set();
  }
  add(...tokens) {
    for (const t of tokens) if (t) this._set.add(t);
  }
  remove(...tokens) {
    for (const t of tokens) this._set.delete(t);
  }
  contains(t) {
    return this._set.has(t);
  }
  toggle(t, force) {
    if (force === undefined) {
      if (this._set.has(t)) {
        this._set.delete(t);
        return false;
      }
      this._set.add(t);
      return true;
    }
    if (force) {
      this._set.add(t);
      return true;
    }
    this._set.delete(t);
    return false;
  }
  toString() {
    return Array.from(this._set).join(' ');
  }
}

class MockStyle {
  constructor() {
    this._props = new Map();
  }
  setProperty(k, v, priority = '') {
    this._props.set(k, { value: String(v), priority: String(priority) });
    this[k] = String(v);
  }
  getPropertyValue(k) {
    return this._props.get(k)?.value || this[k] || '';
  }
  removeProperty(k) {
    this._props.delete(k);
    delete this[k];
  }
}

let activeMutationObservers = [];

class MockNode {
  constructor(nodeType) {
    this.nodeType = nodeType;
    this.parentNode = null;
    this.parentElement = null;
    this.childNodes = [];
    this._listeners = new Map();
  }
  get isConnected() {
    let cur = this;
    while (cur) {
      if (cur.nodeType === Node.DOCUMENT_NODE || cur._isShadowRoot) return true;
      cur = cur.parentNode || cur.host;
    }
    return false;
  }
  get firstElementChild() {
    return this.childNodes.find((c) => c.nodeType === Node.ELEMENT_NODE) || null;
  }
  get children() {
    return this.childNodes.filter((c) => c.nodeType === Node.ELEMENT_NODE);
  }
  get nextElementSibling() {
    if (!this.parentNode) return null;
    const siblings = this.parentNode.childNodes;
    const idx = siblings.indexOf(this);
    for (let i = idx + 1; i < siblings.length; i++) {
      if (siblings[i].nodeType === Node.ELEMENT_NODE) return siblings[i];
    }
    return null;
  }
  appendChild(child) {
    if (child.parentNode) child.remove();
    child.parentNode = this;
    child.parentElement = this.nodeType === Node.ELEMENT_NODE ? this : null;
    this.childNodes.push(child);
    if (this.isConnected) {
      for (const obs of activeMutationObservers) {
        obs._notify([{ type: 'childList', target: this, addedNodes: [child] }]);
      }
    }
    return child;
  }
  insertBefore(newNode, refNode) {
    if (!refNode) return this.appendChild(newNode);
    if (newNode.parentNode) newNode.remove();
    const idx = this.childNodes.indexOf(refNode);
    newNode.parentNode = this;
    newNode.parentElement = this.nodeType === Node.ELEMENT_NODE ? this : null;
    if (idx === -1) {
      this.childNodes.push(newNode);
    } else {
      this.childNodes.splice(idx, 0, newNode);
    }
    return newNode;
  }
  remove() {
    if (!this.parentNode) return;
    const idx = this.parentNode.childNodes.indexOf(this);
    if (idx !== -1) this.parentNode.childNodes.splice(idx, 1);
    this.parentNode = null;
    this.parentElement = null;
  }
  contains(other) {
    let cur = other;
    while (cur) {
      if (cur === this) return true;
      cur = cur.parentNode;
    }
    return false;
  }
  addEventListener(type, fn, opts) {
    const capture = typeof opts === 'boolean' ? opts : Boolean(opts?.capture);
    if (!this._listeners.has(type)) this._listeners.set(type, []);
    this._listeners.get(type).push({ fn, capture });
  }
  removeEventListener(type, fn) {
    const arr = this._listeners.get(type);
    if (!arr) return;
    this._listeners.set(
      type,
      arr.filter((item) => item.fn !== fn)
    );
  }
  dispatchEvent(evt) {
    evt.target = evt.target || this;
    const arr = this._listeners.get(evt.type) || [];
    for (const item of [...arr]) {
      if (evt._immediateStopped) break;
      item.fn.call(this, evt);
    }
    return !evt.defaultPrevented;
  }
}

class MockTextNode extends MockNode {
  constructor(text = '') {
    super(Node.TEXT_NODE);
    this._val = String(text);
    this.mutationWriteCount = 0;
  }
  get nodeValue() {
    return this._val;
  }
  set nodeValue(v) {
    const next = String(v);
    if (this._val !== next) {
      this._val = next;
      this.mutationWriteCount += 1;
      for (const obs of activeMutationObservers) {
        obs._notify([{ type: 'characterData', target: this }]);
      }
    }
  }
  get textContent() {
    return this._val;
  }
  set textContent(v) {
    this.nodeValue = v;
  }
}

function matchesSingleSelector(el, sel) {
  if (!el || el.nodeType !== Node.ELEMENT_NODE) return false;
  sel = sel.trim();
  if (!sel) return false;

  // Handle simple descendant combinator "A B" or adjacent sibling "A + B"
  if (sel.includes(' + ')) {
    const [prevSel, currSel] = sel.split(' + ').map((s) => s.trim());
    if (!matchesSingleSelector(el, currSel)) return false;
    if (!el.parentNode) return false;
    const elems = el.parentNode.children;
    const idx = elems.indexOf(el);
    return idx > 0 && matchesSingleSelector(elems[idx - 1], prevSel);
  }
  if (sel.includes(' ') && !sel.includes('[')) {
    const parts = sel.split(/\s+/);
    const last = parts.pop();
    if (!matchesSingleSelector(el, last)) return false;
    let cur = el.parentElement;
    while (cur) {
      if (matchesSingleSelector(cur, parts.join(' '))) return true;
      cur = cur.parentElement;
    }
    return false;
  }
  // Handle descendant with attribute e.g. '[data-testid="conversation-view"] .prose' or '[id^="base-ui-"] button'
  const descMatch = sel.match(/^(\[[^\]]+\])\s+([a-zA-Z0-9_.*#-]+)$/);
  if (descMatch) {
    if (!matchesSingleSelector(el, descMatch[2])) return false;
    let cur = el.parentElement;
    while (cur) {
      if (matchesSingleSelector(cur, descMatch[1])) return true;
      cur = cur.parentElement;
    }
    return false;
  }

  if (sel === '*') return true;
  if (sel === 'svg:last-child') {
    if (el.tagName.toLowerCase() !== 'svg') return false;
    const p = el.parentElement;
    return Boolean(p && p.children[p.children.length - 1] === el);
  }

  let rem = sel;
  const tagMatch = rem.match(/^[a-zA-Z0-9-]+/);
  if (tagMatch) {
    if (el.tagName.toLowerCase() !== tagMatch[0].toLowerCase()) return false;
    rem = rem.slice(tagMatch[0].length);
  }

  const idMatch = rem.match(/#([a-zA-Z0-9_-]+)/);
  if (idMatch) {
    if (el.id !== idMatch[1]) return false;
    rem = rem.replace(idMatch[0], '');
  }

  const classMatches = [...rem.matchAll(/\.([a-zA-Z0-9_-]+)/g)];
  for (const cm of classMatches) {
    if (!el.classList.contains(cm[1])) return false;
    rem = rem.replace(cm[0], '');
  }

  const attrMatches = [...rem.matchAll(/\[([a-zA-Z0-9_-]+)(?:([\^$*]?=)"?([^"\]]*)"?)?\]/g)];
  for (const am of attrMatches) {
    const attrName = am[1];
    const op = am[2];
    const val = am[3] ?? '';
    const actual = el.getAttribute(attrName);
    if (!op) {
      if (actual === null) return false;
    } else if (op === '=') {
      if (actual !== val) return false;
    } else if (op === '^=') {
      if (actual === null || !actual.startsWith(val)) return false;
    } else if (op === '*=') {
      if (actual === null || !actual.includes(val)) return false;
    }
    rem = rem.replace(am[0], '');
  }

  return rem.trim() === '';
}

function matchesSelectorList(el, selectorGroup) {
  const parts = selectorGroup.split(',');
  return parts.some((p) => matchesSingleSelector(el, p));
}

class MockElement extends MockNode {
  constructor(tagName) {
    super(Node.ELEMENT_NODE);
    this.tagName = String(tagName || 'DIV').toUpperCase();
    this.id = '';
    this.classList = new MockClassList(this);
    this.style = new MockStyle();
    this._attrs = new Map();
    this.offsetWidth = 800;
    this.offsetHeight = 40;
    this._rect = { left: 100, top: 100, right: 900, bottom: 140, width: 800, height: 40 };
    this.shadowRoot = null;
  }
  get className() {
    return this.classList.toString();
  }
  set className(v) {
    this.classList._set.clear();
    for (const t of String(v || '').split(/\s+/)) {
      if (t) this.classList.add(t);
    }
  }
  getAttribute(k) {
    if (k === 'id') return this.id || null;
    if (k === 'class') return this.className || null;
    return this._attrs.has(k) ? this._attrs.get(k) : null;
  }
  setAttribute(k, v) {
    const str = String(v);
    if (k === 'id') this.id = str;
    else if (k === 'class') this.className = str;
    this._attrs.set(k, str);
  }
  removeAttribute(k) {
    if (k === 'id') this.id = '';
    this._attrs.delete(k);
  }
  get textContent() {
    let out = '';
    for (const c of this.childNodes) {
      out += c.textContent || '';
    }
    return out;
  }
  set textContent(v) {
    this.childNodes = [];
    if (v !== '' && v !== null && v !== undefined) {
      this.appendChild(new MockTextNode(String(v)));
    }
  }
  get innerText() {
    if (this.style.getPropertyValue('display') === 'none') return '';
    let out = '';
    for (const c of this.childNodes) {
      if (c.nodeType === Node.TEXT_NODE) out += c.nodeValue;
      else if (c.nodeType === Node.ELEMENT_NODE) out += c.innerText;
    }
    return out;
  }
  get innerHTML() {
    return this._rawHtml || this.textContent;
  }
  set innerHTML(html) {
    this._rawHtml = String(html);
    this.childNodes = [];
    // Parse id="..." elements inside shadow/custom HTML so getElementById / querySelector works
    const tagRe = /<([a-zA-Z0-9-]+)([^>]*)>([^<]*)/g;
    let m;
    const stack = [this];
    while ((m = tagRe.exec(this._rawHtml)) !== null) {
      const tag = m[1];
      const attrStr = m[2] || '';
      const innerTxt = m[3] || '';
      const el = new MockElement(tag);
      const idM = attrStr.match(/\bid="([^"]+)"/);
      if (idM) el.setAttribute('id', idM[1]);
      const clsM = attrStr.match(/\bclass="([^"]+)"/);
      if (clsM) el.className = clsM[1];
      const titleM = attrStr.match(/\btitle="([^"]+)"/);
      if (titleM) el.setAttribute('title', titleM[1]);
      if (innerTxt.trim()) {
        el.appendChild(new MockTextNode(innerTxt));
      }
      stack[stack.length - 1].appendChild(el);
    }
  }
  attachShadow() {
    const sr = new MockElement('SHADOW-ROOT');
    sr._isShadowRoot = true;
    sr.host = this;
    sr.getElementById = (id) => sr.querySelector(`#${id}`);
    this.shadowRoot = sr;
    return sr;
  }
  getBoundingClientRect() {
    return this._rect;
  }
  matches(sel) {
    return matchesSelectorList(this, sel);
  }
  closest(sel) {
    let cur = this;
    while (cur && cur.nodeType === Node.ELEMENT_NODE) {
      if (matchesSelectorList(cur, sel)) return cur;
      cur = cur.parentElement;
    }
    return null;
  }
  querySelectorAll(sel) {
    const res = [];
    const walk = (n) => {
      for (const c of n.childNodes) {
        if (c.nodeType === Node.ELEMENT_NODE) {
          if (matchesSelectorList(c, sel)) res.push(c);
          walk(c);
        }
      }
    };
    walk(this);
    return res;
  }
  querySelector(sel) {
    return this.querySelectorAll(sel)[0] || null;
  }
}

class MockMutationObserver {
  constructor(cb) {
    this._cb = cb;
  }
  observe() {
    if (!activeMutationObservers.includes(this)) {
      activeMutationObservers.push(this);
    }
  }
  disconnect() {
    activeMutationObservers = activeMutationObservers.filter((x) => x !== this);
  }
  _notify(records) {
    this._cb(records);
  }
}

function createTreeWalker(root, whatToShow) {
  const nodes = [];
  const collect = (n) => {
    for (const c of n.childNodes || []) {
      if (whatToShow === NodeFilter.SHOW_TEXT && c.nodeType === Node.TEXT_NODE) {
        nodes.push(c);
      } else if (whatToShow === NodeFilter.SHOW_ELEMENT && c.nodeType === Node.ELEMENT_NODE) {
        nodes.push(c);
      }
      if (c.childNodes && c.childNodes.length > 0) collect(c);
    }
  };
  if (root.nodeType === Node.TEXT_NODE && whatToShow === NodeFilter.SHOW_TEXT) {
    nodes.push(root);
  } else {
    collect(root);
  }
  let idx = -1;
  return {
    get currentNode() {
      return idx >= 0 && idx < nodes.length ? nodes[idx] : root;
    },
    nextNode() {
      idx += 1;
      return idx < nodes.length ? nodes[idx] : null;
    }
  };
}

// ---------------------------------------------------------------------------
// Construct Mock Window / Document / Bridge / React Fiber Tree
// ---------------------------------------------------------------------------
async function runVerification() {
  console.log('=== [Antigravity Desktop Enhance Loader · Node.js 真实沙箱自验] ===');

  const doc = new MockNode(Node.DOCUMENT_NODE);
  const htmlEl = new MockElement('HTML');
  const headEl = new MockElement('HEAD');
  const bodyEl = new MockElement('BODY');
  doc.appendChild(htmlEl);
  htmlEl.appendChild(headEl);
  htmlEl.appendChild(bodyEl);

  doc.documentElement = htmlEl;
  doc.head = headEl;
  doc.body = bodyEl;
  doc.hidden = false;
  doc.cookie = 'csrfToken=test-csrf-token-12345';
  doc.createElement = (tag) => new MockElement(tag);
  doc.createTextNode = (txt) => new MockTextNode(txt);
  doc.createTreeWalker = createTreeWalker;
  doc.querySelectorAll = (sel) => htmlEl.querySelectorAll(sel);
  doc.querySelector = (sel) => htmlEl.querySelector(sel);
  doc.getElementById = (id) => htmlEl.querySelector(`#${id}`);

  const TEST_CID = '3fa9c42a-a5c7-42ab-ad7b-0bea0050088d';

  // Conversation View + Autoscroll Viewport with React Fiber State
  const convView = new MockElement('DIV');
  convView.setAttribute('data-testid', 'conversation-view');
  convView.setAttribute('data-cascade-id', TEST_CID);
  bodyEl.appendChild(convView);

  const autoscrollVp = new MockElement('DIV');
  autoscrollVp.setAttribute('data-testid', 'autoscroll-viewport');
  convView.appendChild(autoscrollVp);

  const liveAgentState = {
    executorLoopStatus: 1, // IDLE
    fullyIdle: false,
    backgroundTasks: [{ id: 'bg-1' }],
    activeSubagents: [],
    pendingMessages: [],
    trajectorySlice: {
      totalStepsLength: 4,
      stepsInSlice: [
        {
          status: 3,
          step: {
            case: 'userInput',
            value: { items: [{ text: '请完成 macOS 桌面增强套件适配与自验' }] }
          }
        },
        {
          status: 3,
          step: {
            case: 'generic',
            value: { args: { Path: '/Users/ien/agy治理/02_desktop_enhance/agy-enhance-loader.js' } }
          },
          metadata: {
            toolCall: { name: 'edit_file' },
            modelUsage: {
              inputTokens: 120000,
              cacheReadTokens: 100000,
              outputTokens: 5000,
              responseId: 'req-sandbox-001'
            }
          }
        },
        {
          status: 1, // RUNNING background/tool step
          step: {
            case: 'generic',
            value: { args: { CommandLine: 'node verify_loader_sandbox.mjs', toolSummary: 'Run sandbox tests' } }
          },
          metadata: {
            toolCall: { name: 'run_command' },
            sourceTrajectoryStepInfo: { stepIndex: 2 }
          }
        },
        {
          status: 3,
          step: {
            case: 'plannerResponse',
            value: {
              thinking: '【本步意图】核对 build_asar.py 的 SHA256 与完整性校验逻辑',
              response: '已完成三方合并与语法校验。'
            }
          }
        }
      ],
      generatorMetadatasInSlice: [
        {
          chatModel: {
            chatStartMetadata: {
              contextWindowMetadata: { maxContextTokens: 1000000 }
            }
          }
        }
      ]
    }
  };

  autoscrollVp.__reactFiber$test = {
    memoizedProps: {
      value: {
        cascadeContext: {
          state: {
            agentStateProvider: {
              getState: () => liveAgentState
            }
          }
        }
      }
    },
    return: null
  };

  // UI Elements for Localization & Dynamic Stream Status Translation
  const uiButton = new MockElement('BUTTON');
  const uiBtnText = new MockTextNode('New Conversation');
  uiButton.appendChild(uiBtnText);
  bodyEl.appendChild(uiButton);

  const quotaSpan = new MockElement('SPAN');
  const quotaText = new MockTextNode(
    'You have hit your weekly limit, it refreshes in 4 hours.'
  );
  quotaSpan.appendChild(quotaText);
  bodyEl.appendChild(quotaSpan);

  const searchInput = new MockElement('INPUT');
  searchInput.setAttribute('placeholder', 'Search conversations...');
  bodyEl.appendChild(searchInput);

  // Leaked <subagent_message> / Ahmad raw dump inside prose
  const proseDiv = new MockElement('DIV');
  proseDiv.className = 'prose';
  const proseP1 = new MockElement('P');
  proseP1.textContent =
    '正常正文结论在此。 Ahmad Subagent 94d0dc79-9a12-47be-805d-a679d6856d6e has finished running and returned the following message: <subagent_message>internal raw dump</subagent_message>';
  const prosePre = new MockElement('PRE');
  prosePre.textContent = '{"composerChain": [], "local_tx_id": "123"}';
  proseDiv.appendChild(proseP1);
  proseDiv.appendChild(prosePre);
  convView.appendChild(proseDiv);

  // Thinking collapsible button + sibling body div (e93889f8 feature)
  const thinkWrap = new MockElement('DIV');
  const thinkBtn = new MockElement('BUTTON');
  thinkBtn.setAttribute('data-testid', 'thinking-collapsible-trigger');
  thinkBtn.setAttribute('aria-expanded', 'true');
  const thinkLabelSpan = new MockElement('SPAN');
  thinkLabelSpan.textContent = 'Thought for 6s';
  const thinkSvg = new MockElement('SVG');
  thinkBtn.appendChild(thinkLabelSpan);
  thinkBtn.appendChild(thinkSvg);
  thinkBtn.__reactFiber$test = {
    memoizedProps: {
      thinking: '【本步意图】排查 ipcHandlers.js 中 fs_1 未定义的潜在缺陷'
    },
    return: null
  };
  const thinkBodyDiv = new MockElement('DIV');
  thinkBodyDiv.textContent = 'Detailed thinking content';
  thinkWrap.appendChild(thinkBtn);
  thinkWrap.appendChild(thinkBodyDiv);
  convView.appendChild(thinkWrap);

  // Second thinking collapsible button with English thinking + grpSteps nextToolDesc derivation (e93889f8 lines 2965-2979)
  const thinkWrap2 = new MockElement('DIV');
  const thinkBtn2 = new MockElement('BUTTON');
  thinkBtn2.setAttribute('data-testid', 'thinking-collapsible-trigger');
  thinkBtn2.setAttribute('aria-expanded', 'true');
  const thinkLabelSpan2 = new MockElement('SPAN');
  thinkLabelSpan2.textContent = 'Thought for 3s';
  thinkBtn2.appendChild(thinkLabelSpan2);
  const step0 = {
    status: 3,
    step: { case: 'plannerResponse', value: { thinking: 'Let us inspect the ASAR header offset and verify SHA256.' } },
    metadata: { sourceTrajectoryStepInfo: { stepIndex: 10 } }
  };
  const step1 = {
    status: 3,
    step: { case: 'generic', value: { args: { Path: '/Users/ien/agy治理/02_desktop_enhance/asar_inject_and_rollback.py' } } },
    metadata: { toolCall: { name: 'read_file' }, sourceTrajectoryStepInfo: { stepIndex: 11 } }
  };
  thinkBtn2.__reactFiber$test = {
    memoizedProps: {
      thinking: 'Let us inspect the ASAR header offset and verify SHA256.',
      step: step0
    },
    return: {
      memoizedProps: {
        steps: [step0, step1]
      },
      return: null
    }
  };
  thinkWrap2.appendChild(thinkBtn2);
  thinkWrap2.appendChild(new MockElement('DIV'));
  convView.appendChild(thinkWrap2);

  // Workspace dropdown items (Google internal & temp diagnostic folders on macOS + Windows + non-temp false positive check)
  const menuContainer = new MockElement('DIV');
  const makeMenuItem = (text, title = '') => {
    const item = new MockElement('DIV');
    item.setAttribute('role', 'option');
    if (title) item.setAttribute('title', title);
    item.textContent = text;
    menuContainer.appendChild(item);
    return item;
  };
  const itemCitc = makeMenuItem('CitC Workspace');
  const itemCog = makeMenuItem('Cog Workspace');
  const itemAbfs = makeMenuItem('ABFS Workspace (Alpha)');
  const itemMacVarTmp = makeMenuItem('agy_diag_1', '/var/folders/zz/abc123/T/agy_diag_1');
  const itemMacSlashTmp = makeMenuItem('ocr_batch_2', '/tmp/ocr_batch_2');
  const itemWinTmp = makeMenuItem('agy_win_tmp', 'C:\\Users\\ien\\AppData\\Local\\Temp\\agy_win_tmp');
  const itemWinSlashTmp = makeMenuItem('agy_win_slash', 'C:/Users/ien/AppData/Local/Temp/agy_win_slash');
  const itemFalsePositiveTmp = makeMenuItem('agy_false', '/Users/ien/notmp/agy_false');
  const itemNormal = makeMenuItem('agy治理', '/Users/ien/agy治理');
  bodyEl.appendChild(menuContainer);

  // Agent Input Box & Footer with Permissions Selector Trigger (for Mission HUD & 1M Context Ring)
  const agentInputBox = new MockElement('DIV');
  agentInputBox.setAttribute('data-testid', 'agent-input-box');
  agentInputBox._rect = { left: 100, top: 600, right: 900, bottom: 720, width: 800, height: 120 };

  const footerEl = new MockElement('DIV');
  footerEl.className = '@container/input-footer';
  footerEl._rect = { left: 110, top: 680, right: 890, bottom: 712, width: 780, height: 32 };

  const leftGroup = new MockElement('DIV');
  const leftBtn = new MockElement('BUTTON');
  leftBtn._rect = { left: 115, top: 684, right: 200, bottom: 708, width: 85, height: 24 };
  leftGroup.appendChild(leftBtn);

  const rightGroup = new MockElement('DIV');
  const turboBtn = new MockElement('BUTTON');
  turboBtn.setAttribute('data-testid', 'permissions-selector-trigger');
  turboBtn._rect = { left: 700, top: 684, right: 780, bottom: 708, width: 80, height: 24 };
  rightGroup.appendChild(turboBtn);

  footerEl.appendChild(leftGroup);
  footerEl.appendChild(rightGroup);
  agentInputBox.appendChild(footerEl);
  bodyEl.appendChild(agentInputBox);

  // Track RPC calls & TitleBarOverlay calls
  const fetchCalls = [];
  let titleBarOverlayCalls = [];
  let currentPlatform = 'MacIntel';
  let currentNowMs = 1793479200000;

  const storageMap = new Map();
  const localStorageMock = {
    getItem: (k) => (storageMap.has(k) ? storageMap.get(k) : null),
    setItem: (k, v) => storageMap.set(k, String(v)),
    removeItem: (k) => storageMap.delete(k)
  };

  const bridgeMock = {
    getPreferences: async () => ({
      schemaVersion: 3,
      revision: 1,
      lsGeneration: 1,
      global: { enabled: true, theme: 'native', lang: 'zh-std', widescreen: true },
      windowDefaults: {}
    }),
    updatePreferences: async (patch) => ({ revision: 2 }),
    backupLegacyFlags: async () => true,
    onPreferencesChanged: () => () => {},
    setTitleBarOverlay: async (opts) => {
      titleBarOverlayCalls.push(opts);
    }
  };

  const FakeDate = function (...args) {
    if (args.length === 0) return new Date(currentNowMs);
    return new Date(...args);
  };
  FakeDate.now = () => currentNowMs;
  FakeDate.parse = Date.parse;
  FakeDate.UTC = Date.UTC;

  const win = new MockNode(Node.DOCUMENT_NODE);
  Object.assign(win, {
    document: doc,
    Node,
    NodeFilter,
    MutationObserver: MockMutationObserver,
    Date: FakeDate,
    URL,
    URLSearchParams,
    AbortController,
    setTimeout,
    clearTimeout,
    setInterval,
    clearInterval,
    queueMicrotask,
    innerWidth: 1440,
    innerHeight: 900,
    location: {
      origin: 'https://127.0.0.1:58123',
      pathname: `/c/${TEST_CID}`,
      search: ''
    },
    navigator: {
      get platform() {
        return currentPlatform;
      },
      get userAgent() {
        return currentPlatform === 'MacIntel'
          ? 'Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0)'
          : 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)';
      }
    },
    localStorage: localStorageMock,
    __APP_CONFIG__: { csrfToken: 'test-csrf-token-12345', devMode: false },
    __AGY_BRIDGE__: bridgeMock,
    getComputedStyle: (el) => ({
      display: el?.style?.getPropertyValue('display') || 'block',
      visibility: 'visible',
      opacity: '1',
      color: 'rgb(240, 240, 240)',
      getPropertyValue: (prop) => (prop === '--background' ? '#131313' : '#131313')
    }),
    fetch: async (url, opts) => {
      const bodyObj = opts?.body ? JSON.parse(opts.body) : null;
      fetchCalls.push({ url, method: opts?.method, headers: opts?.headers, body: bodyObj });
      if (String(url).endsWith('/GetModelResponse')) {
        return {
          ok: true,
          json: async () => ({
            response: JSON.stringify({
              title: '推进苹果端桌面增强三方合并与沙箱核验',
              tasks: [
                { status: 'done', title: '完成原厂基线解包与七模块补丁构建', outcome: '校验通过' },
                { status: 'active', title: '执行真实沙箱自验核查八项核心能力', outcome: '正在推进' }
              ]
            })
          })
        };
      }
      if (String(url).endsWith('/SendAgentMessage') && win.__simulateNextWakeupHttp500) {
        win.__simulateNextWakeupHttp500 = false;
        return { ok: false, status: 500, json: async () => ({ error: 'Internal Server Error' }) };
      }
      if (String(url).endsWith('/SendAgentMessage') && typeof win.__holdNextWakeupFetch === 'function') {
        const handler = win.__holdNextWakeupFetch;
        win.__holdNextWakeupFetch = null;
        return await handler(url, opts);
      }
      return { ok: true, json: async () => ({}) };
    }
  });
  win.window = win;
  win.globalThis = win;

  // Execute Loader in VM Context (matching utils.js onDomReady wrapper)
  const loaderCode = fs.readFileSync(LOADER_PATH, 'utf-8');
  const context = vm.createContext(win);
  vm.runInContext(loaderCode, context, { filename: 'agy-enhance-loader.js' });
  const receipt = await win.__AGY_V3__.readyPromise;

  assert.equal(receipt.status, 'ready', 'Loader readyPromise must resolve with status="ready"');
  assert.equal(receipt.version, '3.6.0-macos-merged', 'Loader version must match 3.6.0-macos-merged');
  console.log('  [PASS 0] Loader 初始化与 readyPromise 契约校验通过:', JSON.stringify(receipt));

  // Wait 20ms for async GetModelResponse HUD update to settle
  await new Promise((r) => setTimeout(r, 20));

  // -------------------------------------------------------------------------
  // Verification 1: UI Chinese Localization, Dynamic Quota Translation & Loop Guard
  // -------------------------------------------------------------------------
  assert.notEqual(uiBtnText.nodeValue, 'New Conversation', 'UI button should be translated to Chinese');
  assert.match(uiBtnText.nodeValue, /新对话|新建会话|新建对话/, 'New Conversation translated to Chinese');
  const writesAfterInit = uiBtnText.mutationWriteCount;
  // Trigger MutationObserver with same node to verify trackedTextNodes/lastWritten loop guard
  for (const obs of activeMutationObservers) {
    obs._notify([{ type: 'characterData', target: uiBtnText }]);
  }
  assert.equal(
    uiBtnText.mutationWriteCount,
    writesAfterInit,
    'trackedTextNodes/lastWritten must prevent re-writing already translated textNode (zero MutationObserver loop)'
  );
  assert.match(
    quotaText.nodeValue,
    /达到本周额度上限.*重置/,
    'Dynamic quota reset notice must be translated to Chinese'
  );
  assert.notEqual(
    searchInput.getAttribute('placeholder'),
    'Search conversations...',
    'Input placeholder must be translated'
  );
  console.log(
    `  [PASS 1] 界面汉化、配额动态翻译与防死循环守卫通过: "${uiBtnText.nodeValue}" | "${quotaText.nodeValue}"`
  );

  // -------------------------------------------------------------------------
  // Verification 2: Widescreen Typography & Comfort CSS (including CSS backslash fix)
  // -------------------------------------------------------------------------
  assert.equal(
    htmlEl.getAttribute('data-agy-widescreen'),
    '1',
    'html[data-agy-widescreen="1"] must be enabled by default'
  );
  const scopedStyle = doc.getElementById('agy-enhance-scoped-style');
  assert.ok(scopedStyle, '#agy-enhance-scoped-style must be mounted');
  assert.match(scopedStyle.textContent, /--max-conversation-width:\s*min\(92vw,\s*1480px\)/);
  assert.match(scopedStyle.textContent, /PingFang SC/);
  assert.ok(
    scopedStyle.textContent.includes('[title*="\\\\AppData\\\\Local\\\\Temp\\\\agy_"]'),
    'CSS selector must contain properly escaped double backslashes \\\\AppData\\\\Local\\\\Temp\\\\agy_ (no \\nppData newline bug)'
  );
  assert.ok(
    scopedStyle.textContent.includes('[title*="AppData/Local/Temp/agy_"]'),
    'CSS selector must also include forward-slash Windows temp path selector'
  );
  console.log('  [PASS 2] 宽屏学术排版 (data-agy-widescreen="1")、macOS 字体栈与 CSS 反斜杠转义核验通过');

  // -------------------------------------------------------------------------
  // Verification 3: Real Step Trajectory Mission HUD (#agy-mission-hud-host)
  // -------------------------------------------------------------------------
  const hudHost = doc.getElementById('agy-mission-hud-host');
  assert.ok(hudHost && hudHost.shadowRoot, '#agy-mission-hud-host with Shadow DOM must be mounted');
  const hudGoal = hudHost.shadowRoot.getElementById('m-goal')?.textContent || '';
  const hudPhase = hudHost.shadowRoot.getElementById('m-phase')?.textContent || '';
  const hudRtText = hudHost.shadowRoot.getElementById('m-rt-text')?.textContent || '';
  assert.equal(hudGoal, '推进苹果端桌面增强三方合并与沙箱核验', 'Mission HUD goal must reflect real trajectory summary');
  assert.equal(hudPhase, '1 / 2', 'Mission HUD phase counter must reflect 1 done / 2 total');
  assert.match(hudRtText, /正在运行：运行 sandbox 测试验证/, 'Mission HUD runtime bar must display active step');
  console.log(`  [PASS 3] 真实步骤轨迹 Mission HUD 核验通过: goal="${hudGoal}" (${hudPhase}), rt="${hudRtText}"`);

  // -------------------------------------------------------------------------
  // Verification 4: <subagent_message> & Ahmad Leaked Raw Tag Cleaning
  // -------------------------------------------------------------------------
  assert.equal(
    proseP1.textContent,
    '正常正文结论在此。',
    'Leaked Ahmad Subagent + <subagent_message> suffix must be stripped while preserving clean prose'
  );
  assert.equal(
    prosePre.style.getPropertyValue('display'),
    'none',
    'Leaked composerChain/local_tx_id raw JSON block must be hidden'
  );
  console.log('  [PASS 4] <subagent_message> / Ahmad 唤醒裸标签与内部调试块清洗核验通过');

  // -------------------------------------------------------------------------
  // Verification 5: Reactive Wakeup Watchdog (Foreground vs Background, HTTP 500 Retry, 3 Conditions, 45s Cooldown & Episode Cap)
  // -------------------------------------------------------------------------
  const wdState0 = win.__AGY_V3__.getWakeupWatchdogState()[TEST_CID];
  assert.ok(wdState0, 'Watchdog state must be initialized for active conversation');
  assert.equal(
    wdState0.hadActiveBgWhileMainIdle,
    false,
    'Foreground run_command at startup must keep mainExecutorRunning=true (hadActiveBgWhileMainIdle=false)'
  );

  // 5a. Verify that a foreground-only run_command (backgroundTasks=[], WaitMsBeforeAsync=5000) is treated as mainExecutorRunning=true and NEVER triggers Condition 3 even after 70s!
  liveAgentState.backgroundTasks = [];
  liveAgentState.fullyIdle = false;
  liveAgentState.trajectorySlice.stepsInSlice[2].status = 1; // RUNNING foreground command (no IsDaemon, WaitMsBeforeAsync undefined/default)
  currentNowMs += 70000;
  win.dispatchEvent({ type: 'resize' });
  await new Promise((r) => setTimeout(r, 10));
  assert.equal(
    fetchCalls.filter((c) => String(c.url).endsWith('/SendAgentMessage')).length,
    0,
    'Foreground run_command must NOT be misclassified as idle background work or trigger 65s wakeup'
  );

  // 5b. Re-arm background task while main executor is idle, then finish it and simulate HTTP 500 on first SendAgentMessage
  liveAgentState.backgroundTasks = [{ id: 'bg-1' }];
  liveAgentState.trajectorySlice.stepsInSlice[2].status = 3; // DONE foreground step
  currentNowMs += 1000;
  win.dispatchEvent({ type: 'resize' });
  assert.equal(
    win.__AGY_V3__.getWakeupWatchdogState()[TEST_CID].hadActiveBgWhileMainIdle,
    true,
    'hadActiveBgWhileMainIdle must be true when backgroundTasks is non-empty and main executor is idle'
  );

  liveAgentState.backgroundTasks = [];
  liveAgentState.fullyIdle = true;
  currentNowMs += 1000;
  win.dispatchEvent({ type: 'resize' }); // records bgFinishedEdgeTs
  win.__simulateNextWakeupHttp500 = true;
  currentNowMs += 7000;
  win.dispatchEvent({ type: 'resize' }); // attempts SendAgentMessage -> HTTP 500
  await new Promise((r) => setTimeout(r, 10));

  const wdAfter500 = win.__AGY_V3__.getWakeupWatchdogState()[TEST_CID];
  assert.ok(wdAfter500.bgFinishedEdgeTs > 0, 'HTTP 500 must NOT swallow bgFinishedEdgeTs');
  assert.equal(wdAfter500.wakeupCount, 0, 'HTTP 500 must NOT increment wakeupCount');

  // Advance 11s past the 10s failure retry backoff -> retry succeeds with HTTP 200!
  currentNowMs += 11000;
  win.dispatchEvent({ type: 'resize' });
  await new Promise((r) => setTimeout(r, 10));

  const wdAfterRetry = win.__AGY_V3__.getWakeupWatchdogState()[TEST_CID];
  assert.equal(wdAfterRetry.bgFinishedEdgeTs, 0, 'Successful SendAgentMessage must clear bgFinishedEdgeTs');
  assert.equal(wdAfterRetry.wakeupCount, 1, 'Successful SendAgentMessage must increment wakeupCount to 1');

  // Verify 45s cooldown blocks immediate re-trigger even when pendingMessages > 0
  liveAgentState.pendingMessages = [{ id: 'msg-1' }];
  currentNowMs += 10000; // only 10s elapsed (< 45s cooldown)
  win.dispatchEvent({ type: 'resize' });
  await new Promise((r) => setTimeout(r, 10));
  assert.equal(
    win.__AGY_V3__.getWakeupWatchdogState()[TEST_CID].wakeupCount,
    1,
    '45s cooldown must prevent duplicate SendAgentMessage within 45s'
  );

  // Advance past 45s cooldown -> Condition 1 (undelivered_pending_message) fires! (wakeupCount -> 2)
  currentNowMs += 40000;
  win.dispatchEvent({ type: 'resize' });
  await new Promise((r) => setTimeout(r, 10));
  assert.equal(
    win.__AGY_V3__.getWakeupWatchdogState()[TEST_CID].wakeupCount,
    2,
    'Watchdog must fire Condition 1 (undelivered_pending_message) after cooldown'
  );

  // Clear pendingMessages, start active background task at T0, then advance >65s -> Condition 3 (background_work_65s_check) fires! (wakeupCount -> 3)
  liveAgentState.pendingMessages = [];
  liveAgentState.backgroundTasks = [{ id: 'bg-long' }];
  liveAgentState.fullyIdle = false;
  currentNowMs += 1000;
  win.dispatchEvent({ type: 'resize' });
  currentNowMs += 70000;
  win.dispatchEvent({ type: 'resize' });
  await new Promise((r) => setTimeout(r, 10));
  assert.equal(
    win.__AGY_V3__.getWakeupWatchdogState()[TEST_CID].wakeupCount,
    3,
    'Watchdog must fire Condition 3 (background_work_65s_check) after >65s idle (wakeupCount=3)'
  );

  // Verify MAX_WAKEUPS_PER_IDLE_EPISODE (3) caps further wakeups when no new steps have occurred
  currentNowMs += 70000;
  win.dispatchEvent({ type: 'resize' });
  await new Promise((r) => setTimeout(r, 10));
  assert.equal(
    win.__AGY_V3__.getWakeupWatchdogState()[TEST_CID].wakeupCount,
    3,
    'MAX_WAKEUPS_PER_IDLE_EPISODE (3) must prevent infinite 65s wakeup loops in the same idle episode'
  );

  // 5c. Round 2 Gate 1b: A 1-step wakeup answer while non-daemon background task is still hung must NOT reset wakeupCount below 3
  liveAgentState.trajectorySlice.totalStepsLength = 5;
  liveAgentState.trajectorySlice.stepsInSlice.push({
    status: 3,
    step: { case: 'plannerResponse', value: { response: 'Still waiting on background task.' } },
    metadata: { sourceTrajectoryStepInfo: { stepIndex: 4 } }
  });
  currentNowMs += 70000;
  win.dispatchEvent({ type: 'resize' });
  await new Promise((r) => setTimeout(r, 10));
  assert.equal(
    win.__AGY_V3__.getWakeupWatchdogState()[TEST_CID].wakeupCount,
    3,
    '1-step wakeup reply while non-daemon task remains hung must NOT reset wakeupCount cap of 3'
  );

  // Clear non-daemon background tasks and advance a step -> resets wakeupCount to 0
  liveAgentState.backgroundTasks = [];
  liveAgentState.fullyIdle = true;
  liveAgentState.trajectorySlice.totalStepsLength = 6;
  currentNowMs += 1000;
  win.dispatchEvent({ type: 'resize' });
  assert.equal(
    win.__AGY_V3__.getWakeupWatchdogState()[TEST_CID].wakeupCount,
    0,
    'Clearing all non-daemon background tasks and advancing step must reset wakeupCount to 0'
  );
  // Clear the completion edge from bg-long finishing so we can test Daemon & 2->1 cleanly
  currentNowMs += 50000;
  win.dispatchEvent({ type: 'resize' });
  await new Promise((r) => setTimeout(r, 10));
  // Reset wakeupCount again after bg-long completion edge was serviced
  liveAgentState.trajectorySlice.totalStepsLength = 7;
  currentNowMs += 1000;
  win.dispatchEvent({ type: 'resize' });
  assert.equal(win.__AGY_V3__.getWakeupWatchdogState()[TEST_CID].wakeupCount, 0);

  // 5d. Round 2 Gate 1a: Daemon-only background task (IsDaemon / taskSnapshot.isDaemon) + quiet turn completion sends ZERO wakeups
  const sendCallsBeforeDaemon = fetchCalls.filter((c) => String(c.url).endsWith('/SendAgentMessage')).length;
  liveAgentState.backgroundTasks = [{ id: 'daemon-1', taskSnapshot: { isDaemon: true } }];
  liveAgentState.executorLoopStatus = 1;
  liveAgentState.fullyIdle = true;
  currentNowMs += 70000;
  win.dispatchEvent({ type: 'resize' });
  currentNowMs += 70000;
  win.dispatchEvent({ type: 'resize' });
  await new Promise((r) => setTimeout(r, 10));
  assert.equal(
    fetchCalls.filter((c) => String(c.url).endsWith('/SendAgentMessage')).length,
    sendCallsBeforeDaemon,
    'Daemon background task (taskSnapshot.isDaemon=true) must send ZERO SendAgentMessage calls across idle episodes'
  );

  // 5e. Round 2 Gate 2: Non-daemon background count dropping 2 -> 1 while idle latches bgFinishedEdgeTs and fires background_work_completed
  liveAgentState.backgroundTasks = [
    { id: 'bg-a' },
    { id: 'bg-b' },
    { id: 'daemon-1', taskSnapshot: { isDaemon: true } }
  ];
  liveAgentState.fullyIdle = false;
  currentNowMs += 1000;
  win.dispatchEvent({ type: 'resize' });
  assert.equal(win.__AGY_V3__.getWakeupWatchdogState()[TEST_CID].lastNonDaemonBgCount, 2);

  // Drop non-daemon count from 2 -> 1 (bg-a finishes while bg-b and daemon-1 remain)
  liveAgentState.backgroundTasks = [
    { id: 'bg-b' },
    { id: 'daemon-1', taskSnapshot: { isDaemon: true } }
  ];
  currentNowMs += 1000;
  win.dispatchEvent({ type: 'resize' });
  const wdAfter2to1 = win.__AGY_V3__.getWakeupWatchdogState()[TEST_CID];
  assert.ok(
    wdAfter2to1.bgFinishedEdgeTs > 0,
    'Non-daemon background count dropping 2 -> 1 must latch bgFinishedEdgeTs > 0'
  );
  currentNowMs += 46000; // past 45s cooldown and >6s after bgFinishedEdgeTs
  win.dispatchEvent({ type: 'resize' });
  await new Promise((r) => setTimeout(r, 10));
  const lastSendBody = fetchCalls.filter((c) => String(c.url).endsWith('/SendAgentMessage')).pop()?.body;
  assert.match(
    lastSendBody?.content || '',
    /background_work_completed/,
    '2 -> 1 non-daemon drop must fire background_work_completed'
  );

  // 5f. Round 2 Gate 3: In-flight SendAgentMessage must be aborted when mainExecutorRunning becomes true, without mutating state on late resolve
  let resolveHeldFetch = null;
  let heldSignal = null;
  win.__holdNextWakeupFetch = (url, opts) => {
    heldSignal = opts?.signal || null;
    return new Promise((resolve) => {
      resolveHeldFetch = resolve;
    });
  };
  // Drop remaining non-daemon task 1 -> 0 to trigger another background_work_completed after cooldown
  liveAgentState.backgroundTasks = [{ id: 'daemon-1', taskSnapshot: { isDaemon: true } }];
  currentNowMs += 1000;
  win.dispatchEvent({ type: 'resize' }); // latches 1 -> 0 edge
  currentNowMs += 46000;
  win.dispatchEvent({ type: 'resize' }); // launches held SendAgentMessage
  assert.equal(
    win.__AGY_V3__.getWakeupWatchdogState()[TEST_CID].rpcInFlight,
    true,
    'Held SendAgentMessage must set rpcInFlight=true'
  );
  // Now mainExecutorRunning becomes true while RPC is still in flight!
  liveAgentState.executorLoopStatus = 2; // EXECUTOR_LOOP_STATUS_RUNNING
  currentNowMs += 500;
  win.dispatchEvent({ type: 'resize' });
  assert.equal(heldSignal?.aborted, true, 'Transition to mainExecutorRunning=true must abort in-flight SendAgentMessage signal');
  const wdAfterAbort = win.__AGY_V3__.getWakeupWatchdogState()[TEST_CID];
  assert.equal(
    wdAfterAbort.rpcInFlight,
    false,
    'abortInFlightWakeupRpc must immediately clear rpcInFlight'
  );
  // Resolve the stale fetch after abort and verify neither wakeupCount nor lastWakeupSentTs is mutated
  currentNowMs += 500;
  if (resolveHeldFetch) {
    resolveHeldFetch({ ok: true, json: async () => ({}) });
  }
  await new Promise((r) => setTimeout(r, 10));
  const wdAfterLateResolve = win.__AGY_V3__.getWakeupWatchdogState()[TEST_CID];
  assert.equal(
    wdAfterLateResolve.wakeupCount,
    wdAfterAbort.wakeupCount,
    'Aborted stale RPC must NOT commit or increment wakeupCount'
  );
  assert.equal(
    wdAfterLateResolve.lastWakeupSentTs,
    wdAfterAbort.lastWakeupSentTs,
    'Aborted stale RPC must NOT overwrite lastWakeupSentTs'
  );
  liveAgentState.executorLoopStatus = 1;

  // 5g. Round 3 Probe P1 & H: Same-count ID swap {bg-a, bg-b} -> {bg-b, bg-c} latches finishedBgIds=['task:bg-a'] and bgFinishedEdgeTs > 0 without being wiped by bg-c starting
  liveAgentState.backgroundTasks = [{ id: 'bg-a' }, { id: 'bg-b' }];
  liveAgentState.fullyIdle = false;
  currentNowMs += 1000;
  win.dispatchEvent({ type: 'resize' });
  const wdBeforeSwap = win.__AGY_V3__.getWakeupWatchdogState()[TEST_CID];
  assert.deepEqual(Array.from(wdBeforeSwap.activeBgIds), ['task:bg-a', 'task:bg-b'], 'activeBgIds must track exact task identities');
  const hudWdPill = hudHost.shadowRoot.getElementById('m-wd');
  assert.equal(hudWdPill?.textContent, '后台 2', 'Mission HUD #m-wd pill must show "后台 2" when 2 background tasks are active');

  // Swap {bg-a, bg-b} -> {bg-b, bg-c} on the same tick (count stays 2, but bg-a finished and bg-c started!)
  liveAgentState.backgroundTasks = [{ id: 'bg-b' }, { id: 'bg-c' }];
  currentNowMs += 1000;
  win.dispatchEvent({ type: 'resize' });
  const wdAfterSwap = win.__AGY_V3__.getWakeupWatchdogState()[TEST_CID];
  assert.deepEqual(Array.from(wdAfterSwap.finishedBgIds), ['task:bg-a'], 'Probe P1: Same-tick swap must record task:bg-a in finishedBgIds');
  assert.ok(wdAfterSwap.bgFinishedEdgeTs > 0, 'Probe P1 & H: Same-tick swap must latch bgFinishedEdgeTs > 0 without being zeroed by bg-c start');
  assert.equal(hudWdPill?.textContent, '等待接管', 'Mission HUD #m-wd pill must show "等待接管" when completion edge is latched');

  currentNowMs += 46000;
  win.dispatchEvent({ type: 'resize' });
  await new Promise((r) => setTimeout(r, 10));
  assert.equal(hudWdPill?.textContent, '已接管', 'Mission HUD #m-wd pill must show "已接管" right after wakeup is sent');

  // 5h. Round 3 Probe P2 & P3: Authoritative backgroundTasks ignores duplicate backgroundTasksUpdate; sparse backgroundTasksUpdate merges by indices & totalLength
  liveAgentState.backgroundTasks = [{ id: 'bg-dup' }];
  liveAgentState.backgroundTasksUpdate = { indices: [0], backgroundTasks: [{ id: 'bg-dup' }], totalLength: 1 };
  currentNowMs += 1000;
  win.dispatchEvent({ type: 'resize' });
  assert.equal(
    win.__AGY_V3__.getWakeupWatchdogState()[TEST_CID].lastNonDaemonBgCount,
    1,
    'Probe P2: Duplicate task in both backgroundTasks and backgroundTasksUpdate must count as 1, not 2'
  );

  // Now test sparse patch (Probe P3): seed 3 tasks, then delete full array and send sparse patch at index 2 with totalLength: 3
  liveAgentState.backgroundTasks = [{ id: 'bg-1' }, { id: 'bg-2' }, { id: 'bg-3' }];
  currentNowMs += 1000;
  win.dispatchEvent({ type: 'resize' });
  delete liveAgentState.backgroundTasks;
  liveAgentState.backgroundTasksUpdate = {
    indices: [2],
    backgroundTasks: [{ id: 'bg-3-updated' }],
    totalLength: 3
  };
  currentNowMs += 1000;
  win.dispatchEvent({ type: 'resize' });
  assert.equal(
    win.__AGY_V3__.getWakeupWatchdogState()[TEST_CID].lastNonDaemonBgCount,
    3,
    'Probe P3: Sparse backgroundTasksUpdate with 1 element and totalLength=3 must retain all 3 indexed tasks'
  );
  delete liveAgentState.backgroundTasksUpdate;

  // 5i. Round 3 Probe P4: Provider row + RUNNING invokeSubagent step join to 1 canonical ID; dropping provider row while step stays RUNNING must NOT fire wakeup
  liveAgentState.backgroundTasks = [];
  liveAgentState.fullyIdle = true;
  liveAgentState.trajectorySlice.totalStepsLength += 1;
  currentNowMs += 1000;
  win.dispatchEvent({ type: 'resize' }); // clear state

  const subStepIdx = 99;
  const runningSubStep = {
    status: 1, // RUNNING
    step: { case: 'invokeSubagent', value: { role: '架构复审智能体' } },
    metadata: { sourceTrajectoryStepInfo: { stepIndex: subStepIdx } }
  };
  liveAgentState.trajectorySlice.stepsInSlice.push(runningSubStep);
  liveAgentState.backgroundTasks = [{ id: 'sub-row-1' }];
  liveAgentState.fullyIdle = false;
  currentNowMs += 1000;
  win.dispatchEvent({ type: 'resize' });
  const wdJoined = win.__AGY_V3__.getWakeupWatchdogState()[TEST_CID];
  assert.deepEqual(
    Array.from(wdJoined.activeBgIds),
    [`step:${subStepIdx}`],
    'Probe P4: Provider row and running invokeSubagent step must join to single canonical ID step:99'
  );

  // Remove provider row while invokeSubagent step stays status === 1 (RUNNING) for >10s
  const sendsBeforeP4 = fetchCalls.filter((c) => String(c.url).endsWith('/SendAgentMessage')).length;
  liveAgentState.backgroundTasks = [];
  currentNowMs += 1000;
  win.dispatchEvent({ type: 'resize' });
  assert.equal(
    win.__AGY_V3__.getWakeupWatchdogState()[TEST_CID].bgFinishedEdgeTs,
    0,
    'Probe P4: Removing provider row while invokeSubagent step is still RUNNING must NOT latch bgFinishedEdgeTs'
  );
  currentNowMs += 48000;
  win.dispatchEvent({ type: 'resize' });
  await new Promise((r) => setTimeout(r, 10));
  assert.equal(
    fetchCalls.filter((c) => String(c.url).endsWith('/SendAgentMessage')).length,
    sendsBeforeP4,
    'Probe P4: Zero SendAgentMessage calls while invokeSubagent step remains RUNNING'
  );
  // Finish the subagent step and reset
  runningSubStep.status = 3;
  liveAgentState.fullyIdle = true;
  liveAgentState.trajectorySlice.totalStepsLength += 1;
  currentNowMs += 1000;
  win.dispatchEvent({ type: 'resize' });

  // 5j. Round 3 Probe J: Second completion arriving while first completion RPC is in flight must preserve newer bgFinishedEdgeTs
  liveAgentState.backgroundTasks = [{ id: 'j-1' }, { id: 'j-2' }];
  liveAgentState.fullyIdle = false;
  currentNowMs += 1000;
  win.dispatchEvent({ type: 'resize' });
  // First finish: 2 -> 1
  liveAgentState.backgroundTasks = [{ id: 'j-2' }];
  currentNowMs += 1000;
  win.dispatchEvent({ type: 'resize' });
  const edge1 = win.__AGY_V3__.getWakeupWatchdogState()[TEST_CID].bgFinishedEdgeTs;
  assert.ok(edge1 > 0);

  let resolveProbeJ = null;
  win.__holdNextWakeupFetch = () =>
    new Promise((resolve) => {
      resolveProbeJ = resolve;
    });
  currentNowMs += 46000;
  win.dispatchEvent({ type: 'resize' }); // launches first completion RPC (held in flight)
  assert.equal(win.__AGY_V3__.getWakeupWatchdogState()[TEST_CID].rpcInFlight, true);

  // While first RPC is on the wire, second task j-2 finishes (1 -> 0)!
  liveAgentState.backgroundTasks = [];
  liveAgentState.fullyIdle = true;
  currentNowMs += 1000;
  win.dispatchEvent({ type: 'resize' });
  const edge2 = win.__AGY_V3__.getWakeupWatchdogState()[TEST_CID].bgFinishedEdgeTs;
  assert.ok(edge2 > edge1, 'Probe J: Second finish while RPC in flight must advance bgFinishedEdgeTs');

  // Now resolve the first RPC with 200 OK -> must NOT zero edge2!
  if (resolveProbeJ) resolveProbeJ({ ok: true, json: async () => ({}) });
  await new Promise((r) => setTimeout(r, 10));
  assert.equal(
    win.__AGY_V3__.getWakeupWatchdogState()[TEST_CID].bgFinishedEdgeTs,
    edge2,
    'Probe J: Resolving first completion RPC must preserve newer bgFinishedEdgeTs from second completion'
  );
  // Advance past 45s cooldown -> second completion fires!
  const sendsBeforeJ2 = fetchCalls.filter((c) => String(c.url).endsWith('/SendAgentMessage')).length;
  currentNowMs += 46000;
  win.dispatchEvent({ type: 'resize' });
  await new Promise((r) => setTimeout(r, 10));
  assert.equal(
    fetchCalls.filter((c) => String(c.url).endsWith('/SendAgentMessage')).length,
    sendsBeforeJ2 + 1,
    'Probe J: Preserved second completion edge must fire after cooldown'
  );

  // 5k. Round 3 Probe K & E: runCommand with blocking: false / runPersistent: true + sparse pendingAgentMessagesUpdate fires Condition 1
  liveAgentState.trajectorySlice.totalStepsLength += 1;
  const nonBlockingCmdStep = {
    status: 1, // RUNNING
    step: { case: 'runCommand', value: { commandLine: 'npm run watch', blocking: false, runPersistent: true } },
    metadata: { sourceTrajectoryStepInfo: { stepIndex: 105 } }
  };
  liveAgentState.trajectorySlice.stepsInSlice.push(nonBlockingCmdStep);
  delete liveAgentState.pendingMessages;
  liveAgentState.pendingAgentMessagesUpdate = {
    indices: [0],
    pendingAgentMessages: [{ id: 'sparse-msg-1' }],
    totalLength: 1
  };
  currentNowMs += 1000;
  win.dispatchEvent({ type: 'resize' });
  const sendsBeforeKE = fetchCalls.filter((c) => String(c.url).endsWith('/SendAgentMessage')).length;
  currentNowMs += 46000;
  win.dispatchEvent({ type: 'resize' });
  await new Promise((r) => setTimeout(r, 10));
  assert.equal(
    fetchCalls.filter((c) => String(c.url).endsWith('/SendAgentMessage')).length,
    sendsBeforeKE + 1,
    'Probe K & E: blocking=false/runPersistent=true command must be classified as background and allow sparse pendingAgentMessagesUpdate to wake'
  );
  nonBlockingCmdStep.status = 3;
  delete liveAgentState.pendingAgentMessagesUpdate;

  // 5l. Round 3 Probe P5 & P9 + Design #3 (__asu buffered sparse patches):
  // Seed 1 task via __asu buffer while liveAgentState.backgroundTasks is undefined, then drop to 0 while fullyIdle stays false (lagging flag)
  delete liveAgentState.backgroundTasks;
  win.__asu = {
    [TEST_CID]: [
      {
        conversationId: TEST_CID,
        backgroundTasksUpdate: {
          indices: [0],
          backgroundTasks: [{ id: 'asu-bg-1' }],
          totalLength: 1
        }
      }
    ]
  };
  liveAgentState.fullyIdle = false;
  currentNowMs += 1000;
  win.dispatchEvent({ type: 'resize' });
  assert.deepEqual(
    Array.from(win.__AGY_V3__.getWakeupWatchdogState()[TEST_CID].activeBgIds),
    ['task:asu-bg-1'],
    'Design #3: __asu buffered backgroundTasksUpdate must merge into activeBgIds'
  );
  // Brief 1-tick mainExecutorRunning flicker via real executorLoopStatus=2 without advancing totalStepsLength must preserve activeBgIds (Probe P9)
  liveAgentState.executorLoopStatus = 2;
  currentNowMs += 1000;
  win.dispatchEvent({ type: 'resize' });
  assert.deepEqual(
    Array.from(win.__AGY_V3__.getWakeupWatchdogState()[TEST_CID].activeBgIds),
    ['task:asu-bg-1'],
    'Probe P9: Brief mainExecutorRunning flicker (executorLoopStatus=2) without step progress must preserve activeBgIds'
  );
  // Return executorLoopStatus to 1 (IDLE), drop task via __asu totalLength: 0 while fullyIdle lags at false (Probe P5)
  liveAgentState.executorLoopStatus = 1;
  win.__asu[TEST_CID].push({
    conversationId: TEST_CID,
    backgroundTasksUpdate: { indices: [], backgroundTasks: [], totalLength: 0 }
  });
  currentNowMs += 1000;
  win.dispatchEvent({ type: 'resize' });
  assert.ok(
    win.__AGY_V3__.getWakeupWatchdogState()[TEST_CID].bgFinishedEdgeTs > 0,
    'Probe P5: Dropping background task from 1 -> 0 while fullyIdle lags at false must still latch bgFinishedEdgeTs'
  );
  delete win.__asu;
  liveAgentState.fullyIdle = true;
  liveAgentState.trajectorySlice.totalStepsLength += 1;
  currentNowMs += 1000;
  win.dispatchEvent({ type: 'resize' }); // clear P5 edge before testing children:active

  // Verify synthetic children:active being replaced by a concrete task ID does NOT latch a false completion edge
  liveAgentState.hasActiveChildren = true;
  liveAgentState.backgroundTasks = [];
  liveAgentState.fullyIdle = false;
  currentNowMs += 1000;
  win.dispatchEvent({ type: 'resize' });
  assert.deepEqual(
    Array.from(win.__AGY_V3__.getWakeupWatchdogState()[TEST_CID].activeBgIds),
    ['children:active'],
    'hasActiveChildren=true with empty task list must seed synthetic children:active'
  );
  assert.equal(win.__AGY_V3__.getWakeupWatchdogState()[TEST_CID].bgFinishedEdgeTs, 0);
  liveAgentState.hasActiveChildren = false;
  liveAgentState.backgroundTasks = [{ id: 'real-child-1' }];
  currentNowMs += 1000;
  win.dispatchEvent({ type: 'resize' });
  assert.equal(
    win.__AGY_V3__.getWakeupWatchdogState()[TEST_CID].bgFinishedEdgeTs,
    0,
    'Replacing synthetic children:active with concrete task:real-child-1 must NOT latch a false completion edge'
  );
  liveAgentState.backgroundTasks = [];
  liveAgentState.fullyIdle = true;
  liveAgentState.trajectorySlice.totalStepsLength += 1;
  currentNowMs += 1000;
  win.dispatchEvent({ type: 'resize' });

  console.log(
    '  [PASS 5] 响应式唤醒看门狗 (集合差分 activeBgIds + __asu 稀疏补丁归并 + 10 项 Grok 第3轮对抗探针 P1-P9/E/H/J/K + HUD 状态胶囊) 100% 通过'
  );

  // -------------------------------------------------------------------------
  // Verification 6: 1M Context Ring (#agy-ctx-ring-pill) & v2.29.0 Official Indicator Coexistence
  // -------------------------------------------------------------------------
  const ringPill = doc.getElementById('agy-ctx-ring-pill');
  assert.ok(ringPill, '#agy-ctx-ring-pill must be mounted');
  ringPill.dispatchEvent({ type: 'click', stopPropagation: () => {} });
  const ringText = ringPill.querySelector('#agy-ctx-ring-text')?.textContent || '';
  const ringArc = ringPill.querySelector('#agy-ctx-ring-arc');
  // 120k + 100k + 5k = 225k / 1000k = 23% · 225k
  assert.equal(ringText, '23% · 225k', 'Context ring must compute exact 225k / 1M (23%) from Fiber usage');
  assert.equal(ringArc?.getAttribute('stroke'), '#10b981', 'Context ring stroke color must be green (<55%)');

  // Verify v2.29.0 flags & official context-usage-indicator / context-usage-panel integration
  const savedFlags = JSON.parse(win.localStorage.getItem('jetski.developer.customFlagOverrides') || '{}');
  assert.equal(savedFlags['enable-context-usage-indicator'], true, 'enable-context-usage-indicator must be enabled');
  assert.equal(win.localStorage.getItem('contextUsageIndicatorEnabled'), 'true', 'contextUsageIndicatorEnabled must default to "true"');

  const officialIndicator = doc.createElement('button');
  officialIndicator.setAttribute('data-testid', 'context-usage-indicator');
  doc.body.appendChild(officialIndicator);
  win.dispatchEvent({ type: 'resize' });
  assert.equal(
    ringPill.style.display,
    'none',
    '#agy-ctx-ring-pill must automatically yield (display: none) when official [data-testid="context-usage-indicator"] is mounted'
  );
  officialIndicator.remove();

  const officialPanel = doc.createElement('div');
  officialPanel.setAttribute('data-testid', 'context-usage-panel');
  const hdrSpan = doc.createElement('span');
  hdrSpan.textContent = 'Context window';
  const subP = doc.createElement('p');
  subP.className = 'font-mono tabular-nums';
  subP.appendChild(doc.createTextNode('120K'));
  subP.appendChild(doc.createTextNode(' of'));
  subP.appendChild(doc.createTextNode(' '));
  subP.appendChild(doc.createTextNode('983K'));
  subP.appendChild(doc.createTextNode(' tokens used'));
  const panelQuotaSpan = doc.createElement('span');
  panelQuotaSpan.className = 'font-mono';
  panelQuotaSpan.appendChild(doc.createTextNode('85'));
  panelQuotaSpan.appendChild(doc.createTextNode('% left'));
  const dt1 = doc.createElement('dt');
  dt1.textContent = 'Instructions and tools';
  const dt2 = doc.createElement('dt');
  dt2.textContent = 'Conversation';
  const dt3 = doc.createElement('dt');
  dt3.textContent = 'Free';
  officialPanel.appendChild(hdrSpan);
  officialPanel.appendChild(subP);
  officialPanel.appendChild(panelQuotaSpan);
  officialPanel.appendChild(dt1);
  officialPanel.appendChild(dt2);
  officialPanel.appendChild(dt3);
  doc.body.appendChild(officialPanel);
  await new Promise((r) => setTimeout(r, 90));
  assert.equal(hdrSpan.textContent, '上下文窗口');
  assert.equal(subP.textContent, '120K / 983K Token 已用');
  assert.equal(panelQuotaSpan.textContent, '85% 剩余');
  assert.equal(dt1.textContent, '系统指令与工具');
  assert.equal(dt2.textContent, '会话正文与工具结果');
  assert.equal(dt3.textContent, '剩余可用空间');
  officialPanel.remove();

  console.log(`  [PASS 6] 1M 上下文环 (#agy-ctx-ring-pill) 与 v2.29.0 官方指示器退让/面板中文化核验通过: "${ringText}"`);

  // -------------------------------------------------------------------------
  // Verification 7: Workspace Clutter & macOS + Windows Temp Folder Filtering (plus non-temp guard)
  // -------------------------------------------------------------------------
  assert.equal(itemCitc.style.getPropertyValue('display'), 'none', 'CitC Workspace must be hidden');
  assert.equal(itemCog.style.getPropertyValue('display'), 'none', 'Cog Workspace must be hidden');
  assert.equal(itemAbfs.style.getPropertyValue('display'), 'none', 'ABFS Workspace must be hidden');
  assert.equal(itemMacVarTmp.style.getPropertyValue('display'), 'none', 'macOS /var/folders/.../T/agy_* must be hidden');
  assert.equal(itemMacSlashTmp.style.getPropertyValue('display'), 'none', 'macOS /tmp/ocr_* must be hidden');
  assert.equal(itemWinTmp.style.getPropertyValue('display'), 'none', 'Windows \\AppData\\Local\\Temp\\agy_* must be hidden');
  assert.equal(itemWinSlashTmp.style.getPropertyValue('display'), 'none', 'Windows C:/.../AppData/Local/Temp/agy_* must be hidden');
  assert.notEqual(itemFalsePositiveTmp.style.getPropertyValue('display'), 'none', 'Non-temp /Users/ien/notmp/agy_false must NOT be hidden');
  assert.notEqual(itemNormal.style.getPropertyValue('display'), 'none', 'Normal user workspace must remain visible');
  console.log(
    '  [PASS 7] Google 内部菜单项 (CitC/Cog/ABFS) 与 macOS/Windows 临时诊断目录精确边界过滤核验通过'
  );

  // -------------------------------------------------------------------------
  // Verification 8: e93889f8 Collapsible Thinking UX (including nextToolDesc) & macOS setTitleBarOverlay Guard
  // -------------------------------------------------------------------------
  const inlineSummary = thinkBtn.querySelector('.agy-think-inline-summary');
  assert.ok(inlineSummary, '.agy-think-inline-summary must be injected into thinking-collapsible-trigger');
  assert.equal(
    inlineSummary.textContent,
    '排查 ipcHandlers.js 中 fs_1 未定义的潜在缺陷',
    'Inline thinking summary must extract qualitative headline'
  );
  assert.match(thinkLabelSpan.textContent, /思考 · 6s/, '"Thought for 6s" inside thinking button must be translated');

  const inlineSummary2 = thinkBtn2.querySelector('.agy-think-inline-summary');
  assert.ok(inlineSummary2, '.agy-think-inline-summary must be injected into second thinking trigger');
  assert.match(
    inlineSummary2.textContent,
    /进而.*asar_inject_and_rollback\.py/,
    'nextToolDesc derivation (e93889f8 lines 2965-2979) must synthesize headline from following tool step when thinking is English'
  );

  // Simulate click capture on thinkingBtn to expand -> sets data-agy-user-expanded="1"
  let propagationStopped = false;
  win.dispatchEvent({
    type: 'click',
    target: thinkBtn,
    stopImmediatePropagation: () => {
      propagationStopped = true;
    },
    stopPropagation: () => {
      propagationStopped = true;
    }
  });
  assert.equal(
    thinkBtn.getAttribute('data-agy-user-expanded'),
    '1',
    'Clicking collapsed thinking trigger must set data-agy-user-expanded="1"'
  );
  assert.equal(propagationStopped, true, 'Click capture must stop propagation when aria-expanded is already true');

  // Verify macOS setTitleBarOverlay guard vs Windows
  assert.equal(
    titleBarOverlayCalls.length,
    0,
    'setWindowTitleBarColors must skip setTitleBarOverlay on macOS (MacIntel)'
  );
  currentPlatform = 'Win32';
  win.dispatchEvent({
    type: 'keydown',
    altKey: true,
    key: 'w',
    code: 'KeyW',
    preventDefault: () => {}
  });
  assert.ok(
    titleBarOverlayCalls.length > 0,
    'setWindowTitleBarColors must invoke setTitleBarOverlay when platform is Win32'
  );
  console.log(
    '  [PASS 8] 会话 e93889f8 思考流单句摘要 (含 nextToolDesc 14 行上下文推导)/点击展开修复与 macOS/Windows 标题栏平台守卫核验通过'
  );

  // Cleanly dispose loader and verify restoration
  win.__AGY_V3__.dispose();
  assert.equal(win.__AGY_V3__.isDisposed(), true, 'Loader must report disposed=true after dispose()');
  assert.equal(uiBtnText.nodeValue, 'New Conversation', 'dispose() must restore original English text');
  console.log('  [PASS 9] Mode C 挂起与无损卸载还原 (dispose) 核验通过');
  console.log('\n✅ 全部 9 大项（含 Grok 4.7 xhigh 审计新增边界断言）100% 通过！');
}

runVerification().catch((err) => {
  console.error('❌ 沙箱自验失败:', err);
  process.exit(1);
});
