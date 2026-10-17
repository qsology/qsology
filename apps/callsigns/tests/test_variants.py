import pytest
from apps.callsigns.models import Callsign, CallsignVariant, CommonSuffix
from django.core.exceptions import ValidationError
from django.db import IntegrityError

pytestmark = pytest.mark.django_db


@pytest.fixture
def variants(callsign):
    """Every shape of M0WME, plus a variant of another callsign"""
    other = Callsign.objects.create(call="K4GBI")
    return {
        "plain": callsign.get_variant(),
        "suffix": callsign.get_variant(suffix="P"),
        "mobile": callsign.get_variant(suffix="M"),
        "prefix": callsign.get_variant(prefix="ZL"),
        "both": callsign.get_variant(prefix="ZL", suffix="P"),
        "other": other.get_variant(suffix="P"),
    }


class TestCallsignVariant:
    @pytest.mark.parametrize(
        ("prefix", "suffix", "expected"),
        [
            ("", "", "M0WME"),
            ("", "P", "M0WME/P"),
            ("ZL", "", "ZL/M0WME"),
            ("ZL", "P", "ZL/M0WME/P"),
        ],
    )
    def test_full_call(self, callsign, prefix, suffix, expected):
        variant = callsign.get_variant(prefix=prefix, suffix=suffix)
        assert variant.full_call == expected
        assert str(variant) == expected

    def test_normalized_to_uppercase(self, callsign):
        variant = callsign.get_variant(prefix="zl/", suffix="/p")
        variant.refresh_from_db()

        assert variant.prefix == "ZL"
        assert variant.suffix == "P"
        assert variant.full_call == "ZL/M0WME/P"

    def test_normalized_on_save(self, callsign):
        variant = CallsignVariant.objects.create(
            callsign=callsign, prefix="vk9", suffix="mm"
        )
        assert variant.full_call == "VK9/M0WME/MM"

    def test_uppercase_enforced_by_database(self, callsign):
        variant = callsign.get_variant(suffix="P")
        with pytest.raises(IntegrityError):
            CallsignVariant.objects.filter(pk=variant.pk).update(suffix="p")

    def test_get_variant_is_idempotent(self, callsign):
        first = callsign.get_variant(suffix="P")
        second = callsign.get_variant(suffix="p")
        assert first == second
        assert callsign.variants.count() == 1

    def test_duplicate_rejected(self, callsign):
        callsign.get_variant(suffix="P")
        with pytest.raises(ValidationError):
            CallsignVariant.objects.create(callsign=callsign, suffix="P")

    @pytest.mark.parametrize(
        ("field", "value"), [("prefix", "Z-L"), ("suffix", "P!")]
    )
    def test_invalid_parts(self, callsign, field, value):
        with pytest.raises(ValidationError):
            CallsignVariant.objects.create(callsign=callsign, **{field: value})

    def test_free_form_suffix(self, callsign):
        variant = callsign.get_variant(suffix="QRP")
        assert variant.full_call == "M0WME/QRP"
        assert variant.common_suffix is None

    @pytest.mark.parametrize("suffix", list(CommonSuffix))
    def test_common_suffix(self, callsign, suffix):
        variant = callsign.get_variant(suffix=suffix)
        assert variant.common_suffix is suffix

    def test_common_suffixes(self):
        assert dict(CommonSuffix.choices) == {
            "P": "Portable - /P",
            "M": "Mobile - /M",
            "AM": "Aeronautical Mobile - /AM",
            "MM": "Maritime Mobile - /MM",
        }

    def test_is_plain(self, variants):
        assert variants["plain"].is_plain
        assert not variants["suffix"].is_plain
        assert not variants["prefix"].is_plain


class TestReciprocal:
    def test_prefix_is_reciprocal_by_default(self, callsign):
        assert callsign.get_variant(prefix="ZL").is_reciprocal
        assert callsign.get_variant(prefix="ZL", suffix="P").is_reciprocal

    def test_suffix_is_not_reciprocal_by_default(self, callsign):
        assert not callsign.get_variant().is_reciprocal
        assert not callsign.get_variant(suffix="P").is_reciprocal

    def test_suffix_can_be_reciprocal(self):
        # e.g. operating in Canada as W1AW/VE3
        callsign = Callsign.objects.create(call="W1AW")
        variant = callsign.get_variant(suffix="VE3", is_reciprocal=True)
        assert variant.is_reciprocal

    def test_prefix_can_be_not_reciprocal(self, callsign):
        assert not callsign.get_variant(
            prefix="GB", is_reciprocal=False
        ).is_reciprocal


class TestVariantFiltering:
    @pytest.mark.parametrize(
        "value", ["M0WME", "m0wme", "M0WME/P", "ZL/M0WME", "ZL/M0WME/P"]
    )
    def test_for_callsign_finds_every_variant(self, variants, value):
        found = set(CallsignVariant.objects.for_callsign(value))
        expected = {v for key, v in variants.items() if key != "other"}
        assert found == expected

    def test_for_callsign_unknown(self, variants):
        assert not CallsignVariant.objects.for_callsign("G0ABC").exists()

    @pytest.mark.parametrize(
        ("value", "key"),
        [
            ("M0WME", "plain"),
            ("m0wme/p", "suffix"),
            ("ZL/M0WME", "prefix"),
            ("zl/m0wme/p", "both"),
        ],
    )
    def test_lookup(self, variants, value, key):
        assert CallsignVariant.objects.lookup(value).get() == variants[key]

    def test_lookup_is_exact(self, variants):
        assert not CallsignVariant.objects.lookup("M0WME/MM").exists()

    def test_plain(self, variants):
        assert set(CallsignVariant.objects.plain()) == {variants["plain"]}

    def test_with_any_suffix(self, variants):
        found = set(CallsignVariant.objects.for_callsign("M0WME").with_suffix())
        assert found == {
            variants["suffix"],
            variants["mobile"],
            variants["both"],
        }

    def test_with_suffix(self, variants):
        found = set(CallsignVariant.objects.with_suffix("/p"))
        assert found == {
            variants["suffix"],
            variants["both"],
            variants["other"],
        }

    def test_with_any_prefix(self, variants):
        found = set(CallsignVariant.objects.with_prefix())
        assert found == {variants["prefix"], variants["both"]}

    def test_with_prefix(self, variants):
        assert not CallsignVariant.objects.with_prefix("VK9").exists()
        assert CallsignVariant.objects.with_prefix("zl").count() == 2

    def test_with_prefix_and_suffix(self, variants):
        found = CallsignVariant.objects.with_prefix("ZL").with_suffix("P")
        assert list(found) == [variants["both"]]

    def test_reciprocal(self, variants):
        found = set(CallsignVariant.objects.reciprocal())
        assert found == {variants["prefix"], variants["both"]}
