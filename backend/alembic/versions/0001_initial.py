"""Initial workbench schema."""
from alembic import op
from sqlalchemy import text
from quantdesk.db import Base
revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    Base.metadata.create_all(op.get_bind())
    op.execute(text("INSERT INTO runtime (id, paused, initialized, epoch, version, config, secrets) VALUES (1, true, false, 0, 0, '{}', '')"))


def downgrade():
    Base.metadata.drop_all(op.get_bind())
