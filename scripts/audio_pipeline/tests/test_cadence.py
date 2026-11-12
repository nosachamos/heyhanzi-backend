"""Public cadence CLI checks using controlled audio and external recognizer evidence."""
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


class CadenceCommandTests(unittest.TestCase):
    def check(self, *, gap=.6, text='我。你。', quiet=True, missing=False, qualification_change=None,
              changed_audio=False):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            rate=24000
            values=[]
            for i in range(int(rate*1.6)):
                t=i/rate
                voiced=(.08<t<.4 or .4+gap<t<1.5) if quiet else .08<t<1.5
                values.append(int(4000*math.sin(2*math.pi*220*t)) if voiced else 0)
            def save():
                with wave.open(str(root/'audio.wav'),'wb') as output:
                    output.setparams((1,2,rate,len(values),'NONE','not compressed'))
                    output.writeframes(struct.pack('<'+'h'*len(values),*values))
            save()
            evidence={'policy':copy.deepcopy(PHONETIC_POLICY),'durationSeconds':1.6,'tokens':[
                {'text':'wo','start':.1,'end':.3,'probability':.999},
                {'text':'3','start':.3,'end':.4,'probability':.999},
                {'text':'ni','start':.4+gap,'end':1.3,'probability':.999},
                {'text':'3','start':1.3,'end':1.4,'probability':.999}]}
            (root/'evidence.json').write_text(json.dumps(evidence))
            args=[sys.executable,'-m','scripts.audio_pipeline.cadence','--audio',str(root/'audio.wav'),
                  '--text',text,'--pinyin','wǒ nǐ','--report',str(root/'report.json')]
            if not missing:
                args+=['--evidence',str(root/'evidence.json')]
            result=subprocess.run(args,capture_output=True,text=True)
            self.assertIn(result.returncode,[0,1],result.stderr)
            report=json.loads((root/'report.json').read_text())
            if qualification_change is not None or changed_audio:
                if qualification_change:
                    qualification_change(report)
                (root/'saved.json').write_text(json.dumps(report))
                if changed_audio:
                    values[100]+=1
                    save()
                result=subprocess.run(args+['--qualification',str(root/'saved.json')],capture_output=True,text=True)
                self.assertIn(result.returncode,[0,1],result.stderr)
                report=json.loads((root/'report.json').read_text())
            self.assertEqual(result.returncode,0 if report['accepted'] else 1)
            return report

    def test_natural_sentence_break_and_exact_qualification_pass(self):
        self.assertTrue(self.check(qualification_change=lambda report:None)['accepted'])

    def test_short_sentence_break_rejects_even_if_legacy_minimum_would_pass(self):
        self.assertIn('bao-sentence-pause-too-short',self.check(gap=.3)['failures'])

    def test_long_observation_gap_without_actual_quiet_rejects(self):
        self.assertFalse(self.check(quiet=False)['accepted'])

    def test_commas_keep_shorter_connected_break(self):
        self.assertTrue(self.check(gap=.2,text='我，你。')['accepted'])

    def test_missing_timing_cannot_pass_a_sentence_break(self):
        self.assertIn('punctuation-timing-unverified',self.check(missing=True)['failures'])

    def test_terminal_mark_has_no_artificial_pause_requirement(self):
        self.assertTrue(self.check(text='我你。',missing=True)['accepted'])

    def test_forged_acceptance_cannot_override_short_pause(self):
        result=self.check(gap=.3,qualification_change=lambda r:r.update(accepted=True,failures=[]))
        self.assertIn('bao-sentence-pause-too-short',result['failures'])

    def test_stale_source_policy_and_same_duration_audio_fail_closed(self):
        for field,value in [('text','别的'),('pinyin','different'),('policy',{})]:
            with self.subTest(field=field):
                result=self.check(qualification_change=lambda r:r.update({field:value}))
                self.assertIn('bao-cadence-evidence-missing-or-stale',result['failures'])
        result=self.check(changed_audio=True)
        self.assertIn('bao-cadence-evidence-missing-or-stale',result['failures'])
        self.assertIn('bao-cadence-encoded-audio-changed',result['failures'])


if __name__=='__main__':
    unittest.main()
