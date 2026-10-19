from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, EmailStr, Field, field_validator

Mode = Literal["flight", "train", "bus"]


class RegisterIn(BaseModel):
    email: EmailStr
    name: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=8, max_length=128)


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserOut(BaseModel):
    id: str
    email: str
    name: str


class _OfferBase(BaseModel):
    offer_id: str
    provider: str
    price: int  # minor units, total for `pax` travellers
    currency: str
    pax: int
    expires_at: datetime


class TransportOffer(_OfferBase):
    kind: Mode
    carrier: str
    number: str
    origin: str
    destination: str
    departure: datetime
    arrival: datetime
    duration_min: int
    stops: int = 0


class HotelOffer(_OfferBase):
    kind: Literal["hotel"] = "hotel"
    name: str
    city: str
    stars: int
    rating: float
    check_in: date
    check_out: date
    nights: int
    price_per_night: int
    amenities: list[str]


class Warning_(BaseModel):
    provider: str
    problem: Literal["timeout", "unavailable", "error"]


class TransportSearchOut(BaseModel):
    offers: list[TransportOffer]
    warnings: list[Warning_]


class HotelSearchOut(BaseModel):
    offers: list[HotelOffer]
    warnings: list[Warning_]


class PassengerIn(BaseModel):
    title: Literal["mr", "ms", "mrs", "miss", "mx"]
    gender: Literal["m", "f"]
    given_name: str = Field(min_length=1, max_length=60)
    family_name: str = Field(min_length=1, max_length=60)
    born_on: date

    @field_validator("born_on")
    @classmethod
    def _not_future(cls, v: date) -> date:
        if v > date.today():
            raise ValueError("born_on cannot be in the future")
        return v


class ContactIn(BaseModel):
    email: EmailStr
    phone: str = Field(pattern=r"^\+[1-9]\d{6,14}$", description="E.164, e.g. +919876543210")


class CheckoutIn(BaseModel):
    offer_ids: list[str] = Field(min_length=1, max_length=5)


class PaymentIn(BaseModel):
    payment_id: str = Field(min_length=1, max_length=64)
    signature: str = Field(min_length=1, max_length=256)


class ConfirmIn(BaseModel):
    payment: PaymentIn | None = None  # required by real gateways, ignored by the demo gateway
    passengers: list[PassengerIn] = Field(min_length=1, max_length=9)
    contact: ContactIn


class CheckoutOut(BaseModel):
    id: str
    status: str
    amount: int
    currency: str
    gateway: str
    gateway_order_id: str | None
    gateway_key_id: str | None = None
    offers: list[dict]


class PassengerOut(BaseModel):
    given_name: str
    family_name: str


class BookingOut(BaseModel):
    id: str
    reference: str
    kind: str
    provider: str
    checkout_id: str
    status: str
    failure_reason: str | None
    total_price: int
    currency: str
    offer: dict
    passengers: list[PassengerOut]
    created_at: datetime

    model_config = {"from_attributes": True}


class ConfirmOut(BaseModel):
    checkout_id: str
    status: str
    bookings: list[BookingOut]


class TripPlanIn(BaseModel):
    origin: str = Field(min_length=2, max_length=64)
    destination: str = Field(min_length=2, max_length=64)
    start: date
    end: date
    travelers: int = Field(default=1, ge=1, le=9)
    mode: Mode = "flight"
    budget: int | None = Field(default=None, gt=0, description="Major units of the offers' currency")
    stars: int = Field(default=3, ge=1, le=5)


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=2000)


class ChatOut(BaseModel):
    reply: str
    cards: list[dict] = []
