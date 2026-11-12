"""Add isolated billing accounts; existing learners start on Free."""
import sqlalchemy as sa

from alembic import op
from app.config_loader import env

revision = '0017_billing'
down_revision = '0016_practical_vocabulary'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('billing_accounts',
        sa.Column('user_id', sa.String(36), sa.ForeignKey('users.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('customer_id', sa.String(255), unique=True),
        sa.Column('subscription_id', sa.String(255), unique=True),
        sa.Column('status', sa.String(32), nullable=False),
        sa.Column('period_end', sa.BigInteger),
        sa.Column('cancel_at_period_end', sa.Boolean, nullable=False),
        sa.Column('checkout_id', sa.String(255)),
        sa.Column('checkout_locale', sa.String(2)),
        sa.Column('checkout_attempt', sa.BigInteger, nullable=False))


def downgrade():
    if env.config['GENERAL']['Environment'] != 'TEST':
        raise RuntimeError('Billing history is permanent. Use a forward migration.')
    op.drop_table('billing_accounts')
