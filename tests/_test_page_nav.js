/* 页面区块锚点契约（页内区块导航已移除）。
 *
 * 仪表盘不再渲染 .page-sidebar / .page-nav，多区块页面改用分段按钮切换。这里
 * 只钉住仍承诺的“旧锚点兼容”性质：
 *
 *   - initPageNav(page) 只给参与导航的区块补稳定 id（page-sec-<hash>），不建侧栏；
 *   - 已有 id 的区块不被覆盖；嵌套/无 h2/隐藏的区块不参与；
 *   - restorePageNavHash() 只在带 #锚点 打开时把本页“可见、可导航”的区块滚到视口，
 *     别的页面的锚点或被隐藏的区块不触发滚动。
 *
 * 对着 dashboard.html 里真实函数跑。Run with Node.
 */
'use strict';
const assert = require('assert');
const {dashboardHtml, dashboardScript} = require('./_dashboard_source.js');

const html = dashboardHtml();
const script = dashboardScript();

/* ---- 1. 标记里不再有侧栏 / 两栏样式 ---- */
assert.ok(html.includes('initPageNav'), 'dashboard.html 应当有 initPageNav');
assert.ok(!/class="page-sidebar"/.test(html), '侧栏不应写死在标记里');
assert.ok(!html.includes('function pageNavSignature'), '侧栏指纹函数应已移除');
assert.ok(!html.includes('PAGE_NAV_COLLAPSE_KEY'), '侧栏收起键应已移除');
assert.ok(!html.includes('.main-page.page-nav-on > .page-sidebar'), '两栏侧栏样式应已移除');
assert.ok(!html.includes('page-nav-item'), '导航项样式/脚本应已移除');
assert.ok(!html.includes('page-sidebar-toggle'), '侧栏收起按钮应已移除');

/* ---- 一个只够跑锚点的极简 DOM ---- */
const ALL = [];
function El(tag, ephemeral){
  this.tagName = String(tag).toUpperCase();
  this.nodeType = 1;
  this.children = [];
  this.parentElement = null;
  this.id = '';
  this._classes = [];
  this.dataset = {};
  this.attrs = {};
  this.listeners = {};
  this.rectTop = 0;
  this.offsetHeight = 40;
  this.scrollHeight = 1000;
  const self = this;
  this.style = { display: '', setProperty(k, v){ self.style[k] = v; },
                 getPropertyValue(k){ return self.style[k] || ''; } };
  this.classList = {
    add(...cs){ cs.forEach(c => { if(!self._classes.includes(c)) self._classes.push(c); }); },
    remove(...cs){ self._classes = self._classes.filter(c => !cs.includes(c)); },
    contains(c){ return self._classes.includes(c); },
    toggle(c, force){
      const has = self.classList.contains(c);
      const want = force === undefined ? !has : !!force;
      if(want && !has) self._classes.push(c);
      if(!want && has) self._classes = self._classes.filter(x => x !== c);
      return want;
    },
  };
  if(!ephemeral) ALL.push(this);
}
Object.defineProperty(El.prototype, 'className', {
  get(){ return this._classes.join(' '); },
  set(v){ this._classes = String(v).split(/\s+/).filter(Boolean); },
});
Object.defineProperty(El.prototype, 'firstChild', { get(){ return this.children[0] || null; } });
Object.defineProperty(El.prototype, 'textContent', {
  get(){ return this.children.map(c => (typeof c === 'string' ? c : c.textContent)).join(''); },
  set(v){ this.children = v === '' ? [] : [String(v)]; },
});
El.prototype.appendChild = function(node){
  if(typeof node !== 'string' && node.parentElement) node.parentElement.removeChild(node);
  if(typeof node !== 'string') node.parentElement = this;
  this.children.push(node);
  return node;
};
El.prototype.removeChild = function(node){
  this.children = this.children.filter(c => c !== node);
  if(typeof node !== 'string') node.parentElement = null;
};
El.prototype.insertBefore = function(node, ref){
  const at = this.children.indexOf(ref);
  if(at < 0) return this.appendChild(node);
  if(node.parentElement) node.parentElement.removeChild(node);
  node.parentElement = this;
  this.children.splice(at, 0, node);
  return node;
};
El.prototype.remove = function(){ if(this.parentElement) this.parentElement.removeChild(this); };
El.prototype.setAttribute = function(k, v){ this.attrs[k] = v; if(k === 'id') this.id = v; };
El.prototype.getAttribute = function(k){ return k in this.attrs ? this.attrs[k] : ''; };
El.prototype.hasAttribute = function(k){ return k in this.attrs; };
El.prototype.removeAttribute = function(k){ delete this.attrs[k]; };
El.prototype.addEventListener = function(type, fn){ (this.listeners[type] = this.listeners[type] || []).push(fn); };
El.prototype.removeEventListener = function(){};
['focus', 'blur', 'click', 'select', 'scrollIntoView', 'insertAdjacentHTML', 'replaceChildren',
 'append', 'prepend', 'after', 'before', 'dispatchEvent', 'setSelectionRange'].forEach(name => {
  El.prototype[name] = function(){};
});
El.prototype.contains = function(node){
  for(let el = node; el; el = el.parentElement) if(el === this) return true;
  return false;
};
El.prototype.matches = function(sel){ return matches(this, sel); };
El.prototype.getBoundingClientRect = function(){ return { top: this.rectTop, left: 0, width: 0, height: 0 }; };
El.prototype.closest = function(sel){
  for(let el = this; el; el = el.parentElement) if(matches(el, sel)) return el;
  return null;
};
El.prototype.querySelectorAll = function(sel){
  const out = [];
  (function walk(node){
    for(const child of node.children){
      if(typeof child === 'string') continue;
      if(matches(child, sel)) out.push(child);
      walk(child);
    }
  })(this);
  return out;
};
El.prototype.querySelector = function(sel){ return this.querySelectorAll(sel)[0] || null; };
El.prototype.cloneNode = function(deep){
  const copy = new El(this.tagName, true);
  copy._classes = this._classes.slice();
  copy.id = this.id;
  copy.dataset = Object.assign({}, this.dataset);
  copy.style = Object.assign({}, this.style);
  if(deep) for(const child of this.children) copy.appendChild(typeof child === 'string' ? child : child.cloneNode(true));
  return copy;
};

/* 只支持本模块用到的选择器形状：tag、.class、.class.class、[id] */
function matches(el, sel){
  const hasId = /\[id\]/.test(sel);
  const parts = sel.replace(/\[id\]/g, '').split('.');
  const tag = parts[0] ? parts[0].toUpperCase() : null;
  if(tag && el.tagName !== tag) return false;
  for(const cls of parts.slice(1)) if(cls && !el.classList.contains(cls)) return false;
  return !(hasId && !el.id);
}

const root = new El('html');
// 吸顶 header：锚点落位要按它的实测高度让位
const header = new El('header');
header.offsetHeight = 40;
root.appendChild(header);
const byId = new Map();
const detached = id => {
  if(!byId.has(id)) byId.set(id, Object.assign(new El('div', true), { id: id }));
  return byId.get(id);
};

global.document = {
  getElementById: id => ALL.find(el => el.id === id) || detached(id),
  querySelector: sel => ALL.find(el => matches(el, sel)) || null,
  querySelectorAll: sel => ALL.filter(el => matches(el, sel)),
  createElement: tag => new El(tag),
  addEventListener(){}, body: root, documentElement: root, head: root,
  readyState: 'complete',
};
global.getComputedStyle = el => ({ display: (el && el.style && el.style.display) || 'block' });
global.window = {
  addEventListener(){}, matchMedia: () => ({ matches: false, addEventListener(){} }),
  pageYOffset: 0, innerHeight: 800,
  scrollTo(opts){ global.__scroll = opts; },
  location: { href: 'http://panel/', search: '', hash: '' },
};
global.location = global.window.location;
global.history = { replaceState(_s, _t, url){ global.__url = url; } };
global.MutationObserver = function(){ this.observe = () => {}; this.disconnect = () => {}; };
global.setTimeout = () => 0;
global.clearTimeout = () => {};
global.setInterval = () => 0;
const store = new Map();
global.localStorage = {
  getItem: k => (store.has(k) ? store.get(k) : null),
  setItem: (k, v) => { store.set(k, String(v)); },
  removeItem: k => { store.delete(k); },
};
global.sessionStorage = global.localStorage;
// Node 21+ 把 navigator 暴露成 getter-only 全局，只能用 defineProperty 安装。
try { Object.defineProperty(global, 'navigator', { value: { userAgent: 'node' }, configurable: true, writable: true }); }
catch(e) { /* 装不上就沿用宿主自带的 */ }
global.alert = () => {};
global.confirm = () => false;
global.fetch = () => Promise.resolve({ ok: false, status: 500, json: async () => ({}), text: async () => '' });

const api = new Function(script + `
  return { initPageNav, pageNavSections, pageNavLabel, pageNavAnchorId,
           restorePageNavHash, switchMainTab };`)();

/* ---- 造页面的小工具 ---- */
function sectionEl(label, opts){
  opts = opts || {};
  const sec = new El('section');
  if(opts.id) sec.id = opts.id;
  if(opts.hidden) sec.style.display = 'none';
  if(label !== null){
    const h = new El('h2');
    h.appendChild(label);
    if(opts.em){ const em = new El('em'); em.id = opts.em; em.appendChild('7'); h.appendChild(em); }
    sec.appendChild(h);
  }
  return sec;
}
function pageEl(id, sections){
  const page = new El('div');
  page.className = 'main-page';
  page.id = id;
  sections.forEach(sec => page.appendChild(sec));
  return page;
}
function attach(page){ root.appendChild(page); return page; }

/* ---- 2. 哪些区块参与导航 ---- */
const secAccount = sectionEl('账号', { em: 'acctCount' });
const secRecent = sectionEl('最近请求');
const secGrowth = sectionEl('成长任务', { hidden: true });
const secUntitled = sectionEl(null);                 // 没有 h2，不成项
const group = sectionEl('高级设置');
const nestedA = sectionEl('嵌套甲');                  // 嵌套 section 属于父区块的内容
const nestedB = sectionEl('嵌套乙');
group.appendChild(nestedA);
group.appendChild(nestedB);
const gateway = pageEl('pageGateway', [secAccount, secRecent, secGrowth, secUntitled, group]);

assert.deepStrictEqual(
  api.pageNavSections(gateway).map(sec => api.pageNavLabel(sec, 0)),
  ['账号', '最近请求', '高级设置'],
  '只收最外层、有 h2、且未隐藏的区块；标题要剔除 <em>');

// 隐藏的嵌套区块本来也不成项
nestedA.style.display = 'none';
assert.deepStrictEqual(
  api.pageNavSections(gateway).map(sec => api.pageNavLabel(sec, 0)),
  ['账号', '最近请求', '高级设置'],
  '隐藏的嵌套区块本来也不成项');
nestedA.style.display = '';
// 整块显隐确实会改变清单
secGrowth.style.display = 'block';
assert.deepStrictEqual(
  api.pageNavSections(gateway).map(sec => api.pageNavLabel(sec, 0)),
  ['账号', '最近请求', '成长任务', '高级设置'],
  '区块显出来后要进导航');
secGrowth.style.display = 'none';

/* ---- 3. 锚点 id：与顺序无关、可复用、冲突加后缀 ---- */
assert.strictEqual(api.pageNavAnchorId('账号', new Set()), api.pageNavAnchorId('账号', new Set()),
  '同一标题必须得到同一个 id');
assert.notStrictEqual(api.pageNavAnchorId('账号', new Set()), api.pageNavAnchorId('最近请求', new Set()),
  '不同标题必须得到不同 id');
const taken = new Set([api.pageNavAnchorId('账号', new Set())]);
assert.strictEqual(api.pageNavAnchorId('账号', taken),
  api.pageNavAnchorId('账号', new Set()) + '-2', 'id 撞车要顺延');

/* ---- 4. initPageNav：只补锚点，不建侧栏 ---- */
attach(gateway);
gateway.classList.add('active');
api.pageNavSections(gateway).forEach((sec, i) => { sec.rectTop = 120 + i * 300; });
api.initPageNav(gateway);
assert.strictEqual(gateway.querySelector('.page-sidebar'), null, '不应再创建侧栏 DOM');
assert.strictEqual(gateway.querySelector('.page-nav'), null, '不应再创建导航列表 DOM');
assert.ok(!gateway.classList.contains('page-nav-on'), '不应再启用页面级两栏');
for(const sec of api.pageNavSections(gateway)){
  assert.ok(sec.id, '每个参与导航的区块都要有锚点 id');
}
const idOfAccount = api.pageNavSections(gateway)[0].id;
assert.ok(idOfAccount.indexOf('page-sec-') === 0, '锚点 id 由标题哈希得到：' + idOfAccount);

// 已有 id 的区块不被覆盖
const pinned = new El('section');
pinned.id = 'keep-me';
const pinnedH2 = new El('h2'); pinnedH2.appendChild('带 id 的区块'); pinned.appendChild(pinnedH2);
const pageKeep = pageEl('pageKeep', [pinned, sectionEl('另一个')]);
attach(pageKeep);
api.initPageNav(pageKeep);
assert.strictEqual(pinned.id, 'keep-me', '已有 id 不应被覆盖');
assert.ok(api.pageNavSections(pageKeep)[1].id.indexOf('page-sec-') === 0, '没有 id 的区块仍会补锚点');

// 重复调用幂等，id 不变
api.initPageNav(gateway);
assert.strictEqual(api.pageNavSections(gateway)[0].id, idOfAccount, '重复调用锚点 id 不变');

/* ---- 5. #锚点 只落在本页可见、可导航的区块上 ---- */
window.location.hash = '#' + idOfAccount;
global.__scroll = null;
api.restorePageNavHash();
assert.ok(global.__scroll, '本页可导航区块的锚点应触发滚动');
assert.strictEqual(global.__scroll.behavior, 'auto', '带锚点打开是直接落位，不是平滑滚动');

window.location.hash = '#' + api.pageNavSections(pageKeep)[0].id;   // 别的页面上的区块
global.__scroll = null;
api.restorePageNavHash();
assert.strictEqual(global.__scroll, null, '别的页面的锚点不该让当前页滚动');

secRecent.style.display = 'none';                     // 本页但已被隐藏
window.location.hash = '#' + secRecent.id;
global.__scroll = null;
api.restorePageNavHash();
assert.strictEqual(global.__scroll, null, '被隐藏的区块不能当锚点落点');
secRecent.style.display = '';

/* ---- 6. 切标签页仍然可用 ---- */
const btnGateway = new El('button'); btnGateway.id = 'btnNavGateway';
root.appendChild(btnGateway);
attach(pageEl('pageSettings', [sectionEl('甲'), sectionEl('乙'), sectionEl('丙')]));
api.switchMainTab('gateway');
assert.ok(gateway.classList.contains('active'), '切到 gateway 应激活该页');

console.log('page anchor assertions passed');
