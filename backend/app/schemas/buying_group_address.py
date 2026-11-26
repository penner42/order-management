"""Validated U.S. checkout address fields."""
from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints

RequiredLine = Annotated[str, StringConstraints(min_length=1, max_length=200, pattern=r"^[^\r\n]+$")]
OptionalLine = Annotated[str, StringConstraints(max_length=200, pattern=r"^[^\r\n]*$")]
State = Annotated[str, StringConstraints(pattern=r"^[A-Z]{2}$")]
ZipCode = Annotated[str, StringConstraints(pattern=r"^[0-9]{5}(-[0-9]{4})?$")]
ZipExtension = Annotated[str, StringConstraints(pattern=r"^([0-9]{4})?$")]


class CheckoutAddress(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    recipient: OptionalLine = ""
    attention: OptionalLine = ""
    address_line1: RequiredLine
    address_line2: OptionalLine = ""
    city: RequiredLine
    state: State
    postal_code: ZipCode


class ShippingAddressDetails(CheckoutAddress):
    label: RequiredLine
    alternate_city: OptionalLine = ""
    zip_extension: ZipExtension = ""


class SavedVariantDetails(CheckoutAddress):
    label: RequiredLine


class SavedVariantRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    details: SavedVariantDetails


class ShippingAddressRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    details: ShippingAddressDetails
    variants: list[SavedVariantRead]
