"""Exact owner-approved catalog availability; this never grants automatic QA."""
import hashlib
import json
import math
import re
from functools import lru_cache
from types import MappingProxyType

POLICY = 'owner-authorized-known-quality-exception-2026-11-01'
PACE_REVIEW_POLICY = 'owner-authorized-fast-pace-review-2026-11-01'
PROFILES = {
    'female': '47a4e2b453fd35ad71fdfe8bcd1739bb1b4abe41fa6ce3475ec883bf112586b1',
    'male': 'd74a42ec22281980e681667b3869f5bb87a8d01dea370f1c910d832f8f44a08f',
}

def summary_digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':')).encode()).hexdigest()


def closure_release_available(release, approval):
    """An exact shipped approval binds the whole summary, profiles and warnings."""
    if not isinstance(release, dict) or not isinstance(approval, dict):
        return False
    if (release.get('reviewPolicy') != POLICY or approval.get('authorization') != POLICY
            or release.get('releaseId') != approval.get('releaseId')
            or summary_digest(release) != approval.get('summarySha256')
            or release.get('profiles') != PROFILES
            or release.get('complete') is not True or release.get('qualified') is not False
            or release.get('automatedAccepted') is not False or release.get('humanReviewed') is not False
            or release.get('availableUnderOwnerAuthorization') is not True):
        return False
    counts = release.get('warningCounts', {})
    if (set(counts) not in ({'red', 'yellow'}, {'red', 'yellow', 'too-fast'})
            or any(type(v) is not int or v < 0 for v in counts.values())
            or type(release.get('recordings')) is not int or release['recordings'] < 1
            or sum(counts.values()) > release['recordings']
            or release.get('unresolvedRecordings') != 0
            or (counts.get('too-fast', 0) > 0 and release.get('paceReviewPolicy') != PACE_REVIEW_POLICY)):
        return False
    return True


def fast_pace_failures_only(quality, recipe=None):
    """Recognize the owner-authorized pace-only exception without changing QA.

    The broader clean-v5 pace flag is allowed only when its saved measurement
    independently proves speech faster than that recipe's floor. A generic pace
    flag, a slow take, missing measurements, or any unrelated failure cannot use
    this exception. Original failures and exact recording provenance stay intact.
    """
    if not isinstance(quality, dict):
        return False
    failures = quality.get('failures')
    if (not isinstance(failures, list) or not failures
            or any(not isinstance(value, str) for value in failures)):
        return False
    failed = set(failures)
    if ('regular-pace-too-fast' not in failed
            or not failed <= {'regular-pace-too-fast', 'pace-outside-policy'}):
        return False
    pace = quality.get('pace', {})
    if not isinstance(pace, dict):
        return False
    pace_failures = pace.get('failures')
    if (not isinstance(pace_failures, list)
            or any(not isinstance(value, str) for value in pace_failures)
            or set(pace_failures) != {'regular-pace-too-fast'}):
        return False
    measured = pace.get('activeSecondsPerSyllable')
    policy = pace.get('policy', {})
    key = 'singleSyllableActiveSeconds' if pace.get('syllableCount') == 1 else 'activeSecondsPerSyllable'
    try:
        floor = policy[key][0]
        if (isinstance(measured, bool) or not math.isfinite(measured)
                or not 0 < measured < floor):
            return False
        if 'pace-outside-policy' in failed:
            if not isinstance(recipe, dict):
                return False
            count = len(re.findall(r'[\u3400-\u9fff]', quality.get('expectedText', '')))
            if not count:
                return False
            bounds = recipe['qualityPolicy']['singleCharacterSeconds' if count == 1 else 'secondsPerCharacter']
            clean_pace = quality.get('secondsPerCharacter')
            if (isinstance(clean_pace, bool) or not math.isfinite(clean_pace)
                    or not 0 < clean_pace < bounds[0]):
                return False
    except (KeyError, TypeError, ValueError, IndexError, OverflowError):
        return False
    return True


def catalog_bindings(plan, release):
    """Read the separately hashed public binding index once per file revision."""
    from pathlib import Path
    path = Path(plan) / 'exercises/catalog-bindings' / (release['releaseId'] + '.json')
    stat = path.stat()
    return _catalog_bindings(str(path), stat.st_mtime_ns, stat.st_size, release['courseBindingsSha256'])




def _immutable(value):
    if isinstance(value, dict):
        return MappingProxyType({key: _immutable(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_immutable(item) for item in value)
    return value


@lru_cache(maxsize=4)
def _catalog_bindings(path, mtime, size, expected):
    from pathlib import Path
    document = json.loads(Path(path).read_text())
    if summary_digest(document) != expected:
        raise ValueError('Catalog binding index differs from approved release')
    return _immutable(document)
