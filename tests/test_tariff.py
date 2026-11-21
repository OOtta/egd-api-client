import pytest

from egd_api_client.spot.tariff import SpotTariff


def test_formula_multiplier_and_surcharge():
    t = SpotTariff(multiplier=1.15, add_czk_mwh=400)
    assert t.energy_price_czk_mwh(2000) == pytest.approx(2700)


@pytest.mark.parametrize(
    ("mode", "expected"),
    [("as-is", -1000 * 1.15 + 400), ("clamp-spot", 400), ("clamp-total", 0.0)],
)
def test_negative_price_modes(mode, expected):
    t = SpotTariff(multiplier=1.15, add_czk_mwh=400, negative=mode)
    assert t.energy_price_czk_mwh(-1000) == pytest.approx(expected)


def test_clamp_total_keeps_positive_results():
    t = SpotTariff(add_czk_mwh=400, negative="clamp-total")
    assert t.energy_price_czk_mwh(-100) == pytest.approx(300)


def test_describe():
    t = SpotTariff(multiplier=1.15, add_czk_mwh=400, monthly_fee=99, vat_percent=21)
    assert t.describe() == "spot × 1.15 + 400 CZK/MWh, fee 99 CZK/month, VAT 21 %"
    assert t.vat_factor == pytest.approx(1.21)
