const assert = require("assert");
const fs = require("fs");

const template = fs.readFileSync("src/web/templates/index.html", "utf8");
const main = fs.readFileSync("src/web/static/js/home/main.js", "utf8");

assert(
  template.replace(/<!--[\s\S]*?-->/g, "").includes('<script src="/static/js/home/main.js?v=20261028-quick-actions-v1"></script>'),
  "the homepage script cache version must change with the protocol fix"
);
assert(!template.includes("智能工具"), "the smart-tool toggle must not render");
assert(!main.includes("toolsToggle"), "the removed toggle must not be queried or bound");
assert(!main.includes("function toggleTools"), "the removed toggle handler must not remain");
assert(main.includes("let toolsEnabled = true"), "agent tools must remain enabled by default");
assert(main.includes("enable_tools: toolsEnabled"), "chat requests must keep agent-tool capability");
assert(
  main.includes('"analyze-ethanol": "请分析乙醇(CCO)的分子性质"'),
  "the ethanol quick action must stay within the supported property workflow"
);
assert(
  !main.includes("请分析乙醇(CCO)的分子性质和ADMET特征"),
  "the ethanol quick action must not advertise an unsupported ADMET workflow"
);
assert(
  main.includes('"admet": "请对乙醇(CCO)进行 ADMET 预测，返回真实计算结果、证据来源和未计算项目"'),
  "the homepage ADMET quick action must send a supported Agent request"
);
assert(
  template.includes('<div class="item" data-action="admet">'),
  "the homepage ADMET card must be handled by the shared quick-action listener"
);
assert(
  !main.includes('if (action === "admet") {\n      return;\n    }'),
  "the homepage ADMET quick action must not be short-circuited"
);

console.log("Homepage smart-tool toggle removal checks passed");
