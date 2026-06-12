// ═══════════════════════════════════════════════════════════
//  反向寻靶模块 – 全局配置与常量
// ═══════════════════════════════════════════════════════════

const RT_CONFIG = {
  // 默认启用 3D 药效团匹配模式
  IS_PHARM_3D_MODE: true,

  // API 端点
  API: {
    STATS:              "/api/reverse_target/stats",
    PREDICT_2D:         "/api/reverse_target/predict",
    PREDICT_3D:         "/api/reverse_target/predict_3d",
    BATCH_PREDICT:      "/api/reverse_target/batch_predict",
    SIMILAR_MOLECULES:  "/api/reverse_target/similar_molecules",
    PHARMACOPHORE:      "/api/reverse_target/pharmacophore",
    HEALTH:             "/api/reverse_target/health",
    SMILES_TO_IMAGE:    "/api/utils/smiles_to_image",
    MCS:                "/api/utils/mcs",
  },

  // 默认参数
  DEFAULTS: {
    THRESHOLD:    0.5,
    TOP_K:        100,
    MAX_REFINE:   50,
    ALPHA_3D:     0.6,
  },
};

// ── 药效团特征元数据 (颜色/图标/标签) ──
const FEAT_META = {
  Donor:            { color: "#3b82f6", icon: "💧", label: "HBD 氢键给体" },
  Acceptor:         { color: "#f59e0b", icon: "🔶", label: "HBA 氢键受体" },
  Hydrophobe:       { color: "#10b981", icon: "🟢", label: "疏水中心" },
  LumpedHydrophobe: { color: "#10b981", icon: "🟢", label: "疏水中心 (大)" },
  Aromatic:         { color: "#8b5cf6", icon: "⬡",  label: "芳香环" },
  NegIonizable:     { color: "#ef4444", icon: "➖", label: "负电荷中心" },
  PosIonizable:     { color: "#06b6d4", icon: "➕", label: "正电荷中心" },
};
