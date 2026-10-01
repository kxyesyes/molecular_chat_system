"use strict";
const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const source = fs.readFileSync(path.join(__dirname, "../src/web/static/js/home/advanced_options.js"), "utf8");
function load(saved) {
  const nodes = new Map();
  function node(id) {
    if (!nodes.has(id)) nodes.set(id, {style: {}, min: 1, max: 10, value: 5,
      listeners: {}, classList: {add() {}, remove() {}},
      addEventListener(event, fn) {this.listeners[event] = fn;}});
    return nodes.get(id);
  }
  let stored = saved ? JSON.stringify(saved) : null;
  const context = vm.createContext({window: {}, console, setTimeout,
    requestAnimationFrame: fn => fn(),
    HomeConfig: {advancedDefaults: {ragCount: 5, temperature: 0.7, moleculeCount: 5},
      storageKeys: {advancedOptions: "options"}},
    localStorage: {getItem: () => stored, setItem: (_, value) => {stored = value;}},
    document: {querySelector: node, getElementById: node, addEventListener() {}}});
  vm.runInContext(source, context);
  const options = context.window.HomeAdvancedOptions;
  options.init();
  return {options, node, saved: () => JSON.parse(stored),
    input(id, value) {const el = node(id); el.value = value; el.listeners.input({target: el});}};
}

function payload(options) {
  // Both homepage transports serialize getConfig().molCount in this way.
  return JSON.parse(JSON.stringify({message: "设计 10 个候选分子", mol_count: options.getConfig().molCount}));
}

const automatic = load();
assert(!("mol_count" in payload(automatic.options)), "untouched default must not override the prompt's ten molecules");
automatic.input("temperatureSlider", 0.3);
assert(!("mol_count" in payload(automatic.options)), "unrelated settings must not opt into a count override");
assert(!("mol_count" in payload(load(automatic.saved()).options)), "automatic mode survives reload");

const legacy = load({molCount: 5, ragCount: 3, temperature: 0.4});
assert(!("mol_count" in payload(legacy.options)), "legacy saved defaults have no evidence of explicit selection");
assert.strictEqual(legacy.options.getConfig().ragCount, 3);
assert.strictEqual(legacy.options.getConfig().temperature, 0.4);

automatic.input("molCountSlider", 8);
assert.strictEqual(payload(automatic.options).mol_count, 8, "explicit slider choice retains its API override");
assert.strictEqual(payload(load(automatic.saved()).options).mol_count, 8, "explicit choice survives reload");
automatic.node("resetAdvancedOptions").listeners.click();
assert(!("mol_count" in payload(automatic.options)), "reset restores automatic count interpretation");
assert(!("mol_count" in payload(load(automatic.saved()).options)), "reset persists automatic mode");

const main = fs.readFileSync(path.join(__dirname, "../src/web/static/js/home/main.js"), "utf8");
assert.match(main, /mol_count: config\.molCount/);
assert.match(main, /mol_count: advancedConfig\.molCount/);
console.log("Homepage generation count ownership checks passed");
