"""Behavior at the external ASR-evidence boundary; real-model pilot is retained separately."""
import copy
import unittest

import numpy as np

from scripts.audio_pipeline.endings import assess_endings
from scripts.audio_pipeline.punctuation import assess_punctuation, observed_syllables
from scripts.audio_pipeline.timing import (
    TIMING_POLICY,
    TIMING_POLICY_V1,
    TIMING_POLICY_V2,
    acoustic_continuity,
    sample_digest,
)


class CharacterTimingTests(unittest.TestCase):
    def setUp(self):
        self.samples = np.zeros(32000, dtype=np.float32)
        for first, last in [(1600, 6400), (16000, 24000)]:
            self.samples[first:last] = .1 * np.sin(np.arange(last - first) * .1)
        self.evidence = {
            "policy": copy.deepcopy(TIMING_POLICY), "durationSeconds": 2.0,
            "pcmSha256": sample_digest(self.samples), "transcript": "我你",
            "acousticContinuity": acoustic_continuity(self.samples),
            "tokens": [{"tokenId": 1, "text": "我", "start": .15, "end": .4, "probability": .95},
                       {"tokenId": 2, "text": "你", "start": 1.0, "end": 1.5, "probability": .96}],
        }

    def assess(self, text="我。你。", pinyin="wǒ nǐ", evidence=None, samples=None):
        return assess_endings(self.samples if samples is None else samples, 16000,
                              text, pinyin, self.evidence if evidence is None else evidence)

    def test_complete_primary_observation_qualifies_real_quiet(self):
        self.assertTrue(self.assess()["accepted"])
        self.assertTrue(assess_punctuation(self.samples, 16000, "我。你。", "wǒ nǐ",
                                           self.evidence)["accepted"])

    def test_changed_audio_of_identical_duration_rejects_stale_evidence(self):
        changed = self.samples.copy()
        changed[200] = .1
        self.assertFalse(self.assess(samples=changed)["accepted"])

    def test_wrong_missing_added_and_latin_content_is_not_discarded(self):
        for values in [[], self.evidence["tokens"][:1], self.evidence["tokens"] * 2,
                       list(reversed(self.evidence["tokens"]))]:
            evidence = copy.deepcopy(self.evidence)
            evidence["tokens"] = values
            evidence["transcript"] = "".join(t["text"] for t in values)
            self.assertFalse(self.assess(evidence=evidence)["accepted"])
        for value in ["猫", "hello", "1", "<|speech|>"]:
            evidence = copy.deepcopy(self.evidence)
            evidence["tokens"][0]["text"] = value
            evidence["transcript"] = value + "你"
            self.assertFalse(self.assess(evidence=evidence)["accepted"])

    def test_unreliable_or_stale_observation_cannot_pass(self):
        for field, value in [("probability", .89), ("probability", float("nan")),
                             ("start", -.1), ("end", 3.0)]:
            evidence = copy.deepcopy(self.evidence)
            evidence["tokens"][-1][field] = value
            self.assertFalse(self.assess(evidence=evidence)["accepted"])
        evidence = copy.deepcopy(self.evidence)
        evidence["policy"]["revision"] = "unknown"
        self.assertFalse(self.assess(evidence=evidence)["accepted"])

    def test_homophone_spelling_and_repeated_characters_remain_observed(self):
        evidence = copy.deepcopy(self.evidence)
        evidence["tokens"][0]["text"] = "他"
        evidence["transcript"] = "他你"
        self.assertTrue(self.assess("她。你。", "tā nǐ", evidence)["accepted"])
        evidence["tokens"][0]["text"] = "你"
        evidence["transcript"] = "你你"
        self.assertTrue(self.assess("你。你。", "nǐ nǐ", evidence)["accepted"])

    def test_homophonic_posterior_mass_does_not_count_wrong_sounds(self):
        evidence = copy.deepcopy(self.evidence)
        evidence["tokens"][-1].update(text="她", probability=.6,
            alternatives=[{"tokenId": 2, "text": "她", "probability": .6},
                          {"tokenId": 3, "text": "他", "probability": .35}])
        evidence["transcript"] = "我她"
        self.assertTrue(self.assess("我。她。", "wǒ tā", evidence)["accepted"])
        evidence["tokens"][-1]["alternatives"][-1]["text"] = "你"
        self.assertFalse(self.assess("我。她。", "wǒ tā", evidence)["accepted"])

    def test_written_erhua_groups_only_existing_observed_characters(self):
        evidence = copy.deepcopy(self.evidence)
        evidence["tokens"][0]["text"] = "哪"
        evidence["tokens"][1]["text"] = "儿"
        evidence["transcript"] = "哪儿"
        rows, ends = observed_syllables("哪儿", "nǎr", evidence, 2.0)
        self.assertEqual(ends, [2])
        self.assertEqual((rows[0]["start"], rows[0]["end"]), (.15, 1.5))
        evidence["tokens"].pop()
        evidence["transcript"] = "哪"
        with self.assertRaises(ValueError):
            observed_syllables("哪儿", "nǎr", evidence, 2.0)

    def rhotic_recording(self, text="哪儿"):
        samples = np.zeros(16000, dtype=np.float32)
        samples[1600:14400] = .1 * np.sin(np.arange(12800) * .1)
        tokens = []
        for index, character in enumerate(text):
            start = .12 + index * .15
            tokens.append({"tokenId": index + 1, "text": character, "start": start, "end": start + .06,
                           "probability": .6 if character == "儿" else .96})
        evidence = {"policy": copy.deepcopy(TIMING_POLICY), "durationSeconds": 1.0,
                    "pcmSha256": sample_digest(samples), "transcript": text,
                    "tokens": tokens, "acousticContinuity": acoustic_continuity(samples)}
        return samples, evidence

    def test_continuous_non_syllabic_r_shares_the_observed_base(self):
        for text, pinyin in [("哪儿", "nǎr"), ("这儿", "zhèr"),
                             ("一点儿", "yī diǎnr"), ("有点儿", "yǒu diǎnr")]:
            samples, evidence = self.rhotic_recording(text)
            result = assess_endings(samples, 16000, text, pinyin, evidence)
            self.assertTrue(result["accepted"], (text, result))
        samples, evidence = self.rhotic_recording()
        rows, ends = observed_syllables("哪儿", "nǎr", evidence, 1.0)
        self.assertEqual(ends, [2])
        self.assertEqual(rows[0]["end"], evidence["tokens"][-1]["end"])
        evidence["tokens"][0]["probability"] = .89
        self.assertFalse(assess_endings(samples, 16000, "哪儿", "nǎr", evidence)["accepted"])

    def test_weak_suffix_cannot_hide_a_detached_burst_after_the_base(self):
        samples, evidence = self.rhotic_recording()
        # The base ends at .18; a detached burst begins at the weak suffix event.
        samples[3200:4320] = 0
        evidence["pcmSha256"] = sample_digest(samples)
        evidence["acousticContinuity"] = acoustic_continuity(samples)
        result = assess_endings(samples, 16000, "哪儿", "nǎr", evidence)
        self.assertFalse(result["accepted"], result)
        self.assertIn("ending-noise-unverified", result["failures"])

    def test_quiet_certificate_is_recomputed_from_samples(self):
        samples, evidence = self.rhotic_recording()
        samples[3200:4320] = 0
        evidence["pcmSha256"] = sample_digest(samples)
        # A genuine waveform hash cannot make a false continuity claim valid.
        evidence["acousticContinuity"]["quietSpans"] = []
        result = assess_endings(samples, 16000, "哪儿", "nǎr", evidence)
        self.assertFalse(result["accepted"], result)
        self.assertIn("continuity", result["reason"])

    def test_standalone_er_remains_a_separate_confident_syllable(self):
        samples, evidence = self.rhotic_recording("女儿")
        result = assess_endings(samples, 16000, "女儿", "nǚ ér", evidence)
        self.assertFalse(result["accepted"], result)
        evidence["tokens"][-1]["probability"] = .96
        self.assertTrue(assess_endings(samples, 16000, "女儿", "nǚ ér", evidence)["accepted"])
        samples, evidence = self.rhotic_recording("儿子")
        rows, ends = observed_syllables("儿子", "ér zi", evidence, 1.0)
        self.assertEqual(ends, [1, 2])
        self.assertFalse(rows[0]["reliable"])
        self.assertTrue(rows[1]["reliable"])

    def test_rhotic_group_keeps_authentic_prefix_and_complete_content(self):
        samples, evidence = self.rhotic_recording("我在哪儿")
        self.assertTrue(assess_endings(samples, 16000, "我在哪儿", "wǒ zài nǎr", evidence)["accepted"])
        for change in ("missing_prefix", "wrong_prefix", "missing_suffix", "extra_speech"):
            changed = copy.deepcopy(evidence)
            if change == "missing_prefix":
                changed["tokens"].pop(0)
            elif change == "wrong_prefix":
                changed["tokens"][0]["text"] = "你"
            elif change == "missing_suffix":
                changed["tokens"].pop()
            else:
                changed["tokens"].append({"text": "我", "start": .8, "end": .86, "probability": .99})
            changed["transcript"] = "".join(t["text"] for t in changed["tokens"])
            self.assertFalse(assess_endings(samples, 16000, "我在哪儿", "wǒ zài nǎr", changed)["accepted"], change)

    def test_exact_v1_evidence_keeps_its_existing_behavior(self):
        evidence = copy.deepcopy(self.evidence)
        evidence["policy"] = copy.deepcopy(TIMING_POLICY_V1)
        evidence.pop("acousticContinuity")
        self.assertTrue(self.assess(evidence=evidence)["accepted"])
        samples, evidence = self.rhotic_recording()
        evidence["policy"] = copy.deepcopy(TIMING_POLICY_V1)
        evidence.pop("acousticContinuity")
        self.assertFalse(assess_endings(samples, 16000, "哪儿", "nǎr", evidence)["accepted"])

    def test_exact_v2_evidence_keeps_continuity_and_waveform_binding(self):
        samples, evidence = self.rhotic_recording()
        evidence["policy"] = copy.deepcopy(TIMING_POLICY_V2)
        for token in evidence["tokens"]:
            token.pop("tokenId")
        self.assertTrue(assess_endings(samples, 16000, "哪儿", "nǎr", evidence)["accepted"])
        samples[3200:4320] = 0
        self.assertFalse(assess_endings(samples, 16000, "哪儿", "nǎr", evidence)["accepted"])
        evidence["pcmSha256"] = sample_digest(samples)
        self.assertFalse(assess_endings(samples, 16000, "哪儿", "nǎr", evidence)["accepted"])
        evidence["acousticContinuity"] = acoustic_continuity(samples)
        self.assertFalse(assess_endings(samples, 16000, "哪儿", "nǎr", evidence)["accepted"])

    def collision_evidence(self):
        evidence = copy.deepcopy(self.evidence)
        evidence["transcript"] = "我比"
        evidence["tokens"][-1].update(text="比", probability=.9995, alternatives=[
            {"tokenId": 2, "text": "比", "probability": .9995},
            {"tokenId": 3, "text": "be", "probability": .00013},
            {"tokenId": 4, "text": " ⁇ ", "probability": .00010},
            {"tokenId": 5, "text": "笔", "probability": .00005},
            {"tokenId": 6, "text": "给", "probability": .00003},
            {"tokenId": 7, "text": "别", "probability": .00002},
            {"tokenId": 8, "text": "be", "probability": .000018},
            {"tokenId": 9, "text": "不", "probability": .00001},
        ])
        return evidence

    def test_distinct_raw_bpe_ids_may_have_identical_decoded_labels(self):
        evidence = self.collision_evidence()
        self.assertTrue(self.assess("我。比。", "wǒ bǐ", evidence)["accepted"])
        # Older policies retain their exact historical validation semantics.
        for policy in (TIMING_POLICY_V1, TIMING_POLICY_V2):
            evidence["policy"] = copy.deepcopy(policy)
            self.assertFalse(self.assess("我。比。", "wǒ bǐ", evidence)["accepted"])

    def test_raw_token_ids_are_required_unique_nonnegative_integers(self):
        for invalid in [None, -1, 2.5, "2", True]:
            for location in ("greedy", "alternative"):
                evidence = self.collision_evidence()
                token = evidence["tokens"][-1]
                target = token if location == "greedy" else token["alternatives"][-1]
                target["tokenId"] = invalid
                self.assertFalse(self.assess("我。比。", "wǒ bǐ", evidence)["accepted"], (location, invalid))
        evidence = self.collision_evidence()
        evidence["tokens"][-1]["alternatives"][-1]["tokenId"] = 3
        self.assertFalse(self.assess("我。比。", "wǒ bǐ", evidence)["accepted"])

    def test_raw_id_evidence_preserves_top1_order_and_probability_mass(self):
        for change in ("top_id", "top_text", "top_probability", "excess_mass", "wrong_order"):
            evidence = self.collision_evidence()
            token = evidence["tokens"][-1]
            alternatives = token["alternatives"]
            if change == "top_id":
                token["tokenId"] = 99
            elif change == "top_text":
                alternatives[0]["text"] = "笔"
            elif change == "top_probability":
                token["probability"] = .99
            elif change == "excess_mass":
                alternatives[1]["probability"] = .1
            else:
                alternatives[1], alternatives[-1] = alternatives[-1], alternatives[1]
            self.assertFalse(self.assess("我。比。", "wǒ bǐ", evidence)["accepted"], change)


if __name__ == "__main__":
    unittest.main()
