"""Pydantic schemas pour les requests/responses de l'API."""
from __future__ import annotations

from datetime import date as _date
from datetime import timedelta as _td
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

# Tolere quelques jours de retard (TZ / horloge derive).
_PAST_DATE_TOLERANCE_DAYS = 2


def _validate_iso_date(value: str, field_name: str = "date") -> str:
	try:
		_date.fromisoformat(value)
	except (TypeError, ValueError) as e:
		raise ValueError(f"{field_name} must be ISO date YYYY-MM-DD, got {value!r}") from e
	return value


def _validate_not_too_far_past(value: str, field_name: str = "date") -> str:
	parsed = _date.fromisoformat(value)
	if parsed < _date.today() - _td(days=_PAST_DATE_TOLERANCE_DAYS):
		raise ValueError(f"{field_name} too far in the past: {value} (Airbnb doesn't expose past prices)")
	return value


# ---------------------------------------------------------------------------
# Requests
# ---------------------------------------------------------------------------


class SearchRequest(BaseModel):
	"""Recherche de listings dans une bbox GPS pour une fenetre check_in/check_out.

	Mirror de pyairbnb.search_all() avec validation stricte cote API.
	"""

	# bbox = bounding box GPS
	ne_lat: float = Field(ge=-90, le=90, description="North-East latitude")
	ne_long: float = Field(ge=-180, le=180, description="North-East longitude")
	sw_lat: float = Field(ge=-90, le=90, description="South-West latitude")
	sw_long: float = Field(ge=-180, le=180, description="South-West longitude")
	check_in: str | None = Field(default=None, description="ISO YYYY-MM-DD, optionnel")
	check_out: str | None = Field(default=None, description="ISO YYYY-MM-DD, optionnel")
	zoom_value: int = Field(default=2, ge=0, le=20)
	currency: str = Field(default="EUR", min_length=3, max_length=3)
	price_min: int = Field(default=0, ge=0)
	price_max: int = Field(default=0, ge=0)
	# Sticky session proxy : permet d'avoir la meme IP IPRoyal pour toutes
	# les requetes d'un meme cluster (ex compset d'un logement).
	task_key: str | None = None

	@field_validator("check_in", "check_out")
	@classmethod
	def _check_iso(cls, v: str | None, info) -> str | None:
		if v is None or v == "":
			return None
		v = _validate_iso_date(v, info.field_name)
		return _validate_not_too_far_past(v, info.field_name)

	@model_validator(mode="after")
	def _check_window(self):
		# Si dates fournies, check_out doit etre apres check_in
		if self.check_in and self.check_out:
			ci = _date.fromisoformat(self.check_in)
			co = _date.fromisoformat(self.check_out)
			if co <= ci:
				raise ValueError("check_out must be strictly after check_in")
		# bbox sanity
		if self.ne_lat <= self.sw_lat:
			raise ValueError("ne_lat must be > sw_lat")
		if self.ne_long <= self.sw_long:
			raise ValueError("ne_long must be > sw_long")
		return self


class HotelDetailsRequest(BaseModel):
	"""Details d'un listing : amenities, capacity, photos, host..."""

	room_id: str = Field(
		min_length=1,
		max_length=50,
		# Airbnb room_id = numerique (8-19 chiffres typiquement).
		pattern=r"^\d+$",
		description="Numeric Airbnb room_id (ex 764133092500775861)",
	)
	currency: str = Field(default="EUR", min_length=3, max_length=3)
	language: str = Field(default="fr", min_length=2, max_length=5)
	task_key: str | None = None


class HotelCalendarRequest(BaseModel):
	"""Calendrier 12 mois (dispo + min_nights, sans prix - Airbnb separe)."""

	room_id: str = Field(min_length=1, max_length=50, pattern=r"^\d+$")
	# api_key : si non fourni, le service le fetch via cache.
	api_key: str | None = Field(default=None, description="Airbnb api_key (sinon auto-fetch)")
	task_key: str | None = None


class HotelPriceRequest(BaseModel):
	"""Prix d'un listing pour une fenetre check_in/check_out."""

	room_id: str = Field(min_length=1, max_length=50, pattern=r"^\d+$")
	check_in: str
	check_out: str
	api_key: str | None = None
	currency: str = Field(default="EUR", min_length=3, max_length=3)
	adults: int = Field(default=1, ge=1, le=30)
	task_key: str | None = None

	@field_validator("check_in", "check_out")
	@classmethod
	def _check_iso(cls, v: str, info) -> str:
		v = _validate_iso_date(v, info.field_name)
		return _validate_not_too_far_past(v, info.field_name)

	@model_validator(mode="after")
	def _check_window(self):
		ci = _date.fromisoformat(self.check_in)
		co = _date.fromisoformat(self.check_out)
		if co <= ci:
			raise ValueError("check_out must be strictly after check_in")
		return self


class ApiKeyRequest(BaseModel):
	"""Force refresh de l'api_key (admin). Sinon utiliser le cache."""

	task_key: str | None = None


# ---------------------------------------------------------------------------
# Responses (passthrough = on retourne ce que pyairbnb renvoie + meta)
# ---------------------------------------------------------------------------


class HealthResponse(BaseModel):
	status: str
	version: str


class ApiKeyResponse(BaseModel):
	api_key: str
	cached: bool
	captured_at: str


class SearchResponse(BaseModel):
	listings: list[dict[str, Any]]
	count: int
	captured_at: str


class HotelDetailsResponse(BaseModel):
	room_id: str
	details: dict[str, Any]
	cached: bool
	captured_at: str


class HotelCalendarResponse(BaseModel):
	room_id: str
	calendar_months: list[dict[str, Any]]
	cached: bool
	captured_at: str


class HotelPriceResponse(BaseModel):
	room_id: str
	check_in: str
	check_out: str
	price: dict[str, Any]
	captured_at: str


class CacheStatsResponse(BaseModel):
	api_key: dict[str, Any]
	calendar: dict[str, Any]
	details: dict[str, Any]
