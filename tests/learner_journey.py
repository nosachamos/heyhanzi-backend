"""Public learner actions used when a test continues beyond required warm-up stories."""

import json
from pathlib import Path
from uuid import uuid4


def complete_occurrence(client, instance_id):
    """Perform authored public actions; paper/speech remain explicit self-reports."""
    response = client.get('/learning/course/exercises/' + instance_id)
    assert response.status_code == 200, response.text
    item = response.json()['exercise']
    run = str(uuid4())
    common = {key: item[key] for key in ['revision', 'fingerprint']}
    response = client.put('/learning/course/runs/' + run, json={**common,
        'instanceId': instance_id, 'prepared': True, 'draft': [], 'supports': []})
    assert response.status_code == 200, response.text
    response = client.get('/learning/course/exercises/' + instance_id, params={'run_id': run})
    assert response.status_code == 200, response.text
    definition = response.json()['exercise']['definition']

    def check(answer, supports=()):
        result = client.post('/learning/course/runs/' + run + '/checks', json={**common,
            'attemptId': str(uuid4()), 'answer': answer, 'supports': list(supports)})
        assert result.status_code == 200, result.text

    for step in definition['steps']:
        if step['kind'] == 'reading':
            response = client.put('/learning/course/runs/' + run, json={**common,
                'instanceId': instance_id, 'prepared': True, 'draft': [step['id']], 'supports': []})
            assert response.status_code == 200, response.text
            continue
        prefix = ['set-v1', step['id']]
        if definition.get('setActivity'):
            if step['kind'] == 'stroke':
                check([*prefix, 'stroke', 'paper', json.dumps({'evidence': 'paper', 'corrections': 0})])
            for question in step.get('questions', []):
                supports = ['audio-heard'] if question.get('audio') or question.get('listeningRequired') else []
                if question['kind'] == 'choice':
                    mode, value = 'choice', next(c['id'] for c in question['choices'] if c['correct'])
                elif question['kind'] == 'production':
                    supports += ['review:' + r['id'] + ':met' for r in question['rubric']]
                    if question['response'] == 'spoken':
                        mode, value = 'unrecorded', ''
                        supports.append('practiced-aloud')
                    else:
                        mode, value = 'hanzi', question.get('model', {}).get('text') or '我' * question.get('minLength', 1)
                else:
                    answer = question['accepted'][0]
                    mode, value = answer.get('mode', 'hanzi'), answer['value']
                check([*prefix, question['id'], mode, value], supports)
            check([*prefix, 'advance'])
        elif step['kind'] == 'check':
            for question in step['questions']:
                answer = ([question['id'], next(c['id'] for c in question['choices'] if c['correct'])]
                    if question['kind'] == 'choice' else question['solutions'][0])
                check(answer)
    assert instance_id in client.get('/learning/course').json()['completed']


def complete_required_before(client, sequence):
    """Follow current required trail steps before a later notebook visit."""
    if isinstance(sequence, str):
        order = json.loads((Path(__file__).resolve().parents[1] / 'alembic/data/0036_notebook_order.json').read_text())['cards']
        sequence = (max(row['sequence'] for row in order if row['levelId'] == 'warmup') + 1
            if sequence == 'warmup-end' else next(row['sequence'] for row in order if row['blockId'] == sequence))
    for section in ['welcome', 'sounds', 'tones', 'spelling']:
        response = client.post('/learning/tracks/mandarin-simplified-v1/pinyin/' + section + '/visit')
        assert response.status_code == 200, response.text
    document = json.loads((Path(__file__).resolve().parents[1] / 'app/data/learning/course-exercises.json').read_text())
    policy = document['learner_policy']
    targets = [item['id'] for item in document['exercises'] if item['id'] not in policy['optional_exercise_ids']
        and policy['trail_placements'][item['id']]['afterSequence'] < sequence]
    for _ in range(300):
        state = client.get('/learning/course').json()
        pending = [id for id in targets if id not in state['completed']
            and (id in state['eligible'] or id in state['grammarReviews'])]
        if not pending:
            return
        id = pending[0]
        review = state['grammarReviews'].get(id)
        if review:
            url = f"/learning/tracks/mandarin-simplified-v1/blocks/{review['blockId']}/vocabulary"
            cursor = None
            for _ in range(100):
                response = client.get(url, params={'limit': 40, **({'after': cursor} if cursor else {})})
                assert response.status_code == 200, response.text
                page = response.json()
                payload = {'vocabularyIds': [w['id'] for w in page['items']],
                    'grammar': [{key: n[key] for key in ['id', 'fingerprint']} for n in page['grammarNotes']],
                    'lexicalUses': [w['lexicalUseReference'] for w in page['items'] if w.get('lexicalUseReference')]}
                result = client.post(url + '/seen', json=payload)
                assert result.status_code == 200, result.text
                cursor = page['nextCursor']
                if not cursor:
                    break
        else:
            complete_occurrence(client, id)
    raise AssertionError('Current required trail did not converge')


def complete_required_warmup_stories(client):
    # Stories now share their main trail with the other approved required types.
    complete_required_before(client, 'warmup-end')
