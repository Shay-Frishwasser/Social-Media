"""on-demand photo briefs without brand-profile mutation"""
from alembic import op
from brandai.db.brand_models import OnDemandPhotoBrief

revision = "0003_on_demand_photo_briefs"
down_revision = "0002_source_payload"
branch_labels = None
depends_on = None

def upgrade():
    OnDemandPhotoBrief.__table__.create(op.get_bind(), checkfirst=True)

def downgrade():
    OnDemandPhotoBrief.__table__.drop(op.get_bind(), checkfirst=True)
