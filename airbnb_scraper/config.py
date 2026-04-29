"""Configuration centralisee, lue depuis env."""
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
	api_key: str
	# Proxy IPRoyal (passe a pyairbnb via proxy_url string).
	iproyal_user: str | None
	iproyal_pass: str | None
	iproyal_host: str
	iproyal_port: int
	iproyal_country: str | None
	iproyal_lifetime: str
	# Cache TTL.
	cache_calendar_ttl_hours: float
	cache_api_key_ttl_hours: float
	cache_details_ttl_hours: float
	cache_max_entries: int
	log_level: str

	@property
	def has_proxy(self) -> bool:
		return bool(self.iproyal_user and self.iproyal_pass)


def load_settings() -> Settings:
	return Settings(
		api_key=os.getenv("API_KEY", ""),
		iproyal_user=os.getenv("IPROYAL_USER"),
		iproyal_pass=os.getenv("IPROYAL_PASS"),
		iproyal_host=os.getenv("IPROYAL_HOST", "geo.iproyal.com"),
		iproyal_port=int(os.getenv("IPROYAL_PORT", "12321")),
		iproyal_country=os.getenv("IPROYAL_COUNTRY") or None,
		iproyal_lifetime=os.getenv("IPROYAL_LIFETIME", "30m"),
		# api_key Airbnb : token JS extrait depuis homepage. Change rarement.
		# 24h TTL = 1 fetch / jour au lieu de 1 / call.
		cache_api_key_ttl_hours=float(os.getenv("CACHE_API_KEY_TTL_HOURS", "24")),
		# calendar : 12h TTL comme booking-scraper. Idempotent multi-logements.
		cache_calendar_ttl_hours=float(os.getenv("CACHE_CALENDAR_TTL_HOURS", "12")),
		# details : changent tres rarement (amenities, photos). 7 jours OK.
		cache_details_ttl_hours=float(os.getenv("CACHE_DETAILS_TTL_HOURS", "168")),
		# Cache LRU bound.
		cache_max_entries=int(os.getenv("CACHE_MAX_ENTRIES", "2000")),
		log_level=os.getenv("LOG_LEVEL", "INFO"),
	)


SETTINGS = load_settings()
