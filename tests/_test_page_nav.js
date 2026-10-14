/* 页内区块导航彻底移除后的契约（v0.5.0，issue：统一保留全局左侧主导航）。
 *
 * 8789 看板不再有任何「本页导航 / 区块导航」表现与残留逻辑：没有
 * .page-sidebar / .page-nav / .page-nav-body DOM、没有 page-nav-item /
 * page-sidebar-toggle 样式，也没有 initPageNav / pageNavSections /
 * pageNavLabel / pageNavAnchorId 这些只服务于页内导航的函数。
 *
 * 只保留一条最小兼容：打开带 #id 的地址时，如果页面上真有这个 id，就滚到它；
 * 不会扫描 section、不会生成锚点、不会创建任何页内导航 UI。
 *
 * 对着 dashboard.html 里真实函数跑。Run with Node.
 */
'use strict';
const assert = require('assert');
const {dashboardHtml, dashboardScript} = require('./_dashboard_source.js');

const html = dashboardHtml();
const script = dashboardScript();

/* ---- 1. 标记和脚本里不再有任何页内导航残留 ---- */
assert.ok(!/class="page-sidebar"/.test(html), '侧栏不应写死在标记里');
assert.ok(!html.includes('page-sidebar'), 'page-sidebar 应已彻底移除');
assert.ok(!html.includes('page-nav'), 'page-nav 相关标记/类名应已彻底移除');
assert.ok(!html.includes('page-nav-body'), 'page-nav-body 包裹层应已彻底移除');
assert.ok(!html.includes('page-nav-item'), '导航项样式应已彻底移除');
assert.ok(!html.includes('page-sidebar-toggle'), '侧栏收起按钮应已彻底移除');
assert.ok(!html.includes('pageNavSignature'), '侧栏指纹函数应已彻底移除');
assert.ok(!html.includes('PAGE_NAV_COLLAPSE_KEY'), '侧栏收起键应已彻底移除');
assert.ok(!html.includes('pageNavSections'), 'pageNavSections 应已彻底移除');
assert.ok(!html.includes('pageNavLabel'), 'pageNavLabel 应已彻底移除');
assert.ok(!html.includes('pageNavAnchorId'), 'pageNavAnchorId 应已彻底移除');
assert.ok(!html.includes('initPageNav'), 'initPageNav 应已彻底移除');
assert.ok(!html.includes('restorePageNavHash'), 'restorePageNavHash 应已彻底移除');
assert.ok(!html.includes('本页导航'), '本页导航文案应已彻底移除');
assert.ok(!html.includes('本页区块导航'), '本页区块导航文案应已彻底移除');

/* ---- 一个只够跑锚点兼容的极简 DOM ---- */
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
  return { restorePageHash, scrollToPageSection, switchMainTab };`)();

/* ---- 2. 只按 id 落位：存在的元素才滚，不存在就不动 ---- */
const target = new El('div');
target.id = 'known-anchor';
target.rectTop = 240;
root.appendChild(target);

window.location.hash = '#known-anchor';
global.__scroll = null;
api.restorePageHash();
assert.ok(global.__scroll, '存在的 #id 应触发滚动');
assert.strictEqual(global.__scroll.behavior, 'auto', '带锚点打开是直接落位，不是平滑滚动');

// 假 DOM 的 getElementById 会对任意 id 惰性建元素，真实 DOM 返回 null；
// 这里把未知 id 显式覆盖成 null，模拟真实浏览器的行为。
const realGetById = global.document.getElementById;
global.document.getElementById = id => (id === 'missing-anchor' ? null : realGetById(id));
window.location.hash = '#missing-anchor';
global.__scroll = null;
api.restorePageHash();
assert.strictEqual(global.__scroll, null, '不存在的 #id 不应触发滚动');

window.location.hash = '';
global.__scroll = null;
api.restorePageHash();
assert.strictEqual(global.__scroll, null, '没有 #锚点 时什么都不做');

/* ---- 3. 不生成任何页内导航 DOM，也不扫描 section 补 id ---- */
const sec = new El('section');
const h2 = new El('h2');
h2.appendChild('账号');
sec.appendChild(h2);
root.appendChild(sec);
api.restorePageHash();
assert.strictEqual(sec.id, '', '不应给 section 生成锚点 id');
assert.strictEqual(document.querySelector('.page-sidebar'), null, '不应创建侧栏 DOM');
assert.strictEqual(document.querySelector('.page-nav'), null, '不应创建导航列表 DOM');
assert.ok(!document.body.classList.contains('page-nav-on'), '不应启用页面级两栏');

/* ---- 4. 切标签页仍然可用 ---- */
const gateway = new El('div');
gateway.className = 'main-page';
gateway.id = 'pageGateway';
root.appendChild(gateway);
const btnGateway = new El('button'); btnGateway.id = 'btnNavGateway';
root.appendChild(btnGateway);
const pageSettings = new El('div');
pageSettings.className = 'main-page';
pageSettings.id = 'pageSettings';
root.appendChild(pageSettings);
api.switchMainTab('gateway');
assert.ok(gateway.classList.contains('active'), '切到 gateway 应激活该页');

console.log('page anchor assertions passed');
