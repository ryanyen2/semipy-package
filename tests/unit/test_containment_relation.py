"""U11: the containment relation for extractor-category slots (R20).

"Every extracted output field value occurs in the input text modulo declared
normalizers." The relation is label-free and snapshot-free: it checks a single
(input, output) pair using only the current input text, so it ships as an
extractor floor (D3, web scraper).
"""
from __future__ import annotations

import json
from dataclasses import dataclass

import pytest
from pydantic import BaseModel

from semipy.contract.relations import (
    CONTAINMENT_NORMALIZERS,
    DEFAULT_CONTAINMENT_NORMALIZERS,
    ContainmentRegistrationError,
    ContainmentRelation,
)


# ---------------------------------------------------------------------------
# D3 fixture: the web-scraper slot -- `#> extract product name, price, and
# availability from this page`.
# ---------------------------------------------------------------------------


@dataclass
class Product:
    name: str
    price: str
    availability: str


_PAGE = (
    "Acme Widget Pro\n"
    "In stock — ships in 2 days.\n"
    "Price:   $19.99\n"
)


def _scraper_relation(**kwargs) -> ContainmentRelation:
    return ContainmentRelation.for_slot(
        output_type=Product,
        input_types={"page": str},
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Scenario 1: extractor output traceable to page text passes.
# ---------------------------------------------------------------------------


def test_output_traceable_to_page_text_passes():
    rel = _scraper_relation()
    out = Product(name="Acme Widget Pro", price="$19.99", availability="In stock")
    result = rel.evaluate({"page": _PAGE}, out)
    assert result.holds
    assert result.failures == []


def test_whitespace_and_case_are_forgiven_by_default_normalizers():
    # Value has collapsed/differently-cased whitespace vs the page; still traces.
    rel = _scraper_relation()
    out = Product(name="acme WIDGET pro", price="$19.99", availability="in stock")
    assert rel.evaluate({"page": _PAGE}, out).holds


# ---------------------------------------------------------------------------
# Scenario 2: a hallucinated field value fails, naming the field.
# ---------------------------------------------------------------------------


def test_hallucinated_field_fails_and_names_the_field():
    rel = _scraper_relation()
    out = Product(name="Acme Widget Pro", price="$49.99", availability="In stock")
    result = rel.evaluate({"page": _PAGE}, out)
    assert not result.holds
    assert result.failing_fields() == ["price"]
    assert "price" in result.message()
    # Fields that *do* trace are not falsely flagged.
    assert "name" not in result.failing_fields()


def test_nested_hallucinated_field_is_named_by_path():
    rel = ContainmentRelation.for_slot(output_type=dict, input_types={"page": str})
    out = {"product": {"name": "Acme Widget Pro", "price": "$49.99"}}
    result = rel.evaluate({"page": _PAGE}, out)
    assert not result.holds
    assert result.failing_fields() == ["product.price"]


# ---------------------------------------------------------------------------
# Scenario 3: numeric / date normalizers pass when declared, fail without.
# ---------------------------------------------------------------------------


def test_parsed_price_passes_with_numeric_normalizer_and_fails_without():
    page = "Laptop of the year — today only $1,299.  Buy now."
    out = {"name": "Laptop", "price": 1299.0}  # a parsed float, not the source string

    without = ContainmentRelation(text_field="page")  # DEFAULT normalizers (no numeric)
    result = without.evaluate({"page": page}, out)
    assert not result.holds
    assert result.failing_fields() == ["price"]

    with_numeric = ContainmentRelation(
        text_field="page",
        normalizers=("case_fold", "whitespace_collapse", "numeric"),
    )
    assert with_numeric.evaluate({"page": page}, out).holds


def test_reformatted_date_passes_with_date_normalizer_and_fails_without():
    page = "The launch event is scheduled for July 11, 2026 in Boston."
    out = {"event": "launch event", "date": "2026-07-11"}  # reformatted ISO date

    without = ContainmentRelation(text_field="page")
    result = without.evaluate({"page": page}, out)
    assert not result.holds
    assert result.failing_fields() == ["date"]

    with_date = ContainmentRelation(
        text_field="page",
        normalizers=("case_fold", "whitespace_collapse", "date_reformat"),
    )
    assert with_date.evaluate({"page": page}, out).holds


def test_numeric_normalizer_does_not_match_an_absent_number():
    # 500 is not on the page; numeric normalizer must not manufacture a match.
    page = "On sale: was $1,299, now $999."
    rel = ContainmentRelation(
        text_field="page",
        normalizers=("case_fold", "whitespace_collapse", "numeric"),
    )
    result = rel.evaluate({"page": page}, {"price": 500.0})
    assert not result.holds
    assert result.failing_fields() == ["price"]


# ---------------------------------------------------------------------------
# Scenario 4: the relation on a non-extractor slot is rejected at registration.
# ---------------------------------------------------------------------------


def test_registration_rejects_slot_with_no_text_input():
    with pytest.raises(ContainmentRegistrationError, match="no str-typed input"):
        ContainmentRelation.for_slot(output_type=str, input_types={"count": int, "rate": float})


def test_registration_rejects_scalar_only_output():
    with pytest.raises(ContainmentRegistrationError, match="scalar-only"):
        ContainmentRelation.for_slot(output_type=float, input_types={"page": str})


def test_registration_rejects_named_text_field_that_is_not_text():
    with pytest.raises(ContainmentRegistrationError, match="not text"):
        ContainmentRelation.for_slot(
            output_type=str, input_types={"page": str, "n": int}, text_field="n"
        )


def test_registration_rejects_named_text_field_not_in_inputs():
    with pytest.raises(ContainmentRegistrationError, match="not an input"):
        ContainmentRelation.for_slot(
            output_type=str, input_types={"page": str}, text_field="body"
        )


def test_registration_rejects_unknown_normalizer():
    with pytest.raises(ContainmentRegistrationError, match="unknown normalizer"):
        ContainmentRelation.for_slot(
            output_type=str, input_types={"page": str}, normalizers=("case_fold", "stem")
        )


def test_registration_requires_naming_the_text_field_when_several_exist():
    with pytest.raises(ContainmentRegistrationError, match="multiple text inputs"):
        ContainmentRelation.for_slot(
            output_type=Product, input_types={"page": str, "raw_html": str}
        )
    # ...and naming one resolves the ambiguity.
    rel = ContainmentRelation.for_slot(
        output_type=Product, input_types={"page": str, "raw_html": str}, text_field="page"
    )
    assert rel.text_field == "page"


# ---------------------------------------------------------------------------
# Registration accepts genuine extractor shapes (str / dict / dataclass / model).
# ---------------------------------------------------------------------------


class ProductModel(BaseModel):
    name: str
    price: str


@pytest.mark.parametrize("output_type", [str, dict, Product, ProductModel])
def test_registration_accepts_str_and_record_outputs(output_type):
    rel = ContainmentRelation.for_slot(output_type=output_type, input_types={"page": str})
    assert rel.text_field == "page"
    assert rel.normalizers == DEFAULT_CONTAINMENT_NORMALIZERS


def test_pydantic_model_output_is_walked_to_leaves():
    rel = ContainmentRelation(text_field="page")
    out = ProductModel(name="Acme Widget Pro", price="$19.99")
    assert rel.evaluate({"page": _PAGE}, out).holds
    bad = ProductModel(name="Acme Widget Pro", price="$49.99")
    assert rel.evaluate({"page": _PAGE}, bad).failing_fields() == ["price"]


# ---------------------------------------------------------------------------
# Scenario 5: serializes into a shipped floor and evaluates at the consumer site
# with NO snapshot data present -- only the current input text (D3).
# ---------------------------------------------------------------------------


def test_relation_round_trips_through_floor_json():
    rel = _scraper_relation(normalizers=("case_fold", "whitespace_collapse", "numeric"))
    shipped = json.dumps(rel.to_dict())               # what travels in the floor
    rehydrated = ContainmentRelation.from_dict(json.loads(shipped))
    assert rehydrated == rel
    assert rehydrated.to_dict()["kind"] == "containment"


def test_from_dict_defaults_normalizers_when_absent():
    rel = ContainmentRelation.from_dict({"text_field": "page"})
    assert rel.normalizers == DEFAULT_CONTAINMENT_NORMALIZERS


def test_shipped_floor_evaluates_on_current_text_without_any_snapshot():
    # Developer side: register + serialize into the shipped floor.
    dev_rel = _scraper_relation()
    floor_entry = json.dumps(dev_rel.to_dict())

    # Consumer side: rehydrate, then evaluate against a *fresh* page fetched now.
    # There is no original snapshot -- only the current input text at hand.
    consumer_rel = ContainmentRelation.from_dict(json.loads(floor_entry))
    current_page = "Gizmo 3000\nOut of stock.\nPrice: $5.00\n"

    faithful = Product(name="Gizmo 3000", price="$5.00", availability="Out of stock")
    assert consumer_rel.evaluate({"page": current_page}, faithful).holds

    field_inventing = Product(name="Gizmo 3000", price="$500.00", availability="Out of stock")
    blocked = consumer_rel.evaluate({"page": current_page}, field_inventing)
    assert not blocked.holds
    assert blocked.failing_fields() == ["price"]


# ---------------------------------------------------------------------------
# Verification (plan U11): the D3 scraper floor blocks a field-inventing
# candidate when evaluated directly (the U9 gate machinery lands later).
# ---------------------------------------------------------------------------


def test_d3_floor_blocks_a_field_inventing_candidate():
    # The floor as it would ship: the containment relation, serialized.
    floor = _scraper_relation().to_dict()
    rel = ContainmentRelation.from_dict(floor)

    page = "SuperMixer 900\nIn stock\nPrice: $129.00\n"
    good_candidate_output = Product(name="SuperMixer 900", price="$129.00", availability="In stock")
    inventing_candidate_output = Product(
        name="SuperMixer 900 Deluxe Edition",  # a name never on the page
        price="$129.00",
        availability="In stock",
    )

    assert rel.evaluate({"page": page}, good_candidate_output).holds
    blocked = rel.evaluate({"page": page}, inventing_candidate_output)
    assert not blocked.holds
    assert blocked.failing_fields() == ["name"]


# ---------------------------------------------------------------------------
# Evaluation edge behavior.
# ---------------------------------------------------------------------------


def test_none_and_boolean_leaves_are_not_flagged():
    # A None optional field and a boolean flag are derived, not extracted spans.
    rel = ContainmentRelation(text_field="page")
    out = {"name": "Acme Widget Pro", "discount": None, "in_stock": True}
    assert rel.evaluate({"page": _PAGE}, out).holds


def test_missing_text_field_makes_nonempty_extractions_fail():
    # No text to trace against -> any real extracted value is unsupported.
    rel = ContainmentRelation(text_field="page")
    result = rel.evaluate({}, {"name": "Acme Widget Pro"})
    assert not result.holds
    assert result.failing_fields() == ["name"]


def test_declared_normalizer_set_is_closed_and_stable():
    assert set(DEFAULT_CONTAINMENT_NORMALIZERS) <= set(CONTAINMENT_NORMALIZERS)
    assert CONTAINMENT_NORMALIZERS == (
        "case_fold",
        "whitespace_collapse",
        "numeric",
        "date_reformat",
    )
