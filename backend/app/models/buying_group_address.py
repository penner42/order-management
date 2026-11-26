"""Shipping addresses and saved variants belonging to buying groups."""
from sqlalchemy import JSON, Column, ForeignKey, Integer
from sqlalchemy.orm import relationship

from app.database import Base


class BuyingGroupAddress(Base):
    __tablename__ = "buying_group_addresses"

    id = Column(Integer, primary_key=True)
    buying_group_id = Column(Integer, ForeignKey("buying_groups.id", ondelete="CASCADE"),
                             nullable=False, index=True)
    details = Column(JSON, nullable=False)
    variants = relationship(
        "BuyingGroupAddressVariant", order_by="BuyingGroupAddressVariant.id",
        cascade="all, delete-orphan", passive_deletes=True, lazy="selectin",
    )


class BuyingGroupAddressVariant(Base):
    __tablename__ = "buying_group_address_variants"

    id = Column(Integer, primary_key=True)
    address_id = Column(Integer, ForeignKey("buying_group_addresses.id", ondelete="CASCADE"),
                        nullable=False, index=True)
    details = Column(JSON, nullable=False)
