"""Forward correction preserves existing lexical identity and learner evidence."""

import json

import psycopg2
import pytest


@pytest.mark.integration
def test_online_reading_upgrade_retains_exposure_and_word_identity(postgres_config, run_cli):
    result = run_cli(postgres_config, '-m', 'alembic', 'upgrade', '0022_notebook_grammar_vocabulary')
    assert result.returncode == 0, result.stderr
    vocabulary_id = 'cmn-16b971db0c7b145ef190fa2a'
    with psycopg2.connect(postgres_config['DATABASE']['Url']) as connection:
        with connection.cursor() as cursor:
            cursor.execute('SELECT row_to_json(v) FROM learning_vocabulary v WHERE id=%s', (vocabulary_id,))
            before = cursor.fetchone()[0]
            assert before['pronunciation']['value'] == 'xiàshàng'
            cursor.execute("SELECT id FROM learning_word_blocks WHERE kind='word' ORDER BY sequence LIMIT 1")
            block = cursor.fetchone()[0]
            cursor.execute("INSERT INTO users (id,created_at,name,email_normalized,failed_logins,failure_window_at,failure_alert_at,new_device_alert_at) "
                           "VALUES ('reading-owner',1,'Reader','reading@example.com',0,0,0,0)")
            cursor.execute("INSERT INTO learning_enrollments (id,user_id,track_id,created_at) "
                           "VALUES ('reading-enrollment','reading-owner','mandarin-simplified-v1',1)")
            cursor.execute('INSERT INTO learning_vocabulary_exposures (enrollment_id,vocabulary_id,block_id,displayed_at) '
                           'VALUES (%s,%s,%s,%s)', ('reading-enrollment', vocabulary_id, block, 123))
    for _ in range(2):
        result = run_cli(postgres_config, '-m', 'alembic', 'upgrade', 'head')
        assert result.returncode == 0, result.stderr
    with psycopg2.connect(postgres_config['DATABASE']['Url']) as connection:
        with connection.cursor() as cursor:
            cursor.execute('SELECT row_to_json(v) FROM learning_vocabulary v WHERE id=%s', (vocabulary_id,))
            after = cursor.fetchone()[0]
            assert {key: value for key, value in after.items() if key != 'language_data'} == {**{key: value for key, value in before.items() if key != 'language_data'}, 'pronunciation': {'scheme': 'pinyin', 'value': 'xiànshàng'}, 'reading_key': 'xiànshàng'}
            assert before['language_data'].items() <= after['language_data'].items()
            cursor.execute('SELECT enrollment_id,vocabulary_id,block_id,displayed_at '
                           'FROM learning_vocabulary_exposures WHERE vocabulary_id=%s', (vocabulary_id,))
            assert cursor.fetchall() == [('reading-enrollment', vocabulary_id, block, 123)]


@pytest.mark.parametrize('vocabulary_id,text,meaning,previous,corrected', [
    ('cmn-16b971db0c7b145ef190fa2a', '线上', 'online', 'xiàshàng', 'xiànshàng'),
    ('cmn-f9589f58b3cd743025baa230', '大都', 'for the most part; on the whole', 'dàdū', 'dàdōu'),
    ('cmn-0f209bc6ed49253576c403e4', '卷子', 'test paper', 'juǎn zi', 'juànzi'),
])
def test_current_source_overlay_keeps_the_same_word_identity(tmp_path, run_cli, app_config,
                                                           vocabulary_id, text, meaning, previous, corrected):
    # Public CLI imports the same overlay used by current compiler/audio builders.
    source = tmp_path / 'source.json'
    output = tmp_path / 'output.json'
    source.write_text(json.dumps([{'id': vocabulary_id, 'text': text,
                                  'translations': {'en': meaning},
                                  'pronunciation': {'scheme': 'pinyin', 'value': previous}}]))
    result = run_cli(app_config, 'scripts/curriculum/apply_vocabulary_corrections.py',
                     '--input', str(source), '--output', str(output))
    assert result.returncode == 0, result.stderr
    rows = json.loads(output.read_text())
    assert rows == [{**json.loads(source.read_text())[0],
                     'pronunciation': {'scheme': 'pinyin', 'value': corrected}}]
