"""Exact-byte owner selection and honest timing at the external ASR boundary."""
import copy
import hashlib
import io
import unittest

import numpy as np
import soundfile as sf

from scripts.audio_pipeline.owner_selection import (
    STATUS,
    candidate_score,
    owner_syllables,
    selection_metadata,
    timing_summary,
    verify_owner_recording,
)
from scripts.audio_pipeline.timing import (
    TIMING_POLICY,
    acoustic_continuity,
    sample_digest,
)


class OwnerSelectionTests(unittest.TestCase):
    def setUp(self):
        samples = np.zeros(48000, dtype=np.float32)
        samples[2400:12000] = .05 * np.sin(np.arange(9600) * .1)
        samples[24000:36000] = .05 * np.sin(np.arange(12000) * .1)
        stream = io.BytesIO()
        sf.write(stream, samples, 24000, format="MP3")
        self.data = stream.getvalue()
        self.samples, _ = sf.read(io.BytesIO(self.data), dtype="float32")
        self.evidence = self.observation("我你")
        self.quality = {
            "accepted": False, "failures": ["speech-quality-below-policy"],
            "termination": {"reason": "eos", "generatedSpeechTokens": 20, "maximumSpeechTokens": 200},
            "expectedText": "我你", "intendedPinyin": "wǒ nǐ", "transcript": "我你",
            "durationSeconds": 2., "dnsmos": {"overall": 2.7, "signal": 2.9, "background": 3.6},
            "endings": {"accepted": False, "failures": ["ending-noise-unverified"]},
            "punctuation": {"failures": []}, "ownerTimingEvidence": self.evidence,
            "ownerTiming": timing_summary("我你", "wǒ nǐ", self.evidence, 2.),
        }
        self.recipe = {"voiceId": "mandarin-female"}
        self.manifest = {"id": "test", "text": "我你", "pinyin": "wǒ nǐ",
                         "voiceId": "mandarin-female", "voiceVersion": "test-version",
                         "profile": self.recipe, "sha256": hashlib.sha256(self.data).hexdigest(),
                         "byteLength": len(self.data), "durationSeconds": 2., "quality": self.quality,
                         "reviewStatus": STATUS, "humanReviewed": False,
                         "attempts": [{"accepted": False, "failures": ["speech-quality-below-policy"]} for _ in range(10)]}
        self.resign()

    def observation(self, transcript):
        return {"policy": TIMING_POLICY, "durationSeconds": 2., "transcript": transcript,
                "pcmSha256": sample_digest(self.samples, 24000),
                "acousticContinuity": acoustic_continuity(self.samples, 24000),
                "tokens": [{"tokenId": i + 1, "text": c, "start": .2 + i * .4,
                            "end": .35 + i * .4, "probability": .7 if i else .99}
                           for i, c in enumerate(transcript)]}

    def resign(self):
        candidate = {"attempt": 0, "sha256": self.manifest["sha256"],
                     "byteLength": self.manifest["byteLength"], "score": candidate_score(self.quality)}
        self.manifest["ownerSelection"] = selection_metadata(self.manifest, [candidate], 0, 0)

    def verify(self):
        return verify_owner_recording("test", self.manifest, self.recipe, "test-version", self.manifest, self.data)

    def test_exact_exception_preserves_failed_automated_result(self):
        self.verify()
        self.assertFalse(self.manifest["quality"]["accepted"])
        self.assertEqual(self.manifest["quality"]["failures"], ["speech-quality-below-policy"])

    def test_fewer_than_ten_failures_cannot_select(self):
        self.manifest["attempts"].pop()
        self.resign()
        with self.assertRaisesRegex(ValueError, "ten measured failed"):
            self.verify()

    def test_reviewed_historical_implementations_keep_original_receipts(self):
        for implementation in [
            "e5a5aa57381a44a766392ad269dee40d1f84efc490a1db90f072d7d9fe61680f",
            "2a049bfbfdb753ed48259cfab34ab4683912c570292778fb430fa12dd25f0680",
        ]:
            with self.subTest(implementation=implementation):
                self.manifest["ownerSelection"]["implementationSha256"] = implementation
                original = copy.deepcopy(self.manifest)
                self.verify()
                self.assertEqual(self.manifest, original)
                self.assertFalse(self.manifest["quality"]["accepted"])

    def test_unknown_historical_implementation_is_rejected(self):
        self.manifest["ownerSelection"]["implementationSha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "exact-byte"):
            self.verify()

    def test_historical_implementation_does_not_waive_binding_or_bytes(self):
        self.manifest["ownerSelection"]["implementationSha256"] = (
            "e5a5aa57381a44a766392ad269dee40d1f84efc490a1db90f072d7d9fe61680f"
        )
        original = copy.deepcopy(self.quality)
        self.quality["dnsmos"]["overall"] = 5
        with self.assertRaisesRegex(ValueError, "exact-byte"):
            self.verify()
        self.quality.clear()
        self.quality.update(original)
        self.data += b"changed"
        with self.assertRaisesRegex(ValueError, "exact-byte"):
            self.verify()

    def test_changed_bytes_or_failed_measurements_invalidate_selection(self):
        self.data += b"changed"
        with self.assertRaisesRegex(ValueError, "exact-byte"):
            self.verify()
        self.data = self.data[:-7]
        self.quality["dnsmos"]["overall"] = 5
        with self.assertRaisesRegex(ValueError, "exact-byte"):
            self.verify()

    def test_capped_or_abrupt_speech_cannot_use_exception(self):
        self.quality["termination"]["reason"] = "max_tokens"
        self.resign()
        with self.assertRaisesRegex(ValueError, "incomplete or unplayable"):
            self.verify()
        self.quality["termination"]["reason"] = "eos"
        self.quality["failures"].append("abrupt-speech-ending")
        self.resign()
        with self.assertRaisesRegex(ValueError, "incomplete or unplayable"):
            self.verify()

    def test_worse_candidate_cannot_be_selected(self):
        self.manifest["ownerSelection"]["candidates"].append({
            "attempt": 1, "sha256": "other", "byteLength": 1,
            "score": [-1, *candidate_score(self.quality)[1:]]})
        with self.assertRaisesRegex(ValueError, "measured best"):
            self.verify()

    def test_low_confidence_is_retained_not_certified(self):
        rows, ends = owner_syllables("我你", "wǒ nǐ", self.evidence, 2.)
        self.assertEqual(ends, [1, 2])
        self.assertFalse(rows[1]["reliable"])
        self.assertEqual(rows[1]["start"], self.evidence["tokens"][1]["start"])

    def test_missing_written_r_uses_real_base_without_inventing_events(self):
        evidence = self.observation("有点")
        rows, ends = owner_syllables("有点儿", "yǒu diǎnr", evidence, 2.)
        self.assertEqual(ends, [1, 3])
        self.assertEqual(len(rows), 2)
        self.assertTrue(rows[1]["orthographicRhoticOmitted"])
        self.assertFalse(rows[1]["reliable"])
        self.assertEqual(rows[1]["end"], evidence["tokens"][1]["end"])

    def test_standalone_er_and_missing_or_extra_content_are_not_invented(self):
        for text, reading, actual in [("女儿", "nǚ ér", "女"), ("我你好", "wǒ nǐ hǎo", "我你"),
                                      ("我你", "wǒ nǐ", "我你你")]:
            with self.subTest(text=text, actual=actual), self.assertRaises(ValueError):
                owner_syllables(text, reading, self.observation(actual), 2.)

    def test_final_pcm_binding_cannot_be_reused_for_another_waveform(self):
        self.quality["ownerTimingEvidence"]["pcmSha256"] = "0" * 64
        self.resign()
        with self.assertRaisesRegex(ValueError, "different audio samples"):
            self.verify()

    def test_content_and_ending_noise_outrank_mos(self):
        wrong = copy.deepcopy(self.quality)
        wrong["ownerTimingEvidence"]["transcript"] = "我他"
        wrong["dnsmos"]["overall"] = 5
        self.assertLess(candidate_score(self.quality), candidate_score(wrong))
        noisy = copy.deepcopy(self.quality)
        noisy["endings"] = {"accepted": False, "failures": ["detached-sentence-ending-noise"]}
        noisy["dnsmos"]["overall"] = 5
        self.assertLess(candidate_score(self.quality), candidate_score(noisy))


if __name__ == "__main__":
    unittest.main()
