"""Exercise the offline pace CLI against waveform/source inputs."""
import json
import math
import struct
import subprocess
import sys
import tempfile
import unittest
import wave
from pathlib import Path


class PaceCommandTests(unittest.TestCase):
    def check(self, active=.24, pause=.1, *, qualification_change=None,
              changed_audio=False, text='我也学习中文', pinyin='wǒ yě xué xí zhōng wén', pace='regular'):
        rate = 24000
        values = []
        for _ in range(6):
            values.extend(round(.08 * math.sin(2 * math.pi * 180 * i / rate) * 32767)
                          for i in range(round(rate * active)))
            values.extend([0] * round(rate * pause))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            audio = root / 'audio.wav'
            def save():
                with wave.open(str(audio), 'wb') as output:
                    output.setparams((1, 2, rate, len(values), 'NONE', 'not compressed'))
                    output.writeframes(struct.pack('<' + 'h' * len(values), *values))
            save()
            args = [sys.executable, '-m', 'scripts.audio_pipeline.pace', '--audio', str(audio),
                    '--text', text, '--pinyin', pinyin, '--pace', pace,
                    '--report', str(root / 'report.json')]
            result = subprocess.run(args, capture_output=True, text=True)
            self.assertIn(result.returncode, [0, 1], result.stderr)
            report = json.loads((root / 'report.json').read_text())
            if qualification_change is not None or changed_audio:
                if qualification_change:
                    qualification_change(report)
                (root / 'saved.json').write_text(json.dumps(report))
                if changed_audio:
                    values[10] += 1
                    save()
                result = subprocess.run(args + ['--qualification', str(root / 'saved.json')],
                                        capture_output=True, text=True)
                self.assertIn(result.returncode, [0, 1], result.stderr)
                report = json.loads((root / 'report.json').read_text())
            self.assertEqual(result.returncode, 0 if report['accepted'] else 1)
            return report

    def test_regular_connected_speech_passes(self):
        result = self.check()
        self.assertTrue(result['accepted'])
        self.assertEqual(result['syllableCount'], 6)

    def test_fast_speech_rejects_even_when_long_pauses_make_total_duration_normal(self):
        result = self.check(active=.13, pause=.5)
        self.assertIn('regular-pace-too-fast', result['failures'])

    def test_forced_slow_speech_does_not_claim_regular(self):
        self.assertIn('regular-pace-too-slow', self.check(active=.8)['failures'])

    def test_identical_qualification_recomputes_and_passes(self):
        self.assertTrue(self.check(qualification_change=lambda report: None)['accepted'])

    def test_forged_acceptance_cannot_override_rushed_audio(self):
        result = self.check(active=.13, qualification_change=lambda r: r.update(accepted=True, failures=[]))
        self.assertIn('regular-pace-too-fast', result['failures'])

    def test_same_duration_changed_samples_reject(self):
        result = self.check(changed_audio=True)
        self.assertIn('regular-pace-evidence-missing-or-stale', result['failures'])
        self.assertIn('regular-pace-encoded-audio-changed', result['failures'])

    def test_stale_policy_source_or_speed_rejects(self):
        for field, value in [('policy', {}), ('text', '别的句子'), ('pinyin', 'different'), ('pace', 'fast')]:
            with self.subTest(field=field):
                result = self.check(qualification_change=lambda r: r.update({field: value}))
                self.assertIn('regular-pace-evidence-missing-or-stale', result['failures'])

    def test_unmapped_readings_and_future_speed_modes_fail_closed(self):
        self.assertFalse(self.check(pinyin='')['accepted'])
        for pace in ['slow', 'fast']:
            self.assertIn('regular-pace-unverified', self.check(pace=pace)['failures'])


if __name__ == '__main__':
    unittest.main()
