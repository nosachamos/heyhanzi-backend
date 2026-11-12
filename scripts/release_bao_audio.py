"""Verify and optionally publish a complete male-only Bao replacement.

Default is local preflight/package only. --upload writes immutable audio objects;
--activate additionally switches local metadata only after all remote bytes verify.
Female references and historical releases are preserved, with no application deploy.
"""
import argparse
import hashlib
import json
import re
import shutil
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from app.services.audio_qualification import release_is_qualified
from scripts.audio_pipeline.cadence import validate_cadence_profile, verify_recording_cadence
from scripts.audio_pipeline.owner_selection import (
    AUTHORIZATION,
    LIMIT,
    STATUS,
    VERSION,
    verify_owner_recording,
)
from scripts.audio_pipeline.profiles import DATA, catalogue, profile, validate_policy
from scripts.audio_pipeline.publish import verify_recording
from scripts.audio_pipeline.run import atomic_json, put_immutable
from scripts.audio_pipeline.synthesis import digest


def require(value, message):
    if not value:
        raise ValueError(message)


def same_reading(left, right):
    # Authored syllable/word separators do not change the spoken reading.
    return ''.join(left.split()).replace('-', '') == ''.join(right.split()).replace('-', '')


def prepare(spool, mapping_path, output):
    """Read and verify the complete source set before any upload or activation."""
    recipe, version = profile('male')
    validate_policy(recipe)
    validate_cadence_profile(recipe)
    require(recipe.get('voiceProfile') == 'bao-natural-adult-v1' and recipe.get('cadencePolicy'),
            'A pinned approved Bao profile is required')
    active = json.loads((DATA / 'active.json').read_text())['releaseId']
    base = DATA / 'releases' / active
    summary = json.loads((base / 'release.json').read_text())
    require(release_is_qualified(summary), 'Current release is incomplete or unauthorized')
    order, entries = catalogue()
    mapping = json.loads(mapping_path.read_text())
    require(isinstance(mapping, dict) and set(mapping) == set(order), 'Mapping must cover the entire current backend catalogue exactly')
    require(all(isinstance(value, str) and re.fullmatch('[a-f0-9]{64}', value) for value in mapping.values()),
            'Mapping values must be canonical recording IDs')
    source = spool / version if (spool / version).is_dir() else spool
    refs = {}
    for index in range(256):
        refs.update(json.loads((base / f'{index:02x}.json').read_text()))
    require(set(refs) == set(order), 'Current release does not match the current complete backend catalogue')
    missing = [key for key in set(mapping.values()) if not all((source / (key + suffix)).is_file() for suffix in ('.mp3', '.json'))]
    require(not missing, f'Incomplete Bao generation: {len(missing)} canonical recordings missing; no upload or activation performed')
    verified = {}
    owner_selections = []
    storage = json.loads((DATA / 'storage.json').read_text())
    changed = {}
    for number, audio_id in enumerate(order, 1):
        source_id = mapping[audio_id]
        entry = entries[audio_id]
        if source_id not in verified:
            record_path = source / (source_id + '.json')
            data_path = source / (source_id + '.mp3')
            record_data = record_path.read_bytes()
            record = json.loads(record_data)
            data = data_path.read_bytes()
            require(record['text'] == entry['text'] and same_reading(record['pinyin'], entry['pinyin']),
                    f'{audio_id}: canonical recording differs from the exact authored text or reading')
            authored = {'text': entry['text'], 'pinyin': record['pinyin']}
            verifier = verify_owner_recording if record.get('reviewStatus') == STATUS else verify_recording
            verifier(source_id, authored, recipe, version, record, data)
            verify_recording_cadence(record, data)
            compact = {key: record[key] for key in ('text', 'pinyin', 'sha256', 'byteLength', 'reviewStatus')}
            compact['quality'] = {'failures': record['quality']['failures']}
            verified[source_id] = {'record': compact, 'recordPath': record_path, 'dataPath': data_path,
                                   'receiptSha256': hashlib.sha256(record_data).hexdigest()}
        item = verified[source_id]
        record = item['record']
        require(record['text'] == entry['text'] and same_reading(record['pinyin'], entry['pinyin']),
                f'{audio_id}: shared canonical recording differs from authored text or reading')
        previous = refs[audio_id]
        female = previous['voices']['mandarin-female']
        require(female['voiceId'] == 'mandarin-female', 'Invalid female reference in previous release')
        key = f'sentences/{version}/{source_id}.mp3'
        male = {'format': 'audio-reference-v1', 'languageTag': 'cmn-Hans', 'id': 'sentence-' + audio_id,
                'voiceId': 'mandarin-male', 'version': record['sha256'], 'mimeType': 'audio/mpeg',
                'byteLength': record['byteLength'], 'url': f"https://{storage['CdnDomain']}/{key}"}
        changed[audio_id] = {**female, 'voices': {'mandarin-female': female, 'mandarin-male': male}}
        if record.get('reviewStatus') == STATUS:
            owner_selections.append({'contentId': audio_id, 'recordingId': source_id,
                                     'sha256': record['sha256'], 'receiptSha256': item['receiptSha256'],
                                     'failedChecks': record['quality']['failures']})
        if number % 500 == 0:
            print(json.dumps({'verifiedBackendEntries': number, 'total': len(order)}), flush=True)
    release_id = digest({'baseRelease': active, 'maleProfile': version,
                         'maleReferences': {key: changed[key]['voices']['mandarin-male'] for key in order}})
    approvals = [value for value in summary.get('humanApprovals', []) if value['voiceId'] == 'mandarin-female']
    owner_count = len(owner_selections)
    release = {'releaseId': release_id, 'baseReleaseId': active,
               'profiles': {**summary['profiles'], 'male': version}, 'contents': len(order), 'recordings': len(order)*2,
               'audioContentAliases': {'male': {key: value for key, value in mapping.items() if key != value}},
               'complete': True, 'qualified': not owner_count, 'automatedAccepted': not (owner_count or approvals),
               'automatedRecordings': len(order)*2 - owner_count - len(approvals),
               'humanApprovedRecordings': len(approvals), 'humanApprovals': approvals, 'humanReviewed': False,
               'ownerSelectedRecordings': owner_count, 'ownerSelections': owner_selections,
               'availableUnderOwnerAuthorization': bool(owner_count),
               'ownerSelectionPolicy': {'version': VERSION, 'authorization': AUTHORIZATION, 'failureLimit': LIMIT,
                                        'voiceId': 'mandarin-male', 'voiceVersion': version},
               'cadencePolicy': recipe['cadencePolicy']['version']}
    require(release_is_qualified(release), 'Prepared release fails the explicit availability policy')
    destination = output / release_id
    destination.mkdir(parents=True, exist_ok=True)
    for index in range(256):
        prefix = f'{index:02x}'
        atomic_json(destination / (prefix+'.json'), {key: value for key, value in changed.items() if key.startswith(prefix)})
    atomic_json(destination / 'release.json', release)
    return release, verified, storage, destination


def upload_and_verify(verified, storage, version, threads):
    import boto3
    from botocore.config import Config

    s3 = boto3.client('s3', region_name=storage['Region'], config=Config(
        max_pool_connections=threads, retries={'mode': 'adaptive', 'total_max_attempts': 8}))
    bucket = storage['Bucket']

    def upload(item):
        source_id, row = item
        for suffix, path, content_type in [('.mp3', row['dataPath'], 'audio/mpeg'),
                                          ('.json', row['recordPath'], 'application/json')]:
            data = path.read_bytes()
            key = f'sentences/{version}/{source_id}{suffix}'
            expected_sha = row['record']['sha256'] if suffix == '.mp3' else row['receiptSha256']
            require(hashlib.sha256(data).hexdigest() == expected_sha,
                    'Source changed after preflight; refuse upload: ' + key)
            put_immutable(s3, bucket, key, data, content_type)
            response = s3.get_object(Bucket=bucket, Key=key)
            try:
                remote = response['Body'].read(len(data)+1)
            finally:
                response['Body'].close()
            require(remote == data, 'Remote immutable object differs: ' + key)
        return source_id

    with ThreadPoolExecutor(max_workers=threads) as pool:
        for number, _ in enumerate(pool.map(upload, verified.items()), 1):
            if number % 500 == 0:
                print(json.dumps({'remoteVerifiedRecordings': number, 'total': len(verified)}), flush=True)


def activate(release, destination):
    require(json.loads((DATA/'active.json').read_text())['releaseId'] == release['baseReleaseId'],
            'Active release changed during verification; refuse to replace concurrent work')
    target = DATA/'releases'/release['releaseId']
    if target.exists():
        require(all((target/name).read_bytes() == (destination/name).read_bytes()
                    for name in ['release.json']+[f'{index:02x}.json' for index in range(256)]),
                'Existing release conflict; immutable historical release preserved')
    else:
        with tempfile.TemporaryDirectory(prefix='.bao-release-', dir=target.parent) as temporary:
            staged = Path(temporary) / release['releaseId']
            shutil.copytree(destination, staged)
            staged.rename(target)
    atomic_json(DATA/'active.json', {'releaseId': release['releaseId']})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--spool', type=Path, required=True)
    parser.add_argument('--mapping', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--upload', action='store_true')
    parser.add_argument('--activate', action='store_true')
    parser.add_argument('--threads', type=int, default=8)
    args = parser.parse_args()
    if args.activate and not args.upload:
        parser.error('--activate requires --upload and complete remote verification')
    if not 1 <= args.threads <= 16:
        parser.error('--threads must be between 1 and 16')
    release, verified, storage, destination = prepare(args.spool, args.mapping, args.output)
    if args.upload:
        upload_and_verify(verified, storage, release['profiles']['male'], args.threads)
    if args.activate:
        activate(release, destination)
    report = {key: value for key, value in release.items() if key not in ('ownerSelections', 'humanApprovals')}
    report.update(uploaded=args.upload, activatedLocally=args.activate, output=str(destination))
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
