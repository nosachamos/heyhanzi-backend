"""Public offline CLI regressions; run in the audio environment, not the API image.

The recognizer's JSON output and input waveform are external boundaries. These
small controlled signals check timing decisions; the real-voice pilot separately
checks the model and production-quality integration.
"""

import copy
import json
import math
import struct
import subprocess
import sys
import tempfile
import unittest
import wave
from pathlib import Path

from scripts.audio_pipeline.phonetic import PHONETIC_POLICY
from scripts.audio_pipeline.punctuation import PUNCTUATION_POLICY


class PunctuationCommandTests(unittest.TestCase):
    def run_check(self, text="我。你。", gap=0.4, *, quiet=True, missing=False,
                  change=None, pinyin="wǒ nǐ", qualification=None):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            rate, duration = 24000, 1.6
            values = []
            for i in range(int(rate * duration)):
                t = i / rate
                voiced = 0.08 < t < 0.4 or 0.4 + gap < t < 1.5
                if not quiet:
                    voiced = 0.08 < t < 1.5
                values.append(int(4000 * math.sin(2 * math.pi * 220 * t)) if voiced else 0)
            with wave.open(str(root / "audio.wav"), "wb") as output:
                output.setparams((1, 2, rate, len(values), "NONE", "not compressed"))
                output.writeframes(struct.pack("<" + "h" * len(values), *values))
            evidence = {"policy": copy.deepcopy(PHONETIC_POLICY), "durationSeconds": duration,
                        "tokens": [
                            {"text": "wo", "start": 0.1, "end": 0.3, "probability": 0.999},
                            {"text": "3", "start": 0.3, "end": 0.4, "probability": 0.999},
                            {"text": "ni", "start": 0.4 + gap, "end": 1.3, "probability": 0.999},
                            {"text": "3", "start": 1.3, "end": 1.4, "probability": 0.999},
                        ]}
            if change:
                change(evidence)
            (root / "evidence.json").write_text(json.dumps(evidence))
            command = [sys.executable, "-m", "scripts.audio_pipeline.punctuation",
                       "--audio", str(root / "audio.wav"), "--text", text, "--pinyin", pinyin,
                       "--report", str(root / "report.json")]
            if qualification is not None:
                saved = {"policy": PUNCTUATION_POLICY, "accepted": True, "failures": [],
                         "timingEvidence": evidence, **qualification}
                (root / "qualification.json").write_text(json.dumps(saved))
                command += ["--qualification", str(root / "qualification.json")]
            elif not missing:
                command += ["--evidence", str(root / "evidence.json")]
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertIn(result.returncode, (0, 1), result.stderr)
            report = json.loads((root / "report.json").read_text())
            self.assertEqual(result.returncode, 0 if report["accepted"] else 1)
            return report

    def test_sentence_boundary_accepts_clear_break(self):
        result = self.run_check()
        self.assertTrue(result["accepted"])
        self.assertEqual(len(result["boundaries"]), 1)

    def test_rushed_period_is_rejected(self):
        result = self.run_check(gap=0.18)
        self.assertEqual(result["failures"], ["punctuation-boundary-too-short"])

    def test_long_alignment_gap_without_actual_quiet_is_rejected(self):
        self.assertFalse(self.run_check(quiet=False)["accepted"])

    def test_comma_can_have_a_shorter_break_than_a_period(self):
        self.assertTrue(self.run_check("我，你。", gap=0.16)["accepted"])
        self.assertFalse(self.run_check("我，你。", gap=0.04)["accepted"])

    def test_list_colon_semicolon_question_and_ellipsis(self):
        for mark in ["、", "：", ":", "；", ";", "？", "?", "！", "!", "…", "——", "\n"]:
            with self.subTest(mark=mark):
                self.assertTrue(self.run_check(f"我{mark}你。")["accepted"])
                self.assertFalse(self.run_check(f"我{mark}你。", gap=0.02)["accepted"])

    def test_quotes_and_terminal_marks_do_not_require_internal_pauses(self):
        for text in ["我你。", "我“你”。", "“我你！”", "我你？！"]:
            with self.subTest(text=text):
                result = self.run_check(text, missing=True)
                self.assertTrue(result["accepted"])
                self.assertEqual(result["status"], "not-applicable")

    def test_nested_quotes_and_repeated_marks_collapse_to_one_boundary(self):
        result = self.run_check("“我？！”“你。”")
        self.assertTrue(result["accepted"])
        self.assertEqual(len(result["boundaries"]), 1)

    def test_missing_timing_cannot_pass(self):
        self.assertEqual(self.run_check(missing=True)["failures"],
                         ["punctuation-timing-unverified"])

    def test_stale_model_cannot_pass(self):
        result = self.run_check(change=lambda e: e["policy"].update(modelRevision="old"))
        self.assertFalse(result["accepted"])

    def test_low_confidence_boundary_cannot_pass(self):
        result = self.run_check(change=lambda e: e["tokens"][2].update(probability=0.7))
        self.assertEqual(result["failures"], ["punctuation-timing-unverified"])

    def test_missing_wrong_extra_or_reordered_syllables_cannot_pass(self):
        changes = [lambda e: e["tokens"].pop(),
                   lambda e: e["tokens"][2].update(text="hao"),
                   lambda e: e["tokens"].extend(e["tokens"]),
                   lambda e: e["tokens"].reverse()]
        for change in changes:
            with self.subTest(change=change):
                self.assertFalse(self.run_check(change=change)["accepted"])

    def test_nonfinite_overlapping_and_out_of_range_timing_cannot_pass(self):
        for values in [{"start": float("nan")}, {"start": 0.2}, {"end": 5.0},
                       {"probability": float("nan")}]:
            with self.subTest(values=values):
                self.assertFalse(self.run_check(
                    change=lambda e: e["tokens"][2].update(values))["accepted"])

    def test_wrong_duration_and_missing_authored_reading_cannot_pass(self):
        self.assertFalse(self.run_check(change=lambda e: e.update(durationSeconds=3))["accepted"])
        self.assertFalse(self.run_check(pinyin="")["accepted"])

    def test_erhua_mapping_does_not_shift_the_boundary(self):
        def erhua(evidence):
            evidence["tokens"][0]["text"] = "huar"
        self.assertTrue(self.run_check("花儿。你。", pinyin="huār nǐ", change=erhua)["accepted"])

    def test_release_recomputes_decisions_instead_of_trusting_accepted_flag(self):
        self.assertTrue(self.run_check(qualification={})["accepted"])
        self.assertFalse(self.run_check(gap=0.18, qualification={})["accepted"])
        self.assertFalse(self.run_check(quiet=False, qualification={})["accepted"])
        self.assertFalse(self.run_check(qualification={"timingEvidence": None})["accepted"])

    def test_release_rejects_stale_policy_even_for_terminal_only_text(self):
        result = self.run_check("我你。", qualification={"policy": {"version": "old"}})
        self.assertEqual(result["failures"], ["punctuation-evidence-missing-or-stale"])


if __name__ == "__main__":
    unittest.main()
