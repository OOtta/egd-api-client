"""Spot tariff definition: how a supplier turns the OTE price into your energy price."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

NegativeMode = Literal["as-is", "clamp-spot", "clamp-total"]


@dataclass(frozen=True, slots=True)
class SpotTariff:
    """Effective energy price per MWh = spot × multiplier + add_czk_mwh.

    For periods with a **negative spot price** (strictly below 0) the supplier may use other
    coefficients: ``negative_multiplier`` / ``negative_add_czk_mwh``. Each one that is
    ``None`` falls back to the normal ``multiplier`` / ``add_czk_mwh``. Which pair applies
    is decided by the sign of the raw OTE price, before any clamping.

    ``negative`` then controls the treatment of negative prices:

    * ``as-is``       – formula applied unchanged: ``spot × neg_mult + neg_add`` (a negative
      spot price lowers the bill; a multiplier > 1 amplifies the credit).
    * ``clamp-spot``  – the negative spot is replaced by 0 *before* the coefficients, so the
      price is ``0 × neg_mult + neg_add = neg_add`` (you still pay the surcharge).
    * ``clamp-total`` – the formula with the negative coefficients is applied, then the
      result is floored at 0 (you never get paid for consuming).

    ``monthly_fee`` is a fixed supplier fee (CZK/month, prorated by days for partial months).
    ``vat_percent`` is applied on top of everything (0 = prices without VAT).
    """

    multiplier: float = 1.0
    add_czk_mwh: float = 0.0
    monthly_fee: float = 0.0
    vat_percent: float = 0.0
    negative: NegativeMode = "as-is"
    negative_multiplier: float | None = None
    negative_add_czk_mwh: float | None = None

    def coefficients(self, spot_czk_mwh: float) -> tuple[float, float]:
        """(multiplier, add CZK/MWh) applicable to a period with this spot price."""
        if spot_czk_mwh < 0:
            m = self.multiplier if self.negative_multiplier is None else self.negative_multiplier
            a = self.add_czk_mwh if self.negative_add_czk_mwh is None else self.negative_add_czk_mwh
            return m, a
        return self.multiplier, self.add_czk_mwh

    def energy_price_czk_mwh(self, spot_czk_mwh: float) -> float:
        """Effective energy price without VAT."""
        mult, add = self.coefficients(spot_czk_mwh)
        spot = spot_czk_mwh
        if self.negative == "clamp-spot" and spot < 0:
            spot = 0.0
        price = spot * mult + add
        if self.negative == "clamp-total" and price < 0:
            price = 0.0
        return price

    @property
    def vat_factor(self) -> float:
        return 1.0 + self.vat_percent / 100.0

    @staticmethod
    def _formula(mult: float, add: float) -> str:
        parts = ["spot"]
        if mult != 1.0:
            parts.append(f"× {mult:g}")
        if add:
            sign = "+" if add >= 0 else "−"
            parts.append(f"{sign} {abs(add):g} CZK/MWh")
        return " ".join(parts)

    @property
    def has_negative_coefficients(self) -> bool:
        return self.negative_multiplier is not None or self.negative_add_czk_mwh is not None

    def describe(self) -> str:
        text = self._formula(self.multiplier, self.add_czk_mwh)
        if self.has_negative_coefficients:
            text += f" (negative spot: {self._formula(*self.coefficients(-1.0))})"
        if self.monthly_fee:
            text += f", fee {self.monthly_fee:g} CZK/month"
        text += f", VAT {self.vat_percent:g} %" if self.vat_percent else ", excl. VAT"
        if self.negative != "as-is":
            text += f", negative prices: {self.negative}"
        return text


@dataclass(frozen=True, slots=True)
class FixedTariff:
    """Comparison tariff: fixed CZK/kWh (excl. VAT) plus optional monthly fee.

    ``price_czk_kwh`` is the high-tariff (VT) price, or the only price of a single-rate
    product. ``price_low_czk_kwh`` is the low-tariff (NT) price of a two-rate product; it
    needs a :class:`~egd_api_client.lowtariff.LowTariffSchedule` to know which intervals
    were NT.
    """

    price_czk_kwh: float
    monthly_fee: float = 0.0
    price_low_czk_kwh: float | None = None

    def describe(self) -> str:
        if self.price_low_czk_kwh is None:
            text = f"{self.price_czk_kwh:g} CZK/kWh"
        else:
            text = f"VT {self.price_czk_kwh:g} / NT {self.price_low_czk_kwh:g} CZK/kWh"
        if self.monthly_fee:
            text += f" + {self.monthly_fee:g} CZK/month"
        return text


@dataclass(frozen=True, slots=True)
class DistributionTariff:
    """User-supplied distribution charges, added to both the spot and the fixed result.

    ``high_czk_kwh`` – per-kWh distribution charge in the high tariff (VT), or for all
    consumption when ``low_czk_kwh`` is not given. ``low_czk_kwh`` – per-kWh charge in the
    low tariff (NT); needs a low-tariff schedule. ``monthly_fee`` – fixed monthly charges
    (circuit breaker etc.), prorated by days. All amounts in CZK excl. VAT; nothing is
    built in – enter the values of your distribution rate.
    """

    high_czk_kwh: float
    low_czk_kwh: float | None = None
    monthly_fee: float = 0.0

    def describe(self) -> str:
        if self.low_czk_kwh is None:
            text = f"{self.high_czk_kwh:g} CZK/kWh"
        else:
            text = f"VT {self.high_czk_kwh:g} / NT {self.low_czk_kwh:g} CZK/kWh"
        if self.monthly_fee:
            text += f" + {self.monthly_fee:g} CZK/month"
        return text
