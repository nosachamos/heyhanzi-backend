"""The two added exact senses must be visible before their grammar is used."""
import hashlib
import json
from pathlib import Path

import psycopg2
import pytest

from app.main import create_app
from tests.test_auth_flows import browser

ROOT = Path(__file__).resolve().parents[1]
UI = ROOT.parent / 'heyhanzi-ui-web'
SEED = ROOT / 'alembic/data/0022_notebook_grammar_vocabulary.json'
TRACK = 'mandarin-simplified-v1'


@pytest.mark.integration
def test_grammar_senses_are_visible_without_displacing_prior_introductions(postgres_config, run_cli):
    result = run_cli(postgres_config, 'scripts/bootstrap_database.py')
    assert result.returncode == 0, result.stderr
    rows = json.loads(SEED.read_text())
    prior_path = UI / 'artifacts/learning/story-first-page-exposure-audit.json'
    prior = json.loads(prior_path.read_text()) if prior_path.exists() else {'first_display': {}}
    with psycopg2.connect(postgres_config['DATABASE']['Url']) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT b.id,b.sequence FROM learning_word_blocks b JOIN learning_stages s ON s.id=b.stage_id "
                           "WHERE s.track_id=%s AND b.kind='word' ORDER BY b.sequence", (TRACK,))
            cards = cursor.fetchall()
    audit = {'introduction_sha256': hashlib.sha256(SEED.read_bytes()).hexdigest(), 'first_display': {}, 'changed_cards': []}
    with browser(create_app(postgres_config)) as client:
        for word in rows:
            block, sequence = word['introduction_block_id'], word['introduction_sequence']
            result = client.post('/learning/guest/lesson', json={'trackId': TRACK, 'completed': [id for id, n in cards if n <= sequence],
                                 'exposures': {}, 'operation': 'vocabulary', 'blockId': block})
            assert result.status_code == 200, result.text
            visible = {item['id']: item for item in result.json()['items']}
            assert word['id'] in visible
            assert visible[word['id']]['translations'] == word['translations']
            assert visible[word['id']]['pronunciation']['value'] == word['pronunciation']['value']
            former = {key for key, value in prior['first_display'].items() if value['block'] == block}
            assert former <= visible.keys(), ('Existing first-page introductions displaced', former - visible.keys())
            audit['first_display'][word['id']] = {'block': block, 'sequence': sequence, 'text': word['text'],
                                                   'translations': visible[word['id']]['translations'],
                                                   'pronunciation': visible[word['id']]['pronunciation']}
            audit['changed_cards'].append({'block': block, 'retained_prior_sources': sorted(former), 'visible_sources': sorted(visible)})
        # The structural particle is not exposed merely from its earlier familiar character.
        early = next((id, n) for id, n in cards if id.endswith('char-6240'))
        result = client.post('/learning/guest/lesson', json={'trackId': TRACK, 'completed': [id for id, n in cards if n <= early[1]],
                             'exposures': {}, 'operation': 'vocabulary', 'blockId': early[0]})
        assert result.status_code == 200
        assert 'course-lexeme-W-SYL-4854' not in {item['id'] for item in result.json()['items']}
    artifact = UI / 'artifacts/learning/notebook-grammar-vocabulary-audit.json'
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + '\n')
