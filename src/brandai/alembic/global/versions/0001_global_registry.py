"""global registry baseline"""
from alembic import op
from brandai.db.base import GlobalBase
from brandai.db import global_models  # noqa: F401

revision = "0001_global"
down_revision = None
branch_labels = None
depends_on = None

def upgrade():
    GlobalBase.metadata.create_all(op.get_bind())

def downgrade():
    GlobalBase.metadata.drop_all(op.get_bind())
