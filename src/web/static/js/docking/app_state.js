// ==================== 全局状态 ====================
const AppState = {
  viewer: null,
  currentProteinData: null,
  currentProteinFormat: "pdb",
  currentLigandData: null,
  originalLigandData: null,
  dockedLigandData: null,
  currentComplexData: null,
  currentViewerMode: null,
  currentJobId: null,
  stepManager: null,
  dockingBoxCenterTouched: false,
  dockingMode: "single",
  currentBatchResults: [],
  interactions: {
    hydrogenBonds: false,
    piPiInteractions: false,
    hydrophobicInteractions: false,
    allInteractions: false,
  },
  // 当前图片样式
  currentStyle: "default",
};

// ==================== 工具函数 ====================
const Utils = {
  // 计算两点间距离
  calculateDistance(p1, p2) {
    const dx = p1.x - p2.x;
    const dy = p1.y - p2.y;
    const dz = p1.z - p2.z;
    return Math.sqrt(dx * dx + dy * dy + dz * dz);
  },

  // 计算点集的中心
  calculateCenter(atoms) {
    const centerX = atoms.reduce((sum, a) => sum + a.x, 0) / atoms.length;
    const centerY = atoms.reduce((sum, a) => sum + a.y, 0) / atoms.length;
    const centerZ = atoms.reduce((sum, a) => sum + a.z, 0) / atoms.length;
    return { x: centerX, y: centerY, z: centerZ };
  },

  // 计算点集的包围盒
  calculateBoundingBox(atoms) {
    let minX = Infinity,
      minY = Infinity,
      minZ = Infinity;
    let maxX = -Infinity,
      maxY = -Infinity,
      maxZ = -Infinity;

    atoms.forEach((a) => {
      minX = Math.min(minX, a.x);
      minY = Math.min(minY, a.y);
      minZ = Math.min(minZ, a.z);
      maxX = Math.max(maxX, a.x);
      maxY = Math.max(maxY, a.y);
      maxZ = Math.max(maxZ, a.z);
    });

    return { minX, minY, minZ, maxX, maxY, maxZ };
  },

  // 检查原子是否属于蛋白质
  isProteinAtom(atom) {
    return atom.resn && CONFIG.PROTEIN_RESIDUES.includes(atom.resn);
  },

  // 检查残基是否为芳香残基
  isAromaticResidue(resName) {
    return CONFIG.AROMATIC_RESIDUES.includes(resName);
  },

  // 创建虚线圆柱体
  addDashedCylinder(viewer, start, end, options = {}) {
    const defaults = {
      radius: 0.1,
      color: "cyan",
      dashed: true,
      fromCap: 1,
      toCap: 1,
    };
    viewer.addCylinder({ start, end, ...defaults, ...options });
  },

  // 显示临时提示信息
  showToast(message, type = "info", duration = 3000) {
    const notification = document.createElement("div");
    notification.style.cssText = `
      position: fixed;
      top: 80px;
      right: 20px;
      padding: 12px 20px;
      border-radius: 8px;
      color: white;
      font-size: 14px;
      z-index: 10000;
      animation: slideInRight 0.3s ease-out;
      box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
    `;

    const colors = {
      info: "#3b82f6",
      success: "#10b981",
      error: "#ef4444",
      warning: "#f59e0b",
    };

    notification.style.background = colors[type] || colors.info;
    notification.textContent = message;
    document.body.appendChild(notification);

    setTimeout(() => {
      notification.style.animation = "slideOutRight 0.3s ease-out";
      setTimeout(() => notification.remove(), 300);
    }, duration);
  },
};
