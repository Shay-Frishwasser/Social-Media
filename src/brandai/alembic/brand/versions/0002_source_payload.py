"""retain source payload and media type"""
from alembic import op
import sqlalchemy as sa

revision = "0002_source_payload"
down_revision = "0001_brand"
branch_labels = None
depends_on = None

def upgrade():
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("sources")}
    if "raw_content" not in columns:
        op.add_column("sources", sa.Column("raw_content", sa.Text(), nullable=True))
    if "media_type" not in columns:
        op.add_column("sources", sa.Column("media_type", sa.String(length=120), nullable=True))

def downgrade():
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("sources")}
    if "media_type" in columns: op.drop_column("sources", "media_type")
    if "raw_content" in columns: op.drop_column("sources", "raw_content")
