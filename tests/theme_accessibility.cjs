// Run: node tests/theme_accessibility.cjs
// Keyboard navigation must follow the selected panel after each editor re-render.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
let focused;
class Element {
  constructor(tag) { this.tagName = tag; this.children = []; this.attrs = new Map(); this.style = {setProperty() {}}; this.classList = {add() {}, remove() {}}; }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = nodes; }
  setAttribute(name, value) { this.attrs.set(name, String(value)); }
  getAttribute(name) { return this.attrs.get(name); }
  addEventListener(type, callback) { this['on' + type] = callback; }
  focus() { focused = this; }
}
const context = vm.createContext({document: {createElement: tag => new Element(tag)}});
vm.runInContext(fs.readFileSync(path.join(__dirname, '..', 'static', 'newsletter-theme-editor.js'), 'utf8'), context);
const editor = vm.runInContext('ThemeEditor', context);
const container = new Element('div'); let changes = 0;
editor.init(container, () => { changes++; });
editor.setDesign({});
const nodes = (node = container) => [node, ...node.children.flatMap(child => nodes(child))];
const tabs = () => nodes().filter(node => node.getAttribute('role') === 'tab');
const panel = () => nodes().find(node => node.getAttribute('role') === 'tabpanel');
function expectSelection(index) {
  assert.equal(tabs().filter(tab => tab.tabIndex === 0).length, 1);
  tabs().forEach((tab, i) => {
    assert.equal(tab.tabIndex, i === index ? 0 : -1);
    assert.equal(tab.getAttribute('aria-selected'), String(i === index));
    assert.equal(tab.getAttribute('aria-controls'), panel().id);
  });
  assert.equal(panel().getAttribute('aria-labelledby'), tabs()[index].id);
}
function key(index, value) {
  let prevented = false;
  tabs()[index].onkeydown({key: value, preventDefault() { prevented = true; }});
  return prevented;
}
expectSelection(0);
assert.equal(key(0, 'ArrowRight'), true); expectSelection(1); assert.equal(focused, tabs()[1]);
assert.equal(key(1, 'ArrowRight'), true); expectSelection(0); assert.equal(focused, tabs()[0]);
assert.equal(key(0, 'ArrowLeft'), true); expectSelection(1);
assert.equal(key(1, 'Home'), true); expectSelection(0);
assert.equal(key(0, 'End'), true); expectSelection(1);
assert.equal(key(1, 'Tab'), false); expectSelection(1);
assert.equal(changes, 0, 'Reading another panel must not dirty a theme');
tabs()[0].onclick(); expectSelection(0);
editor.setDesign({}, true);
assert.ok(tabs().every(tab => tab.disabled));
assert.equal(key(0, 'ArrowRight'), false); expectSelection(0);
tabs()[1].onclick(); expectSelection(0);
editor.setDesign({});
assert.ok(tabs().every(tab => !tab.disabled));
assert.equal(key(0, 'ArrowRight'), true); expectSelection(1);
editor.reset(); editor.setDesign({}); expectSelection(0);
console.log('PASS theme tabs: Arrow keys, Home/End, roving focus, panel associations, locking and reset');
