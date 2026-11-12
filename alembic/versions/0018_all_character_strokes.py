"""Connect the complete supported Mandarin inventory to immutable geometry.

Resources are read and inserted in bounded batches. Only stroke references change;
the curriculum, pronunciation, identifiers and learner history are untouched.
"""
import hashlib
import json
from pathlib import Path

import sqlalchemy as sa

from alembic import op
from app.config_loader import env

revision = '0018_all_character_strokes'
down_revision = '0017_billing'
branch_labels = None
depends_on = None

DATA = Path(__file__).resolve().parents[1] / 'data/0018_strokes'


def upgrade():
    connection = op.get_bind()
    strokes = sa.table('learning_character_strokes', sa.column('language_tag', sa.String),
                       sa.column('character', sa.String), sa.column('version', sa.String),
                       sa.column('data', sa.JSON))
    words = sa.table('learning_word_blocks', sa.column('id', sa.String),
                     sa.column('text', sa.String), sa.column('language_data', sa.JSON))
    manifest = json.loads((DATA / 'manifest.json').read_text(encoding='utf-8'))
    entries = manifest['entries']
    language = manifest['languageTag']
    if (manifest['format'] != 'character-stroke-import-v1' or language != 'cmn-Hans-CN'
            or len(entries) != manifest['characterCount'] or manifest['characterCount'] != 1940
            or len({entry['character'] for entry in entries}) != 1940):
        raise RuntimeError('Invalid complete character stroke manifest')

    existing = set(connection.execute(sa.select(strokes.c.language_tag, strokes.c.character,
                                                strokes.c.version)).all())
    references, pending = {}, []
    for entry in entries:
        character, version = entry['character'], entry['version']
        key = (language, character, version)
        references[character] = {'format': 'stroke-reference-v1', 'languageTag': language,
                                 'character': character, 'version': version}
        if entry['file'] is None:
            if key not in existing:
                raise RuntimeError(f'Missing previously published stroke resource: {character}')
            continue
        if entry['file'] != f'{ord(character):x}.json':
            raise RuntimeError(f'Invalid stroke payload filename: {character}')
        data = json.loads((DATA / entry['file']).read_text(encoding='utf-8'))
        digest = hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True,
                                           separators=(',', ':')).encode()).hexdigest()
        if (digest != version or data['character'] != character or data['format'] != 'hanzi-writer-v1'
                or not data['strokes'] or len(data['strokes']) != len(data['medians'])
                or any(len(median) < 2 for median in data['medians'])):
            raise RuntimeError(f'Invalid stroke resource: {character}')
        if key not in existing:
            pending.append(dict(language_tag=language, character=character, version=version, data=data))
        if len(pending) == 25:
            connection.execute(strokes.insert(), pending)
            pending.clear()
    if pending:
        connection.execute(strokes.insert(), pending)

    # Retain real previously published references, including future forward repairs.
    # The old illustrative 人 sample is the sole legacy replacement; its immutable
    # resource remains stored, along with every other historical resource.
    updates = []
    statement = words.update().where(words.c.id == sa.bindparam('block_id')).values(
        language_data=sa.bindparam('metadata'))
    for rows in connection.execute(sa.select(words)).mappings().partitions(100):
        for row in rows:
            metadata = dict(row['language_data'])
            if metadata.get('kind') != 'mandarin-simplified':
                continue
            preserved = {}
            for reference in metadata.get('strokeOrder', []):
                resource = connection.execute(sa.select(strokes.c.data).where(
                    strokes.c.language_tag == reference['languageTag'],
                    strokes.c.character == reference['character'],
                    strokes.c.version == reference['version'])).scalar_one()
                if resource.get('format') == 'hanzi-writer-v1':
                    preserved[reference['character']] = reference
            characters = list(dict.fromkeys(row['text']))
            if any(character not in references for character in characters):
                raise RuntimeError(f'Missing character geometry for block {row["id"]}')
            metadata['strokeOrder'] = [preserved.get(character, references[character])
                                       for character in characters]
            if metadata != row['language_data']:
                updates.append({'block_id': row['id'], 'metadata': metadata})
        if updates:
            connection.execute(statement, updates)
            updates.clear()


def downgrade():
    if env.config['GENERAL']['Environment'] != 'TEST':
        raise RuntimeError('Published stroke resources are permanent. Use a forward migration.')
    # Disposable schema rehearsals can proceed through earlier migrations, whose
    # table drops own cleanup. Never invalidate references by deleting resources.
