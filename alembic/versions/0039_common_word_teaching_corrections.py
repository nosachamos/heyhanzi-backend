"""Require current scope displays for corrected existing lexical identities."""
import json
from pathlib import Path

import sqlalchemy as sa

from alembic import op

revision = '0039_common_word_teaching_corrections'
down_revision = '0038_lexical_use_exposures'
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    metadata = sa.MetaData()
    metadata.reflect(connection, only=['learning_vocabulary'])
    vocabulary = metadata.tables['learning_vocabulary']
    path = Path(__file__).resolve().parents[1] / 'data/0039_common_word_teaching_corrections.json'
    for item in json.loads(path.read_text())['items']:
        current = connection.execute(sa.select(vocabulary).where(
            vocabulary.c.id == item['id'])).mappings().one()
        data = dict(current['language_data'] or {})
        if (current['text'] != item['text']
                or current['pronunciation'] != {'scheme': 'pinyin', 'value': item['reading']}
                or current['translations']['en'] != item['old']
                or current['sense_key'] or data.get('lexical_uses')
                or data.get('introduction_block_id') != item['introduction_block_id']
                or data.get('introduction_sequence') != item['introduction_sequence']):
            raise RuntimeError('Pinned existing lexical teaching source changed: ' + item['id'])
        # Keep raw translations, sense key, reading and every old receipt. The
        # existing lexical-use reference now hashes the new revision and scope;
        # only an actual display can supply that new fingerprint to a learner.
        data.update(core_vocabulary_revision=item['revision'],
            lexical_uses=item['lexical_uses'],
            meaning_correction_provenance={'policy': 'existing-identity-teaching-correction-v1',
                'previous_translations': current['translations'],
                'current_english_heading': item['new']})
        connection.execute(vocabulary.update().where(vocabulary.c.id == item['id']).values(language_data=data))


def downgrade():
    raise RuntimeError('Current teaching and prior display history are retained; use a forward migration.')
