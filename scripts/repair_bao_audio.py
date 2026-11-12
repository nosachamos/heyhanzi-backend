"""Finish bounded Bao retries without resetting failed evidence or relaxing cadence.

Use only after all producer jobs for the selected IDs have stopped. Each utterance
has at most24 total synthesis attempts: initialten, onefurtherten, thenfourstrict.
There are no uploads, profile edits, silence insertion or time stretching here.
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import soundfile as sf

from scripts.audio_pipeline.cadence import validate_cadence_profile, verify_recording_cadence
from scripts.audio_pipeline.owner_selection import STATUS, verify_owner_recording
from scripts.audio_pipeline.pace import verify_pace
from scripts.audio_pipeline.profiles import profile, validate_policy
from scripts.audio_pipeline.publish import verify_recording
from scripts.audio_pipeline.run import atomic_json

MAX_ATTEMPTS = 24


def snapshot(path, history):
    if not path.exists():
        return
    raw = path.read_bytes()
    target = history / (path.name + '.' + hashlib.sha256(raw).hexdigest())
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and target.read_bytes() != raw:
        raise ValueError('Repair history collision')
    target.write_bytes(raw)


def status(key, entry, root, recipe, version):
    mp3, metadata, rejected = [root / (key + suffix) for suffix in ('.mp3', '.json', '.rejected.json')]
    if mp3.exists() and metadata.exists():
        try:
            record = json.loads(metadata.read_text())
            data = mp3.read_bytes()
            verifier = verify_owner_recording if record.get('reviewStatus') == STATUS else verify_recording
            verifier(key, entry, recipe, version, record, data)
            verify_recording_cadence(record, data)
            if entry.get('pace'):
                samples, rate = sf.read(mp3, dtype='float32')
                if verify_pace(samples, rate, entry['text'], entry['pinyin'], record['quality'].get('pace')):
                    raise ValueError('Cached audio lacks valid explicit regular-pace evidence')
            return {'state': 'complete', 'attempts': len(record.get('attempts', [])),
                    'reviewStatus': record['reviewStatus'], 'sha256': record['sha256']}
        except (ValueError, KeyError, TypeError) as error:
            return {'state': 'invalid-cached-recording', 'reason': str(error)}
    if mp3.exists() or metadata.exists():
        return {'state': 'partial-recording', 'reason': 'Preserved incomplete cache pair; inspect before retrying'}
    saved = json.loads(rejected.read_text()) if rejected.exists() else {'id': key, **entry, 'attempts': []}
    if (saved.get('id') != key or saved.get('text') != entry['text']
            or saved.get('pinyin') != entry['pinyin'] or not isinstance(saved.get('attempts'), list)
            or any(item.get('accepted') is not False for item in saved['attempts'])):
        return {'state': 'invalid-retry-history', 'reason': 'Failed history does not match authored entry'}
    count = len(saved['attempts'])
    return {'state': 'exhausted' if count >= MAX_ATTEMPTS else 'pending', 'attempts': count,
            'lastFailures': saved['attempts'][-1].get('failures', []) if count else []}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, action='append', required=True)
    parser.add_argument('--ids', type=Path, help='Optional JSON list of selected failed/pending IDs')
    parser.add_argument('--spool', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    for name in ('source', 'weights', 'reference', 'asr', 'phonetic'):
        parser.add_argument('--'+name, type=Path, required=True)
    parser.add_argument('--generator', type=Path, default=Path(__file__).resolve().parents[2]
                        / 'heyhanzi-ui-web/scripts/generate-workshop-audio.py')
    args = parser.parse_args()
    entries = {}
    for path in args.manifest:
        for key, value in json.loads(path.read_text()).items():
            if key in entries and entries[key] != value:
                raise ValueError('Conflicting manifests for ' + key)
            entries[key] = value
    if args.ids:
        selected = json.loads(args.ids.read_text())
        if not isinstance(selected, list) or not set(selected) <= entries.keys():
            raise ValueError('Unknown repair IDs')
        entries = {key: entries[key] for key in selected}
    recipe, version = profile('male')
    validate_policy(recipe)
    validate_cadence_profile(recipe)
    if recipe.get('voiceProfile') != 'bao-natural-adult-v1':
        raise ValueError('Repair requires the approved Bao profile')
    root = args.spool / version
    root.mkdir(parents=True, exist_ok=True)
    args.output.mkdir(parents=True, exist_ok=True)
    initial_profile_bytes = (Path(__file__).resolve().parents[1] / 'app/data/sentence-audio/voices/male.json').read_bytes()
    history = args.output / 'history'
    jobs = []
    for start, end in ((0, 10), (10, 20), (20, 24)):
        current = {key: status(key, entry, root, recipe, version) for key, entry in entries.items()}
        batch = {key: entries[key] for key, value in current.items()
                 if value['state'] == 'pending' and start <= value['attempts'] < end}
        if not batch:
            continue
        if profile('male') != (recipe, version):
            raise ValueError('Voice profile changed during repair; no further attempts launched')
        for key, entry in batch.items():
            rejected = root / (key + '.rejected.json')
            snapshot(rejected, history)
            saved = json.loads(rejected.read_text()) if rejected.exists() else {'id': key, **entry, 'attempts': []}
            if end <= 20:
                saved['ownerRetentionPassStart'] = start
            atomic_json(rejected, saved)
        manifest = args.output / f'attempts-{start+1}-{end}.json'
        atomic_json(manifest, batch)
        command = [sys.executable, str(args.generator), '--voice', 'male', '--spool', str(args.spool),
                   '--manifest', str(manifest)]
        for name in ('source', 'weights', 'reference', 'asr', 'phonetic'):
            command += ['--'+name, str(getattr(args, name))]
        if end <= 20:
            command += ['--owner-best-available-after', '10']
        environment = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]))
        log = args.output / f'attempts-{start+1}-{end}.log'
        with log.open('w') as stream:
            completed = subprocess.run(command, env=environment, stdout=stream, stderr=subprocess.STDOUT)
        jobs.append({'fromAttempt': start+1, 'throughAttempt': end, 'entries': len(batch),
                     'exitCode': completed.returncode, 'log': str(log)})
        atomic_json(args.output / 'jobs.json', jobs)
    final = {key: status(key, entry, root, recipe, version) for key, entry in entries.items()}
    complete = all(value['state'] == 'complete' for value in final.values())
    final_profile_bytes = (Path(__file__).resolve().parents[1] / 'app/data/sentence-audio/voices/male.json').read_bytes()
    if initial_profile_bytes != final_profile_bytes:
        raise ValueError('Voice profile changed during bounded repair')
    report = {'profile': recipe['voiceProfile'], 'voiceVersion': version, 'maximumAttemptsPerUtterance': MAX_ATTEMPTS,
              'complete': complete, 'availableRecordings': sum(value['state'] == 'complete' for value in final.values()),
              'ownerSelectedRecordings': sum(value.get('reviewStatus') == STATUS for value in final.values()),
              'entries': final, 'jobs': jobs, 'profileChanged': False, 'uploadedOrActivated': False}
    atomic_json(args.output / 'report.json', report)
    print(json.dumps({key: value for key, value in report.items() if key not in ('entries', 'jobs')}))
    return 0 if complete else 1


if __name__ == '__main__':
    raise SystemExit(main())
