"""
slicer/params.py
The slicing-parameter contract: what the vendored engine reads, and what we
hand it.

The engine takes one ``slicing_data`` dict. It is not a documented interface --
upstream builds it in ``threeD_slice.py``, a FastAPI handler that probes a
pydantic model with ``getattr`` -- so this module was derived by reading the
engine instead of by trusting that handler. Three reasons that mattered:

1. **Upstream's builder does not run.** ``threeD_slice.py:165`` reads
   ``slice_data`` inside ``_use_prusa_slicer``, which takes no such parameter.
   It is a ``NameError`` on the first call, so the PrusaSlicer path cannot have
   been exercised in that form, and it could not be ported as-is.
2. **It is incomplete.** It passes 175 keys; the engine reads 230. The other 55
   fall through to the engine's own ``.get`` fallbacks.
3. **Only 13 keys are genuinely required.** Everything else the engine defaults
   itself, so a faithful 230-key transcription would mostly be restating
   upstream's fallbacks in a second place where they could drift.

Measured against ``prusa_slicer.py`` and ``slicers_shared_methods.py``, the
engine reads 230 distinct keys. Every one is accounted for here:

| Group | Count | What it is |
| --- | --- | --- |
| ``SLICER_DEFAULTS`` | 200 | Exposure, hatching, contours, skins, zones. |
| ``MACHINE_DEFAULTS`` | 16 | Bed, parking, powder, recoater. Physical. |
| ``RASTER_DEFAULTS`` | 4 | Mask resolution, which trades detail for time. |
| ``RUNTIME_KEYS`` | 9 | Paths, model size, and values the engine sets itself. |
| ``infill_image_*`` | 1 | ``flat_surface_skin_max_run_layers``, derived. |

The values come from OLOS's own UI defaults
(``OLOS_Interface/src/constants.ts``, ``DEFAULT_PRUSA_SLICER_SETTINGS``), parsed
rather than retyped, so a part sliced here matches what OLOS would produce from
the same settings. Where the UI defines no default, the engine's own fallback is
used and marked below.

**Units are the engine's, everywhere in this file.** That is the one
normalisation applied to upstream's numbers: its UI stores six overlap settings
as percentages and its handler divides them by 100 on the way in. Keeping two
representations is how a 15 becomes 1500%, so this module holds fractions and
``PERCENT_KEYS`` tells the UI which to *display* as percentages.

Known hazard, recorded rather than guessed at: the ``solid_*`` overlap settings
(``solid_line_overlap``, ``solid_contour_overlap`` and the anchor-pad,
transition and close-top-bottom variants) are read by the engine as fractions,
are defined by OLOS's UI as plain numbers, and are passed by its handler not at
all -- they are in the 55. Their defaults are 0, where the two conventions agree,
so nothing is wrong today. An operator who sets one to 15 meaning 15% would get
1500%. Solid mode is off by default; when it is wired up, that needs settling
against a real print, not a reading of the code.
"""

#: Keys the engine requires outright -- bracket access, no fallback. Supplying
#: anything less is a ``KeyError`` from inside a 6000-line file, so
#: :func:`build_slicing_data` checks them up front instead.
#:
#: ``layer_index_offset``, ``visicut_style`` and ``work_origin_x``/``_y`` are
#: also bracket-accessed, but the engine assigns them itself before reading
#: them, so they are not inputs.
REQUIRED_KEYS = (
    "model_file_path",
    "gcode_file_path",
    "model_size",
    "layer_height",
    "layer_speed",
    "contour_power",
    "contour_speed",
    "infill_power",
    "infill_speed",
    "infill_image_pixel_size",
    "infill_image_size",
    "infill_noise",
    "use_image_size",
)

#: Set by the engine, or supplied per run. Never defaults, never a UI control.
RUNTIME_KEYS = (
    "model_file_path",
    "gcode_file_path",
    "model_size",
    "layer_index_offset",
    "visicut_style",
    "work_origin_x",
    "work_origin_y",
    "print_height_limit",
    "print_height_lower_limit",
)

#: Stored as fractions, shown as percentages. See the unit note above.
PERCENT_KEYS = (
    'contour_infill_overlap',
    'flat_surface_skin_contour_overlap',
    'flat_surface_skin_hatch_overlap',
    'flat_surface_skin_second_pass_contour_overlap',
    'flat_surface_skin_second_pass_hatch_overlap',
    'roof_hollow_infill_overlap',
)

#: Upstream UI names that differ from the engine's, and the transform between
#: them. Kept so an OLOS profile can still be read, and so the renames are
#: recorded somewhere other than a handler that does not run.
#:
#:   ``reservoirLiftPerLayer`` -> ``powder_feed``, negated: the reservoir lifts
#:   to feed powder, and the engine wants the signed axis move.
#:   ``recoaterFeedRate``     -> ``recoater_feed``, unchanged.
UPSTREAM_ALIASES = {
    "reservoir_lift_per_layer": ("powder_feed", lambda v: -abs(float(v))),
    "recoater_feed_rate": ("recoater_feed", lambda v: int(v)),
}

#: Exposure, hatching, contours, skins and per-zone settings: 200 keys.
SLICER_DEFAULTS = {
    'auto_contour_line_count_enabled': False,
    'body_engraving_enabled': False,
    'contour_infill_overlap': 0.15,
    'contour_line_count': 3,
    'contour_power': 50,
    'contour_speed': 3000,
    'engraving_acceleration': 500,
    'engraving_contour_thickness': 0.8,
    'engraving_grid_line_thickness': 0.8,
    'engraving_grid_spacing': 2,
    'engraving_mode': 'vectoring',
    'engraving_raster_direction': 'x',
    'fill_enclosed_hollows': False,
    'first_contour_power': 55,
    'first_contour_speed': 2000,
    'first_engraving_hatch_spacing': 0.2,
    'first_infill_power': 55,
    'first_infill_speed': 2000,
    'first_layers_count': 1,
    'first_zone_engraving_enabled': False,
    'flat_surface_skin_boundary_mode': 'engrave',
    'flat_surface_skin_contour_line_count': 3,
    'flat_surface_skin_contour_overlap': 0.15,
    'flat_surface_skin_cross_hatch': False,
    'flat_surface_skin_hatch_overlap': 0.0,
    'flat_surface_skin_hatch_spacing': 0.2,
    'flat_surface_skin_layers': 3,
    'flat_surface_skin_pattern': 'hatch',
    'flat_surface_skin_power': 50,
    'flat_surface_skin_raster_direction': 'x',
    'flat_surface_skin_roof_only_layers': False,
    'flat_surface_skin_second_pass_boundary_mode': 'engrave',
    'flat_surface_skin_second_pass_contour_line_count': 3,
    'flat_surface_skin_second_pass_contour_overlap': 0.15,
    'flat_surface_skin_second_pass_cross_hatch': False,
    'flat_surface_skin_second_pass_enabled': False,
    'flat_surface_skin_second_pass_hatch_overlap': 0.0,
    'flat_surface_skin_second_pass_power': 50,
    'flat_surface_skin_second_pass_raster_direction': 'x',
    'flat_surface_skin_second_pass_speed': 3000,
    'flat_surface_skin_speed': 3000,
    'flat_surface_skin_spiral_direction': 'outside-in',
    'grid_clearance': 0.2,
    'grid_spacing': 2,
    'hatch_rotation_group_layers': 5,
    'hatch_rotation_grouping_enabled': False,
    'hatch_rotation_per_layer': 67,
    'infill_angle': 0,
    'infill_hatch_spacing': 1,
    'infill_image_pixel_size': (1, 1),
    'infill_image_size': (1, 1),
    'infill_line_count': 3,
    'infill_line_thickness': 0.8,
    'infill_noise': 5,
    'infill_power': 50,
    'infill_speed': 3000,
    'infill_type': 'Lines',
    'intermediate_contour_power': 45,
    'intermediate_contour_speed': 2500,
    'intermediate_engraving_hatch_spacing': 0.2,
    'intermediate_infill_power': 45,
    'intermediate_infill_speed': 2500,
    'intermediate_layers_count': 2,
    'intermediate_zone_enabled': False,
    'intermediate_zone_engraving_enabled': False,
    'internal_floor_support_angle': 45,
    'internal_floor_support_boundary_clearance': 0,
    'internal_floor_support_enabled': False,
    'internal_floor_support_grid_clearance': 0.2,
    'internal_floor_support_grid_spacing': 2,
    'internal_floor_support_group_layers': 1,
    'internal_floor_support_grouping_enabled': False,
    'internal_floor_support_hatch_spacing': 1,
    'internal_floor_support_line_count': 3,
    'internal_floor_support_line_overlap': 0,
    'internal_floor_support_minimum_area': 0.1,
    'internal_floor_support_mode': 'vectoring',
    'internal_floor_support_optimize_scan_order': True,
    'internal_floor_support_power': 50,
    'internal_floor_support_rotation_per_layer': 67,
    'internal_floor_support_speed': 3000,
    'internal_floor_support_track_width': 0.2,
    'internal_floor_support_type': 'Lines',
    'is_hollowing_enabled': False,
    'is_pad_enabled': False,
    'is_support_enabled': False,
    'layer_height': 1,
    'layer_speed': 1000,
    'line_bundle_spacing': 0.2,
    'match_engraved_shell_to_vector_wall': True,
    'multi_variable_grid_enabled': False,
    'nominal_vector_track_width': 0.2,
    'optimize_scan_order': True,
    'roof_hollow_infill_enabled': False,
    'roof_hollow_infill_overlap': 0.15,
    'scanline_spacing': 0.2,
    'secondary_variable_engraving_grid_line_thickness': 0.8,
    'secondary_variable_engraving_grid_spacing': 1,
    'secondary_variable_grid_clearance': 0.2,
    'secondary_variable_grid_hatch_spacing': 1,
    'secondary_variable_grid_layers_before_roof': 6,
    'secondary_variable_grid_line_count': 3,
    'secondary_variable_grid_spacing': 1,
    'slanted_surface_skin_cross_hatch': False,
    'slanted_surface_skin_enabled': True,
    'slanted_surface_skin_hatch_spacing': 0.2,
    'slanted_surface_skin_layers': 3,
    'slanted_surface_skin_power': 50,
    'slanted_surface_skin_raster_direction': 'diagonal',
    'slanted_surface_skin_speed': 3000,
    'solid_alternate_lines': True,
    'solid_alternate_mode': 'edge-interleave',
    'solid_anchor_pad_enabled': False,
    'solid_anchor_pad_layers': 1,
    'solid_anchor_pad_line_overlap': 0,
    'solid_anchor_pad_power': 50,
    'solid_anchor_pad_rotate_each_layer': False,
    'solid_anchor_pad_rotation_per_layer': 90,
    'solid_anchor_pad_size': 20,
    'solid_anchor_pad_speed': 3000,
    'solid_close_top_bottom_enabled': False,
    'solid_close_top_bottom_layers': 3,
    'solid_close_top_bottom_line_overlap': 0,
    'solid_close_top_bottom_power': 50,
    'solid_close_top_bottom_speed': 3000,
    'solid_contour_enabled': True,
    'solid_contour_line_count': 3,
    'solid_contour_overlap': 0,
    'solid_contour_power': 50,
    'solid_contour_speed': 3000,
    'solid_cross_hatch': False,
    'solid_first_engraving_hatch_spacing': 0.2,
    'solid_first_layers_count': 1,
    'solid_first_power': 50,
    'solid_first_speed': 3000,
    'solid_first_zone_enabled': False,
    'solid_first_zone_engraving_enabled': True,
    'solid_intermediate_engraving_hatch_spacing': 0.2,
    'solid_intermediate_layers_count': 1,
    'solid_intermediate_power': 50,
    'solid_intermediate_speed': 3000,
    'solid_intermediate_zone_enabled': False,
    'solid_intermediate_zone_engraving_enabled': True,
    'solid_island_scan_strategy': 'edge-interleave',
    'solid_island_scanning_enabled': False,
    'solid_island_size': 3,
    'solid_line_overlap': 0,
    'solid_merge_objects_by_layer': True,
    'solid_mode_enabled': False,
    'solid_power': 50,
    'solid_raster_direction': 'x',
    'solid_speed': 3000,
    'solid_track_width': 0.2,
    'solid_transition_contour_enabled': True,
    'solid_transition_contour_line_count': 1,
    'solid_transition_contour_overlap': 0,
    'solid_transition_contour_power': 50,
    'solid_transition_contour_speed': 3000,
    'solid_transition_enabled': False,
    'solid_transition_grid_clearance': 0.2,
    'solid_transition_grid_spacing': 1,
    'solid_transition_hatch_spacing': 1,
    'solid_transition_layers': 1,
    'solid_transition_line_count': 1,
    'solid_transition_line_overlap': 0,
    'solid_transition_pattern': 'lines',
    'solid_transition_power': 50,
    'solid_transition_raster_direction': 'x',
    'solid_transition_rotate_each_layer': False,
    'solid_transition_rotation_per_layer': 90,
    'solid_transition_speed': 3000,
    'solid_transition_track_width': 0.2,
    'surface_skin_alternate_lines': False,
    'surface_skin_alternate_mode': 'two-pass',
    'surface_skin_enabled': True,
    'surface_skin_engraving_enabled': False,
    'surface_skin_hatch_spacing': 0.2,
    'surface_skin_layers': 3,
    'surface_skin_power': 50,
    'surface_skin_speed': 3000,
    'tertiary_variable_engraving_grid_line_thickness': 0.8,
    'tertiary_variable_engraving_grid_spacing': 1,
    'tertiary_variable_grid_clearance': 0.2,
    'tertiary_variable_grid_enabled': False,
    'tertiary_variable_grid_hatch_spacing': 1,
    'tertiary_variable_grid_layers_before_roof': 6,
    'tertiary_variable_grid_line_count': 3,
    'tertiary_variable_grid_spacing': 1,
    'use_custom_grid_clearance': False,
    'use_image_size': False,
    'variable_engraving_grid_line_thickness': 0.8,
    'variable_engraving_grid_spacing': 1,
    'variable_grid_clearance': 0.2,
    'variable_grid_hatch_rotation_per_layer': 67,
    'variable_grid_hatch_spacing': 1,
    'variable_grid_infill_enabled': False,
    'variable_grid_infill_layers_before_roof': 3,
    'variable_grid_line_count': 3,
    'variable_grid_spacing': 1,
    'variable_grid_target': 'hollow',
}

#: The machine itself. **These are OLOS's numbers, not this machine's** -- they
#: describe the OLSK printer the engine was written for and have never been
#: checked against this one. ``bed_width`` 96 mm is the figure
#: ``slicer/retarget.py`` centres in the galvo field, so it is load-bearing for
#: the galvo path too.
#:
#: ``bed_height`` is ``None`` deliberately: the engine falls back to
#: ``bed_width``, giving a square bed, and writing 96 twice would hide that.
MACHINE_DEFAULTS = {
    'bed_height': None,
    'bed_offset_x': -238.5,
    'bed_offset_y': -158,
    'bed_width': 96,
    'no_routine_first_layers': 0,
    'parking_feed': 10000,
    'parking_x': -395.5,
    'parking_y': -4.5,
    'powder_feed': -0.3,
    'powder_feed_rate': 50,
    'recoater_feed': 3000,
    'recoater_home': -54.5,
    'recoater_out': -405,
    'recoater_return_bed_drop_distance': 0.2,
    'recoater_return_bed_drop_enabled': False,
    'recoater_return_bed_feed_rate': 50,
}

#: Mask resolution. The engine renders each layer to a PNG at
#: ``slice_pixels_per_mm`` and finds contours in it, so this sets the floor on
#: feature detail and the ceiling on slicing time -- it is quadratic in both.
#: ``slice_padding_mm`` is ``None`` so the engine computes it from the model:
#: the padding exists to keep geometry off the mask edge, where ``findContours``
#: would lose the boundary.
RASTER_DEFAULTS = {
    'slice_max_pixels': 4096,
    'slice_min_pixels': 256,
    'slice_padding_mm': None,
    'slice_pixels_per_mm': 15,
}


#: OLOS ships ``layerHeight: 1``, i.e. one millimetre, and that default is kept
#: above because this module's job is to reproduce OLOS faithfully. It is not a
#: usable LPBF layer: powder-bed layers are 0.02-0.1 mm, the proven example
#: files on this machine step the piston by 0.075 mm, and 1 mm of steel powder
#: will not fuse through. The slicing UI must therefore not present the
#: inherited default as a suggestion -- hence this range, which is advisory for
#: the UI and is *not* enforced here, because a test coupon at 0.2 mm is a
#: legitimate thing to ask for.
LAYER_HEIGHT_MM_RANGE = (0.02, 0.2)
LAYER_HEIGHT_MM_TYPICAL = 0.05


def defaults():
    """One flat dict of every defaulted key, ready to be edited and passed to
    :func:`build_slicing_data`. Runtime keys are absent by design."""
    merged = {}
    merged.update(SLICER_DEFAULTS)
    merged.update(MACHINE_DEFAULTS)
    merged.update(RASTER_DEFAULTS)
    return merged


class MissingParameters(KeyError):
    """Raised when a required key is absent, naming all of them at once."""


def build_slicing_data(model_file_path, gcode_file_path, model_size,
                       overrides=None, print_height_limit=None,
                       print_height_lower_limit=None):
    """Assemble the ``slicing_data`` dict the engine's ``slice_model`` expects.

    ``model_size`` needs ``.x``/``.y`` attributes in millimetres -- the model's
    footprint on the bed. :class:`slicer.job.ModelSize` is one.

    ``overrides`` is applied last and is not filtered, so a caller can set any
    of the 230 keys, including ones with no default here. An unknown key is
    accepted rather than rejected: the engine reads what it reads, and a typo
    that silently does nothing is a better failure than one that stops a
    print-ready configuration from loading at all. The UI is where a typo
    cannot happen, because its fields come from these dicts.
    """
    data = defaults()
    data["model_file_path"] = model_file_path
    data["gcode_file_path"] = gcode_file_path
    data["model_size"] = model_size
    data["print_height_limit"] = print_height_limit
    data["print_height_lower_limit"] = print_height_lower_limit
    if overrides:
        data.update(overrides)

    missing = [k for k in REQUIRED_KEYS if data.get(k) is None]
    if missing:
        raise MissingParameters(
            "slicing_data is missing required keys: " + ", ".join(missing))
    return data
