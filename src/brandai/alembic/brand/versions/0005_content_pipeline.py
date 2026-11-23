"""brand-local content lifecycle and immutable content revisions"""

from alembic import op

from brandai.db.brand_models import ContentItem, ContentRevision
from brandai.db.schema import protect_revision_table

revision = "0005_content_pipeline"
down_revision = "0004_strategy_cadence"
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    ContentItem.__table__.create(connection, checkfirst=True)
    ContentRevision.__table__.create(connection, checkfirst=True)
    protect_revision_table(connection, "content_revisions")
    connection.exec_driver_sql(
        "CREATE TRIGGER IF NOT EXISTS immutable_content_revisions_replace "
        "BEFORE INSERT ON content_revisions WHEN EXISTS ("
        "SELECT 1 FROM content_revisions WHERE id = new.id OR "
        "(content_id = new.content_id AND version = new.version)) "
        "BEGIN SELECT RAISE(ABORT, 'content_revisions is immutable'); END"
    )


def downgrade():
    connection = op.get_bind()
    ContentRevision.__table__.drop(connection, checkfirst=True)
    ContentItem.__table__.drop(connection, checkfirst=True)
