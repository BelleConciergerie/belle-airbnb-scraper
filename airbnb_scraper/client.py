"""Wrappers async autour de pyairbnb (sync) avec caches.

pyairbnb est synchrone (curl_cffi). On utilise asyncio.to_thread() pour
executer les calls sans bloquer la boucle event. Permet d'absorber 10-20
requests concurrentes sans saturer.

Caches strategie :
- api_key : si dans cache et frais, return direct. Sinon fetch + cache.
  TTL 24h car api_key change rarement (rotation hebdo cote Airbnb au plus).
- calendar : key = (room_id, proxy_session_hash). TTL 12h. Idempotent
  multi-logements : 5 logements partagent le meme listing -> 1 fetch / 12h.
- details : key = room_id. TTL 7 jours. Amenities + photos changent peu.
- price : NON cache (varie par check_in/check_out, hit rate trop bas).

Erreurs : on laisse pyairbnb raise. FastAPI handler convertit en 502 bien
documente. Pas de retry interne (le caller belle-pricing fait deja les retry).
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import date as _date
from typing import Any

import pyairbnb

from .cache import get_api_key_cache, get_calendar_cache, get_details_cache
from .proxy import get_proxy_manager

log = logging.getLogger("airbnb_scraper.client")


def _proxy_hash(proxy_url: str | None) -> str:
	"""Hash court du proxy_url pour le cache key (pas le password en clair)."""
	if not proxy_url:
		return "no-proxy"
	# On hash juste les 30 derniers chars (qui contiennent la session-XXX).
	return hashlib.sha256(proxy_url[-100:].encode()).hexdigest()[:8]


# ---------------------------------------------------------------------------
# api_key
# ---------------------------------------------------------------------------


async def get_api_key(task_key: str | None = None, force_refresh: bool = False) -> tuple[str, bool]:
	"""Retourne (api_key, was_cached). Cache 24h par session proxy."""
	cache = get_api_key_cache()
	proxy_url = get_proxy_manager().get_proxy_url(task_key=task_key)
	cache_key = _proxy_hash(proxy_url)

	if not force_refresh:
		cached = await cache.get(cache_key)
		if cached:
			return cached, True

	api_key = await asyncio.to_thread(pyairbnb.get_api_key, proxy_url=proxy_url or "")
	if not api_key:
		raise RuntimeError("pyairbnb.get_api_key returned empty (Airbnb changed format?)")
	await cache.set(cache_key, api_key)
	return api_key, False


# ---------------------------------------------------------------------------
# search_all
# ---------------------------------------------------------------------------


async def search_all(
	ne_lat: float, ne_long: float, sw_lat: float, sw_long: float,
	check_in: str | None, check_out: str | None,
	zoom_value: int = 2, currency: str = "EUR",
	price_min: int = 0, price_max: int = 0,
	task_key: str | None = None,
) -> list[dict[str, Any]]:
	"""Search listings dans la bbox. Pas de cache (varie trop par dates/bbox)."""
	proxy_url = get_proxy_manager().get_proxy_url(task_key=task_key)
	# pyairbnb.search_all accepte check_in/out string ou None.
	listings = await asyncio.to_thread(
		pyairbnb.search_all,
		check_in=check_in or "",
		check_out=check_out or "",
		ne_lat=ne_lat, ne_long=ne_long,
		sw_lat=sw_lat, sw_long=sw_long,
		zoom_value=zoom_value,
		price_min=price_min, price_max=price_max,
		currency=currency,
		proxy_url=proxy_url or "",
	)
	return listings or []


# ---------------------------------------------------------------------------
# get_details
# ---------------------------------------------------------------------------


async def get_details(
	room_id: str, currency: str = "EUR", language: str = "fr",
	task_key: str | None = None,
) -> tuple[dict[str, Any], bool]:
	"""Retourne (details, was_cached). Cache 7j."""
	cache = get_details_cache()
	cache_key = f"{room_id}:{currency}:{language}"
	cached = await cache.get(cache_key)
	if cached is not None:
		return cached, True

	proxy_url = get_proxy_manager().get_proxy_url(task_key=task_key)
	details = await asyncio.to_thread(
		pyairbnb.get_details,
		room_id=room_id, currency=currency, language=language,
		proxy_url=proxy_url or "",
	)
	# pyairbnb.get_details retourne un dict riche
	if details:
		await cache.set(cache_key, details)
	return details or {}, False


# ---------------------------------------------------------------------------
# get_calendar
# ---------------------------------------------------------------------------


async def get_calendar(
	room_id: str, api_key: str | None = None,
	task_key: str | None = None,
) -> tuple[list[dict[str, Any]], bool]:
	"""Retourne (calendar_months, was_cached). Cache 12h.

	Si api_key non fourni, on le fetch via le cache api_key (ou refresh).
	"""
	cache = get_calendar_cache()
	cache_key = room_id  # task_key n'affecte pas le calendar (data publique du listing)
	cached = await cache.get(cache_key)
	if cached is not None:
		return cached, True

	# Fetch api_key si pas fourni
	if not api_key:
		api_key, _ = await get_api_key(task_key=task_key)

	proxy_url = get_proxy_manager().get_proxy_url(task_key=task_key)
	calendar = await asyncio.to_thread(
		pyairbnb.get_calendar,
		api_key=api_key, room_id=str(room_id),
		proxy_url=proxy_url or "",
	)
	if calendar:
		await cache.set(cache_key, calendar)
	return calendar or [], False


# ---------------------------------------------------------------------------
# get_price (no cache : varie par dates)
# ---------------------------------------------------------------------------


async def get_price(
	room_id: str, check_in: str, check_out: str,
	api_key: str | None = None, currency: str = "EUR",
	adults: int = 1, task_key: str | None = None,
) -> dict[str, Any]:
	"""Prix pour une fenetre. Pas cache car varie par (room, dates)."""
	if not api_key:
		api_key, _ = await get_api_key(task_key=task_key)

	proxy_url = get_proxy_manager().get_proxy_url(task_key=task_key)
	# pyairbnb.get_price attend des objets date, pas des strings ISO.
	ci = _date.fromisoformat(check_in)
	co = _date.fromisoformat(check_out)
	price = await asyncio.to_thread(
		pyairbnb.get_price,
		room_id=str(room_id), check_in=ci, check_out=co,
		currency=currency, api_key=api_key,
		proxy_url=proxy_url or "",
	)
	return price or {}
