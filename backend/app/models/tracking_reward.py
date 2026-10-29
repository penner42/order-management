"""One warehouse reward per tracking identity and provider account."""
from sqlalchemy import Column, DateTime, ForeignKey, Integer, JSON, Numeric, String, UniqueConstraint

from app.database import Base


class TrackingReward(Base):
    __tablename__ = "tracking_rewards"

    id = Column(Integer, primary_key=True)
    buying_group_id = Column(Integer, ForeignKey("buying_groups.id", ondelete="CASCADE"), nullable=False, index=True)
    provider_identity = Column(String(64), nullable=False)
    tracking_key = Column(String(255), nullable=False)
    tracking_number = Column(String(255), nullable=False)
    aliases = Column(JSON, nullable=False)
    amount = Column(Numeric(12, 2), nullable=False)
    receipt_id = Column(String(255), nullable=True)
    observed_at = Column(DateTime(timezone=True), nullable=True)
    __table_args__ = (UniqueConstraint("buying_group_id", "provider_identity", "tracking_key",
                                      name="uq_tracking_reward_account_key"),)
