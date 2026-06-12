"""
BRICS Fragment Database Builder
步骤：
1. 从两个分子CSV文件读取分子
2. BRICS算法切割为片段
3. 片段清洗与去重
4. 按标签计算说明.xlsx的规则打标签
5. 输出带标签的片段库 fragments_labeled.csv
"""

import pandas as pd
import numpy as np
from rdkit import Chem
from rdkit.Chem import BRICS, Descriptors, rdMolDescriptors, AllChem
from rdkit.Chem.rdMolDescriptors import CalcTPSA, CalcNumRotatableBonds, CalcFractionCSP3
from rdkit.Chem.Lipinski import NumHDonors, NumHAcceptors
import re
from collections import Counter
import logging
import os
import sys
import time

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(os.path.join(os.path.dirname(__file__), 'build_fragment_db.log'), encoding='utf-8')
    ]
)
logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────
# Step 1: 读取分子
# ─────────────────────────────────────────────────────────────────
def load_molecules(csv_paths: list) -> list:
    """从多个CSV读取SMILES，合并去重"""
    all_smiles = []
    for path in csv_paths:
        logger.info(f"读取文件: {path}")
        df = pd.read_csv(path, usecols=['canonical_smiles'])
        smiles_list = df['canonical_smiles'].dropna().tolist()
        all_smiles.extend(smiles_list)
        logger.info(f"  → {len(smiles_list):,} 条")
    # 去重
    unique = list(dict.fromkeys(all_smiles))
    logger.info(f"合并去重后: {len(unique):,} 个唯一分子")
    return unique


# ─────────────────────────────────────────────────────────────────
# Step 2: BRICS 切割
# ─────────────────────────────────────────────────────────────────
def brics_decompose(smiles_list: list, batch_size: int = 10000) -> Counter:
    """
    对分子列表执行 BRICS 分解，返回片段SMILES及其出现频次
    片段含 [*] 虚原子作为连接点
    """
    fragment_counter = Counter()
    total = len(smiles_list)
    start = time.time()

    for i, smi in enumerate(smiles_list):
        if i > 0 and i % batch_size == 0:
            elapsed = time.time() - start
            eta = elapsed / i * (total - i)
            logger.info(f"  BRICS进度: {i:,}/{total:,} ({i/total*100:.1f}%)  ETA: {eta:.0f}s")

        mol = Chem.MolFromSmiles(smi)
        if mol is None:
            continue
        try:
            frags = BRICS.BRICSDecompose(mol)
            fragment_counter.update(frags)
        except Exception:
            continue

    logger.info(f"BRICS切割完成，原始片段类型数: {len(fragment_counter):,}")
    return fragment_counter


# ─────────────────────────────────────────────────────────────────
# Step 3: 片段清洗
# ─────────────────────────────────────────────────────────────────
def clean_fragments(fragment_counter: Counter,
                    min_freq: int = 2,
                    min_heavy_atoms: int = 3,
                    max_heavy_atoms: int = 30,
                    max_dummy_atoms: int = 4) -> pd.DataFrame:
    """
    清洗规则：
    - 频次 >= min_freq（过滤低频噪音）
    - 重原子数 [min_heavy_atoms, max_heavy_atoms]
    - 虚原子数 <= max_dummy_atoms
    - 片段必须能被 RDKit 成功解析
    - 去除纯虚原子片段
    """
    logger.info("开始片段清洗...")
    records = []

    for frag_smi, freq in fragment_counter.items():
        if freq < min_freq:
            continue

        # 统计虚原子数（[数字*]格式）
        dummy_count = frag_smi.count('[')  # 粗估，精确在下面
        
        mol = Chem.MolFromSmiles(frag_smi)
        if mol is None:
            continue

        # 精确统计虚原子
        dummy_atoms = sum(1 for a in mol.GetAtoms() if a.GetAtomicNum() == 0)
        if dummy_atoms > max_dummy_atoms:
            continue

        # 重原子数（排除虚原子）
        heavy_atoms = sum(1 for a in mol.GetAtoms() if a.GetAtomicNum() != 0)
        if heavy_atoms < min_heavy_atoms or heavy_atoms > max_heavy_atoms:
            continue

        # 规范化SMILES
        canonical = Chem.MolToSmiles(mol, canonical=True)
        if canonical is None:
            continue

        records.append({
            'fragment_smiles': canonical,
            'frequency': freq,
            'heavy_atoms': heavy_atoms,
            'dummy_atoms': dummy_atoms,
        })

    df = pd.DataFrame(records)
    # 按规范SMILES去重（保留频次最高的）
    df = df.sort_values('frequency', ascending=False).drop_duplicates('fragment_smiles').reset_index(drop=True)
    logger.info(f"清洗后片段数: {len(df):,}")
    return df


# ─────────────────────────────────────────────────────────────────
# Step 4: 计算片段的物化属性
# ─────────────────────────────────────────────────────────────────
def calc_properties(df: pd.DataFrame) -> pd.DataFrame:
    """计算每个片段的基础物化属性，用于后续打标签"""
    logger.info("计算片段物化属性...")

    logp_list, mw_list, tpsa_list = [], [], []
    hbd_list, hba_list, rotbonds_list, fsp3_list = [], [], [], []
    n_atoms_list, o_atoms_list, s_atoms_list = [], [], []
    halogen_list = []
    aromatic_ring_list, aliphatic_ring_list = [], []
    hetero_ring_list = []
    n_hetero_list, o_hetero_list, s_hetero_list = [], [], []

    for smi in df['fragment_smiles']:
        mol = Chem.MolFromSmiles(smi)
        if mol is None:
            logp_list.append(np.nan); mw_list.append(np.nan); tpsa_list.append(np.nan)
            hbd_list.append(np.nan); hba_list.append(np.nan); rotbonds_list.append(np.nan)
            fsp3_list.append(np.nan); n_atoms_list.append(0); o_atoms_list.append(0)
            s_atoms_list.append(0); halogen_list.append(0)
            aromatic_ring_list.append(0); aliphatic_ring_list.append(0)
            hetero_ring_list.append(0); n_hetero_list.append(0)
            o_hetero_list.append(0); s_hetero_list.append(0)
            continue

        try:
            logp = Descriptors.MolLogP(mol)
            mw = Descriptors.MolWt(mol)
            tpsa = CalcTPSA(mol)
            hbd = NumHDonors(mol)
            hba = NumHAcceptors(mol)
            rotbonds = CalcNumRotatableBonds(mol)
            fsp3 = CalcFractionCSP3(mol)

            # 原子计数
            n_atoms = sum(1 for a in mol.GetAtoms() if a.GetAtomicNum() == 7)
            o_atoms = sum(1 for a in mol.GetAtoms() if a.GetAtomicNum() == 8)
            s_atoms = sum(1 for a in mol.GetAtoms() if a.GetAtomicNum() == 16)
            halogens = sum(1 for a in mol.GetAtoms() if a.GetAtomicNum() in (9, 17, 35, 53))

            # 环统计
            ring_info = mol.GetRingInfo()
            ar_count = 0
            ali_count = 0
            hetero_total = 0
            n_hetero = 0
            o_hetero = 0
            s_hetero = 0
            for ring in ring_info.AtomRings():
                ring_atoms = [mol.GetAtomWithIdx(i) for i in ring]
                is_aromatic = all(a.GetIsAromatic() for a in ring_atoms)
                has_hetero = any(a.GetAtomicNum() not in (6, 0) for a in ring_atoms)
                if is_aromatic:
                    ar_count += 1
                else:
                    ali_count += 1
                if has_hetero:
                    hetero_total += 1
                    if any(a.GetAtomicNum() == 7 for a in ring_atoms):
                        n_hetero += 1
                    if any(a.GetAtomicNum() == 8 for a in ring_atoms):
                        o_hetero += 1
                    if any(a.GetAtomicNum() == 16 for a in ring_atoms):
                        s_hetero += 1

        except Exception:
            logp = mw = tpsa = hbd = hba = rotbonds = fsp3 = np.nan
            n_atoms = o_atoms = s_atoms = halogens = 0
            ar_count = ali_count = hetero_total = n_hetero = o_hetero = s_hetero = 0

        logp_list.append(logp); mw_list.append(mw); tpsa_list.append(tpsa)
        hbd_list.append(hbd); hba_list.append(hba); rotbonds_list.append(rotbonds)
        fsp3_list.append(fsp3); n_atoms_list.append(n_atoms); o_atoms_list.append(o_atoms)
        s_atoms_list.append(s_atoms); halogen_list.append(halogens)
        aromatic_ring_list.append(ar_count); aliphatic_ring_list.append(ali_count)
        hetero_ring_list.append(hetero_total); n_hetero_list.append(n_hetero)
        o_hetero_list.append(o_hetero); s_hetero_list.append(s_hetero)

    df = df.copy()
    df['logp'] = logp_list
    df['mw'] = mw_list
    df['tpsa'] = tpsa_list
    df['hbd'] = hbd_list
    df['hba'] = hba_list
    df['rotbonds'] = rotbonds_list
    df['fsp3'] = fsp3_list
    df['n_atoms'] = n_atoms_list
    df['o_atoms'] = o_atoms_list
    df['s_atoms'] = s_atoms_list
    df['halogen_count'] = halogen_list
    df['aromatic_ring_count'] = aromatic_ring_list
    df['aliphatic_ring_count'] = aliphatic_ring_list
    df['heterocycles_total'] = hetero_ring_list
    df['n_heterocycles_count'] = n_hetero_list
    df['o_heterocycles_count'] = o_hetero_list
    df['s_heterocycles_count'] = s_hetero_list

    logger.info("属性计算完成")
    return df


# ─────────────────────────────────────────────────────────────────
# Step 5: SMARTS 匹配辅助
# ─────────────────────────────────────────────────────────────────
def smarts_match(mol, smarts: str) -> bool:
    """检查mol是否匹配给定SMARTS，匹配失败返回False"""
    try:
        patt = Chem.MolFromSmarts(smarts)
        if patt is None:
            return False
        return mol.HasSubstructMatch(patt)
    except Exception:
        return False


# 酸性基团 SMARTS
ACID_SMARTS = [
    '[CX3](=O)[OX2H1]',          # 羧酸
    '[SX4](=O)(=O)[OX2H1]',      # 磺酸
    '[PX4](=O)([OX2H1])[OX2H1]', # 膦酸
]

# 碱性胺 SMARTS（严格排除酰胺/磺酰胺/芳香N/吡咯N）
BASE_SMARTS = [
    '[NX3H2;!$(NC=O);!$(Nc);!$(N[S](=O)=O);!$(N[a])]',   # 伯胺
    '[NX3H1;!$(NC=O);!$(Nc);!$(N[S](=O)=O);!$(N[a]);!$(N1cc[nH]1)]', # 仲胺
    '[NX3H0;!$(NC=O);!$(Nc);!$(N[S](=O)=O);!$(N[a])]',   # 叔胺
    '[nX2;$(n1cccc1);!$(n1cc[nH]c1)]',                     # 吡啶型N（芳香碱性N）
]

# 亲核基团
NUCLEOPHILE_SMARTS = [
    '[NX3H2;!$(NC=O)]',
    '[OX2H;!$(OC=O)]',
    '[SX2H]',
]

# 亲电基团
ELECTROPHILE_SMARTS = [
    '[CX3]=[CX3][CX3]=O',         # Michael受体
    '[C;R3]1[O;R3][C;R3]1',       # 环氧
    '[CX3](=O)[ClX1]',             # 酰氯
    'N=C=S',                       # 异硫氰酸酯
]

# 强吸电子基
EWG_SMARTS = [
    '[#6][CX3](=O)[#6]',           # 酮
    '[NX3](=O)[O-]',               # 硝基
    '[NX3+](=O)[O-]',              # 硝基（另一种写法）
    '[CX3](=O)[NX3]',              # 酰胺（吸电子）
    '[SX4](=O)(=O)',               # 磺酰
]

# 生物电子等排体
BIOISOSTERE_SMARTS = [
    '[n]1nnnn1',                   # 四唑
    '[NX3][CX3](=O)[NX3]',        # 脲
    '[OX2][CX3](=O)[NX3]',        # 氨基甲酸酯
    '[NX3][SX4](=O)(=O)',          # 磺酰胺
]

# 酰胺
AMIDE_SMARTS = '[NX3][CX3](=O)[#6]'

# 酯
ESTER_SMARTS = '[CX3](=O)O[#6]'


# ─────────────────────────────────────────────────────────────────
# Step 6: 打标签
# ─────────────────────────────────────────────────────────────────
def label_fragments(df: pd.DataFrame) -> pd.DataFrame:
    """
    按标签计算说明.xlsx的规则为每个片段打标签
    所有标签均为 0/1 整型布尔值
    """
    logger.info("开始打标签...")
    df = df.copy()

    # ── 物化性质类 ──────────────────────────────────────────────
    # 亲脂/亲水/两亲（互斥，优先级：亲脂 > 亲水 > 两亲）
    is_lipophilic = (df['logp'] >= 2.5) & (df['tpsa'] < 30)
    is_hydrophilic = (~is_lipophilic) & ((df['tpsa'] >= 60) | (df['logp'] <= 0.5))
    is_amphiphilic = (~is_lipophilic) & (~is_hydrophilic)

    df['label_lipophilic']   = is_lipophilic.astype(int)
    df['label_hydrophilic']  = is_hydrophilic.astype(int)
    df['label_amphiphilic']  = is_amphiphilic.astype(int)

    df['label_high_flexibility'] = (df['rotbonds'] >= 3).astype(int)
    df['label_high_mw']          = (df['mw'] >= 230).astype(int)
    df['label_high_sp3']         = (df['fsp3'] >= 0.60).astype(int)
    df['label_has_aromatic_ring']  = (df['aromatic_ring_count'] > 0).astype(int)
    df['label_has_aliphatic_ring'] = (df['aliphatic_ring_count'] > 0).astype(int)
    df['label_high_hbd']  = (df['hbd'] >= 2).astype(int)
    df['label_high_hba']  = (df['hba'] >= 3).astype(int)
    df['label_high_tpsa'] = (df['tpsa'] >= 50).astype(int)
    df['label_low_tpsa']  = (df['tpsa'] <= 25).astype(int)

    # ── 酸碱性（SMARTS匹配）────────────────────────────────────
    logger.info("  SMARTS匹配酸碱性...")
    is_acid_list, is_base_list = [], []
    is_nucl_list, is_elec_list, is_ewg_list = [], [], []
    is_bio_list, is_amide_list, is_ester_list = [], [], []

    for smi in df['fragment_smiles']:
        mol = Chem.MolFromSmiles(smi)
        if mol is None:
            is_acid_list.append(0); is_base_list.append(0)
            is_nucl_list.append(0); is_elec_list.append(0); is_ewg_list.append(0)
            is_bio_list.append(0); is_amide_list.append(0); is_ester_list.append(0)
            continue

        is_acid = any(smarts_match(mol, s) for s in ACID_SMARTS)
        is_base = any(smarts_match(mol, s) for s in BASE_SMARTS)
        is_acid_list.append(int(is_acid))
        is_base_list.append(int(is_base))

        is_nucl_list.append(int(any(smarts_match(mol, s) for s in NUCLEOPHILE_SMARTS)))
        is_elec_list.append(int(any(smarts_match(mol, s) for s in ELECTROPHILE_SMARTS)))
        is_ewg_list.append(int(any(smarts_match(mol, s) for s in EWG_SMARTS)))
        is_bio_list.append(int(any(smarts_match(mol, s) for s in BIOISOSTERE_SMARTS)))
        is_amide_list.append(int(smarts_match(mol, AMIDE_SMARTS)))
        is_ester_list.append(int(smarts_match(mol, ESTER_SMARTS)))

    df['label_acidic']   = is_acid_list
    df['label_basic']    = is_base_list
    df['label_zwitterionic'] = [int(a and b) for a, b in zip(is_acid_list, is_base_list)]
    df['label_neutral']  = [int(not a and not b) for a, b in zip(is_acid_list, is_base_list)]

    df['label_nucleophilic'] = is_nucl_list
    df['label_electrophilic'] = is_elec_list
    df['label_strong_ewg'] = is_ewg_list

    # ── 结构特征（基于物化属性列）──────────────────────────────
    df['label_aromatic_ring']     = (df['aromatic_ring_count'] >= 1).astype(int)
    df['label_multi_aromatic']    = (df['aromatic_ring_count'] >= 2).astype(int)
    df['label_has_heterocycle']   = (df['heterocycles_total'] > 0).astype(int)
    df['label_multi_heterocycle'] = (df['heterocycles_total'] >= 2).astype(int)
    df['label_n_heterocycle']     = (df['n_heterocycles_count'] > 0).astype(int)
    df['label_o_heterocycle']     = (df['o_heterocycles_count'] > 0).astype(int)
    df['label_s_heterocycle']     = (df['s_heterocycles_count'] > 0).astype(int)
    df['label_has_halogen']       = (df['halogen_count'] > 0).astype(int)
    df['label_high_halogen']      = (df['halogen_count'] >= 2).astype(int)
    df['label_nitrogen_rich']     = (df['n_atoms'] >= 3).astype(int)
    df['label_oxygen_rich']       = (df['o_atoms'] >= 3).astype(int)
    df['label_sulfur_rich']       = (df['s_atoms'] >= 2).astype(int)

    # ── 特殊/官能团类 ────────────────────────────────────────
    df['label_bioisostere'] = is_bio_list
    df['label_has_amide']   = is_amide_list
    df['label_has_ester']   = is_ester_list

    logger.info("标签计算完成")
    return df


# ─────────────────────────────────────────────────────────────────
# 主流程
# ─────────────────────────────────────────────────────────────────
def main():
    base_dir = os.path.dirname(__file__)

    csv_files = [
        os.path.join(base_dir, 'sa3_5_qed0.7_ro3_with_methyl.csv'),
        os.path.join(base_dir, 'sa3_qed0.7_ro3_with_methyl.csv'),
    ]
    output_path = os.path.join(base_dir, 'fragments_labeled.csv')

    # ── Step 1: 读取分子 ──────────────────────────────────────
    logger.info("=" * 60)
    logger.info("Step 1: 读取分子")
    smiles_list = load_molecules(csv_files)

    # ── Step 2: BRICS 切割 ────────────────────────────────────
    logger.info("=" * 60)
    logger.info("Step 2: BRICS切割")
    fragment_counter = brics_decompose(smiles_list, batch_size=5000)

    # ── Step 3: 清洗 ──────────────────────────────────────────
    logger.info("=" * 60)
    logger.info("Step 3: 片段清洗")
    df_clean = clean_fragments(
        fragment_counter,
        min_freq=2,
        min_heavy_atoms=3,
        max_heavy_atoms=30,
        max_dummy_atoms=4
    )
    logger.info(f"清洗后保留: {len(df_clean):,} 个片段")

    # ── Step 4: 计算物化属性 ──────────────────────────────────
    logger.info("=" * 60)
    logger.info("Step 4: 计算物化属性")
    df_props = calc_properties(df_clean)

    # ── Step 5: 打标签 ────────────────────────────────────────
    logger.info("=" * 60)
    logger.info("Step 5: 打标签")
    df_labeled = label_fragments(df_props)

    # ── 输出 ──────────────────────────────────────────────────
    logger.info("=" * 60)
    df_labeled.to_csv(output_path, index=False, encoding='utf-8-sig')
    logger.info(f"输出文件: {output_path}")
    logger.info(f"总片段数: {len(df_labeled):,}")

    # 打印标签分布概览
    label_cols = [c for c in df_labeled.columns if c.startswith('label_')]
    logger.info("\n标签分布概览:")
    for col in label_cols:
        cnt = df_labeled[col].sum()
        pct = cnt / len(df_labeled) * 100
        logger.info(f"  {col:<35} {cnt:>7,} ({pct:5.1f}%)")

    logger.info("=" * 60)
    logger.info("全部完成!")
    return df_labeled


if __name__ == '__main__':
    main()
