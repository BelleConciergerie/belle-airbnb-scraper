"""Construit le proxy_url pour pyairbnb : IPRoyal ou DataImpulse.

pyairbnb attend un proxy_url string `http://user:pwd@host:port`. On garde
ce format ici (vs httpx.Proxy auth separee dans booking-scraper) car
pyairbnb passe le string directement a curl_cffi qui supporte les 2.

Deux fournisseurs, choisis par le prefixe du task_key (`ip:` IPRoyal, `di:` DataImpulse), sinon par
PROXY_PROVIDER (defaut iproyal) ; sans task_key, une IP qui change a chaque requete chez DataImpulse
(port 823), une session "default" chez IPRoyal (comportement historique).

Session sticky par task_key : meme task = meme IP pendant la duree de la session (IPROYAL_LIFETIME,
30 min par defaut ; DataImpulse : un port fixe entre 10000 et 20000, jusqu'a 120 min). Permet de capturer
un compset complet d'un logement avec une seule IP, evite la rotation aleatoire qui peut faire varier
les rankings Airbnb.
"""
from __future__ import annotations

import threading
import uuid

from .config import SETTINGS

PROVIDERS = ("iproyal", "dataimpulse")
_PREFIXES = {"ip:": "iproyal", "di:": "dataimpulse"}


def split_task_key(task_key: str | None) -> tuple[str | None, str | None]:
	"""(fournisseur, task_key sans prefixe) ; fournisseur None quand le task_key n'en precise pas."""
	if not task_key:
		return None, None
	for prefix, provider in _PREFIXES.items():
		if task_key.startswith(prefix):
			return provider, (task_key[len(prefix):] or None)
	return None, task_key


class ProxyManager:
	"""Sticky session par task_key et par fournisseur. Thread-safe (lock interne)."""

	def __init__(self):
		self._sessions: dict[str, str] = {}
		self._lock = threading.Lock()

	def provider_for(self, task_key: str | None = None) -> str | None:
		"""Le fournisseur a utiliser, ou None si aucun n'est configure pour ce choix."""
		asked, _ = split_task_key(task_key)
		provider = asked or SETTINGS.proxy_provider
		if provider == "dataimpulse" and SETTINGS.has_dataimpulse:
			return "dataimpulse"
		if provider == "iproyal" and SETTINGS.has_iproyal:
			return "iproyal"
		# le fournisseur demande n'est pas configure : l'autre, s'il l'est
		if SETTINGS.has_iproyal:
			return "iproyal"
		if SETTINGS.has_dataimpulse:
			return "dataimpulse"
		return None

	def _session(self, provider: str, key: str) -> str:
		k = f"{provider}:{key}"
		with self._lock:
			session = self._sessions.get(k)
			if not session:
				session = uuid.uuid4().hex[:8]
				self._sessions[k] = session
		return session

	def get_proxy_url(self, task_key: str | None = None) -> str | None:
		"""Retourne le proxy_url string. None si proxy non configure."""
		provider = self.provider_for(task_key)
		if provider is None:
			return None
		_, key = split_task_key(task_key)
		if provider == "dataimpulse":
			user = SETTINGS.dataimpulse_user
			if SETTINGS.dataimpulse_country:
				user = f"{user}__cr.{SETTINGS.dataimpulse_country.lower()}"
			port = SETTINGS.dataimpulse_port
			if key:
				# session fixe : un port stable pour la session de cette task
				session = self._session(provider, key)
				span = max(1, SETTINGS.dataimpulse_sticky_max - SETTINGS.dataimpulse_sticky_min)
				port = SETTINGS.dataimpulse_sticky_min + int(session, 16) % span
			return f"http://{user}:{SETTINGS.dataimpulse_pass}@{SETTINGS.dataimpulse_host}:{port}"
		session = self._session(provider, key or "default")
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
		provider = self.provider_for(task_key) or SETTINGS.proxy_provider
		_, key = split_task_key(task_key)
		with self._lock:
			self._sessions[f"{provider}:{key or 'default'}"] = uuid.uuid4().hex[:8]


_PROXY_MGR: ProxyManager | None = None


def get_proxy_manager() -> ProxyManager:
	global _PROXY_MGR
	if _PROXY_MGR is None:
		_PROXY_MGR = ProxyManager()
	return _PROXY_MGR
