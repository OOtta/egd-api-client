"""Spot tariff definition: how a supplier turns the OTE price into your energy price."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

NegativeMode = Literal["as-is", "clamp-spot", "clamp-total"]


@dataclass(frozen=True, slots=True)
class SpotTariff:
    """Effective energy price per MWh = spot × multiplier + add_czk_mwh.

    ``negative`` controls what happens when the spot price is negative:

    * ``as-is``       – the formula is applied unchanged (a negative spot price lowers the
      bill; with multiplier > 1 the credit is amplified as well).
    * ``clamp-spot``  – negative spot prices are treated as 0 before the formula
      (you still pay ``add_czk_mwh``).
    * ``clamp-total`` – the formula is applied, then the result is floored at 0
      (you never get paid for consuming).

    ``monthly_fee`` is a fixed supplier fee (CZK/month, prorated by days for partial months).
    ``vat_percent`` is applied on top of everything (0 = prices without VAT).
    """

    multiplier: float = 1.0
    add_czk_mwh: float = 0.0
    monthly_fee: float = 0.0
    vat_percent: float = 0.0
    negative: NegativeMode = "as-is"

    def energy_price_czk_mwh(self, spot_czk_mwh: float) -> float:
        """Effective energy price without VAT."""
        spot = spot_czk_mwh
        if self.negative == "clamp-spot" and spot < 0:
            spot = 0.0
        price = spot * self.multiplier + self.add_czk_mwh
        if self.negative == "clamp-total" and price < 0:
            price = 0.0
        return price

    @property
    def vat_factor(self) -> float:
        return 1.0 + self.vat_percent / 100.0

    def describe(self) -> str:
        parts = ["spot"]
        if self.multiplier != 1.0:
            parts.append(f"× {self.multiplier:g}")
        if self.add_czk_mwh:
            sign = "+" if self.add_czk_mwh >= 0 else "−"
            parts.append(f"{sign} {abs(self.add_czk_mwh):g} CZK/MWh")
        text = " ".join(parts)
        if self.monthly_fee:
            text += f", fee {self.monthly_fee:g} CZK/month"
        text += f", VAT {self.vat_percent:g} %" if self.vat_percent else ", excl. VAT"
        if self.negative != "as-is":
            text += f", negative prices: {self.negative}"
        return text


@dataclass(frozen=True, slots=True)
class FixedTariff:
    """Comparison tariff: fixed CZK/kWh (excl. VAT) plus optional monthly fee."""

    price_czk_kwh: float
    monthly_fee: float = 0.0
