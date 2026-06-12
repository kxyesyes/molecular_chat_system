// ==================== 配置常量 ====================
const CONFIG = {
  // 氢键距离范围 (Å)
  HBOND_DISTANCE_MIN: 2.4,
  HBOND_DISTANCE_MAX: 3.6,

  // π-π相互作用距离范围 (Å)
  PIPI_DISTANCE_MIN: 3.3,
  PIPI_DISTANCE_MAX: 6.0,

  // 疏水作用距离范围 (Å)
  HYDROPHOBIC_DISTANCE_MIN: 3.0,
  HYDROPHOBIC_DISTANCE_MAX: 5.0,

  // 芳香环碳原子距离阈值 (Å)
  AROMATIC_C_DISTANCE: 1.6,

  // 蛋白质标准残基
  PROTEIN_RESIDUES: [
    "ALA",
    "ARG",
    "ASN",
    "ASP",
    "CYS",
    "GLN",
    "GLU",
    "GLY",
    "HIS",
    "ILE",
    "LEU",
    "LYS",
    "MET",
    "PHE",
    "PRO",
    "SER",
    "THR",
    "TRP",
    "TYR",
    "VAL",
  ],

  // 芳香残基
  AROMATIC_RESIDUES: ["PHE", "TYR", "TRP", "HIS"],

  // 水分子残基名
  WATER_RESIDUES: ["HOH", "WAT", "SOL", "H2O", "DOD", "TIP3"],

  // 常见非目标小分子/辅因子/离子（默认从配体识别中排除）
  NON_LIGAND_RESIDUES: [
    "NA",
    "K",
    "MG",
    "CA",
    "ZN",
    "MN",
    "FE",
    "CU",
    "CO",
    "NI",
    "SR",
    "CD",
    "PB",
    "AL",
    "CL",
    "BR",
    "I",
    "F",
    "SO4",
    "PO4",
    "PEG",
    "GOL",
    "EOH",
    "MPD",
    "TRS",
    "MES",
    "HEM",
    "NAG",
    "BMA",
    "MAN",
    "GAL",
    "GLC",
    "FUC",
    "ACE",
  ],

  // 配体识别最小重原子数
  LIGAND_MIN_HEAVY_ATOMS: 5,

  // 用户指定的配体残基名（可手动设置）
  USER_SPECIFIED_LIGAND_RESIDUES: [],

  // 缓存的配体信息（从用户输入获取）
  CACHED_LIGAND_INFO: null,

  // 查看器颜色方案
  VIEWER_COLORS: [
    "greenCarbon",
    "blueCarbon",
    "magentaCarbon",
    "yellowCarbon",
    "cyanCarbon",
    "orangeCarbon",
  ],

  // 对接步骤总数
  TOTAL_DOCKING_STEPS: 6,
};

// ==================== 对接步骤定义 ====================
const DOCKING_STEPS = [
  {
    id: "prepare_protein",
    title: "准备蛋白质结构",
    description: "PDB -> PDBQT",
    icon: "🧬",
    details: "ADFRsuite prepare_receptor",
  },
  {
    id: "prepare_ligand",
    title: "准备配体分子",
    description: "生成3D并写出PDBQT",
    icon: "💊",
    details: "RDKit + Meeko",
  },
  {
    id: "setup_docking",
    title: "配置对接参数",
    description: "生成Vina配置",
    icon: "⚙️",
    details: "config.txt",
  },
  {
    id: "run_docking",
    title: "执行分子对接",
    description: "运行Vina",
    icon: "🔄",
    details: "vina.exe --config",
  },
  {
    id: "parse_results",
    title: "解析对接结果",
    description: "提取能量与RMSD",
    icon: "📊",
    details: "解析PDBQT",
  },
  {
    id: "visualize",
    title: "生成可视化",
    description: "加载复合物",
    icon: "👁️",
    details: "3Dmol 显示",
  },
];
