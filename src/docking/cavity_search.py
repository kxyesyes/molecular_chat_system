"""Geometry-only cavity candidates from six-direction occlusion on a 1.5 Å grid."""

import math

import numpy as np
from scipy import ndimage
from scipy.spatial import cKDTree


def detect_cavities(content: bytes) -> dict:
    coords = []
    for line in content.splitlines():
        if line.startswith(b"ENDMDL"):
            break
        if not line.startswith(b"ATOM  ") or line[16:17] not in (b" ", b"A"):
            continue
        if line[12:16].strip().lstrip(b"0123456789").startswith((b"H", b"D")):
            continue
        try:
            xyz = [float(line[i:i + 8]) for i in (30, 38, 46)]
        except ValueError as exc:
            raise ValueError("受体含无效的 PDB 原子坐标") from exc
        if not all(math.isfinite(v) and abs(v) < 10000 for v in xyz):
            raise ValueError("受体原子坐标超出 PDB 范围")
        coords.append(xyz)
    if not coords:
        raise ValueError("未找到蛋白质重原子坐标")
    atoms = np.asarray(coords)
    spacing = 1.5
    origin = atoms.min(axis=0) - 3
    shape = np.ceil((atoms.max(axis=0) + 3 - origin) / spacing).astype(int) + 1
    if math.prod(map(int, shape)) > 2_000_000:
        raise ValueError("受体范围过大，请选择目标链后重试")
    # Query one slab at a time to keep memory bounded for large receptors.
    tree = cKDTree(atoms)
    yz = np.indices(tuple(shape[1:])).reshape(2, -1).T * spacing + origin[1:]
    points = np.empty((len(yz), 3))
    points[:, 1:] = yz
    distances = np.empty(tuple(shape), dtype=np.float32)
    for i in range(shape[0]):
        points[:, 0] = origin[0] + i * spacing
        distances[i] = tree.query(points, distance_upper_bound=9)[0].reshape(tuple(shape[1:]))
    solid = distances <= 2.4
    occlusion = np.zeros_like(solid, dtype=np.uint8)
    for axis in range(3):
        # Count directions blocked by the protein within six grid steps.
        for origin_offset in (-3, 3):
            occlusion += ndimage.maximum_filter1d(solid, 7, axis=axis, origin=origin_offset,
                                                 mode="constant", cval=0)
    labels, count = ndimage.label((distances >= 3.0) & (occlusion >= 5))
    sizes = np.bincount(labels.ravel(), minlength=count + 1)[1:]
    cavities = []
    for index in np.argsort(-sizes, kind="stable"):
        volume = float(sizes[index]) * spacing ** 3
        if volume < 55 or len(cavities) == 5:
            break
        voxels = np.argwhere(labels == index + 1)
        lo, hi = voxels.min(axis=0), voxels.max(axis=0)
        extents = (hi - lo + 1) * spacing
        if max(extents) + 8 > 50:
            continue
        cavities.append({
            "center": (origin + (lo + hi) * spacing / 2).round(2).tolist(),
            "size": np.maximum(10, extents + 8).tolist(),
            "volume": round(volume, 1),
        })
    return {"method": "grid-occlusion-6-v1", "spacing_angstrom": spacing, "cavities": cavities}
