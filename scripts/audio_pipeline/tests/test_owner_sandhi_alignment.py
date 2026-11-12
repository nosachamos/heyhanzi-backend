"""A measured third-tone spelling equivalence never invents spoken units."""
import copy
import unittest

from scripts.audio_pipeline.owner_sandhi_alignment import YI_POLICY, owner_sandhi_syllables
from scripts.audio_pipeline.phonetic import PHONETIC_POLICY
from scripts.audio_pipeline.timing import TIMING_POLICY


class OwnerSandhiAlignmentTests(unittest.TestCase):
    def setUp(self):
        self.text, self.pinyin, self.duration = "游好一点儿", "yóu hǎo yī diǎnr", 2.
        self.primary = {"policy": TIMING_POLICY, "durationSeconds": 2., "transcript": "友好一点",
                        "tokens": [{"tokenId": i + 1, "text": char, "start": .1 + i * .4,
                                    "end": .2 + i * .4, "probability": .8 if i == 0 else .99}
                                   for i, char in enumerate("友好一点")]}
        self.phonetic = {"policy": PHONETIC_POLICY, "durationSeconds": 2., "tokens": [
            {"text": value, "start": start, "end": end, "probability": .999}
            for value, start, end in [("you", .1, .15), ("2", .15, .2),
                                      ("hao", .5, .55), ("3", .55, .6),
                                      ("yi", .9, .95), ("4", .95, 1.),
                                      ("dianr", 1.3, 1.35), ("3", 1.35, 1.4)]]}

    def align(self):
        return owner_sandhi_syllables(self.text, self.pinyin, self.primary, self.duration, self.phonetic)

    def test_confirmed_surface_uses_original_events_and_preserves_observations(self):
        original = copy.deepcopy(self.primary)
        rows, ends, supplement = self.align()
        self.assertEqual(self.primary, original)
        self.assertEqual(ends, [1, 2, 3, 5])
        self.assertEqual([(r["start"], r["end"]) for r in rows],
                         [(r["start"], r["end"]) for r in self.primary["tokens"]])
        self.assertTrue(rows[0]["sandhiSpellingEquivalent"])
        self.assertFalse(rows[0]["reliable"])
        self.assertTrue(rows[-1]["orthographicRhoticOmitted"])
        self.assertEqual(supplement["equivalences"][0]["authoredReading"], "you2")
        self.assertEqual(supplement["equivalences"][0]["observedCitationReading"], "you3")
        self.assertEqual(supplement["qualification"], "owner-selected alignment only; original failures retained")

    def test_actual_third_tone_is_not_mislabeled_authored_second_tone(self):
        self.phonetic["tokens"][1]["text"] = "3"
        with self.assertRaisesRegex(ValueError, "No unique independently"):
            self.align()

    def test_punctuation_between_the_pair_cannot_create_sandhi_equivalence(self):
        for separator in ["。", "，", ",", "？", "——", "："]:
            with self.subTest(separator=separator):
                self.text = "游" + separator + "好一点儿"
                with self.assertRaisesRegex(ValueError, "No unique independently"):
                    self.align()
        self.text = "游 好一点儿"
        self.assertEqual(len(self.align()[0]), 4)

    def test_unrelated_tone_substitution_is_not_accepted(self):
        self.primary["tokens"][0]["text"] = "幼"
        self.primary["transcript"] = "幼好一点"
        with self.assertRaisesRegex(ValueError, "No unique independently"):
            self.align()

    def test_independent_low_confidence_cannot_establish_equivalence(self):
        self.phonetic["tokens"][0]["probability"] = .97
        with self.assertRaisesRegex(ValueError, "No unique independently"):
            self.align()

    def test_unrelated_orphan_tone_and_incomplete_tail_do_not_erase_a_complete_local_pair(self):
        self.phonetic['tokens'].insert(0, {'text': '3', 'start': .02, 'end': .04, 'probability': .9})
        self.phonetic['tokens'].append({'text': 'wo', 'start': 1.6, 'end': 1.7, 'probability': .99})
        original = copy.deepcopy(self.phonetic)
        rows, _, _ = self.align()
        self.assertEqual(self.phonetic, original)
        self.assertTrue(rows[0]['sandhiSpellingEquivalent'])

    def test_pair_cannot_bridge_an_orphan_tone(self):
        self.phonetic['tokens'].insert(2, {'text': '3', 'start': .3, 'end': .35, 'probability': .999})
        with self.assertRaisesRegex(ValueError, 'No unique independently'):
            self.align()

    def test_another_time_region_cannot_supply_the_same_sounds(self):
        for token in self.phonetic["tokens"]:
            token["start"] += .3
            token["end"] += .3
        with self.assertRaisesRegex(ValueError, "No unique independently"):
            self.align()

    def test_missing_or_extra_primary_speech_cannot_be_filled(self):
        for tokens in [self.primary["tokens"][:-1], self.primary["tokens"] + [
                {"tokenId": 9, "text": "我", "start": 1.6, "end": 1.7, "probability": .99}]]:
            with self.subTest(tokens=tokens):
                self.primary["tokens"] = tokens
                self.primary["transcript"] = "".join(t["text"] for t in tokens)
                with self.assertRaisesRegex(ValueError, "No unique independently"):
                    self.align()

    def test_changed_phonetic_policy_or_duration_is_rejected(self):
        self.phonetic["policy"] = {"model": "other"}
        with self.assertRaisesRegex(ValueError, "pinned independent"):
            self.align()
        self.phonetic["policy"] = PHONETIC_POLICY
        self.phonetic["durationSeconds"] = 3.
        with self.assertRaisesRegex(ValueError, "pinned independent"):
            self.align()

    def test_unnecessary_supplement_cannot_change_a_valid_original_mapping(self):
        self.primary["tokens"][0]["text"] = "游"
        self.primary["transcript"] = "游好一点"
        with self.assertRaisesRegex(ValueError, "already covers"):
            self.align()


class OwnerYiSandhiAlignmentTests(unittest.TestCase):
    def setUp(self):
        OwnerSandhiAlignmentTests.setUp(self)
        self.text, self.pinyin = '一忘一点儿', 'yī wàng yī diǎnr'
        self.primary['transcript'] = '遗忘一点'
        self.primary['tokens'][0]['text'] = '遗'
        self.primary['tokens'][1]['text'] = '忘'
        self.phonetic['tokens'][0]['text'] = 'yi'
        self.phonetic['tokens'][2]['text'] = 'wang'
        self.phonetic['tokens'][3]['text'] = '4'

    def align(self):
        return owner_sandhi_syllables(self.text, self.pinyin, self.primary, self.duration, self.phonetic)

    def test_yi_before_fourth_tone_uses_separate_policy_and_original_events(self):
        original = copy.deepcopy(self.primary)
        rows, ends, supplement = self.align()
        self.assertEqual(supplement['policy'], YI_POLICY)
        self.assertEqual(supplement['equivalences'][0]['authoredReading'], 'yi1')
        self.assertEqual(supplement['equivalences'][0]['observedCitationReading'], 'yi2')
        self.assertEqual(supplement['equivalences'][0]['followingReading'], 'wang4')
        self.assertEqual(ends, [1, 2, 3, 5])
        self.assertEqual([(row['start'], row['end']) for row in rows],
                         [(row['start'], row['end']) for row in original['tokens']])
        self.assertEqual(self.primary, original)
        self.assertFalse(rows[0]['reliable'])

    def test_yi_rule_does_not_cover_other_characters_tones_boundaries_or_missing_speech(self):
        for mutation in ['punctuation', 'not-yi', 'following-third', 'wrong-surface', 'low-confidence', 'orphan', 'missing-speech']:
            with self.subTest(mutation=mutation):
                self.setUp()
                if mutation == 'punctuation':
                    self.text = '一，忘一点儿'
                elif mutation == 'not-yi':
                    self.text, self.pinyin = '以忘一点儿', 'yǐ wàng yī diǎnr'
                elif mutation == 'following-third':
                    self.text, self.pinyin = '一想一点儿', 'yī xiǎng yī diǎnr'
                    self.primary['tokens'][1]['text'] = '想'
                    self.primary['transcript'] = '遗想一点'
                    self.phonetic['tokens'][2]['text'] = 'xiang'
                    self.phonetic['tokens'][3]['text'] = '3'
                elif mutation == 'wrong-surface':
                    self.phonetic['tokens'][1]['text'] = '1'
                elif mutation == 'low-confidence':
                    self.phonetic['tokens'][0]['probability'] = .97
                elif mutation == 'orphan':
                    self.phonetic['tokens'].insert(2, {'text': '3', 'start': .3, 'end': .35, 'probability': .999})
                else:
                    self.primary['tokens'].pop()
                    self.primary['transcript'] = '遗忘一'
                with self.assertRaises(ValueError):
                    self.align()

    def test_ordinal_digits_and_calendar_contexts_cannot_use_mechanically_offered_sandhi(self):
        cases = [('第一课', 'dì yī kè', 'di4 yi2 ke4'),
                 ('第一次', 'dì yī cì', 'di4 yi2 ci4'),
                 ('一四', 'yī sì', 'yi2 si4'),
                 ('六一课', 'liù yī kè', 'liu4 yi2 ke4'),
                 ('十一课', 'shí yī kè', 'shi2 yi2 ke4'),
                 ('星期一放假', 'xīng qī yī fàng jià', 'xing1 qi1 yi2 fang4 jia4'),
                 ('一月', 'yī yuè', 'yi2 yue4'),
                 ('一日', 'yī rì', 'yi2 ri4'),
                 ('一号', 'yī hào', 'yi2 hao4'),
                 ('一万', 'yī wàn', 'yi2 wan4')]
        for text, pinyin, readings in cases:
            with self.subTest(text=text):
                observed = text.replace('一', '遗', 1)
                duration = len(text) * .25 + .4
                primary = {'policy': TIMING_POLICY, 'durationSeconds': duration, 'transcript': observed,
                           'tokens': [{'tokenId': i, 'text': char, 'start': .1 + i * .25,
                                       'end': .2 + i * .25, 'probability': .999}
                                      for i, char in enumerate(observed)]}
                phonetic = {'policy': PHONETIC_POLICY, 'durationSeconds': duration, 'tokens': []}
                for i, reading in enumerate(readings.split()):
                    start = .1 + i * .25
                    phonetic['tokens'].extend([
                        {'text': reading[:-1], 'start': start, 'end': start + .05, 'probability': .999},
                        {'text': reading[-1], 'start': start + .05, 'end': start + .1, 'probability': .999}])
                if text == '一万':
                    self.assertEqual(owner_sandhi_syllables(text, pinyin, primary, duration, phonetic)[2]['policy'], YI_POLICY)
                else:
                    with self.assertRaises(ValueError):
                        owner_sandhi_syllables(text, pinyin, primary, duration, phonetic)


if __name__ == "__main__":
    unittest.main()
