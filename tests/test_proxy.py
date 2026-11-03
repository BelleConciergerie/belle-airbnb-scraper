"""Choix du fournisseur de proxy et forme du proxy_url (IPRoyal, DataImpulse), sans reseau."""
from __future__ import annotations

import dataclasses

from airbnb_scraper import proxy as proxy_mod
from airbnb_scraper.config import SETTINGS


def _settings(**over):
	base = dict(
		iproyal_user="iu", iproyal_pass="ip", iproyal_host="geo.iproyal.com", iproyal_port=12321,
		iproyal_country="fr", iproyal_lifetime="30m",
		dataimpulse_user="du", dataimpulse_pass="dp", dataimpulse_host="gw.dataimpulse.com", dataimpulse_port=823,
		dataimpulse_country="fr", dataimpulse_sticky_min=10000, dataimpulse_sticky_max=20000,
		proxy_provider="iproyal",
	)
	base.update(over)
	return dataclasses.replace(SETTINGS, **base)


def test_prefixe_du_task_key_choisit_le_fournisseur(monkeypatch):
	monkeypatch.setattr(proxy_mod, "SETTINGS", _settings())
	m = proxy_mod.ProxyManager()
	assert m.provider_for(None) == "iproyal"
	assert m.provider_for("compset-1") == "iproyal"
	assert m.provider_for("di:compset-1") == "dataimpulse"
	assert m.provider_for("ip:compset-1") == "iproyal"
	assert proxy_mod.split_task_key("di:") == ("dataimpulse", None)


def test_dataimpulse_rotation_sans_task_key_et_session_fixe_avec(monkeypatch):
	monkeypatch.setattr(proxy_mod, "SETTINGS", _settings(proxy_provider="dataimpulse"))
	m = proxy_mod.ProxyManager()
	assert m.get_proxy_url(None) == "http://du__cr.fr:dp@gw.dataimpulse.com:823"
	a = m.get_proxy_url("di:lot-1")
	assert a == m.get_proxy_url("di:lot-1")
	port = int(a.rsplit(":", 1)[1])
	assert 10000 <= port < 20000
	m.rotate("di:lot-1")
	assert m.get_proxy_url("di:lot-1").startswith("http://du__cr.fr:dp@gw.dataimpulse.com:")


def test_iproyal_inchange(monkeypatch):
	monkeypatch.setattr(proxy_mod, "SETTINGS", _settings())
	m = proxy_mod.ProxyManager()
	url = m.get_proxy_url("compset-1")
	assert url.startswith("http://iu:ip_country-fr_session-") and url.endswith("_lifetime-30m@geo.iproyal.com:12321")
	assert m.get_proxy_url("compset-1") == url


def test_fournisseur_demande_non_configure_retombe_sur_l_autre(monkeypatch):
	monkeypatch.setattr(proxy_mod, "SETTINGS", _settings(dataimpulse_user=None, dataimpulse_pass=None))
	m = proxy_mod.ProxyManager()
	assert m.provider_for("di:x") == "iproyal"
	monkeypatch.setattr(proxy_mod, "SETTINGS", _settings(iproyal_user=None, iproyal_pass=None))
	assert proxy_mod.ProxyManager().provider_for(None) == "dataimpulse"
	monkeypatch.setattr(proxy_mod, "SETTINGS", _settings(iproyal_user=None, iproyal_pass=None, dataimpulse_user=None, dataimpulse_pass=None))
	assert proxy_mod.ProxyManager().get_proxy_url("x") is None
