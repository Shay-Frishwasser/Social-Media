"""brand-local publishing, encrypted connections and persistent jobs"""

from alembic import op

from brandai.db.brand_models import BackgroundJob, PublishRecord, SocialConnection

revision = "0007_publishing_and_jobs"
down_revision = "0006_research_and_visual_assets"
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    for model in (SocialConnection, PublishRecord, BackgroundJob):
        model.__table__.create(connection, checkfirst=True)


def downgrade():
    connection = op.get_bind()
    for model in (BackgroundJob, PublishRecord, SocialConnection):
        model.__table__.drop(connection, checkfirst=True)
