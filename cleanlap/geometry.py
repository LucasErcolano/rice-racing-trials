"""Non-actuating geometry admission. AC metres, XZ, heading atan2(dz, dx)."""
from dataclasses import dataclass
import hashlib
import json
import math
import numpy as np
from shapely.geometry import Polygon
from track.penalties import slalom_runs


@dataclass
class World:
    document: dict
    road: object
    reference: np.ndarray
    cones: list
    slaloms: list
    gates: list
    unresolved: list
    digest: str

    @property
    def ready(self):
        return not self.unresolved


def ingest(document, ring_indices=None, checkpoints=()):
    """No survey assertion in an input file can authorize live actuation."""
    raw = json.dumps(document, allow_nan=False, sort_keys=True).encode()
    if document.get('schema') != 'track-geometry-v1':
        raise ValueError('unsupported geometry schema')
    ai = np.asarray(document['ai_path'], dtype=float)
    if ai.ndim != 2 or ai.shape[1] != 2 or len(ai) < 4 or not np.isfinite(ai).all():
        raise ValueError('invalid AI path')
    if np.linalg.norm(ai[0]-ai[-1]) < 0.01:
        ai = ai[:-1]
    lines = document['limits']['polylines']
    if ring_indices is None:
        if len(lines) != 2:
            raise ValueError('ambiguous road: select explicit outer,inner boundary indices')
        ring_indices = sorted(range(2), key=lambda i: Polygon(lines[i]).area, reverse=True)
    outer, inner = [lines[i] for i in ring_indices]
    for line in (outer, inner):
        if len(line) < 4 or math.dist(line[0], line[-1]) > 0.15:
            raise ValueError('open boundary gap exceeds 0.15 m')
        if not Polygon(line).is_valid:
            raise ValueError('invalid boundary polygon')
    if not Polygon(outer).contains(Polygon(inner)):
        raise ValueError('road boundaries not nested')
    road = Polygon(outer, [inner])
    cones = document['cones']['clusters']  # Keep EVERY candidate, including AI-line contacts.
    if any(not all(math.isfinite(float(c[k])) for k in ('x','z','footprint_m')) or c['footprint_m'] <= 0 for c in cones):
        raise ValueError('invalid cone candidate')
    inferred = slalom_runs(document, outer, inner) if cones else []
    unresolved = ['vehicle_calibration_unverified', 'boundary_interpretation_unverified',
                  'cone_identity_unverified', 'slalom_unresolved', 'spawn_connector_unverified',
                  'installed_asset_identity_unverified']
    gates = [document.get('timing_gate'), *checkpoints]
    if any(not g or g.get('direction') not in (-1,1) for g in gates):
        raise ValueError('missing directed timing gate')
    raw=json.dumps({'document':document,'ring_indices':ring_indices,'checkpoints':list(checkpoints)},
                   sort_keys=True,allow_nan=False).encode()
    return World(document, road, ai, cones, inferred, gates, unresolved,
                 hashlib.sha256(raw).hexdigest())
