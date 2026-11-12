"""Both voices retain independent, honest owner-selection release identities."""
import copy
import json
import unittest
from pathlib import Path

from app.services import audio_qualification as staged

BACKEND = Path(__file__).resolve().parents[1]


def selected_release():
    profiles = {'female': 'f' * 64, 'male': 'a' * 64}
    selections = [{'contentId': 'b' * 64, 'recordingId': 'c' * 64, 'sha256': 'd' * 64,
        'receiptSha256': 'e' * 64, 'voiceId': 'mandarin-' + voice, 'voiceVersion': version,
        'failedChecks': ['speech-quality-below-policy']} for voice, version in profiles.items()]
    return {'complete': True, 'qualified': False, 'automatedAccepted': False,
        'contents': 1, 'recordings': 2, 'automatedRecordings': 0,
        'humanApprovedRecordings': 0, 'humanApprovals': [], 'profiles': profiles,
        'ownerSelectedRecordings': 2, 'availableUnderOwnerAuthorization': True,
        'ownerSelectionPolicy': {'version': 'owner-best-available-v1',
            'authorization': 'course-rollout-owner-2026-10-22', 'failureLimit': 10,
            'voices': {'mandarin-' + voice: version for voice, version in profiles.items()}},
        'ownerSelections': selections}


class TwoVoiceReleaseTests(unittest.TestCase):
    def test_same_content_has_independent_voice_authorizations(self):
        release = selected_release()
        self.assertTrue(staged.release_is_qualified(release))
        self.assertFalse(release['qualified'])

    def test_one_owner_voice_and_one_strict_voice(self):
        for retained in (0, 1):
            with self.subTest(retained=retained):
                release = selected_release()
                release['ownerSelections'] = [release['ownerSelections'][retained]]
                release.update(ownerSelectedRecordings=1, automatedRecordings=1)
                self.assertTrue(staged.release_is_qualified(release))

    def test_historical_male_schema_remains_accepted(self):
        release = selected_release()
        release.update(ownerSelectedRecordings=1, automatedRecordings=1)
        release['ownerSelections'] = [{k: v for k, v in release['ownerSelections'][1].items()
                                      if k not in ('voiceId', 'voiceVersion')}]
        release['ownerSelectionPolicy'] = {'version': 'owner-best-available-v1',
            'authorization': 'course-rollout-owner-2026-10-22', 'failureLimit': 10,
            'voiceId': 'mandarin-male', 'voiceVersion': release['profiles']['male']}
        self.assertTrue(staged.release_is_qualified(release))
        release['ownerSelections'][0]['voiceId'] = 'mandarin-female'
        self.assertFalse(staged.release_is_qualified(release))

    def test_all_existing_archived_release_results_are_preserved(self):
        files = list((BACKEND / 'app/data/sentence-audio/releases').glob('*/release.json'))
        self.assertTrue(files)
        for path in files:
            with self.subTest(release=path.parent.name):
                release = json.loads(path.read_text())
                self.assertTrue(staged.release_is_qualified(release))

    def test_manual_approval_and_owner_selection_are_distinct_voice_records(self):
        approval = copy.deepcopy(json.loads((BACKEND / 'app/data/sentence-audio/manual-approvals.json').read_text())[0])
        self.assertEqual(approval['voiceId'], 'mandarin-female')
        release = selected_release()
        release['profiles']['female'] = approval['voiceVersion']
        release['ownerSelectionPolicy']['voices']['mandarin-female'] = approval['voiceVersion']
        release.update(humanApprovals=[approval], humanApprovedRecordings=1, ownerSelectedRecordings=1)
        release['ownerSelections'] = [release['ownerSelections'][1]]
        release['ownerSelections'][0]['contentId'] = approval['contentId']
        self.assertTrue(staged.release_is_qualified(release))
        duplicate = copy.deepcopy(release)
        duplicate['ownerSelections'][0].update(voiceId='mandarin-female', voiceVersion=approval['voiceVersion'])
        self.assertFalse(staged.release_is_qualified(duplicate))
        wrong_profile = copy.deepcopy(release)
        wrong_profile['profiles'] = {'female': 'f' * 64, 'male': approval['voiceVersion']}
        wrong_profile['ownerSelectionPolicy']['voices'] = {
            'mandarin-female': 'f' * 64, 'mandarin-male': approval['voiceVersion']}
        wrong_profile['ownerSelections'][0]['voiceVersion'] = approval['voiceVersion']
        self.assertFalse(staged.release_is_qualified(wrong_profile))
        different_content = copy.deepcopy(release)
        different_content['ownerSelections'][0]['contentId'] = 'a' * 64
        self.assertFalse(staged.release_is_qualified(different_content))

    def test_invalid_counts_profiles_policy_or_voice_identity_reject(self):
        mutations = [
            lambda r: r.update(complete=False), lambda r: r.update(qualified=True),
            lambda r: r.update(automatedAccepted=True), lambda r: r.update(ownerSelectedRecordings=0),
            lambda r: r.update(automatedRecordings=1), lambda r: r.update(contents=2),
            lambda r: r.update(contents=True), lambda r: r.update(recordings=3),
            lambda r: r.update(availableUnderOwnerAuthorization=False),
            lambda r: r.update(profiles=None), lambda r: r['profiles'].update(female='invalid'),
            lambda r: r['ownerSelectionPolicy'].update(failureLimit=9),
            lambda r: r['ownerSelectionPolicy'].update(authorization='unknown'),
            lambda r: r['ownerSelectionPolicy']['voices'].update({'mandarin-female': 'a' * 64}),
            lambda r: r['ownerSelections'][0].update(voiceId='unknown'),
            lambda r: r['ownerSelections'][0].update(voiceId=['mandarin-female']),
            lambda r: r['ownerSelections'][0].pop('voiceId'),
            lambda r: r['ownerSelections'][0].update(voiceVersion='a' * 64),
            lambda r: r['ownerSelections'][0].update(receiptSha256='invalid'),
            lambda r: r['ownerSelections'][0].update(failedChecks=[]),
            lambda r: r['ownerSelections'][0].update(failedChecks=['']),
            lambda r: r['ownerSelections'].__setitem__(1, copy.deepcopy(r['ownerSelections'][0])),
            lambda r: r['ownerSelections'][1].update(contentId='a' * 64),
            lambda r: r['humanApprovals'].append({'approvalId': 'invented'}),
        ]
        for number, mutate in enumerate(mutations):
            with self.subTest(case=number):
                release = selected_release()
                mutate(release)
                self.assertFalse(staged.release_is_qualified(release))


if __name__ == '__main__':
    unittest.main()
