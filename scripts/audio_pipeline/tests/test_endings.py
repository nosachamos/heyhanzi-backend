"""Public offline gate behavior; waveform and recognizer output are external inputs."""
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

from scripts.audio_pipeline.endings import ENDING_POLICY
from scripts.audio_pipeline.phonetic import PHONETIC_POLICY


class EndingCommandTests(unittest.TestCase):
    def check(self, *, noise=None, vowel_tail=False, onset=False, evidence_change=None,
              saved=None, gain=1.0):
        rate, duration = 24000, 2.4
        values = []
        for i in range(int(rate * duration)):
            t = i / rate
            # CTC witnesses end at .4/1.7; real vowels decay after those witnesses.
            voiced = .08 < t < (.56 if vowel_tail else .44) or 1.3 < t < (1.98 if vowel_tail else 1.78)
            value = .08 * math.sin(2 * math.pi * 180 * t) if voiced else 0
            if onset and 1.18 < t < 1.3:
                value += .025 * math.sin(2 * math.pi * 3700 * t)
            if noise == 'pause-click' and .78 < t < .782:
                value += .07
            if noise == 'pause-buzz' and .76 < t < .86:
                value += .018 * math.sin(2 * math.pi * 2800 * t)
            if noise == 'pause-unclosed-buzz' and .76 < t < 1.2:
                value += .018 * math.sin(2 * math.pi * 2800 * t)
            if noise == 'tail-click' and 2.1 < t < 2.102:
                value += .07
            if noise == 'tail-buzz' and 2.1 < t < 2.2:
                value += .018 * math.sin(2 * math.pi * 2800 * t)
            if noise == 'tail-unclosed-buzz' and t > 2.1:
                value += .018 * math.sin(2 * math.pi * 2800 * t)
            if noise == 'last-frame-click' and t > 2.399:
                value += .07
            if noise == 'quiet-floor' and .6 < t < 1.05:
                value += .0001 * math.sin(2 * math.pi * 2800 * t)
            values.append(round(value * gain * 32767))
        evidence = {'policy': copy.deepcopy(PHONETIC_POLICY), 'durationSeconds': duration,
                    'tokens': [{'text': 'wo', 'start': .1, 'end': .3, 'probability': .999},
                               {'text': '3', 'start': .3, 'end': .4, 'probability': .999},
                               {'text': 'ni', 'start': 1.3, 'end': 1.6, 'probability': .999},
                               {'text': '3', 'start': 1.6, 'end': 1.7, 'probability': .999}]}
        if evidence_change:
            evidence_change(evidence)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with wave.open(str(root / 'audio.wav'), 'wb') as output:
                output.setparams((1, 2, rate, len(values), 'NONE', 'not compressed'))
                output.writeframes(struct.pack('<' + 'h' * len(values), *values))
            args = [sys.executable, '-m', 'scripts.audio_pipeline.endings',
                    '--audio', str(root / 'audio.wav'), '--text', '我。你。', '--pinyin', 'wǒ nǐ',
                    '--report', str(root / 'report.json')]
            if saved is not None:
                value = {'policy': ENDING_POLICY, 'accepted': True, 'failures': [],
                         'timingEvidence': evidence, **saved}
                (root / 'qualification.json').write_text(json.dumps(value))
                args += ['--qualification', str(root / 'qualification.json')]
            else:
                (root / 'evidence.json').write_text(json.dumps(evidence))
                args += ['--evidence', str(root / 'evidence.json')]
            result = subprocess.run(args, capture_output=True, text=True)
            self.assertIn(result.returncode, [0, 1], result.stderr)
            report = json.loads((root / 'report.json').read_text())
            self.assertEqual(result.returncode, 0 if report['accepted'] else 1)
            return report

    def test_clean_pauses_and_terminal_padding_pass(self):
        self.assertTrue(self.check()['accepted'])

    def test_normal_vowel_decay_after_recognizer_end_is_preserved(self):
        self.assertTrue(self.check(vowel_tail=True)['accepted'])

    def test_next_sentence_consonant_is_protected(self):
        self.assertTrue(self.check(onset=True)['accepted'])

    def test_inaudible_noise_floor_is_not_an_artifact(self):
        self.assertTrue(self.check(noise='quiet-floor')['accepted'])

    def test_detached_clicks_and_buzz_rejected_inside_story_and_at_file_end(self):
        for noise in ['pause-click', 'pause-buzz', 'pause-unclosed-buzz', 'tail-click', 'tail-buzz',
                      'tail-unclosed-buzz', 'last-frame-click']:
            for gain in [.5, 1, 2]:
                with self.subTest(noise=noise, gain=gain):
                    self.assertEqual(self.check(noise=noise, gain=gain)['failures'],
                                     ['detached-sentence-ending-noise'])

    def test_timing_must_cover_complete_known_syllables(self):
        for change in [lambda e: e['tokens'].pop(),
                       lambda e: e['tokens'][-1].update(probability=.1),
                       lambda e: e.update(durationSeconds=9),
                       lambda e: e['tokens'][-1].update(end=float('nan')),
                       lambda e: e.update(policy={'version': 'old'})]:
            self.assertEqual(self.check(evidence_change=change)['failures'],
                             ['ending-noise-unverified'])

    def test_delivery_rechecks_audio_instead_of_trusting_saved_accepted_flag(self):
        self.assertTrue(self.check(saved={})['accepted'])
        self.assertFalse(self.check(noise='tail-buzz', saved={})['accepted'])
        self.assertEqual(self.check(saved={'policy': {'version': 'old'}})['failures'],
                         ['ending-evidence-missing-or-stale'])
        self.assertFalse(self.check(saved={'timingEvidence': None})['accepted'])


if __name__ == '__main__':
    unittest.main()
