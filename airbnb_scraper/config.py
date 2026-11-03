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
	# Proxy DataImpulse (second fournisseur) : gw.dataimpulse.com, port 823 pour une IP qui change a chaque requete,
	# ports 10000 a 20000 pour une session fixe (jusqu'a 120 min) ; le pays se met dans l'identifiant (`__cr.fr`).
	dataimpulse_user: str | None
	dataimpulse_pass: str | None
	dataimpulse_host: str
	dataimpulse_port: int
	dataimpulse_country: str | None
	dataimpulse_sticky_min: int
	dataimpulse_sticky_max: int
	# Fournisseur par defaut quand le task_key ne le precise pas : "iproyal" ou "dataimpulse".
	proxy_provider: str
	# Cache TTL.
	cache_calendar_ttl_hours: float
	cache_api_key_ttl_hours: float
	cache_details_ttl_hours: float
	cache_max_entries: int
	log_level: str

	@property
	def has_iproyal(self) -> bool:
		return bool(self.iproyal_user and self.iproyal_pass)

	@property
	def has_dataimpulse(self) -> bool:
		return bool(self.dataimpulse_user and self.dataimpulse_pass)

	@property
	def has_proxy(self) -> bool:
		return self.has_iproyal or self.has_dataimpulse


def load_settings() -> Settings:
	return Settings(
		api_key=os.getenv("API_KEY", ""),
		iproyal_user=os.getenv("IPROYAL_USER"),
		iproyal_pass=os.getenv("IPROYAL_PASS"),
		iproyal_host=os.getenv("IPROYAL_HOST", "geo.iproyal.com"),
		iproyal_port=int(os.getenv("IPROYAL_PORT", "12321")),
		iproyal_country=os.getenv("IPROYAL_COUNTRY") or None,
		iproyal_lifetime=os.getenv("IPROYAL_LIFETIME", "30m"),
		dataimpulse_user=os.getenv("DATAIMPULSE_USER"),
		dataimpulse_pass=os.getenv("DATAIMPULSE_PASS"),
		dataimpulse_host=os.getenv("DATAIMPULSE_HOST", "gw.dataimpulse.com"),
		dataimpulse_port=int(os.getenv("DATAIMPULSE_PORT", "823")),
		dataimpulse_country=os.getenv("DATAIMPULSE_COUNTRY", "fr") or None,
		dataimpulse_sticky_min=int(os.getenv("DATAIMPULSE_STICKY_MIN", "10000")),
		dataimpulse_sticky_max=int(os.getenv("DATAIMPULSE_STICKY_MAX", "20000")),
		proxy_provider=os.getenv("PROXY_PROVIDER", "iproyal").strip().lower(),
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
