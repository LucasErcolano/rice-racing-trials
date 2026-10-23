"""Read-only inspection of legally supplied host car ZIP/packed physics.

Uses the optional GPL acd.py library, never vendored and never writes physics.
Only scalar evidence/hashes are exported, not proprietary unpacked content.
"""
import argparse
import configparser
import hashlib
import json
import math
from pathlib import Path
import zipfile


def sha(data): return hashlib.sha256(data).hexdigest()


def summarize(data, car, acd_hash):
    def value(filename, section, key):
        raw=data.get(filename)
        result={'value':None,'source':f'{car}/data.acd::{filename}[{section}].{key}',
                'file_sha256':sha(raw) if raw is not None else None,'status':'UNKNOWN'}
        if raw is not None:
            parser=configparser.ConfigParser(interpolation=None,strict=False,inline_comment_prefixes=(';','//'))
            try:
                parser.read_string(raw.decode('utf-8-sig'))
                v=float(parser[section][key])
                if not math.isfinite(v): raise ValueError('nonfinite')
                result.update(value=v,status='SOURCE_VALUE_NOT_INSTALLED_CALIBRATION')
            except (ValueError,KeyError,UnicodeError,configparser.Error): pass
        return result
    report={'car':car,'data_acd_sha256':acd_hash,'wheelbase_m':value('suspensions.ini','BASIC','WHEELBASE'),
            'cg_location':value('suspensions.ini','BASIC','CG_LOCATION'),
            'front_track_m':value('suspensions.ini','FRONT','TRACK'),
            'rear_track_m':value('suspensions.ini','REAR','TRACK'),
            'steering_wheel_lock_deg':value('car.ini','CONTROLS','STEER_LOCK'),
            'steer_ratio':value('car.ini','CONTROLS','STEER_RATIO'),
            'steer_rod_ratio':value('car.ini','CONTROLS','LINEAR_STEER_ROD_RATIO'),
            'front_tyre_width_m':value('tyres.ini','FRONT','WIDTH'),
            'front_tyre_radius_m':value('tyres.ini','FRONT','RADIUS'),
            'rear_tyre_width_m':value('tyres.ini','REAR','WIDTH'),
            'rear_tyre_radius_m':value('tyres.ini','REAR','RADIUS'),
            'verified_road_wheel_limit_rad':None,'verified_full_wheel_envelope':None,
            'reference_transform_verified':False,'installed_identity_verified':False,
            'notes':['Steer lock/ratio alone does not verify Ackermann limits, suspension travel, toe, camber or X360 transfer.',
                     'Full envelope requires all wheel transforms at both locks and suspension extremes plus body-to-telemetry transform.']}
    return report


def inspect(archive, car='madformulateam_mft02'):
    import acd
    archive=Path(archive)
    with zipfile.ZipFile(archive) as z:
        name=f'content/cars/{car}/data.acd'
        raw=z.read(name)
        data=acd.read_bytes(raw,acd.new_crypt_key(f'{car}/data.acd'))
        report=summarize(data,car,sha(raw))
        report['collider_sha256']=sha(z.read(f'content/cars/{car}/collider.kn5'))
    with archive.open('rb') as file:
        report['source_archive_sha256']=hashlib.file_digest(file,'sha256').hexdigest()
    report['archive_path']=str(archive.resolve())
    report['unpacked_in_memory_only']=True
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('archive');p.add_argument('--car',default='madformulateam_mft02');p.add_argument('--output',required=True)
    args=p.parse_args(); result=inspect(args.archive,args.car)
    with Path(args.output).open('x',encoding='utf-8') as out:
        json.dump(result,out,indent=2,allow_nan=False)
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
