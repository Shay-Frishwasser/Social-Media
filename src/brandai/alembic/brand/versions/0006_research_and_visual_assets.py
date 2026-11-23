"""archived external quotations and brand-local visual asset exports"""

import sqlalchemy as sa
from alembic import op

from brandai.db.brand_models import ExternalEvidence, TopicResearch, VisualAsset
from brandai.db.schema import protect_revision_table

revision = "0006_research_and_visual_assets"
down_revision = "0005_content_pipeline"
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    for model in (TopicResearch, ExternalEvidence, VisualAsset):
        model.__table__.create(connection, checkfirst=True)
    columns = {column["name"] for column in sa.inspect(connection).get_columns("content_revisions")}
    if "research_json" not in columns:
        op.add_column("content_revisions", sa.Column("research_json", sa.JSON(), nullable=False, server_default="{}"))
    protect_revision_table(connection, "external_evidence")


def downgrade():
    connection = op.get_bind()
    op.drop_column("content_revisions", "research_json")
    for model in (VisualAsset, ExternalEvidence, TopicResearch):
        model.__table__.drop(connection, checkfirst=True)
