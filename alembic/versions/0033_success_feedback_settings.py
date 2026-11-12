"""Save named success studies and an explicitly activated immutable snapshot."""
import sqlalchemy as sa

from alembic import op

revision = '0033_success_feedback_settings'
down_revision = '0032_audio_review_decisions'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('success_feedback_presets',
        sa.Column('id', sa.String(64), primary_key=True),
        sa.Column('name', sa.String(80), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('configuration', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.BigInteger(), nullable=False),
        sa.Column('updated_at', sa.BigInteger(), nullable=False),
        sa.Column('updated_by', sa.String(36), sa.ForeignKey('users.id', ondelete='SET NULL')),
        sa.CheckConstraint('version > 0', name=op.f('ck_success_feedback_presets_positive_version')))
    op.create_table('success_feedback_state',
        sa.Column('id', sa.String(16), primary_key=True),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('active_snapshot', sa.JSON(), nullable=True),
        sa.Column('updated_at', sa.BigInteger(), nullable=False),
        sa.Column('activated_by', sa.String(36), sa.ForeignKey('users.id', ondelete='SET NULL')),
        sa.CheckConstraint("id = 'global'", name=op.f('ck_success_feedback_state_singleton')),
        sa.CheckConstraint('version > 0', name=op.f('ck_success_feedback_state_positive_version')))
    # Frozen initial configuration. Future source default changes must not
    # overwrite an administrator's saved studies or an activated selection.
    preset = {
        'id': 'default', 'name': 'Selected celebration', 'version': 1,
        'configuration': {
            'small': {'animation': 'silk', 'sound': 'selected-success'},
            'step': {'animation': 'wave', 'sound': 'selected-success'},
            'complete': {'animation': 'wave', 'sound': 'selected-success'},
        },
        'createdAt': 0, 'updatedAt': 0,
    }
    op.bulk_insert(sa.table('success_feedback_presets',
        sa.column('id', sa.String()), sa.column('name', sa.String()),
        sa.column('version', sa.Integer()), sa.column('configuration', sa.JSON()),
        sa.column('created_at', sa.BigInteger()), sa.column('updated_at', sa.BigInteger())),
        [{'id': preset['id'], 'name': preset['name'], 'version': 1,
          'configuration': preset['configuration'], 'created_at': 0, 'updated_at': 0}])
    op.bulk_insert(sa.table('success_feedback_state',
        sa.column('id', sa.String()), sa.column('version', sa.Integer()),
        sa.column('active_snapshot', sa.JSON()), sa.column('updated_at', sa.BigInteger())),
        [{'id': 'global', 'version': 1, 'active_snapshot': preset, 'updated_at': 0}])


def downgrade():
    raise RuntimeError('Saved feedback studies and live settings are permanent; use a forward migration.')
