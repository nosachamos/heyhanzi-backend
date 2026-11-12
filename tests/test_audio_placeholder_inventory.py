"""Public offline audit CLI prevents retired templates entering synthesis inventories."""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def audit(tmp_path, entries):
    manifest = tmp_path / 'manifest.json'
    report = tmp_path / 'report.json'
    manifest.write_text(json.dumps(entries, ensure_ascii=False))
    result = subprocess.run(
        [sys.executable, '-m', 'scripts.curriculum.audit_audio_placeholders',
         '--manifest', str(manifest), '--output', str(report)],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    return result, json.loads(report.read_text())


def test_rejects_all_retired_quoted_word_templates(tmp_path):
    texts = ['我在学“圭”。', '我正在学习「苹果」。', '你会说“东”吗？', '这是“慕”。']
    result, report = audit(tmp_path, {str(i): {'text': text} for i, text in enumerate(texts)})
    assert result.returncode == 1
    assert report['rejected'] == 4
    assert report['complete'] is False
    assert report['mediaModified'] is False
    assert [entry['text'] for entry in report['entries']] == texts


def test_preserves_natural_learning_sentences_dialogue_and_isolated_words(tmp_path):
    texts = ['我在学习中文。', '我学会了游泳。', '苹果。', '她说：“今天下雨了。”', '这是什么？']
    result, report = audit(tmp_path, {str(i): {'text': text} for i, text in enumerate(texts)})
    assert result.returncode == 0
    assert report['complete'] is True
    assert report['checked'] == len(texts)
    assert report['entries'] == []


def catalogue_fixture(tmp_path, *, placeholder=False):
    vocabulary = tmp_path / 'vocabulary.json'
    vocabulary.write_text(json.dumps({'vocabulary': [
        {'id': 'apple', 'text': '苹果', 'pronunciation': {'value': 'píngguǒ'}}]}))
    examples = tmp_path / 'examples'
    examples.mkdir()
    (examples / 'index.json').write_text(json.dumps({'characters': {}}))
    common = tmp_path / 'common'
    common.mkdir()
    (common / 'index.json').write_text(json.dumps({'words': {'apple': {'text': '苹果', 'file': 'aa.json'}}}))
    tokens = ([{'text': '我在学“', 'pinyin': 'wǒ zài xué'}] if placeholder else [
        {'text': '我', 'pinyin': 'wǒ'}, {'text': '吃', 'pinyin': 'chī'}]) + [
        {'text': '苹果', 'pinyin': 'píngguǒ', 'vocabularyId': 'apple'},
        {'text': '”。' if placeholder else '。', 'pinyin': ''}]
    (common / 'aa.json').write_text(json.dumps({'apple': [{
        'targetVocabularyId': 'apple', 'tokens': tokens,
        'pinyin': ' '.join(token['pinyin'] for token in tokens).strip()}]}))
    return vocabulary, examples, common


def test_staged_audio_catalogue_includes_contextual_common_word_examples(tmp_path):
    vocabulary, examples, common = catalogue_fixture(tmp_path)
    output = tmp_path / 'staged'
    result = subprocess.run([sys.executable, '-m', 'scripts.curriculum.build_audio_catalogue',
                             '--vocabulary', str(vocabulary), '--examples', str(examples),
                             '--common-examples', str(common), '--output', str(output)],
                            cwd=ROOT, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    entries = {key: value for path in (output / 'catalogue').glob('*.json')
               for key, value in json.loads(path.read_text()).items()}
    assert set(json.loads((output / 'generation-order.json').read_text())) == set(entries)
    assert {entry['text'] for entry in entries.values()} == {'苹果。', '我吃苹果。'}
    sentence = next(entry for entry in entries.values() if entry['text'] == '我吃苹果。')
    assert sentence['contextualReadings'][-1] == {
        'text': '苹果', 'pinyin': 'píngguǒ', 'start': 2, 'end': 4}


def test_staged_audio_catalogue_fails_before_writing_retired_common_template(tmp_path):
    vocabulary, examples, common = catalogue_fixture(tmp_path, placeholder=True)
    output = tmp_path / 'staged'
    result = subprocess.run([sys.executable, '-m', 'scripts.curriculum.build_audio_catalogue',
                             '--vocabulary', str(vocabulary), '--examples', str(examples),
                             '--common-examples', str(common), '--output', str(output)],
                            cwd=ROOT, capture_output=True, text=True, check=False)
    assert result.returncode != 0
    assert 'retired lexical-placeholder' in result.stderr
    assert not output.exists()


def test_staged_catalogue_accepts_reviewed_bound_form_but_rejects_incidental_substring(tmp_path):
    vocabulary, examples, common = catalogue_fixture(tmp_path)
    vocabulary.write_text(json.dumps({'vocabulary': [
        {'id': 'pine', 'text': '松', 'pronunciation': {'value': 'sōng'}}]}))
    (common / 'index.json').write_text(json.dumps({'words': {'pine': {
        'text': '松', 'reading_key': 'sōng', 'file': 'aa.json'}}}))
    tokens = [{'text': '松树', 'pinyin': 'sōng shù', 'vocabularyId': 'pine-tree'},
              {'text': '很', 'pinyin': 'hěn'}, {'text': '高', 'pinyin': 'gāo'},
              {'text': '。', 'pinyin': ''}]
    entry = {'targetVocabularyId': 'pine', 'tokens': tokens, 'pinyin': 'sōng shù hěn gāo',
             'targetSpan': {'tokenIndex': 0, 'start': 0, 'end': 1, 'text': '松',
                            'pinyin': 'sōng', 'vocabularyId': 'pine'}}
    for valid in [True, False]:
        value = entry if valid else {key: item for key, item in entry.items() if key != 'targetSpan'}
        (common / 'aa.json').write_text(json.dumps({'pine': [value]}))
        output = tmp_path / ('reviewed' if valid else 'incidental')
        result = subprocess.run([sys.executable, '-m', 'scripts.curriculum.build_audio_catalogue',
                                 '--vocabulary', str(vocabulary), '--examples', str(examples),
                                 '--common-examples', str(common), '--output', str(output)],
                                cwd=ROOT, capture_output=True, text=True, check=False)
        if valid:
            assert result.returncode == 0, result.stderr
            entries = [item for path in (output / 'catalogue').glob('*.json')
                       for item in json.loads(path.read_text()).values()]
            sentence = next(item for item in entries if item['text'] == '松树很高。')
            assert sentence['contextualReadings'][0] == {
                'text': '松树', 'pinyin': 'sōng shù', 'start': 0, 'end': 2}
        else:
            assert result.returncode != 0
            assert 'lacks its exact target proof' in result.stderr
            assert not output.exists()
