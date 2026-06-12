"""
对接报告生成模块
"""
import os
import datetime
import logging
from fastapi import Response

logger = logging.getLogger(__name__)

PDBQT_TO_ELEMENT = {
    "A": "C",
    "C": "C",
    "N": "N",
    "NA": "N",
    "OA": "O",
    "O": "O",
    "S": "S",
    "SA": "S",
    "P": "P",
    "F": "F",
    "Cl": "CL",
    "CL": "CL",
    "Br": "BR",
    "BR": "BR",
    "I": "I",
    "HD": "H",
    "H": "H",
    "Mg": "MG",
    "MG": "MG",
    "Zn": "ZN",
    "ZN": "ZN",
    "Fe": "FE",
    "FE": "FE",
    "Ca": "CA",
    "CA": "CA",
    "Mn": "MN",
    "MN": "MN",
    "Cu": "CU",
    "CU": "CU",
}


def generate_report(job_id: str, results: list, config_lines: list, fmt: str, 
                   viewer_png_b64: str, smiles_images_b64: list, job_dir: str):
    """生成对接报告"""
    
    # 构造 Markdown 内容
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    md = []
    md.append(f"# 分子对接报告\n")
    md.append(f"- 任务ID: `{job_id}`\n")
    md.append(f"- 生成时间: {now}\n")
    md.append(f"\n## 对接参数\n")
    
    if config_lines:
        for line in config_lines:
            md.append(f"- {line}")
    else:
        md.append("- 无可用配置")

    md.append("\n## 结果概览\n")
    if results:
        md.append("| Pose | 结合能 (kcal/mol) | RMSD 下限 | RMSD 上限 |\n")
        md.append("| ---- | -----------------:| --------:| --------:|\n")
        for i, r in enumerate(results, 1):
            md.append(f"| {i} | {r.binding_energy:.2f} | {r.rmsd_lb:.2f} | {r.rmsd_ub:.2f} |")
    else:
        md.append("未解析到有效结果。")

    # 嵌入图片
    if viewer_png_b64:
        md.append("\n## 3D 视图截图\n")
        md.append("![viewer](data:image/png;base64," + viewer_png_b64 + ")\n")
    
    if smiles_images_b64:
        md.append("\n## 配体2D图\n")
        for idx, item in enumerate(smiles_images_b64, 1):
            if isinstance(item, str) and item.strip():
                md.append(f"\n配体 {idx}:\n\n")
                md.append("![](data:image/png;base64," + item + ")\n")

    # 根据格式返回
    if fmt == "html":
        return _generate_html_report(job_id, now, config_lines, results, viewer_png_b64, smiles_images_b64)
    elif fmt == "zip":
        return _generate_zip_report(job_id, md, viewer_png_b64, smiles_images_b64, job_dir)
    else:
        # 默认返回 Markdown
        content = "\n".join(md)
        return Response(
            content=content,
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f"attachment; filename=docking_report_{job_id}.md"}
        )


def _pdbqt_line_to_pdb(line: str) -> str:
    padded = line.rstrip("\n").ljust(80)
    ad_type = padded[77:].strip().split()[0] if padded[77:].strip() else ""
    element = PDBQT_TO_ELEMENT.get(ad_type)

    if not element:
        atom_name = padded[12:16].strip()
        if len(atom_name) >= 2 and atom_name[:2].upper() in {"CL", "BR", "ZN", "FE", "MG", "MN", "CU", "CA"}:
            element = atom_name[:2].upper()
        elif atom_name:
            element = atom_name[0].upper()
        else:
            element = "C"

    return padded[:76] + element.rjust(2) + "  "


def _extract_pose_pdb_from_pdbqt_text(pdbqt_text: str, pose_index: int = 1) -> str:
    lines = pdbqt_text.splitlines()
    current_pose = 0
    collecting = False
    atoms = []

    for line in lines:
        if line.startswith("MODEL"):
            current_pose += 1
            collecting = current_pose == pose_index
            continue
        if line.startswith("ENDMDL"):
            if collecting:
                break
            collecting = False
            continue
        if collecting and line.startswith(("ATOM", "HETATM")):
            atoms.append(_pdbqt_line_to_pdb(line))

    if not atoms:
        atoms = [_pdbqt_line_to_pdb(line) for line in lines if line.startswith(("ATOM", "HETATM"))]

    if not atoms:
        return ""
    return "\n".join([*atoms, "END"]) + "\n"


def _read_pose_pdb_from_pdbqt_file(file_path: str, pose_index: int = 1) -> str:
    if not os.path.exists(file_path):
        return ""
    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            return _extract_pose_pdb_from_pdbqt_text(f.read(), pose_index=pose_index)
    except Exception as e:
        logger.warning(f"从 PDBQT 生成 PDB 失败 ({file_path}): {e}")
        return ""


def _generate_html_report(job_id: str, now: str, config_lines: list, results: list,
                         viewer_png_b64: str, smiles_images_b64: list):
    """生成 HTML 格式报告"""
    
    def _result_rows_html():
        if not results:
            return "<tr><td colspan='4'>未解析到有效结果</td></tr>"
        rows = []
        for i, r in enumerate(results, 1):
            rows.append(
                f"<tr><td>Pose {i}</td>"
                f"<td style='text-align:right'>{r.binding_energy:.2f}</td>"
                f"<td style='text-align:right'>{r.rmsd_lb:.2f}</td>"
                f"<td style='text-align:right'>{r.rmsd_ub:.2f}</td></tr>"
            )
        return "\n".join(rows)

    html_parts = []
    html_parts.append("<!DOCTYPE html>")
    html_parts.append("<html><head><meta charset='utf-8'><title>分子对接报告</title>\n"
                      "<style>body{font-family:Arial,Helvetica,'微软雅黑';padding:20px;color:#1f2937}"
                      "table{border-collapse:collapse;width:100%}th,td{border:1px solid #e5e7eb;padding:8px}"
                      "th{background:#f9fafb;text-align:left}.tag{display:inline-block;background:#eef2ff;"
                      "color:#3730a3;padding:2px 8px;border-radius:999px;font-size:12px;margin-left:8px}"
                      "</style></head><body>")
    html_parts.append(f"<h1>分子对接报告 <span class='tag'>ID: {job_id}</span></h1>")
    html_parts.append(f"<p>生成时间：{now}</p>")
    html_parts.append("<h2>对接参数</h2>")
    
    if config_lines:
        html_parts.append("<ul>" + "".join([f"<li>{line}</li>" for line in config_lines]) + "</ul>")
    else:
        html_parts.append("<p>无可用配置</p>")
    
    html_parts.append("<h2>结果概览</h2>")
    html_parts.append("<table><thead><tr><th>Pose</th><th>结合能 (kcal/mol)</th>"
                     "<th>RMSD 下限</th><th>RMSD 上限</th></tr></thead><tbody>")
    html_parts.append(_result_rows_html())
    html_parts.append("</tbody></table>")
    
    if viewer_png_b64:
        html_parts.append("<h2>3D 视图截图</h2>")
        html_parts.append(f"<img alt='viewer' style='max-width:100%;border:1px solid #e5e7eb' "
                         f"src='data:image/png;base64,{viewer_png_b64}' />")
    
    if smiles_images_b64:
        html_parts.append("<h2>配体2D图</h2>")
        for idx, item in enumerate(smiles_images_b64, 1):
            if isinstance(item, str) and item.strip():
                html_parts.append(f"<div style='margin:10px 0'><div>配体 {idx}</div>"
                                f"<img alt='ligand_{idx}' style='max-width:480px;border:1px solid #e5e7eb' "
                                f"src='data:image/png;base64,{item}' /></div>")
    
    html_parts.append("</body></html>")
    html_content = "\n".join(html_parts)
    
    return Response(
        content=html_content,
        media_type="text/html; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename=docking_report_{job_id}.html"}
    )


def _generate_zip_report(job_id: str, md: list, viewer_png_b64: str, 
                        smiles_images_b64: list, job_dir: str):
    """生成 ZIP 格式报告"""
    import io
    import zipfile
    
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # 写入 markdown 报告
        md_content = "\n".join(md)
        zf.writestr(f"report/docking_report_{job_id}.md", md_content)
        
        # 附加原始结果/配置
        file_mapping = {
            "result.pdbqt": f"poses/docked_pose_all_{job_id}.pdbqt",
            "receptor.pdbqt": "protein/receptor.pdbqt",
            "ligand.pdbqt": "ligand/ligand.pdbqt",
            "config.txt": "meta/config.txt",
        }
        for name, arcname in file_mapping.items():
            fp = os.path.join(job_dir, name)
            if os.path.exists(fp):
                try:
                    zf.write(fp, arcname=arcname)
                except Exception:
                    pass

        # 为 PyMOL 等作图工具补充标准 PDB 文件
        receptor_pdb = _read_pose_pdb_from_pdbqt_file(os.path.join(job_dir, "receptor.pdbqt"))
        if receptor_pdb:
            zf.writestr("protein/receptor.pdb", receptor_pdb)

        ligand_pdb = _read_pose_pdb_from_pdbqt_file(os.path.join(job_dir, "ligand.pdbqt"))
        if ligand_pdb:
            zf.writestr("ligand/ligand.pdb", ligand_pdb)

        best_pose_pdb = _read_pose_pdb_from_pdbqt_file(os.path.join(job_dir, "result.pdbqt"), pose_index=1)
        if best_pose_pdb:
            zf.writestr("poses/docked_pose_1.pdb", best_pose_pdb)

        manifest_lines = [
            f"job_id={job_id}",
            "report=docking markdown summary",
            "protein/receptor.pdbqt=prepared receptor for docking",
            "protein/receptor.pdb=standard PDB converted from receptor.pdbqt",
            "ligand/ligand.pdbqt=prepared ligand for docking",
            "ligand/ligand.pdb=standard PDB converted from ligand.pdbqt",
            "poses/docked_pose_all_<job_id>.pdbqt=all docked poses from Vina",
            "poses/docked_pose_1.pdb=best pose converted to standard PDB",
            "meta/config.txt=Vina configuration used for this job",
        ]
        zf.writestr("meta/manifest.txt", "\n".join(manifest_lines) + "\n")

    buf.seek(0)
    return Response(
        content=buf.read(),
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename=docking_report_{job_id}.zip"}
    )
