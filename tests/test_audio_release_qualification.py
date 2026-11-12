"""Release availability honors bounded owner exceptions without claiming strict QA."""
import copy
import json
from pathlib import Path

import pytest

from app.services.audio_qualification import release_is_qualified


def selected_release():
    return {'complete': True, 'qualified': False, 'automatedAccepted': False,
            'contents': 1, 'recordings': 2, 'automatedRecordings': 1,
            'humanApprovedRecordings': 0, 'humanApprovals': [],
            'profiles': {'female': 'f'*64, 'male': 'a'*64},
            'ownerSelectedRecordings': 1, 'availableUnderOwnerAuthorization': True,
            'ownerSelectionPolicy': {'version': 'owner-best-available-v1',
                                     'authorization': 'course-rollout-owner-2026-10-22',
                                     'failureLimit': 10, 'voiceId': 'mandarin-male', 'voiceVersion': 'a'*64},
            'ownerSelections': [{'contentId': 'b'*64, 'recordingId': 'c'*64,
                                 'sha256': 'd'*64, 'receiptSha256': 'e'*64,
                                 'failedChecks': ['speech-quality-below-policy']}]}


def test_original_active_release_remains_available():
    root=Path(__file__).resolve().parents[1]/'app/data/sentence-audio'
    release=json.loads((root/'active.json').read_text())['releaseId']
    assert release_is_qualified(json.loads((root/'releases'/release/'release.json').read_text()))


def test_owner_selected_release_available_with_failed_checks_and_honest_status():
    release=selected_release()
    assert release_is_qualified(release)
    assert release['qualified'] is False
    assert release['ownerSelections'][0]['failedChecks']


@pytest.mark.parametrize('field,value', [('complete',False),('qualified',True),
    ('automatedAccepted',True),('ownerSelectedRecordings',0),('automatedRecordings',2),
    ('availableUnderOwnerAuthorization',False),('contents',2)])
def test_invalid_or_mislabelled_owner_release_rejects(field,value):
    release=selected_release()
    release[field]=value
    assert not release_is_qualified(release)


@pytest.mark.parametrize('field,value', [('authorization','different'),('failureLimit',9),
    ('voiceId','mandarin-female'),('voiceVersion','f'*64),('version','unknown')])
def test_unknown_authorization_or_profile_cannot_enable_release(field,value):
    release=selected_release()
    release['ownerSelectionPolicy'][field]=value
    assert not release_is_qualified(release)


@pytest.mark.parametrize('field,value', [('receiptSha256','bad'),('sha256','bad'),
    ('recordingId','bad'),('contentId','bad'),('failedChecks',[]),('failedChecks',[''])])
def test_selection_requires_exact_media_receipt_binding_and_preserved_failures(field,value):
    release=selected_release()
    release['ownerSelections'][0][field]=value
    assert not release_is_qualified(release)


def test_duplicate_content_cannot_inflate_complete_coverage():
    release=selected_release()
    release['ownerSelections']*=2
    release.update(contents=2,recordings=4,ownerSelectedRecordings=2,automatedRecordings=2)
    assert not release_is_qualified(release)


def test_unknown_human_approval_cannot_be_smuggled_into_owner_release():
    release=selected_release()
    release['humanApprovals']=[{'approvalId':'fabricated'}]
    release['humanApprovedRecordings']=1
    release['automatedRecordings']=0
    assert not release_is_qualified(release)


def test_plain_automated_release_cannot_hide_owner_selection():
    release=copy.deepcopy(selected_release())
    release['automatedAccepted']=True
    assert not release_is_qualified(release)
