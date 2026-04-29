"""FastAPI v0.1 - wrapper pyairbnb avec caches.

Routes :
  GET  /health           liveness public (no auth)
  POST /search           search_all bbox/dates
  GET  /api_key          get_api_key (cache 24h)
  POST /api_key/refresh  force refresh (admin)
  POST /hotel/details    get_details (cache 7j)
  POST /hotel/calendar   get_calendar (cache 12h)
  POST /hotel/price      get_price (NO cache, varie par dates)
  GET  /cache/stats      stats des 3 caches
  POST /cache/clear      vide les caches (admin)

Auth : header X-API-Key sur toutes les routes sauf /health.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, Header, HTTPException, status

# pyairbnb leve UnavailableError pour les dates booked (semantique = pas dispo,
# pas une vraie erreur). On l'attrape pour repondre 200 + {"unavailable": true}
# au lieu de 502 -> evite les retries client inutiles (3 × 4-8s par date booked).
from pyairbnb.price import UnavailableError

from . import __version__
from . import client as _client
from .cache import get_api_key_cache, get_calendar_cache, get_details_cache
from .config import SETTINGS
from .models import (
	ApiKeyRequest,
	ApiKeyResponse,
	CacheStatsResponse,
	HealthResponse,
	HotelCalendarRequest,
	HotelCalendarResponse,
	HotelDetailsRequest,
	HotelDetailsResponse,
	HotelPriceRequest,
	HotelPriceResponse,
	SearchRequest,
	SearchResponse,
)

logging.basicConfig(
	level=getattr(logging, SETTINGS.log_level, logging.INFO),
	format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("airbnb_scraper")


def _now_iso() -> str:
	return datetime.now(timezone.utc).isoformat()


@asynccontextmanager
async def lifespan(_app: FastAPI):
	# Fail-fast au boot si API_KEY manquant (pattern booking-scraper).
	if not SETTINGS.api_key:
		raise RuntimeError(
			"API_KEY environment variable not set. "
			"Set it in .env or docker-compose.yml before starting the service."
		)
	log.info("belle-airbnb-scraper v%s started", __version__)
	yield
	log.info("Service stopped")


app = FastAPI(
	title="belle-airbnb-scraper",
	version=__version__,
	lifespan=lifespan,
)


def require_api_key(x_api_key: str | None = Header(default=None, alias="X-API-Key")):
	if x_api_key != SETTINGS.api_key:
		raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/health", response_model=HealthResponse, tags=["meta"])
async def health() -> HealthResponse:
	"""Liveness public. Pas d'auth, pas de call externe."""
	return HealthResponse(status="ok", version=__version__)


# ---- api_key ----

@app.get(
	"/api_key",
	response_model=ApiKeyResponse,
	tags=["scraping"],
	dependencies=[Depends(require_api_key)],
)
async def api_key_get(task_key: str | None = None) -> ApiKeyResponse:
	"""Retourne l'api_key Airbnb (cached 24h). Auto-fetch si absent / expire."""
	try:
		key, cached = await _client.get_api_key(task_key=task_key)
	except Exception as e:
		raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"get_api_key failed: {e!s}")
	return ApiKeyResponse(api_key=key, cached=cached, captured_at=_now_iso())


@app.post(
	"/api_key/refresh",
	response_model=ApiKeyResponse,
	tags=["scraping"],
	dependencies=[Depends(require_api_key)],
)
async def api_key_refresh(req: ApiKeyRequest) -> ApiKeyResponse:
	"""Force un refresh de l'api_key (bypass cache). Admin."""
	try:
		key, _ = await _client.get_api_key(task_key=req.task_key, force_refresh=True)
	except Exception as e:
		raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"get_api_key force refresh failed: {e!s}")
	return ApiKeyResponse(api_key=key, cached=False, captured_at=_now_iso())


# ---- search ----

@app.post(
	"/search",
	response_model=SearchResponse,
	tags=["scraping"],
	dependencies=[Depends(require_api_key)],
)
async def search(req: SearchRequest) -> SearchResponse:
	"""Search bbox + dates. Pas de cache (trop variable)."""
	try:
		listings = await _client.search_all(
			ne_lat=req.ne_lat, ne_long=req.ne_long,
			sw_lat=req.sw_lat, sw_long=req.sw_long,
			check_in=req.check_in, check_out=req.check_out,
			zoom_value=req.zoom_value, currency=req.currency,
			price_min=req.price_min, price_max=req.price_max,
			task_key=req.task_key,
		)
	except Exception as e:
		log.exception("search_all failed")
		raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"search_all failed: {e!s}")
	return SearchResponse(listings=listings, count=len(listings), captured_at=_now_iso())


# ---- hotel details ----

@app.post(
	"/hotel/details",
	response_model=HotelDetailsResponse,
	tags=["scraping"],
	dependencies=[Depends(require_api_key)],
)
async def hotel_details(req: HotelDetailsRequest) -> HotelDetailsResponse:
	"""Details listing : amenities, capacity, photos. Cache 7j."""
	try:
		details, cached = await _client.get_details(
			room_id=req.room_id, currency=req.currency, language=req.language,
			task_key=req.task_key,
		)
	except Exception as e:
		log.exception("get_details failed for %s", req.room_id)
		raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"get_details failed: {e!s}")
	if not details:
		raise HTTPException(status.HTTP_404_NOT_FOUND, f"Listing {req.room_id} not found")
	return HotelDetailsResponse(
		room_id=req.room_id, details=details, cached=cached, captured_at=_now_iso(),
	)


# ---- hotel calendar ----

@app.post(
	"/hotel/calendar",
	response_model=HotelCalendarResponse,
	tags=["scraping"],
	dependencies=[Depends(require_api_key)],
)
async def hotel_calendar(req: HotelCalendarRequest) -> HotelCalendarResponse:
	"""Calendrier 12 mois (dispo + min_nights, sans prix). Cache 12h."""
	try:
		calendar, cached = await _client.get_calendar(
			room_id=req.room_id, api_key=req.api_key, task_key=req.task_key,
		)
	except Exception as e:
		log.exception("get_calendar failed for %s", req.room_id)
		raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"get_calendar failed: {e!s}")
	return HotelCalendarResponse(
		room_id=req.room_id, calendar_months=calendar, cached=cached, captured_at=_now_iso(),
	)


# ---- hotel price ----

@app.post(
	"/hotel/price",
	response_model=HotelPriceResponse,
	tags=["scraping"],
	dependencies=[Depends(require_api_key)],
)
async def hotel_price(req: HotelPriceRequest) -> HotelPriceResponse:
	"""Prix d'un listing pour une fenetre check_in/check_out. NO cache.

	UnavailableError (dates booked) -> 200 + price={"unavailable": true}. Avant :
	502 + retries client inutiles (3 × 4-8s = ~15s perdus par date booked
	sur les compsets de zone tendue style Cannes haute saison).
	"""
	try:
		price = await _client.get_price(
			room_id=req.room_id, check_in=req.check_in, check_out=req.check_out,
			api_key=req.api_key, currency=req.currency, adults=req.adults,
			task_key=req.task_key,
		)
	except UnavailableError as e:
		# Dates booked : reponse semantiquement valide, pas une erreur reseau.
		# On retourne 200 avec un flag explicite que le caller (belle-pricing)
		# detecte pour ne pas retry.
		log.info("hotel_price unavailable %s %s..%s: %s",
		         req.room_id, req.check_in, req.check_out, str(e)[:100])
		return HotelPriceResponse(
			room_id=req.room_id, check_in=req.check_in, check_out=req.check_out,
			price={"unavailable": True, "reason": str(e)[:200]},
			captured_at=_now_iso(),
		)
	except Exception as e:
		log.exception("get_price failed for %s", req.room_id)
		raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"get_price failed: {e!s}")
	return HotelPriceResponse(
		room_id=req.room_id, check_in=req.check_in, check_out=req.check_out,
		price=price, captured_at=_now_iso(),
	)


# ---- cache management ----

@app.get(
	"/cache/stats",
	response_model=CacheStatsResponse,
	tags=["meta"],
	dependencies=[Depends(require_api_key)],
)
async def cache_stats() -> CacheStatsResponse:
	"""Stats des 3 caches : entries, hit_rate, oldest_age."""
	api_stats = await get_api_key_cache().stats()
	cal_stats = await get_calendar_cache().stats()
	det_stats = await get_details_cache().stats()
	return CacheStatsResponse(api_key=api_stats, calendar=cal_stats, details=det_stats)


@app.post("/cache/clear", tags=["meta"], dependencies=[Depends(require_api_key)])
async def cache_clear() -> dict:
	"""Vide les 3 caches (admin)."""
	await get_api_key_cache().clear()
	await get_calendar_cache().clear()
	await get_details_cache().clear()
	return {"status": "cleared"}
