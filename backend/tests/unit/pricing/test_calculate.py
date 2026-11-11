import decimal
from decimal import Decimal

import pytest

from app.pricing import (
    LABELS,
    RULES_VERSION,
    Constraint,
    GlobalSettings,
    Offer,
    OfferRejection,
    PricingResult,
    Product,
    ProductSettings,
    ResultReason,
    ResultState,
    calculate_price,
    label,
)

D = Decimal
BRAND = "Bosch"
ARTICLE = "0986452041"


def make_product(purchase: str = "1000", published: str | None = "1300") -> Product:
    return Product(
        brand=BRAND,
        article=ARTICLE,
        purchase_price=D(purchase),
        published_price=D(published) if published is not None else None,
    )


def make_settings(
    markup: str = "20", step: str = "10", ceiling: str | None = None, threshold: str = "20"
) -> ProductSettings:
    return ProductSettings(
        min_markup_percent=D(markup),
        step=D(step),
        ceiling=D(ceiling) if ceiling is not None else None,
        quarantine_threshold_percent=D(threshold),
    )


def make_offer(
    price: str,
    *,
    brand: str = BRAND,
    article: str = ARTICLE,
    stock: int = 5,
    days: int = 2,
    source: str = "html",
) -> Offer:
    return Offer(
        source=source, brand=brand, article=article, price=D(price), stock=stock, delivery_days=days
    )


def calc(
    *offers: Offer,
    purchase: str = "1000",
    published: str | None = "1300",
    markup: str = "20",
    step: str = "10",
    ceiling: str | None = None,
    threshold: str = "20",
    global_settings: GlobalSettings | None = None,
    partial_data: bool = False,
) -> PricingResult:
    return calculate_price(
        make_product(purchase, published),
        make_settings(markup, step, ceiling, threshold),
        offers,
        global_settings=global_settings or GlobalSettings(),
        partial_data=partial_data,
    )


def rejections(result: PricingResult) -> list[OfferRejection]:
    return [rejected.reason for rejected in result.rejected_offers]


# --- Control example and bounds ---------------------------------------------


def test_control_example_1250_gives_1240_with_full_explanation() -> None:
    offer = make_offer("1250", article="0 986-452 041")
    result = calc(offer)

    assert result.recommended_price == D("1240.00")
    assert result.state is ResultState.PUBLISHED
    assert result.reason is None
    assert result.accepted_offers == (offer,)
    assert result.rejected_offers == ()
    assert result.min_competitor_price == D("1250")
    assert result.target_price_raw == D("1240")
    assert result.target_price == D("1240.00")
    assert result.lower_bound == D("1200.00")
    assert result.ceiling is None
    assert result.applied_constraints == ()
    assert result.suspicious_price_limit == D("500.0")
    assert result.change_percent is not None
    assert result.change_percent_display == D("-4.6")


def test_control_example_1100_is_limited_by_lower_bound() -> None:
    result = calc(make_offer("1100"))
    assert result.target_price == D("1090.00")
    assert result.recommended_price == D("1200.00")
    assert result.applied_constraints == (Constraint.LOWER_BOUND,)


def test_lower_bound_is_rounded_up_to_kopeck() -> None:
    # 100.01 * 1.13 = 113.0113 -> 113.02 (half-up would give 113.01).
    result = calc(make_offer("100"), purchase="100.01", markup="13", published="113")
    assert result.lower_bound == D("113.02")
    assert result.recommended_price == D("113.02")


def test_lower_bound_exact_value_is_not_raised() -> None:
    result = calc(make_offer("1100"), purchase="999.99", markup="20")
    # 999.99 * 1.2 = 1199.988 -> 1199.99
    assert result.lower_bound == D("1199.99")
    assert result.recommended_price == D("1199.99")


def test_target_price_is_rounded_half_up() -> None:
    assert calc(make_offer("1250.005")).target_price == D("1240.01")
    assert calc(make_offer("1250.0049")).target_price == D("1240.00")
    assert calc(make_offer("1250.005")).target_price_raw == D("1240.005")


def test_ceiling_limits_the_result_from_above() -> None:
    result = calc(make_offer("1500"), ceiling="1300", published="1280")
    assert result.target_price == D("1490.00")
    assert result.ceiling == D("1300.00")
    assert result.recommended_price == D("1300.00")
    assert result.applied_constraints == (Constraint.CEILING,)


def test_ceiling_is_rounded_down_to_kopeck() -> None:
    result = calc(make_offer("1500"), ceiling="1300.019", published="1280")
    assert result.ceiling == D("1300.01")
    assert result.recommended_price == D("1300.01")


def test_ceiling_above_target_does_not_constrain() -> None:
    result = calc(make_offer("1250"), ceiling="1500")
    assert result.recommended_price == D("1240.00")
    assert result.applied_constraints == ()


def test_ceiling_equal_to_lower_bound_is_allowed() -> None:
    result = calc(make_offer("1500"), ceiling="1200", published="1210")
    assert result.state is ResultState.PUBLISHED
    assert result.recommended_price == D("1200.00")
    assert result.applied_constraints == (Constraint.CEILING,)


def test_ceiling_equal_to_lower_bound_with_low_target() -> None:
    result = calc(make_offer("1100"), ceiling="1200", published="1210")
    assert result.recommended_price == D("1200.00")
    assert result.applied_constraints == (Constraint.LOWER_BOUND,)


def test_ceiling_below_lower_bound_is_rules_conflict() -> None:
    result = calc(make_offer("1500"), ceiling="1199.99", published="1250")
    assert result.state is ResultState.RETAINED
    assert result.reason is ResultReason.RULES_CONFLICT
    assert result.recommended_price is None
    assert result.applied_constraints == ()
    assert result.change_percent is None
    # The numbers stay available for the explanation.
    assert result.lower_bound == D("1200.00")
    assert result.ceiling == D("1199.99")
    assert result.target_price == D("1490.00")


def test_conflict_is_checked_after_rounding() -> None:
    # Lower bound 1000.001 * 1.2 = 1200.0012 -> 1200.01; ceiling 1200.009 -> 1200.00.
    conflict = calc(make_offer("1500"), purchase="1000.001", ceiling="1200.009")
    assert conflict.lower_bound == D("1200.01")
    assert conflict.ceiling == D("1200.00")
    assert conflict.reason is ResultReason.RULES_CONFLICT
    # With an exact bound of 1200.00 the floored ceiling 1200.00 is equal: allowed.
    equal = calc(make_offer("1500"), purchase="1000", ceiling="1200.009", published="1210")
    assert equal.ceiling == D("1200.00")
    assert equal.recommended_price == D("1200.00")


# --- Purchase price ----------------------------------------------------------


@pytest.mark.parametrize("purchase", ["0", "0.00", "-100", "-0.01"])
def test_non_positive_purchase_keeps_previous_price(purchase: str) -> None:
    result = calc(make_offer("1250"), purchase=purchase)
    assert result.state is ResultState.RETAINED
    assert result.reason is ResultReason.INVALID_PURCHASE_PRICE
    assert result.recommended_price is None


def test_invalid_purchase_wins_over_conflict_and_missing_offers() -> None:
    assert calc(purchase="0", ceiling="-1").reason is ResultReason.INVALID_PURCHASE_PRICE


def test_conflict_wins_over_missing_offers() -> None:
    assert calc(ceiling="1000").reason is ResultReason.RULES_CONFLICT


# --- Offer filtering ---------------------------------------------------------


def test_empty_offer_list_keeps_previous_price() -> None:
    result = calc()
    assert result.state is ResultState.RETAINED
    assert result.reason is ResultReason.NO_ELIGIBLE_OFFERS
    assert result.recommended_price is None
    assert result.min_competitor_price is None
    assert result.target_price_raw is None
    assert result.target_price is None
    assert result.lower_bound == D("1200.00")


def test_other_brand_is_rejected() -> None:
    other = make_offer("1100", brand="MANN")
    result = calc(other, make_offer("1250"))
    assert rejections(result) == [OfferRejection.OTHER_BRAND]
    assert result.rejected_offers[0].offer is other
    assert result.recommended_price == D("1240.00")


def test_only_other_brand_keeps_previous_price() -> None:
    result = calc(make_offer("1250", brand="MANN-FILTER"))
    assert rejections(result) == [OfferRejection.OTHER_BRAND]
    assert result.reason is ResultReason.NO_ELIGIBLE_OFFERS


def test_other_article_is_rejected() -> None:
    result = calc(make_offer("900", article="OC 90"), make_offer("1250"))
    assert rejections(result) == [OfferRejection.OTHER_ARTICLE]
    assert result.recommended_price == D("1240.00")


def test_brand_synonyms_and_article_separators_match() -> None:
    product = Product(
        brand="Mercedes-Benz",
        article="A 000 180 26 09",
        purchase_price=D(1000),
        published_price=D(1300),
    )
    offers = [
        make_offer("1250", brand="MB", article="A0001802609"),
        make_offer("1300", brand=" mercedes  benz", article="a000-180-26-09"),
    ]
    result = calculate_price(product, make_settings(), offers)
    assert result.rejected_offers == ()
    assert result.recommended_price == D("1240.00")


@pytest.mark.parametrize("price", ["0", "0.00", "-5"])
def test_non_positive_price_is_rejected(price: str) -> None:
    result = calc(make_offer(price), make_offer("1250"))
    assert rejections(result) == [OfferRejection.NON_POSITIVE_PRICE]
    assert result.min_competitor_price == D("1250")


def test_out_of_stock_is_rejected() -> None:
    result = calc(make_offer("1100", stock=0), make_offer("1250", stock=1))
    assert rejections(result) == [OfferRejection.OUT_OF_STOCK]
    assert result.recommended_price == D("1240.00")


def test_delivery_longer_than_allowed_is_rejected() -> None:
    result = calc(make_offer("1100", days=15), make_offer("1250", days=14))
    assert rejections(result) == [OfferRejection.DELIVERY_TOO_LONG]
    assert result.recommended_price == D("1240.00")


def test_max_delivery_days_is_a_setting() -> None:
    result = calc(make_offer("1250", days=4), global_settings=GlobalSettings(max_delivery_days=3))
    assert rejections(result) == [OfferRejection.DELIVERY_TOO_LONG]


def test_price_below_factor_is_suspiciously_low() -> None:
    result = calc(make_offer("499.99"), make_offer("1300"), published="1250")
    assert rejections(result) == [OfferRejection.SUSPICIOUSLY_LOW_PRICE]
    assert result.recommended_price == D("1290.00")


def test_price_exactly_at_factor_is_not_rejected() -> None:
    result = calc(make_offer("500.00"), published="1200")
    assert result.rejected_offers == ()
    assert result.min_competitor_price == D("500.00")
    assert result.recommended_price == D("1200.00")
    assert result.applied_constraints == (Constraint.LOWER_BOUND,)


def test_suspicious_factor_is_a_setting() -> None:
    settings = GlobalSettings(suspicious_price_factor=D("0.9"))
    result = calc(make_offer("899.99"), make_offer("900"), global_settings=settings)
    assert result.suspicious_price_limit == D("900.0")
    assert rejections(result) == [OfferRejection.SUSPICIOUSLY_LOW_PRICE]


@pytest.mark.parametrize(
    ("offer", "expected"),
    [
        (make_offer("0", brand="MANN", article="OC90", stock=0, days=99), "other_article"),
        (make_offer("0", brand="MANN", stock=0, days=99), "other_brand"),
        (make_offer("0", stock=0, days=99), "non_positive_price"),
        (make_offer("1", stock=0, days=99), "out_of_stock"),
        (make_offer("1", days=99), "delivery_too_long"),
        (make_offer("1"), "suspiciously_low_price"),
    ],
)
def test_first_matching_rejection_reason_wins(offer: Offer, expected: str) -> None:
    assert rejections(calc(offer)) == [OfferRejection(expected)]


def test_minimum_eligible_price_is_used() -> None:
    result = calc(make_offer("1400"), make_offer("1250", source="json"), make_offer("1300"))
    assert result.min_competitor_price == D("1250")
    assert len(result.accepted_offers) == 3


def test_offers_may_be_any_iterable() -> None:
    result = calculate_price(make_product(), make_settings(), iter([make_offer("1250")]))
    assert result.recommended_price == D("1240.00")


# --- Decision: quarantine, unchanged, published ------------------------------


def test_price_spike_control_product_goes_to_quarantine() -> None:
    result = calc(make_offer("1500"), make_offer("2000"), published="1990")
    assert result.recommended_price == D("1490.00")
    assert result.applied_constraints == ()
    assert result.state is ResultState.QUARANTINE
    assert result.reason is ResultReason.PRICE_SPIKE
    assert result.change_percent_display == D("-25.1")
    assert result.change_percent is not None
    assert D("-25.13") < result.change_percent < D("-25.12")


def test_first_recommendation_without_published_price_goes_to_quarantine() -> None:
    result = calc(make_offer("1250"), published=None)
    assert result.recommended_price == D("1240.00")
    assert result.state is ResultState.QUARANTINE
    assert result.reason is ResultReason.NO_PREVIOUS_PRICE
    assert result.change_percent is None
    assert result.change_percent_display is None


@pytest.mark.parametrize(("published", "change"), [("1000", "20"), ("1500", "-20")])
def test_change_exactly_at_threshold_is_published(published: str, change: str) -> None:
    offer = make_offer("1210") if published == "1000" else make_offer("1100")
    purchase = "500" if published == "1000" else "1000"
    result = calc(offer, purchase=purchase, published=published)
    assert result.recommended_price == D("1200.00")
    assert result.change_percent == D(change)
    assert result.state is ResultState.PUBLISHED


def test_change_just_above_threshold_goes_to_quarantine() -> None:
    result = calc(make_offer("1210"), purchase="500", published="999.99")
    assert result.state is ResultState.QUARANTINE
    assert result.reason is ResultReason.PRICE_SPIKE
    # The display value rounds to 20.0, but the decision uses the exact change.
    assert result.change_percent_display == D("20.0")


def test_threshold_is_a_setting() -> None:
    result = calc(make_offer("1250"), published="1300", threshold="4")
    assert result.state is ResultState.QUARANTINE  # 4.615 % > 4 %


def test_recommendation_equal_to_published_is_unchanged() -> None:
    result = calc(make_offer("1250"), published="1240")
    assert result.state is ResultState.UNCHANGED
    assert result.reason is None
    assert result.recommended_price == D("1240.00")
    assert result.change_percent == 0
    assert result.change_percent_display == D("0.0")


def test_scenario_first_run_publishes_1240_over_1260() -> None:
    result = calc(
        make_offer("1250", article="0 986-452 041"),
        make_offer("1100", brand="MANN"),
        make_offer("450", brand="bosch", article="0986.452.041"),
        published="1260",
    )
    assert result.state is ResultState.PUBLISHED
    assert result.recommended_price == D("1240.00")
    assert rejections(result) == [
        OfferRejection.OTHER_BRAND,
        OfferRejection.SUSPICIOUSLY_LOW_PRICE,
    ]


# --- Partial data, inputs, labels, context -----------------------------------


@pytest.mark.parametrize("partial", [True, False])
def test_partial_data_flag_is_carried_without_changing_the_price(partial: bool) -> None:
    result = calc(make_offer("1250"), partial_data=partial)
    assert result.partial_data is partial
    assert result.recommended_price == D("1240.00")
    assert result.state is ResultState.PUBLISHED


def test_partial_data_flag_is_carried_when_price_is_retained() -> None:
    assert calc(partial_data=True).partial_data is True


def test_result_keeps_inputs_and_rules_version() -> None:
    product = make_product()
    settings = make_settings(ceiling="1500")
    global_settings = GlobalSettings(suspicious_price_factor=D("0.4"), max_delivery_days=10)
    result = calculate_price(product, settings, [], global_settings=global_settings)
    assert result.rules_version == RULES_VERSION
    assert result.product is product
    assert result.settings is settings
    assert result.global_settings is global_settings


def test_every_code_has_a_russian_label() -> None:
    codes = [*OfferRejection, *ResultState, *ResultReason, *Constraint]
    assert set(LABELS) == set(codes)
    assert label(OfferRejection.OTHER_BRAND) == "другой бренд"
    assert label(OfferRejection.SUSPICIOUSLY_LOW_PRICE) == "подозрительно низкая цена"
    assert label(ResultState.QUARANTINE) == "карантин"
    assert label(Constraint.LOWER_BOUND) == "ограничено снизу"


def test_result_does_not_depend_on_caller_decimal_context() -> None:
    with decimal.localcontext() as ctx:
        ctx.prec = 3
        ctx.rounding = decimal.ROUND_DOWN
        result = calc(make_offer("1500"), make_offer("2000"), published="1990")
    assert result.recommended_price == D("1490.00")
    assert result.state is ResultState.QUARANTINE


# --- Input validation --------------------------------------------------------


def test_published_price_must_be_positive() -> None:
    with pytest.raises(ValueError, match="published_price"):
        make_product(published="0")


def test_product_needs_brand_and_article() -> None:
    with pytest.raises(ValueError, match="article"):
        Product(brand=BRAND, article=" - ", purchase_price=D(1000))
    with pytest.raises(ValueError, match="brand"):
        Product(brand="  ", article=ARTICLE, purchase_price=D(1000))


@pytest.mark.parametrize(
    "kwargs",
    [{"markup": "-1"}, {"step": "-10"}, {"threshold": "-5"}],
)
def test_negative_settings_are_rejected(kwargs: dict[str, str]) -> None:
    with pytest.raises(ValueError, match=">= 0"):
        make_settings(**kwargs)


def test_offer_counts_must_be_non_negative_ints() -> None:
    with pytest.raises(ValueError, match="stock"):
        make_offer("1", stock=-1)
    with pytest.raises(ValueError, match="delivery_days"):
        make_offer("1", days=-1)
    with pytest.raises(TypeError):
        make_offer("1", stock=True)
