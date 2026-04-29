"""Cache LRU + TTL pour les responses Airbnb.

3 caches separes (TTL different) :
- api_key : 24h (api_key change rarement, hub de tous les calls)
- calendar : 12h (idempotent multi-logements, gros gain bandwidth)
- details : 7 jours (amenities/photos changent tres rarement)

Implementation : OrderedDict pour LRU + timestamp pour TTL. Eviction au
ratio cache_max_entries (default 2000) avec popitem(last=False).

Thread-safe via asyncio.Lock car les wrappers pyairbnb sont async (run
dans un thread executor mais le cache est partage entre tous).
"""
from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from typing import Any

from .config import SETTINGS


class TTLCache:
	"""Async-safe LRU + TTL cache."""

	def __init__(self, name: str, ttl_hours: float, maxsize: int = 2000):
		self.name = name
		self.ttl_seconds = ttl_hours * 3600
		self.maxsize = maxsize
		self._store: OrderedDict[str, tuple[float, Any]] = OrderedDict()
		self._lock = asyncio.Lock()
		self._hits = 0
		self._misses = 0

	async def get(self, key: str) -> Any | None:
		async with self._lock:
			entry = self._store.get(key)
			if entry is None:
				self._misses += 1
				return None
			ts, value = entry
			age = time.time() - ts
			if age > self.ttl_seconds:
				self._store.pop(key, None)
				self._misses += 1
				return None
			# LRU : remonte
			self._store.move_to_end(key)
			self._hits += 1
			return value

	async def set(self, key: str, value: Any) -> None:
		async with self._lock:
			self._store[key] = (time.time(), value)
			self._store.move_to_end(key)
			while len(self._store) > self.maxsize:
				self._store.popitem(last=False)

	async def clear(self) -> None:
		async with self._lock:
			self._store.clear()
			self._hits = 0
			self._misses = 0

	async def stats(self) -> dict:
		async with self._lock:
			total = self._hits + self._misses
			hit_rate = self._hits / total if total > 0 else 0.0
			oldest_age_h: float | None = None
			if self._store:
				oldest_ts = min(ts for ts, _ in self._store.values())
				oldest_age_h = round((time.time() - oldest_ts) / 3600, 2)
			return {
				"name": self.name,
				"entries": len(self._store),
				"maxsize": self.maxsize,
				"ttl_hours": self.ttl_seconds / 3600,
				"hits": self._hits,
				"misses": self._misses,
				"hit_rate": round(hit_rate, 3),
				"oldest_age_hours": oldest_age_h,
			}


# Caches process-wide
_CALENDAR_CACHE: TTLCache | None = None
_API_KEY_CACHE: TTLCache | None = None
_DETAILS_CACHE: TTLCache | None = None


def get_calendar_cache() -> TTLCache:
	global _CALENDAR_CACHE
	if _CALENDAR_CACHE is None:
		_CALENDAR_CACHE = TTLCache(
			"calendar",
			SETTINGS.cache_calendar_ttl_hours,
			SETTINGS.cache_max_entries,
		)
	return _CALENDAR_CACHE


def get_api_key_cache() -> TTLCache:
	global _API_KEY_CACHE
	if _API_KEY_CACHE is None:
		_API_KEY_CACHE = TTLCache(
			"api_key",
			SETTINGS.cache_api_key_ttl_hours,
			# Petit cache : 1 entree par proxy session typiquement.
			maxsize=50,
		)
	return _API_KEY_CACHE


def get_details_cache() -> TTLCache:
	global _DETAILS_CACHE
	if _DETAILS_CACHE is None:
		_DETAILS_CACHE = TTLCache(
			"details",
			SETTINGS.cache_details_ttl_hours,
			SETTINGS.cache_max_entries,
		)
	return _DETAILS_CACHE
