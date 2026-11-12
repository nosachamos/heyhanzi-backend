"""An offline derivative remains distinct from generated and owner-selected takes."""
import copy
import json
from pathlib import Path

import pytest

from app.services.audio_qualification import release_is_qualified

RECORD = json.loads((Path(__file__).parent / 'fixtures/processed-duan-release-record.json').read_text())
FEMALE = '47a4e2b453fd35ad71fdfe8bcd1739bb1b4abe41fa6ce3475ec883bf112586b1'


def processed_release():
    return {'complete': True, 'qualified': False, 'automatedAccepted': False,
            'contents': 1, 'recordings': 2, 'automatedRecordings': 1,
            'humanApprovedRecordings': 0, 'humanApprovals': [],
            'profiles': {'male': RECORD['voiceVersion'], 'female': FEMALE},
            'ownerSelectedRecordings': 0, 'ownerSelections': [],
            'processedRecordings': 1, 'processedSelections': [copy.deepcopy(RECORD)]}


def test_exact_processed_record_with_strict_companion_preserves_classification():
    release = processed_release()
    original = copy.deepcopy(release)
    assert release_is_qualified(release)
    assert release == original
    assert not release['qualified'] and not release['automatedAccepted']
    assert release['processedRecordings'] == 1 and release['automatedRecordings'] == 1


@pytest.mark.parametrize(('field', 'value'), [
    ('processedRecordings', 0), ('processedRecordings', True), ('processedRecordings', 2),
    ('automatedAccepted', True), ('qualified', True), ('complete', False),
    ('contents', True), ('contents', 2), ('automatedRecordings', 2), ('recordings', 3),
])
def test_misclassified_or_incomplete_processed_release_rejects(field, value):
    release = processed_release()
    release[field] = value
    assert not release_is_qualified(release)


@pytest.mark.parametrize('field', [
    'contentId', 'generationId', 'recordingId', 'voiceVersion', 'sha256',
    'provenanceSha256', 'parentId', 'parentReceiptSha256', 'parentMp3Sha256',
])
def test_changed_derivative_or_parent_identity_rejects(field):
    release = processed_release()
    release['processedSelections'][0][field] = '0' * 64
    assert not release_is_qualified(release)


@pytest.mark.parametrize(('field', 'value'), [
    ('voiceId', 'mandarin-female'), ('reviewStatus', 'automated-pass'),
    ('normalGeneratorQualified', True), ('normalGeneratorQualified', 0),
    ('failedChecks', []), ('derivativeEOS', True),
])
def test_generated_speech_claim_cannot_replace_processed_evidence(field, value):
    release = processed_release()
    release['processedSelections'][0][field] = value
    assert not release_is_qualified(release)


def test_missing_provenance_duplicate_or_changed_profile_rejects():
    release = processed_release()
    release['processedSelections'][0].pop('provenanceSha256')
    assert not release_is_qualified(release)
    release = processed_release()
    release['processedSelections'].append(copy.deepcopy(RECORD))
    release['processedRecordings'] = 2
    assert not release_is_qualified(release)
    release = processed_release()
    release['profiles']['male'] = 'f' * 64
    assert not release_is_qualified(release)


def test_owner_companion_must_have_a_distinct_voice_and_same_content():
    release = processed_release()
    release.update(ownerSelectedRecordings=1, automatedRecordings=0,
                   availableUnderOwnerAuthorization=True)
    release['ownerSelections'] = [{
        'contentId': RECORD['contentId'], 'recordingId': RECORD['contentId'],
        'sha256': '1' * 64, 'receiptSha256': '2' * 64, 'voiceId': 'mandarin-female',
        'voiceVersion': FEMALE, 'failedChecks': ['excessive-pause'],
    }]
    release['ownerSelectionPolicy'] = {
        'version': 'owner-best-available-v1', 'authorization': 'course-rollout-owner-2026-10-22',
        'failureLimit': 10, 'voices': {'mandarin-female': FEMALE,
                                     'mandarin-male': RECORD['voiceVersion']},
    }
    assert release_is_qualified(release)
    duplicate = copy.deepcopy(release)
    duplicate['ownerSelections'][0].update(voiceId='mandarin-male',
                                           voiceVersion=RECORD['voiceVersion'])
    assert not release_is_qualified(duplicate)
    release['ownerSelections'][0]['contentId'] = 'a' * 64
    assert not release_is_qualified(release)
