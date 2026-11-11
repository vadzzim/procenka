"""The pricing core: one pure function from inputs to an explainable result.

Rules follow the "Правила расчёта" section of ``plan.md``:

1. Offers are filtered: other article, other brand, price <= 0, out of stock,
   delivery too long, price strictly below purchase x factor (first match wins).
2. Target = minimum eligible competitor price - step, rounded ROUND_HALF_UP to a kopeck.
3. Lower bound = purchase x (1 + markup / 100), rounded ROUND_CEILING to a kopeck.
4. Ceiling (optional) is rounded ROUND_FLOOR to a kopeck; a ceiling strictly
   below the lower bound is a rules conflict (equality is allowed).
5. The rounded target is clamped into [lower bound, ceiling].
6. Invalid purchase, rules conflict or no eligible offers keep the previous price.
   Otherwise: same as published -> unchanged; no published price -> quarantine;
   abs(new - old) / old x 100 strictly above the threshold -> quarantine;
   else published.
"""

from collections.abc import Iterable
from decimal import Context, DivisionByZero, InvalidOperation, Overflow, localcontext

from app.pricing.models import (
    DEFAULT_GLOBAL_SETTINGS,
    RULES_VERSION,
    Constraint,
    GlobalSettings,
    Offer,
    OfferRejection,
    PricingResult,
    Product,
    ProductSettings,
    RejectedOffer,
    ResultReason,
    ResultState,
)
from app.pricing.money import HUNDRED, ceil_cents, floor_cents, half_up_cents, half_up_tenth
from app.pricing.normalize import normalize_article, normalize_brand

# Fixed arithmetic context so the result never depends on the caller's
# decimal.getcontext(). 28 significant digits are far above NUMERIC(12,2).
_CONTEXT = Context(prec=28, traps=[InvalidOperation, DivisionByZero, Overflow])


def _rejection_reason(
    offer: Offer,
    product: Product,
    global_settings: GlobalSettings,
) -> OfferRejection | None:
    if normalize_article(offer.article) != product.normalized_article:
        return OfferRejection.OTHER_ARTICLE
    if normalize_brand(offer.brand) != product.normalized_brand:
        return OfferRejection.OTHER_BRAND
    if offer.price <= 0:
        return OfferRejection.NON_POSITIVE_PRICE
    if offer.stock <= 0:
        return OfferRejection.OUT_OF_STOCK
    if offer.delivery_days > global_settings.max_delivery_days:
        return OfferRejection.DELIVERY_TOO_LONG
    if offer.price < product.purchase_price * global_settings.suspicious_price_factor:
        return OfferRejection.SUSPICIOUSLY_LOW_PRICE
    return None


def calculate_price(
    product: Product,
    settings: ProductSettings,
    offers: Iterable[Offer],
    *,
    global_settings: GlobalSettings = DEFAULT_GLOBAL_SETTINGS,
    partial_data: bool = False,
) -> PricingResult:
    """Calculate the recommended price of one product from fresh competitor offers.

    ``partial_data`` (one source failed) does not change the calculation; it is
    carried into the result so the UI can mark it.
    """
    with localcontext(_CONTEXT):
        purchase = product.purchase_price

        accepted: list[Offer] = []
        rejected: list[RejectedOffer] = []
        for offer in offers:
            reason = _rejection_reason(offer, product, global_settings)
            if reason is None:
                accepted.append(offer)
            else:
                rejected.append(RejectedOffer(offer=offer, reason=reason))

        suspicious_price_limit = purchase * global_settings.suspicious_price_factor
        lower_bound = ceil_cents(purchase * (1 + settings.min_markup_percent / HUNDRED))
        ceiling = floor_cents(settings.ceiling) if settings.ceiling is not None else None

        min_competitor_price = min((o.price for o in accepted), default=None)
        target_price_raw = (
            min_competitor_price - settings.step if min_competitor_price is not None else None
        )
        target_price = half_up_cents(target_price_raw) if target_price_raw is not None else None

        constraints: list[Constraint] = []
        recommended = None
        change_percent = None
        change_percent_display = None

        state: ResultState
        result_reason: ResultReason | None
        if purchase <= 0:
            state, result_reason = ResultState.RETAINED, ResultReason.INVALID_PURCHASE_PRICE
        elif ceiling is not None and ceiling < lower_bound:
            state, result_reason = ResultState.RETAINED, ResultReason.RULES_CONFLICT
        elif target_price is None:
            state, result_reason = ResultState.RETAINED, ResultReason.NO_ELIGIBLE_OFFERS
        else:
            recommended = target_price
            if recommended < lower_bound:
                recommended = lower_bound
                constraints.append(Constraint.LOWER_BOUND)
            if ceiling is not None and recommended > ceiling:
                recommended = ceiling
                constraints.append(Constraint.CEILING)

            published = product.published_price
            if published is None:
                state, result_reason = ResultState.QUARANTINE, ResultReason.NO_PREVIOUS_PRICE
            else:
                difference = recommended - published
                change_percent = difference / published * HUNDRED
                change_percent_display = half_up_tenth(change_percent)
                if difference == 0:
                    state, result_reason = ResultState.UNCHANGED, None
                # abs(new - old) / old * 100 > threshold, compared without division.
                elif abs(difference) * HUNDRED > settings.quarantine_threshold_percent * published:
                    state, result_reason = ResultState.QUARANTINE, ResultReason.PRICE_SPIKE
                else:
                    state, result_reason = ResultState.PUBLISHED, None

        return PricingResult(
            rules_version=RULES_VERSION,
            product=product,
            settings=settings,
            global_settings=global_settings,
            partial_data=partial_data,
            accepted_offers=tuple(accepted),
            rejected_offers=tuple(rejected),
            suspicious_price_limit=suspicious_price_limit,
            min_competitor_price=min_competitor_price,
            target_price_raw=target_price_raw,
            target_price=target_price,
            lower_bound=lower_bound,
            ceiling=ceiling,
            applied_constraints=tuple(constraints),
            recommended_price=recommended,
            state=state,
            reason=result_reason,
            change_percent=change_percent,
            change_percent_display=change_percent_display,
        )
