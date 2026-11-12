"""Pace-only availability preserves every other automated safety check."""
import copy

import pytest
from pydantic import ValidationError

from app.schemas.learning import AudioReference
from app.services.audio_catalog_closure import (
    PACE_REVIEW_POLICY,
    POLICY,
    PROFILES,
    closure_release_available,
    fast_pace_failures_only,
    summary_digest,
)


def fast_quality():
    return {
        'accepted': False,
        'expectedText': '明天他要回国了。',
        'failures': ['regular-pace-too-fast'],
        'pace': {
            'failures': ['regular-pace-too-fast'],
            'activeSecondsPerSyllable': 0.175,
            'syllableCount': 7,
            'policy': {'activeSecondsPerSyllable': [0.18, 0.65]},
        },
    }


def test_fast_only_exception_retains_original_failure_and_qa_status():
    quality = fast_quality()
    before = copy.deepcopy(quality)
    assert fast_pace_failures_only(quality)
    assert quality == before
    assert not quality['accepted']


@pytest.mark.parametrize('failure', [
    'transcript-mismatch', 'ending-noise-unverified', 'detached-sentence-ending-noise',
    'punctuation-timing-unverified', 'word-timing-unverified', 'excessive-pause',
    'speech-quality-below-policy', 'no-speech', 'incomplete-generation',
    'regular-pace-too-slow',
])
def test_another_failure_cannot_be_reclassified_as_just_fast(failure):
    quality = fast_quality()
    quality['failures'].append(failure)
    assert not fast_pace_failures_only(quality)


def test_broad_pace_failure_needs_measured_fast_speech_under_pinned_recipe():
    quality = fast_quality()
    quality['failures'].append('pace-outside-policy')
    recipe = {'qualityPolicy': {'secondsPerCharacter': [0.13, 0.65]}}
    quality['secondsPerCharacter'] = 0.12
    assert fast_pace_failures_only(quality, recipe)
    assert not fast_pace_failures_only(quality)
    quality['secondsPerCharacter'] = 0.7
    assert not fast_pace_failures_only(quality, recipe)


@pytest.mark.parametrize('measured', [None, False, float('nan'), float('inf'), 0, 0.3])
def test_missing_or_contradictory_pace_measurement_fails_closed(measured):
    quality = fast_quality()
    quality['pace']['activeSecondsPerSyllable'] = measured
    assert not fast_pace_failures_only(quality)


def test_extra_pace_category_requires_exact_authorized_release_binding():
    release = dict(releaseId='a' * 64, reviewPolicy=POLICY, profiles=PROFILES,
        complete=True, qualified=False, automatedAccepted=False, humanReviewed=False,
        availableUnderOwnerAuthorization=True, warningCounts={'red': 1, 'yellow': 1, 'too-fast': 2},
        recordings=10, unresolvedRecordings=0, paceReviewPolicy=PACE_REVIEW_POLICY)
    approval = dict(releaseId=release['releaseId'], authorization=POLICY,
                    summarySha256=summary_digest(release))
    assert closure_release_available(release, approval)
    release['paceReviewPolicy'] = 'invented'
    approval['summarySha256'] = summary_digest(release)
    assert not closure_release_available(release, approval)


def test_audio_reference_exposes_distinct_pace_warning_and_authorization():
    fields = dict(languageTag='cmn-Hans', id='sentence-example', version='a' * 64,
        mimeType='audio/mpeg', byteLength=1000, qualityWarning='too-fast',
        qualitySelectionPolicy=PACE_REVIEW_POLICY)
    ref = AudioReference(**fields)
    assert ref.model_dump()['qualityWarning'] == 'too-fast'
    with pytest.raises(ValidationError):
        AudioReference(**{**fields, 'qualitySelectionPolicy': POLICY})
    with pytest.raises(ValidationError):
        AudioReference(**{**fields, 'qualityWarning': 'red'})
