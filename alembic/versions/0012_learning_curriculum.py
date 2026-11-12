"""Add character-first levels, units, reading markers and lexical prerequisites.

Existing enrollment, word IDs, media references and completion records survive.
New level content is a prototype curriculum based on the pinned 2025 character
lists; eligible vocabulary is not automatically recorded as learned vocabulary.
"""
import json
from pathlib import Path

import sqlalchemy as sa

from alembic import op
from app.config_loader import env

revision = '0012_learning_curriculum'
down_revision = '0011_ni_hao_media'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('learning_tracks', sa.Column('curriculum', sa.JSON))
    op.add_column('learning_stages', sa.Column('unit_number', sa.Integer))
    op.add_column('learning_word_blocks', sa.Column('kind', sa.String(32), nullable=False, server_default='word'))
    op.add_column('learning_word_blocks', sa.Column('sequence', sa.Integer))
    op.create_index('ix_learning_word_blocks_sequence', 'learning_word_blocks', ['sequence'])
    op.create_table('learning_levels',
        sa.Column('id', sa.String(80), primary_key=True),
        sa.Column('track_id', sa.String(64), sa.ForeignKey('learning_tracks.id'), nullable=False),
        sa.Column('key', sa.String(32), nullable=False), sa.Column('names', sa.JSON, nullable=False),
        sa.Column('position', sa.Integer, nullable=False),
        sa.UniqueConstraint('track_id', 'key', name='uq_learning_levels_track_key'), sa.UniqueConstraint('track_id', 'position', name='uq_learning_levels_track_position'))
    op.create_index('ix_learning_levels_track_id', 'learning_levels', ['track_id'])
    op.create_table('learning_block_characters',
        sa.Column('block_id', sa.String(96), sa.ForeignKey('learning_word_blocks.id'), primary_key=True),
        sa.Column('character', sa.String(8), primary_key=True))
    op.create_table('learning_vocabulary',
        sa.Column('id', sa.String(96), primary_key=True), sa.Column('language_tag', sa.String(32), nullable=False),
        sa.Column('text', sa.String(128), nullable=False), sa.Column('translations', sa.JSON, nullable=False),
        sa.Column('pronunciation', sa.JSON, nullable=False), sa.Column('language_data', sa.JSON, nullable=False),
        sa.Column('level', sa.String(32)), sa.Column('rank', sa.BigInteger, nullable=False))
    op.create_index('ix_learning_vocabulary_language_tag', 'learning_vocabulary', ['language_tag'])
    op.create_table('learning_vocabulary_characters',
        sa.Column('vocabulary_id', sa.String(96), sa.ForeignKey('learning_vocabulary.id'), primary_key=True),
        sa.Column('character', sa.String(8), primary_key=True))
    op.create_index('ix_learning_vocabulary_characters_character', 'learning_vocabulary_characters', ['character'])
    connection = op.get_bind()
    metadata = sa.MetaData()
    metadata.reflect(connection, only=['learning_tracks', 'learning_stages', 'learning_word_blocks', 'learning_levels',
                                      'learning_block_characters', 'learning_vocabulary', 'learning_vocabulary_characters'])
    tables = metadata.tables
    track_id = 'mandarin-simplified-v1'
    source = json.loads((Path(__file__).resolve().parents[1] / 'data/0012_curriculum.json').read_text())
    connection.execute(tables['learning_tracks'].update().where(tables['learning_tracks'].c.id == track_id).values(
        curriculum={'framework': 'HSK', 'edition': '2025-11', 'learningUnit': 'character', 'unitSize': 10, 'storyEvery': 5,
                    'sourceRevision': source['provenance']['revision']}))
    levels = [{'id': f'{track_id}-{key}', 'track_id': track_id, 'key': key, 'names': names, 'position': position}
              for position, (key, names) in enumerate([('warmup', {'en': 'Warm-up', 'zh': '热身'})] +
                  [(f'hsk-{n}', {'en': f'HSK {n}', 'zh': f'HSK {n}'}) for n in range(1, 7)])]
    connection.execute(tables['learning_levels'].insert(), levels)
    block_characters, rows, stages = [], [], []
    sequence = 0

    def story(stage_id, group, number, count, position):
        nonlocal sequence
        sequence += 1
        rows.append({'id': f'{stage_id}-story-{number}', 'stage_id': stage_id, 'position': position,
                     'kind': 'story', 'sequence': sequence, 'text': '📖',
                     'translations': {'en': 'Reading stop', 'zh': '阅读站'},
                     'pronunciation': {'scheme': 'none', 'value': ''},
                     'language_data': {'kind': 'story-marker', 'planned': True, 'afterCount': count,
                                       'requiredBlockIds': group}})

    warmup_stage = f'{track_id}-warmup'
    connection.execute(tables['learning_stages'].update().where(tables['learning_stages'].c.id == warmup_stage).values(unit_number=1))
    warmup = connection.execute(sa.select(tables['learning_word_blocks']).where(
        tables['learning_word_blocks'].c.stage_id == warmup_stage).order_by(tables['learning_word_blocks'].c.position)).mappings().all()
    group = []
    for number, word in enumerate(warmup, 1):
        sequence += 1
        connection.execute(tables['learning_word_blocks'].update().where(tables['learning_word_blocks'].c.id == word['id']).values(sequence=sequence))
        block_characters.extend({'block_id': word['id'], 'character': char} for char in set(word['language_data']['characters']))
        group.append(word['id'])
        if number % 5 == 0:
            story(warmup_stage, group, number // 5, number, 20 + number // 5)
            group = []
    stage_position = 1
    for level in range(1, 7):
        characters = [char for char in source['characters'] if char['level'] == level]
        for start in range(0, len(characters), 10):
            stage_position += 1
            unit = start // 10 + 1
            stage_id = f'{track_id}-hsk-{level}-unit-{unit}'
            stages.append({'id': stage_id, 'track_id': track_id, 'kind': 'unit', 'position': stage_position,
                           'level': f'hsk-{level}', 'unit_number': unit,
                           'names': {'en': f'Unit {unit}', 'zh': f'第 {unit} 单元'}})
            group = []
            items = characters[start:start + 10]
            for number, char in enumerate(items, 1):
                sequence += 1
                block_id = f'{track_id}-char-{ord(char["text"]):x}'
                rows.append({'id': block_id, 'stage_id': stage_id, 'position': number, 'kind': 'word', 'sequence': sequence,
                             'text': char['text'], 'translations': char['translations'], 'pronunciation': char['pronunciation'],
                             'language_data': {'kind': 'mandarin-simplified', 'teachingUnit': 'character',
                                              'pinyin': char['pronunciation']['value'], 'characters': [char['text']], 'strokeOrder': []}})
                block_characters.append({'block_id': block_id, 'character': char['text']})
                group.append(block_id)
                if number % 5 == 0 or number == len(items):
                    story(stage_id, group, (number - 1) // 5 + 1, number, 10 + (number - 1) // 5 + 1)
                    group = []
    connection.execute(tables['learning_stages'].insert(), stages)
    connection.execute(tables['learning_word_blocks'].insert(), rows)
    connection.execute(tables['learning_block_characters'].insert(), block_characters)
    vocabulary, prerequisites = [], []
    for word in source['vocabulary']:
        vocabulary.append({'id': word['id'], 'language_tag': 'cmn-Hans', 'text': word['text'], 'translations': word['translations'],
                           'pronunciation': word['pronunciation'], 'language_data': {'kind': 'mandarin-simplified', 'characters': word['characters']},
                           'level': f'hsk-{word["level"]}', 'rank': word['rank']})
        prerequisites.extend({'vocabulary_id': word['id'], 'character': char} for char in word['characters'])
    connection.execute(tables['learning_vocabulary'].insert(), vocabulary)
    connection.execute(tables['learning_vocabulary_characters'].insert(), prerequisites)


def downgrade():
    if env.config['GENERAL']['Environment'] != 'TEST':
        raise RuntimeError('Curriculum and learner progress are permanent. Use a forward migration.')
    # Rehearsal only: the release-schema suite owns a disposable TEST database.
    connection = op.get_bind()
    connection.execute(sa.text("DELETE FROM learning_word_completions WHERE block_id IN (SELECT id FROM learning_word_blocks WHERE kind = 'story' OR stage_id LIKE 'mandarin-simplified-v1-hsk-%')"))
    op.drop_table('learning_block_characters')
    op.drop_table('learning_vocabulary_characters')
    op.drop_table('learning_vocabulary')
    connection.execute(sa.text("DELETE FROM learning_word_blocks WHERE kind = 'story' OR stage_id LIKE 'mandarin-simplified-v1-hsk-%'"))
    connection.execute(sa.text("DELETE FROM learning_stages WHERE id LIKE 'mandarin-simplified-v1-hsk-%'"))
    op.drop_table('learning_levels')
    op.drop_index('ix_learning_word_blocks_sequence', table_name='learning_word_blocks')
    op.drop_column('learning_word_blocks', 'sequence')
    op.drop_column('learning_word_blocks', 'kind')
    op.drop_column('learning_stages', 'unit_number')
    op.drop_column('learning_tracks', 'curriculum')
