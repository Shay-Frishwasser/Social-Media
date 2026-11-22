"""per-brand storage and memory baseline"""
from alembic import op
from brandai.db.base import BrandBase
from brandai.db import brand_models  # noqa: F401
from brandai.db.schema import create_brand_schema

revision = "0001_brand"
down_revision = None
branch_labels = None
depends_on = None

def upgrade():
    create_brand_schema(op.get_bind())

def downgrade():
    connection = op.get_bind()
    connection.exec_driver_sql("DROP TABLE IF EXISTS source_chunks_fts")
    BrandBase.metadata.drop_all(connection)
