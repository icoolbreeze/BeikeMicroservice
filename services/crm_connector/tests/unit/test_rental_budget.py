from app.application.rental_budget import price_range_for_budget


def test_price_range_caps_the_upper_allowance_and_uses_half_budget_floor() -> None:
    assert price_range_for_budget(700) == (350, 900)
    assert price_range_for_budget(2_000) == (1_000, 2_500)
    assert price_range_for_budget(3_000) == (1_500, 3_500)


def test_shared_rent_omits_the_lower_budget_bound() -> None:
    assert price_range_for_budget(2_000, shared_rent=True) == (0, 2_500)
