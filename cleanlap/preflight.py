"""Explicit live gates and conflict-safe local controls backup/restore."""
import configparser
import hashlib
import io
import json
import math
import os
from pathlib import Path


def digest(data):
    return hashlib.sha256(data).hexdigest()


class ControlsTransaction:
    def __init__(self, path, backup):
        self.path, self.backup = Path(path).resolve(), Path(backup).resolve()
        self.original = self.applied = None

    def apply(self):
        original = self.path.read_bytes()
        parser = configparser.ConfigParser(interpolation=None,strict=True)
        parser.optionxform = str
        parser.read_string(original.decode('utf-8-sig'))
        if not parser.has_section('HEADER'):
            raise ValueError('missing controls HEADER')
        parser['HEADER']['INPUT_METHOD'] = 'X360'
        if not parser.has_section('X360'):
            parser.add_section('X360')
        parser['X360']['COMBINE_WITH_KEYBOARD_CONTROL'] = '0'
        out = io.StringIO(); parser.write(out)
        applied = out.getvalue().encode('utf-8')
        self.backup.mkdir(parents=True,exist_ok=False)
        (self.backup/'original.ini').write_bytes(original)
        (self.backup/'applied.ini').write_bytes(applied)
        (self.backup/'transaction.json').write_text(json.dumps({'target':str(self.path),
            'original_sha256':digest(original),'applied_sha256':digest(applied)},indent=2),encoding='utf-8')
        if self.path.read_bytes() != original:
            raise ValueError('controls changed during backup; no write performed')
        self.path.write_bytes(applied)
        if self.path.read_bytes() != applied:
            raise OSError('controls readback failed')
        self.original,self.applied = original,applied

    def verify_applied(self):
        """Read-only verification of the pre-launch transaction."""
        metadata=json.loads((self.backup/'transaction.json').read_text(encoding='utf-8'))
        original=(self.backup/'original.ini').read_bytes()
        applied=(self.backup/'applied.ini').read_bytes()
        if digest(applied)!=metadata['applied_sha256']:
            raise ValueError('applied controls backup hash mismatch')
        if str(self.path)!=metadata['target'] or digest(original)!=metadata['original_sha256']:
            raise ValueError('controls backup identity/hash mismatch')
        if digest(self.path.read_bytes())!=metadata['applied_sha256']:
            raise ValueError('controls conflict: prepared X360 profile not present')
        return metadata

    def restore(self):
        record = json.loads((self.backup/'transaction.json').read_text(encoding='utf-8'))
        if record['target'] != str(self.path):
            raise ValueError('restore target mismatch')
        original = (self.backup/'original.ini').read_bytes()
        applied = (self.backup/'applied.ini').read_bytes()
        if digest(original) != record['original_sha256'] or digest(applied) != record['applied_sha256']:
            raise ValueError('backup hash mismatch')
        current = self.path.read_bytes()
        if current == original:
            return  # idempotent
        if current != applied:
            raise ValueError('controls edited externally: preserve current file; manual merge required')
        self.path.write_bytes(original)
        if self.path.read_bytes() != original:
            raise OSError('restore readback failed')


def authorize(calibration, world, plan, frame, *, arm=False, vm_released=False):
    """Attestations are operator evidence, not measurements made by this program."""
    reasons=[]
    if not arm: reasons.append('explicit_arm_required')
    if not vm_released: reasons.append('explicit_vm_release_required')
    if calibration.get('schema') != 'cleanlap-calibration-1': reasons.append('calibration_schema')
    for key in ('installed_assets_verified','boundaries_surveyed','cones_surveyed','slaloms_surveyed',
                'full_wheel_envelope_verified','reference_transform_verified','steering_verified',
                'manual_override_verified','stop_verified','csp_telemetry_verified'):
        if calibration.get(key) is not True: reasons.append(key)
    if calibration.get('geometry_sha256') != world.digest: reasons.append('geometry_identity')
    if calibration.get('plan_sha256') != digest(json.dumps(plan,sort_keys=True,allow_nan=False).encode()):
        reasons.append('plan_identity')
    for name in ('wheelbase','max_steer','half_width','half_length'):
        if calibration.get(name) != plan['vehicle'][name]: reasons.append(name+'_plan_mismatch')
    for key in ('stick_per_road_rad',):
        value=calibration.get(key)
        if type(value) not in (int,float) or not math.isfinite(value) or value <= 0:
            reasons.append(key)
    if calibration.get('steer_sign') not in (-1,1): reasons.append('steer_sign')
    if frame.get('track') != calibration.get('track') or frame.get('car') != calibration.get('car'):
        reasons.append('telemetry_identity')
    if not calibration.get('operator') or not calibration.get('evidence_paths'):
        reasons.append('measurement_provenance_required')
    evidence=calibration.get('evidence_paths')
    if not isinstance(evidence,list): reasons.append('measurement_evidence_unreadable')
    else:
        for item in evidence:
            try:
                path=Path(item['path'])
                if not path.is_file() or digest(path.read_bytes()) != item['sha256']:
                    reasons.append('measurement_evidence_unreadable')
            except (OSError,KeyError,TypeError,ValueError): reasons.append('measurement_evidence_unreadable')
    scale=calibration.get('stick_per_road_rad')
    if type(scale) in (float,int) and scale*plan['vehicle']['max_steer'] > 1.:
        reasons.append('steering_range_not_representable')
    return sorted(set(reasons))
