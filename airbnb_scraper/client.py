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
	"""Retourne (api_key, was_cached). Cache 24h GLOBAL (token public Airbnb).

	Single-flight global : N coros qui demandent api_key en parallele declenchent
	UN seul fetch pyairbnb upstream, les autres attendent l'Event et recuperent
	le meme token (api_key Airbnb est un token public extrait depuis la home page
	www.airbnb.com, valide cross-IP).

	FIX fan-out : avant on keyait sur _proxy_hash(proxy_url) qui contient
	session-{task_key} -> 100 task_keys uniques = 100 cache_keys distincts = 100
	fetches concurrents (cassait le single-flight). Maintenant cache_key="global"
	-> 1 seul fetch pyairbnb partage par tous les sub-jobs RQ.

	`task_key` reste utilise pour le proxy_url (IP IPRoyal dediee au fetch lui-meme),
	mais le RESULT api_key est cache globalement.
	"""
	cache = get_api_key_cache()
	proxy_url = get_proxy_manager().get_proxy_url(task_key=task_key)
	# Cache key partagee : api_key Airbnb est un token PUBLIC cross-IP,
	# pas de raison de scoper par task_key/proxy_session.
	cache_key = "global"

	async def _fetch():
		api_key = await asyncio.to_thread(pyairbnb.get_api_key, proxy_url=proxy_url or "")
		if not api_key:
			raise RuntimeError("pyairbnb.get_api_key returned empty (Airbnb changed format?)")
		return api_key

	if force_refresh:
		# Bypass cache : fetch direct + ecrase la cache
		api_key = await _fetch()
		await cache.set(cache_key, api_key)
		return api_key, False

	api_key, was_cached = await cache.get_or_fetch(cache_key, _fetch)
	return api_key, was_cached


# ---------------------------------------------------------------------------
# search_all
# ---------------------------------------------------------------------------


async def search_all(
	ne_lat: float, ne_long: float, sw_lat: float, sw_long: float,
	check_in: str | None, check_out: str | None,
	zoom_value: int = 2, currency: str = "EUR",
	price_min: int = 0, price_max: int = 0,
	adults: int = 0, children: int = 0,
	min_bedrooms: int = 0, min_beds: int = 0,
	task_key: str | None = None,
) -> list[dict[str, Any]]:
	"""Search listings dans la bbox. Pas de cache (varie trop par dates/bbox).

	adults/children/min_bedrooms/min_beds = filtres serveur Airbnb (mirror des
	filtres du site). adults=N ne ramene que les biens accueillant >= N personnes.
	Critique pour les gros biens : une recherche bbox plafonne ~280 resultats cote
	Airbnb, sans filtre ils sont noyes dans les studios. 0 = pas de filtre.
	"""
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
		adults=adults, children=children,
		min_bedrooms=min_bedrooms, min_beds=min_beds,
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
	"""Retourne (details, was_cached). Cache 7j avec single-flight.

	Single-flight (P1-A1) : N coros qui demandent meme room_id en parallele
	declenchent UN seul fetch pyairbnb (les autres attendent).
	"""
	cache = get_details_cache()
	cache_key = f"{room_id}:{currency}:{language}"

	async def _fetch():
		proxy_url = get_proxy_manager().get_proxy_url(task_key=task_key)
		details = await asyncio.to_thread(
			pyairbnb.get_details,
			room_id=room_id, currency=currency, language=language,
			proxy_url=proxy_url or "",
			# Airbnb redirige les IP FR www.airbnb.com -> www.airbnb.fr via un stub
			# handoff /v2/domain_switch (page sans #data-deferred-state-0 -> parse 502).
			# Proxy IPRoyal FR : on tape le domaine FR directement.
			domain="www.airbnb.fr",
		)
		# Retourne None si vide pour ne PAS cacher un miss (laisse retry plus tard).
		return details if details else None

	value, was_cached = await cache.get_or_fetch(cache_key, _fetch)
	return value or {}, was_cached


# ---------------------------------------------------------------------------
# get_calendar
# ---------------------------------------------------------------------------


async def get_calendar(
	room_id: str, api_key: str | None = None,
	task_key: str | None = None,
) -> tuple[list[dict[str, Any]], bool]:
	"""Retourne (calendar_months, was_cached). Cache 12h avec single-flight.

	Si api_key non fourni, on le fetch via le cache api_key (ou refresh).

	Single-flight (P1-A1) : 100 logements qui partagent le meme room_id en
	compset cross-Belle declenchent UN seul fetch pyairbnb. Avant : 100 calls
	-> ~600 KB × 100 = 60 MB / cycle. Apres : ~600 KB.
	"""
	cache = get_calendar_cache()
	cache_key = room_id  # task_key n'affecte pas le calendar (data publique du listing)

	async def _fetch():
		# Fetch api_key si pas fourni (lazy + utilise lui-meme single-flight).
		nonlocal api_key
		if not api_key:
			api_key, _ = await get_api_key(task_key=task_key)
		proxy_url = get_proxy_manager().get_proxy_url(task_key=task_key)
		calendar = await asyncio.to_thread(
			pyairbnb.get_calendar,
			api_key=api_key, room_id=str(room_id),
			proxy_url=proxy_url or "",
		)
		# Retourne None si vide pour ne pas cacher un miss.
		return calendar if calendar else None

	value, was_cached = await cache.get_or_fetch(cache_key, _fetch)
	return value or [], was_cached


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
		adults=adults,
		currency=currency, api_key=api_key,
		proxy_url=proxy_url or "",
	)
	return price or {}
