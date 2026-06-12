/* ═══════════════════════════════════════════════════════════
   分子设计模块 – 全局状态 & 常量
   ═══════════════════════════════════════════════════════════ */
"use strict";

/**
 * 全局状态对象，各子模块通过 DesignState 读写共享数据
 */
var DesignState = {
  smiles: "",
  selectedFrag: null,
  filters: new Set(),
  search: "",
  page: 1,
  pageSize: 20,
  history: [],
  candidates: [],
  iter: 0,
  prevProps: null,
  curProps: null,
  propsTimer: null,
  ketcherReady: false,
};

/**
 * 常用基团列表 (硬编码)
 */
var COMMON_FRAGS = [
  { s: "[*]C(F)(F)F", l: "-CF₃", tags: ["lipo"] },
  { s: "[*]OC", l: "-OMe", tags: ["hydro"] },
  { s: "[*]C(=O)O", l: "-COOH", tags: ["acid"] },
  { s: "[*]N", l: "-NH₂", tags: ["base"] },
  { s: "[*]Cl", l: "-Cl", tags: ["halo"] },
  { s: "[*]F", l: "-F", tags: ["halo"] },
  { s: "[*]c1ccccc1", l: "-Ph", tags: ["arom"] },
  { s: "[*]C#N", l: "-CN", tags: ["neutral"] },
];
