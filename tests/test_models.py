"""Tests des Pydantic models : validation entrees."""
import pytest
from pydantic import ValidationError

from airbnb_scraper.models import (
	HotelDetailsRequest,
	HotelPriceRequest,
	SearchRequest,
)


class TestSearchRequest:
	def test_valid_bbox(self):
		r = SearchRequest(ne_lat=43.6, ne_long=7.05, sw_lat=43.55, sw_long=7.0)
		assert r.zoom_value == 2  # default

	def test_invalid_lat_range(self):
		with pytest.raises(ValidationError):
			SearchRequest(ne_lat=99, ne_long=7, sw_lat=43, sw_long=7)

	def test_bbox_inverted(self):
		# ne_lat <= sw_lat -> error
		with pytest.raises(ValidationError):
			SearchRequest(ne_lat=43.0, ne_long=7.05, sw_lat=43.5, sw_long=7.0)

	def test_dates_inverted(self):
		with pytest.raises(ValidationError):
			SearchRequest(
				ne_lat=43.6, ne_long=7.05, sw_lat=43.55, sw_long=7.0,
				check_in="2026-05-15", check_out="2026-05-14",
			)

	def test_dates_optional(self):
		r = SearchRequest(ne_lat=43.6, ne_long=7.05, sw_lat=43.55, sw_long=7.0)
		assert r.check_in is None
		assert r.check_out is None


class TestHotelDetailsRequest:
	def test_valid_room_id(self):
		r = HotelDetailsRequest(room_id="764133092500775861")
		assert r.currency == "EUR"

	def test_room_id_must_be_numeric(self):
		with pytest.raises(ValidationError):
			HotelDetailsRequest(room_id="../../etc/passwd")

	def test_room_id_alphanumeric_rejected(self):
		# Airbnb room_id est numerique uniquement
		with pytest.raises(ValidationError):
			HotelDetailsRequest(room_id="abc123")


class TestHotelPriceRequest:
	def test_valid_window(self):
		r = HotelPriceRequest(
			room_id="764133092500775861",
			check_in="2026-05-15", check_out="2026-05-16",
		)
		assert r.adults == 1

	def test_co_must_be_after_ci(self):
		with pytest.raises(ValidationError):
			HotelPriceRequest(
				room_id="764133092500775861",
				check_in="2026-05-15", check_out="2026-05-15",
			)
