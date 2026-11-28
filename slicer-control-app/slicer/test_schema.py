"""
slicer/test_schema.py
Checks that the settings table, the parameter defaults and the engine agree.

Run with: python3 -m slicer.test_schema   (from slicer-control-app/)

``schema.py`` and ``params.py`` describe the same 220 settings from two
directions, and both were generated from upstream rather than typed. That makes
drift the thing to guard against: a value corrected in one file and not the
other produces an interface whose defaults are not the defaults that get
sliced, and nothing would raise. These tests are cheap and they fail loudly.

The engine itself is the third party to the agreement, so the key sets are read
back out of its source rather than taken on trust.
"""

import re
import sys
from pathlib import Path

from slicer import params, schema


def engine_keys():
    """Every ``slicing_data`` key the vendored engine reads.

    Both access forms, tolerating the line wrapping the engine uses heavily --
    a regex that assumed ``get("key"`` sat on one line undercounted this by 148
    keys the first time it was run.
    """
    found = set()
    base = Path(__file__).resolve().parent / "olos"
    for name in ("prusa_slicer.py", "slicers_shared_methods.py"):
        src = (base / name).read_text()
        found |= set(re.findall(
            r'slicing_data\s*\[\s*[\'"]([a-z_0-9]+)[\'"]\s*\]', src))
        found |= set(re.findall(
            r'slicing_data\s*\.\s*get\s*\(\s*[\'"]([a-z_0-9]+)[\'"]', src))
    return found


CHECKS = []


def check(fn):
    CHECKS.append(fn)
    return fn


@check
def test_schema_and_params_agree_on_every_default():
    a, b = schema.defaults(), params.defaults()
    assert set(a) == set(b), (
        f"only in schema: {sorted(set(a) - set(b))}\n"
        f"only in params: {sorted(set(b) - set(a))}")
    bad = {k: (a[k], b[k]) for k in a if a[k] != b[k]}
    assert not bad, f"defaults disagree: {bad}"


@check
def test_every_schema_key_is_one_the_engine_reads():
    """A field for a key the engine ignores is a control that does nothing."""
    extra = sorted(set(schema.defaults()) - engine_keys())
    assert not extra, f"the engine never reads: {extra}"


@check
def test_every_engine_key_is_accounted_for():
    """The engine must not read anything the table has never heard of.

    ``flat_surface_skin_max_run_layers`` is the one exception, and it is
    deliberate: the engine derives it as ``flat_surface_skin_layers + 2`` and
    there is nothing for an operator to set.
    """
    DERIVED = {"flat_surface_skin_max_run_layers"}
    known = set(schema.defaults()) | set(params.RUNTIME_KEYS) | DERIVED
    missing = sorted(engine_keys() - known)
    assert not missing, f"read by the engine, in neither table: {missing}"


@check
def test_no_runtime_key_has_a_control():
    """Paths, the model size and the engine's own scratch values are not
    settings, and showing them would invite someone to set them."""
    overlap = sorted(set(schema.defaults()) & set(params.RUNTIME_KEYS))
    assert not overlap, f"runtime keys given controls: {overlap}"


@check
def test_required_keys_all_have_a_control_or_come_from_the_run():
    """Every required key must be reachable: set in the interface, or supplied
    per run. One that is neither is a KeyError waiting for a fresh profile."""
    for key in params.REQUIRED_KEYS:
        assert key in schema.BY_KEY or key in params.RUNTIME_KEYS, \
            f"{key} is required but nothing provides it"


@check
def test_defaults_match_their_declared_kind():
    for f in schema.FIELDS:
        if f.default is None:
            continue              # explicitly "let the engine decide"
        if f.kind == "bool":
            assert isinstance(f.default, bool), f.key
        elif f.kind == "int":
            assert isinstance(f.default, int) and not isinstance(f.default, bool), \
                f"{f.key} is kind=int but default is {f.default!r}"
        elif f.kind == "float":
            assert isinstance(f.default, (int, float)) and not isinstance(f.default, bool), \
                f"{f.key} is kind=float but default is {f.default!r}"
        elif f.kind == "tuple":
            assert isinstance(f.default, tuple), f.key
        elif f.kind == "choice":
            allowed = [v for v, _ in f.choices]
            assert f.default in allowed, \
                f"{f.key} defaults to {f.default!r}, not in {allowed}"


@check
def test_every_choice_offers_its_own_default():
    """A select whose default is not among its options silently changes the
    setting the moment the interface loads."""
    for f in schema.FIELDS:
        if f.kind == "choice":
            assert f.choices, f.key
            assert f.default in [v for v, _ in f.choices], f.key


@check
def test_percent_fields_are_stored_as_fractions():
    """The engine wants fractions; operators type percentages. The table must
    hold fractions, and round-trip through the display conversion."""
    for f in schema.FIELDS:
        if not f.percent:
            continue
        assert f.default is None or f.default < 1.0, \
            f"{f.key} = {f.default!r} looks like a percentage, not a fraction"
        shown = schema.to_display(f, 0.15)
        assert abs(shown - 15.0) < 1e-9, shown
        assert abs(schema.from_display(f, shown) - 0.15) < 1e-9


@check
def test_every_field_lands_in_a_declared_group():
    names = set(schema.GROUP_ORDER)
    for f in schema.FIELDS:
        assert f.group in names, f"{f.key} is in undeclared group {f.group!r}"


@check
def test_every_field_is_reachable_through_the_groups():
    """Nothing editable may be invisible: the interface walks the groups, so a
    field in no group is a setting with no control."""
    reachable = {f.key for _, _, rows in schema.groups_with_fields() for f in rows}
    editable = {f.key for f in schema.FIELDS if f.editable}
    assert reachable == editable, sorted(editable - reachable)


@check
def test_the_hidden_three_are_the_sla_flags():
    """The only hidden fields should be PrusaSlicer's hollowing, support and
    pad toggles, which OLOS disables and the powder bed makes unnecessary."""
    hidden = sorted(f.key for f in schema.FIELDS if f.hidden)
    assert hidden == ["is_hollowing_enabled", "is_pad_enabled",
                      "is_support_enabled"], hidden


@check
def test_coercion_and_clamping():
    f = schema.BY_KEY["contour_line_count"]
    assert schema.coerce(f, "3") == 3
    assert schema.coerce(f, 3.7) == 4
    g = schema.BY_KEY["layer_height"]
    assert isinstance(schema.coerce(g, "0.05"), float)
    assert schema.clamp(g, -5) == 0          # OLOS declares min="0"
    c = schema.BY_KEY["engraving_mode"]
    assert schema.coerce(c, "engraving") == "engraving"
    try:
        schema.coerce(c, "nonsense")
    except ValueError:
        pass
    else:
        raise AssertionError("an invalid choice was accepted")


@check
def test_a_built_slicing_data_passes_the_engines_own_lookups():
    """Everything the engine reads must resolve from a schema-built dict.

    Not a type check: this asserts the table is complete enough that no
    ``slicing_data[...]`` can raise, which is the failure the engine gives from
    six thousand lines in with no indication of which key was missing.
    """
    class MS:
        x = y = z = 20.0
    data = params.build_slicing_data("/m.stl", "/o.nc", MS(),
                                     overrides=schema.defaults())

    # Keys the engine writes into slicing_data itself before reading them back,
    # so their absence here is correct, not a gap:
    #   visicut_style       -- set true at the top of slice_model
    #   work_origin_x/_y    -- set to 0.0 once the layer regions are built
    #   layer_index_offset  -- computed from the print-height lower limit
    # and one it derives rather than reads:
    #   flat_surface_skin_max_run_layers = flat_surface_skin_layers + 2
    ENGINE_SETS = {"visicut_style", "work_origin_x", "work_origin_y",
                   "layer_index_offset", "flat_surface_skin_max_run_layers"}
    for key in engine_keys() - ENGINE_SETS:
        assert key in data, f"the engine reads {key}, the built dict lacks it"


def main():
    failures = 0
    for fn in CHECKS:
        try:
            fn()
        except Exception as exc:
            failures += 1
            print(f"FAIL  {fn.__name__}: {exc}")
        else:
            print(f"ok    {fn.__name__}")
    print(f"\n{len(CHECKS) - failures}/{len(CHECKS)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
