#!/usr/bin/env python3
"""Offline audit of the RR99 rebrand against the original MFT02 (no game assets stored in the repo).

Inputs are extracted directories passed by the operator:
  --orig   original MFT02 (extracted RAR, contains content/cars/...)
  --full   RR99 standalone mod (extracted)
  --patch  RR99 rebrand patch (extracted)
  --client-zip / --server-zip  optional RiceTrials ZIPs: the patch must not overlap their file names.
Exit code 0 only if every gate passes. Writes a JSON report with --output.
"""
import argparse, hashlib, json, os, re, sys, zipfile

IDS = ('madformulateam_mft02', 'madformulateam_mft02_hy', 'madformulateam_mft02_evo')
CRITICAL = re.compile(r'\.(acd|kn5|knh|ksanim|bank|ini|lut|rto|ai)$|GUIDs\.txt$', re.I)
UI_ALLOWED = {'ui/ui_car.json', 'ui/badge.png'}


def sha(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for b in iter(lambda: f.read(1 << 20), b''):
            h.update(b)
    return h.hexdigest()


def load_json(path):
    with open(path, encoding='utf-8-sig') as f:
        return json.load(f)


def tree(root):
    out = {}
    for d, _, files in os.walk(root):
        for f in files:
            p = os.path.join(d, f)
            out[os.path.relpath(p, root).replace(os.sep, '/')] = sha(p)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--orig', required=True)
    ap.add_argument('--full', required=True)
    ap.add_argument('--patch', required=True)
    ap.add_argument('--client-zip')
    ap.add_argument('--server-zip')
    ap.add_argument('--output')
    a = ap.parse_args(argv)
    O, F, P = tree(a.orig), tree(a.full), tree(a.patch)
    gates = {}

    def gate(name, ok, detail=''):
        gates[name] = {'ok': bool(ok), 'detail': detail}

    # README_RR99.txt is the current name; LEEME_RR99.txt is accepted only for older patch ZIPs.
    PATCH_DOCS = ('Install-RR99.ps1', 'README_RR99.txt', 'LEEME_RR99.txt')
    car_files = lambda t: {k: v for k, v in t.items() if k.startswith('content/cars/')}
    O, Fc, Pc = car_files(O), car_files(F), car_files(P)
    gate('patch_files_identical_to_full', all(Fc.get(k) == v for k, v in Pc.items()),
         f'{len(Pc)} car files in patch')
    gate('patch_only_contains_cars_and_docs',
         all(k.startswith('content/cars/') or k in PATCH_DOCS for k in P),
         ', '.join(sorted(k for k in P if not k.startswith('content/cars/'))))
    changed = {k for k in Fc if k in O and O[k] != Fc[k]}
    added = set(Fc) - set(O)
    removed = set(O) - set(Fc)
    rel = lambda k: '/'.join(k.split('/')[3:])
    gate('changed_files_are_ui_only', all(rel(k) in UI_ALLOWED for k in changed), f'{len(changed)} changed')
    gate('added_files_are_rr99_skin_only', all(rel(k).startswith('skins/rr99/') for k in added), f'{len(added)} added')
    gate('removed_files_are_old_skins_only', all(rel(k).startswith('skins/') for k in removed), f'{len(removed)} removed')
    gate('no_physics_geometry_audio_changed',
         not [k for k in changed | removed | added if CRITICAL.search(k) and '/skins/' not in k],
         'acd/kn5/knh/anim/bank/ini identical')
    patch_changed = {k for k in Pc if k in O and O[k] != Pc[k]}
    gate('patch_reproduces_visual_changes', {k for k in changed | added} <= set(Pc),
         f'{len(changed | added)} visual files, all present in patch')
    id_ok = True
    for cid in IDS:
        id_ok &= os.path.isfile(os.path.join(a.patch, 'content/cars', cid, 'ui/ui_car.json'))
        id_ok &= not any(k.startswith(f'content/cars/{cid}/') and k.endswith(('.kn5', '.acd')) for k in P)
    dirs = {k.split('/')[2] for k in P if k.startswith('content/cars/')}
    gate('internal_ids_unchanged', id_ok and dirs == set(IDS), ', '.join(sorted(dirs)))
    uis = {}
    for cid in IDS:
        o, n = (load_json(os.path.join(root, 'content/cars', cid, 'ui/ui_car.json')) for root in (a.orig, a.patch))
        uis[cid] = {'keys_same': set(o) == set(n), 'version_same': o.get('version') == n.get('version'),
                    'parent_same': o.get('parent') == n.get('parent')}
    gate('ui_car_schema_version_parent_kept', all(all(v.values()) for v in uis.values()), json.dumps(uis))
    for label, z in (('client', a.client_zip), ('server', a.server_zip)):
        if z:
            names = {n for n in zipfile.ZipFile(z).namelist() if not n.endswith('/')}
            gate(f'patch_does_not_touch_{label}_zip', not (names & set(P)), f'{len(names)} entries')
    # Visual completeness: textures the original base skin shipped but rr99 does not
    base = {rel(k) for k in O if '/skins/2022_base/' in k and k.startswith(f'content/cars/{IDS[0]}/')}
    mine = {rel(k) for k in Pc if k.startswith(f'content/cars/{IDS[0]}/skins/rr99/')}
    notes = {'textures_in_2022_base_missing_from_rr99': sorted(x.split('/')[-1] for x in base - {
        'skins/2022_base/' + m.split('/')[-1] for m in mine})}
    report = {'ok': all(g['ok'] for g in gates.values()), 'gates': gates, 'notes': notes,
              'patch_sha256': {k: v for k, v in sorted(P.items())}}
    if a.output:
        with open(a.output, 'w') as f:
            json.dump(report, f, indent=1)
    for k, g in gates.items():
        print(('PASS ' if g['ok'] else 'FAIL ') + k + (' : ' + g['detail'] if g['detail'] else ''))
    print('notes:', json.dumps(notes))
    print('RESULT:', 'OK' if report['ok'] else 'FAILED')
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    sys.exit(main())
