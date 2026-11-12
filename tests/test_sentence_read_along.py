"""Published homophone reuse retains the selected visible character's timing."""
from app.services.sentence_audio import pronunciation


def test_homophone_recording_keeps_the_requested_character_in_both_voices():
    result = pronunciation('玉', {'scheme': 'pinyin', 'value': 'yù'})
    for voice in ['mandarin-female', 'mandarin-male']:
        reference = result['audio']['voices'][voice]
        timing = reference['readAlong']
        assert timing['audioSha256'] == reference['version']
        assert timing['text'] == '玉。'
        assert [word['text'] for word in timing['words'] if 'start' in word] == ['玉']
