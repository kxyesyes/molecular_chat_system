"""Shared request and result schemas for molecular docking workflows."""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


GridTuple = Tuple[float, float, float]


@dataclass
class DockingRequest:
    """Normalized input for a docking job."""

    receptor_path: str
    ligand_path: str
    center: GridTuple = (0.0, 0.0, 0.0)
    size: GridTuple = (20.0, 20.0, 20.0)
    exhaustiveness: int = 8
    num_modes: int = 10
    energy_range: float = 3.0
    manual_center: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def center_x(self) -> float:
        return self.center[0]

    @property
    def center_y(self) -> float:
        return self.center[1]

    @property
    def center_z(self) -> float:
        return self.center[2]

    @property
    def size_x(self) -> float:
        return self.size[0]

    @property
    def size_y(self) -> float:
        return self.size[1]

    @property
    def size_z(self) -> float:
        return self.size[2]


@dataclass
class DockingJobResult:
    """High-level result returned by docking services and Agent tools."""

    job_id: str
    success: bool
    output_path: Optional[str] = None
    message: str = ""
    results: List[Dict[str, Any]] = field(default_factory=list)
    artifacts: Dict[str, str] = field(default_factory=dict)
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "job_id": self.job_id,
            "success": self.success,
            "output_path": self.output_path,
            "message": self.message,
            "results": self.results,
            "artifacts": self.artifacts,
            "error": self.error,
        }
