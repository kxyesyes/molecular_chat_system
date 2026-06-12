import json
import subprocess
import textwrap
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
NODE = "node"


class DockingPdbqtViewerTest(unittest.TestCase):
    def test_ligand_pdbqt_preview_is_normalized_before_3dmol_rendering(self):
        viewer_js = PROJECT_ROOT / "src/web/static/js/docking/viewer_manager.js"
        script = textwrap.dedent(
            f"""
            const fs = require("fs");
            const vm = require("vm");
            const code = fs.readFileSync({json.dumps(str(viewer_js))}, "utf8");
            const captured = [];
            const sandbox = {{
              console,
              setTimeout: (fn) => fn(),
              window: {{}},
              AppState: {{
                viewer: {{
                  clear() {{}},
                  addModel(data, format) {{
                    captured.push({{ data, format }});
                    return {{ setStyle() {{}} }};
                  }},
                  setStyle() {{}},
                  zoomTo() {{}},
                  render() {{}}
                }},
                currentProteinFormat: "pdb",
                currentStyle: "default",
                interactions: {{}}
              }}
            }};
            vm.runInNewContext(code + "\\nthis.__ViewerManager = ViewerManager;", sandbox);
            const pdbqt = [
              "ROOT",
              "ATOM      1  C1  LIG     1       0.000   1.402   0.000  0.00  0.00    +0.000 A",
              "ATOM      2  C2  LIG     1       1.214   0.701   0.000  0.00  0.00    +0.000 A",
              "ATOM      3  C3  LIG     1       1.214  -0.701   0.000  0.00  0.00    +0.000 A",
              "BRANCH   3   4",
              "ATOM      4  C4  LIG     1       2.428  -1.402   0.000  0.00  0.00    +0.000 C",
              "ATOM      5  O1  LIG     1       3.642  -0.701   0.000  0.00  0.00    -0.300 OA",
              "ENDBRANCH   3   4",
              "TORSDOF 1"
            ].join("\\n");
            sandbox.__ViewerManager.loadMolecule(pdbqt, "ligand");
            const model = captured[0];
            console.log(JSON.stringify({{
              format: model.format,
              atomCount: (model.data.match(/^(ATOM|HETATM)/gm) || []).length,
              hasPdbqtControlRecords: /ROOT|BRANCH|TORSDOF/.test(model.data),
              aromaticElement: model.data.split("\\n")[0].slice(76, 78)
            }}));
            """
        )

        result = subprocess.run(
            [NODE, "-e", script],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        payload = json.loads(result.stdout.strip().splitlines()[-1])

        self.assertEqual(payload["format"], "pdb")
        self.assertEqual(payload["atomCount"], 5)
        self.assertFalse(payload["hasPdbqtControlRecords"])
        self.assertEqual(payload["aromaticElement"].strip(), "C")


if __name__ == "__main__":
    unittest.main()
