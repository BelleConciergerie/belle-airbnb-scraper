"""Construit le proxy_url IPRoyal pour pyairbnb.

pyairbnb attend un proxy_url string `http://user:pwd@host:port`. On garde
ce format ici (vs httpx.Proxy auth separee dans booking-scraper) car
pyairbnb passe le string directement a curl_cffi qui supporte les 2.

Session sticky par task_key : meme task = meme IP IPRoyal pendant la
duree IPROYAL_LIFETIME (30m default). Permet de capturer un compset complet
d'un logement avec une seule IP, evite la rotation aleatoire qui peut faire
varier les rankings Airbnb.
"""
from __future__ import annotations

import threading
import uuid

from .config import SETTINGS


class ProxyManager:
	"""Sticky session par task_key. Thread-safe (lock interne)."""

	def __init__(self):
		self._sessions: dict[str, str] = {}
		self._lock = threading.Lock()

	def get_proxy_url(self, task_key: str | None = None) -> str | None:
		"""Retourne le proxy_url string. None si proxy non configure."""
		if not SETTINGS.has_proxy:
			return None
		key = task_key or "default"
		with self._lock:
			session = self._sessions.get(key)
			if not session:
				session = uuid.uuid4().hex[:8]
				self._sessions[key] = session
		pwd_parts = [SETTINGS.iproyal_pass]
		if SETTINGS.iproyal_country:
			pwd_parts.append(f"country-{SETTINGS.iproyal_country.lower()}")
		pwd_parts.append(f"session-{session}")
		if SETTINGS.iproyal_lifetime:
			pwd_parts.append(f"lifetime-{SETTINGS.iproyal_lifetime}")
		pwd = "_".join(pwd_parts)
		return f"http://{SETTINGS.iproyal_user}:{pwd}@{SETTINGS.iproyal_host}:{SETTINGS.iproyal_port}"

	def rotate(self, task_key: str | None = None) -> None:
		"""Force nouvelle IP pour cette task (ex apres ban/timeout)."""
		key = task_key or "default"
		with self._lock:
			self._sessions[key] = uuid.uuid4().hex[:8]


_PROXY_MGR: ProxyManager | None = None


def get_proxy_manager() -> ProxyManager:
	global _PROXY_MGR
	if _PROXY_MGR is None:
		_PROXY_MGR = ProxyManager()
	return _PROXY_MGR
