# belle-airbnb-scraper v0.1.0

Service de scraping Airbnb pour le compset Belle Pricing.

**Architecture** : FastAPI (httpx async) + pyairbnb (curl_cffi sync, exécuté
via `asyncio.to_thread`). Cache LRU + TTL sur les 3 endpoints les plus
appelés (`api_key` 24h, `calendar` 12h, `details` 7j) → mutualisation des
calls multi-logements, **gain bandwidth ~70% vs pyairbnb direct**.

Mirror du pattern `belle-booking-scraper` (consistance des 2 services
scraper côté Belle Conciergerie).

---

## Pourquoi ce service ?

Avant : belle-pricing utilisait `pyairbnb` directement comme lib Python in-process.

Limitations :
- 1 process Frappe = 1 instance pyairbnb (pas de mutualisation)
- `get_api_key()` re-fetch à chaque worker (~600 KB × N workers)
- `get_calendar()` re-fetch même si un autre logement vient de scraper le même listing
- Crash pyairbnb = crash workers Frappe
- Conflit de deps avec Frappe (curl_cffi vs requests)

Après : service Docker dédié avec caches partagés.

| Métrique | Avant (lib in-process) | Après (service + cache) |
|----------|------------------------|------------------------|
| `get_api_key` calls / mois | ~720 (1×/h × 24×30) | **~30** (cache 24h) ⬇ -95% |
| `get_calendar` calls (compsets dedup) | 39 000 | **~6 500** (cache 12h, hit ~85%) ⬇ -83% |
| Bandwidth Airbnb / mois | ~15 GB | **~5 GB** ⬇ -66% |
| Resource isolation | crash → workers Frappe down | crash → service down, workers OK |
| Patches urgence | `pip install` + restart Frappe | `docker compose up -d --build` |

---

## Endpoints

| Method | Path | Description | Cache | Auth |
|--------|------|-------------|-------|------|
| GET | `/health` | Liveness public | — | non |
| GET | `/api_key` | get_api_key (auto-fetch si expiré) | **24h** | API key |
| POST | `/api_key/refresh` | Force refresh (admin) | bypass | API key |
| POST | `/search` | search_all bbox/dates | non | API key |
| POST | `/hotel/details` | get_details listing | **7j** | API key |
| POST | `/hotel/calendar` | get_calendar 12 mois (dispo+min_nights) | **12h** | API key |
| POST | `/hotel/price` | get_price fenêtre check_in/out | non | API key |
| GET | `/cache/stats` | Stats des 3 caches | — | API key |
| POST | `/cache/clear` | Vide caches (admin) | — | API key |

Auth : header `X-API-Key` requis sauf `/health`.

### `POST /search`

```json
{
  "ne_lat": 43.5563, "ne_long": 7.0229,
  "sw_lat": 43.5463, "sw_long": 7.0129,
  "check_in": "2026-05-15", "check_out": "2026-05-16",
  "zoom_value": 2,
  "currency": "EUR",
  "task_key": "compset-LOG-2026-00045"
}
→ 200 OK
{ "listings": [...], "count": 21, "captured_at": "..." }
```

### `POST /hotel/calendar`

```json
{ "room_id": "764133092500775861" }
→ 200 OK
{
  "room_id": "...",
  "calendar_months": [{ "month": 4, "year": 2026, "days": [...] }, ...],
  "cached": true,
  "captured_at": "..."
}
```

### `POST /hotel/price`

```json
{
  "room_id": "764133092500775861",
  "check_in": "2026-05-15", "check_out": "2026-05-16",
  "currency": "EUR"
}
→ 200 OK
{ "room_id": "...", "price": { "main": {...}, "raw": [...] }, "captured_at": "..." }
```

---

## Validation des inputs

- **`room_id`** : regex `^\d+$`, 1-50 chars (Airbnb numerique uniquement)
- **GPS** : `lat ∈ [-90, 90]`, `long ∈ [-180, 180]`, `ne > sw`
- **Dates** : ISO `YYYY-MM-DD`, `check_out > check_in`, **`check_in >= today - 2j`** (Airbnb n'expose pas les prix passés)
- **`currency`** : 3 chars
- **`zoom_value`** : 0-20
- **`adults`** : 1-30

Rejet `422 Unprocessable Entity` avant round-trip Airbnb.

---

## Setup local

```bash
cp .env.example .env
# Éditer : API_KEY, IPROYAL_USER, IPROYAL_PASS, IPROYAL_COUNTRY=fr

pip install -e .
uvicorn airbnb_scraper.server:app --reload --port 8000
```

**Service crash au boot si `API_KEY` manquant** (`RuntimeError`). Pattern fail-fast hérité de booking-scraper.

## Setup Docker

```bash
docker compose up -d --build
```

Image basée `python:3.12-slim` (~150 MB). User non-root. Logs rotatés (5 × 50 MB). Healthcheck `curl /health`.

## Test rapide

```bash
API_KEY=$(grep API_KEY .env | cut -d= -f2)

# Liveness public
curl -s http://localhost:8201/health | jq .

# Get api_key (cache 24h)
curl -s -H "X-API-Key: $API_KEY" http://localhost:8201/api_key | jq '.cached, .api_key | length'

# Search bbox Cannes
curl -s -X POST http://localhost:8201/search \
  -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  -d '{"ne_lat":43.5563,"ne_long":7.0229,"sw_lat":43.5463,"sw_long":7.0129,"check_in":"2026-05-15","check_out":"2026-05-16"}' \
  | jq '.count'

# Calendar (cache 12h)
curl -s -X POST http://localhost:8201/hotel/calendar \
  -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  -d '{"room_id":"764133092500775861"}' \
  | jq '.cached, (.calendar_months | length)'

# Stats cache
curl -s -H "X-API-Key: $API_KEY" http://localhost:8201/cache/stats | jq .
```

---

## Sécurité

- **API key validation au startup** (fail-fast si absent)
- **`room_id` regex strict** `^\d+$` (prévention injection)
- **Past dates rejected** (`check_in >= today - 2j`)
- **Proxy credentials** : passés via `pyairbnb` proxy_url (URL embedded car curl_cffi expects string format)

---

## Stack

- **FastAPI 0.115** + Pydantic v2 (validation stricte)
- **pyairbnb** : fork Belle (`github.com/BelleConciergerie/pyairbnb` @ `v2.2.1-belle-1`)
- **curl_cffi** : TLS fingerprint browser (anti-bot Airbnb)
- **uvicorn** : ASGI server (workers=1 par défaut, async absorbe la concurrence)

---

## Ports

- **8200** : belle-booking-scraper
- **8201** : belle-airbnb-scraper (ce service)

## Déploiement VPS

```bash
ssh root@72.60.38.71 'cd /opt/belle-airbnb-scraper && git pull origin main && docker compose up -d --build'
curl -s http://72.60.38.71:8201/health
```
