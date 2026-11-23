"""brand-local playbook overrides and planned content slots"""
from alembic import op

from brandai.db.brand_models import PlannedContentSlot, PlatformRuleOverride

revision = "0004_strategy_cadence"
down_revision = "0003_on_demand_photo_briefs"
branch_labels = None
depends_on = None


def upgrade():
    PlatformRuleOverride.__table__.create(op.get_bind(), checkfirst=True)
    PlannedContentSlot.__table__.create(op.get_bind(), checkfirst=True)


def downgrade():
    PlannedContentSlot.__table__.drop(op.get_bind(), checkfirst=True)
    PlatformRuleOverride.__table__.drop(op.get_bind(), checkfirst=True)
