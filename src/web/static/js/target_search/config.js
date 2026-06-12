const TargetSearchConfig = {
  API: {
    STATS: "/api/target-db/stats",
    HEALTH: "/api/target-db/health",
    VALIDATION: "/api/target-db/validation",
    PDE_OVERVIEW: "/api/target-db/pde-overview",
    SEARCH: "/api/target-db/search",
    TARGET: (id) => `/api/target-db/targets/${id}`,
    STRUCTURES: (id) => `/api/target-db/targets/${id}/structures`,
    PREFLIGHT: (id, format) => `/api/target-db/structures/${id}/preflight?format=${encodeURIComponent(format)}`,
    DOWNLOAD: (id, format) => `/api/target-db/structures/${id}/download?format=${encodeURIComponent(format)}`,
    SEND_TO_DOCKING: (id) => `/api/target-db/structures/${id}/send-to-docking`,
  },
};
