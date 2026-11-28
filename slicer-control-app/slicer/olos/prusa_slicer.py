"""
PrusaSlicer wrapper — V2 PATCH.

On top of V1:
- Uses generate_init_block() for bed offset header
- Passes slicing_data + layer_index to layer generator for routine injection
- Tight boundary clamping
"""

import os
import io
import re
import subprocess
import zipfile
from PIL import Image
import numpy as np
import cv2
from shapely.affinity import translate
from shapely.geometry import (
    LineString, MultiLineString, Point, Polygon, MultiPolygon,
    GeometryCollection, box
)
from shapely.ops import unary_union
from shapely.strtree import STRtree
# ``autotrace`` (pyautotrace) is imported lazily inside
# _generate_svg_contour, the only place it is used -- and which nothing
# in this engine calls. Contours come from shapely and cv2.findContours
# instead. Keeping it at module scope would make a native-library
# dependency mandatory for an unreachable code path.
import xml.etree.ElementTree as ET
from .slicers_shared_methods import (
    generate_gcode_for_layer, prepare_contour_settings, prepare_infill_settings,
    send_slicing_progress_to_interface, generate_init_block,
    get_zone_scan_mode
)
from .shim import TempFilesManager
from .shim import ImageToGcodeHelper
from .shim import CoreConstants as constants
from .binary import resolve_prusa_slicer
from .binary import cli_platform_flags


class PrusaSlicer:
    def __init__(self):
        self.image_to_gcode_helper = ImageToGcodeHelper()
        tmp_files_manager = TempFilesManager()
        self._zip_tmp_file_path = tmp_files_manager.create_temp_file('.zip')
        # Roof and variable-grid lookaheads revisit the same layer candidates
        # many times. These geometries are deterministic for one slicing job,
        # so retain them for the lifetime of this slicer instance.
        self._surface_skin_cache_layers = None
        self._flat_roof_skin_region_cache = {}
        self._slanted_surface_skin_candidate_cache = {}
        self._current_surface_skin_candidate_cache = {}

    def _prepare_surface_skin_caches(self, layer_regions):
        if self._surface_skin_cache_layers is layer_regions:
            return
        self._surface_skin_cache_layers = layer_regions
        self._flat_roof_skin_region_cache.clear()
        self._slanted_surface_skin_candidate_cache.clear()
        self._current_surface_skin_candidate_cache.clear()

    @classmethod
    def slice_model(cls, slicing_data, websocket_write_queue):
        slicer = cls()
        slicing_data["visicut_style"] = True
        slice_padding = slicer._slice_padding_for_model(
            slicing_data["model_size"], slicing_data)
        display_pixels_x, display_pixels_y = slicer._display_pixels_for_model(
            slicing_data["model_size"], slicing_data, slice_padding)
        display_width = float(slicing_data["model_size"].y) + 2 * slice_padding
        display_height = float(slicing_data["model_size"].x) + 2 * slice_padding

        prusa_slicer_path = resolve_prusa_slicer()

        slicer_cmd = [
            prusa_slicer_path,
            *cli_platform_flags(),
            "--export-sla", slicing_data["model_file_path"],
            "--layer-height", str(slicing_data["layer_height"]),
            "--sla-archive-format", "SL1",
            "--center", f"{display_width / 2}, {display_height / 2}",
            "--rotate", "90",
            "--display-pixels-x", str(display_pixels_x),
            "--display-pixels-y", str(display_pixels_y),
            "--display-width", str(display_width),
            "--display-height", str(display_height),
            "--output", slicer._zip_tmp_file_path
        ]

        solid_mode_enabled = bool(
            slicing_data.get("solid_mode_enabled", False))
        hollowing_enabled = (
            False if solid_mode_enabled
            else bool(slicing_data.get("is_hollowing_enabled", False)))
        supports_enabled = (
            False if solid_mode_enabled
            else bool(slicing_data.get("is_support_enabled", False)))
        pad_enabled = (
            False if solid_mode_enabled
            else bool(slicing_data.get("is_pad_enabled", False)))
        slicer_cmd.extend([
            f"--hollowing-enable={int(hollowing_enabled)}",
            f"--supports-enable={int(supports_enabled)}",
            f"--pad-enable={int(pad_enabled)}",
        ])

        # Upstream discarded both streams and trusted the exit code. PrusaSlicer
        # is a GUI binary with a CLI mode, and it has several ways to exit 0
        # having written nothing -- an argument handed to a running instance, a
        # model it declines to slice, a display geometry it rejects. Keep stderr
        # so the reason can be reported, and then verify the archive rather than
        # letting zipfile fail later on a missing or stale file with no context.
        proc = subprocess.run(slicer_cmd, check=True,
                              stdout=subprocess.DEVNULL,
                              stderr=subprocess.PIPE, text=True)

        archive = slicer._zip_tmp_file_path
        if not os.path.isfile(archive) or os.path.getsize(archive) == 0:
            raise RuntimeError(
                "PrusaSlicer exited successfully but wrote no SL1 archive to "
                f"{archive}.\n"
                + ((proc.stderr or "").strip() or "It printed nothing to stderr."))
        if not zipfile.is_zipfile(archive):
            raise RuntimeError(
                f"PrusaSlicer wrote {os.path.getsize(archive)} bytes to {archive}, "
                "but it is not a zip archive.\n"
                + ((proc.stderr or "").strip() or "It printed nothing to stderr."))

        with open(slicing_data["gcode_file_path"], "w") as gcode_file:
            z = 0
            zip_ref = zipfile.ZipFile(slicer._zip_tmp_file_path, 'r')

            png_files = sorted(
                f for f in zip_ref.namelist() if f.lower().endswith('.png'))
            source_total_layers = len(png_files)
            layer_index_offset = slicer._layer_start_index(
                slicing_data.get("print_height_lower_limit"),
                slicing_data["layer_height"])
            slicing_data["layer_index_offset"] = layer_index_offset
            png_files = slicer._limit_layer_files(
                png_files,
                slicing_data.get("print_height_limit"),
                slicing_data.get("print_height_lower_limit"),
                slicing_data["layer_height"])
            image_size = (
                float(slicing_data["model_size"].x) + 2 * slice_padding,
                float(slicing_data["model_size"].y) + 2 * slice_padding)
            model_image_size = (
                float(slicing_data["model_size"].x),
                float(slicing_data["model_size"].y))
            user_image_size = slicing_data["infill_image_size"] if slicing_data["use_image_size"] else image_size

            bed_width, bed_height = slicer._machine_bed_size(slicing_data)
            model_bounds = (0, 0, bed_width, bed_height)
            machine_bounds = (0, 0, bed_width, bed_height, "circle")
            layer_regions = []
            for layer_file in png_files:
                img_infill = zip_ref.open(layer_file, 'r').read()
                region = slicer._mask_region_from_png(img_infill, image_size)
                region = slicer._remove_slice_padding(
                    region, slice_padding, model_image_size)
                region = slicer._center_region_on_bed(
                    region, model_image_size, bed_width, bed_height)
                slicer._validate_region_on_bed(region, machine_bounds)
                layer_regions.append(region)
            # Keep the controller coordinate convention used by the proven UGS
            # files: G53 moves to the configured bed reference, G92 resets that
            # point to (0, 0), and 0..bed-size paths place the model on the bed.
            slicing_data["work_origin_x"] = 0.0
            slicing_data["work_origin_y"] = 0.0
            total_layers = len(png_files)
            base_engraving_mode = slicer._normalise_engraving_mode(
                slicing_data.get("engraving_mode", "vectoring"))
            engraving_acceleration = slicer._validate_acceleration(
                slicing_data.get("engraving_acceleration", 500))
            recoater_feed = int(slicing_data.get("recoater_feed", 3000) or 3000)
            surface_skin_enabled = bool(
                slicing_data.get("surface_skin_enabled", True))
            surface_skin_layers = max(
                1, int(slicing_data.get("surface_skin_layers", 3) or 3))
            surface_skin_engraving_enabled = bool(
                slicing_data.get("surface_skin_engraving_enabled", False))
            surface_skin_hatch_spacing = max(
                0.01, float(slicing_data.get(
                    "surface_skin_hatch_spacing", 0.2) or 0.2))
            surface_skin_power = int(max(
                0, min(100, float(slicing_data.get(
                    "surface_skin_power",
                    slicing_data.get("infill_power", 50)) or 50))))
            surface_skin_speed = int(max(
                1, float(slicing_data.get(
                    "surface_skin_speed",
                    slicing_data.get("infill_speed", 3000)) or 3000)))
            surface_skin_alternate_lines = bool(
                slicing_data.get("surface_skin_alternate_lines", False))
            surface_skin_alternate_mode = str(slicing_data.get(
                "surface_skin_alternate_mode",
                "two-pass") or "two-pass").strip().lower()
            if surface_skin_alternate_mode not in (
                "two-pass", "edge-interleave"
            ):
                surface_skin_alternate_mode = "two-pass"
            flat_surface_skin_layers = max(
                1, int(slicing_data.get(
                    "flat_surface_skin_layers", surface_skin_layers) or surface_skin_layers))
            slanted_surface_skin_layers = max(
                1, int(slicing_data.get(
                    "slanted_surface_skin_layers", surface_skin_layers) or surface_skin_layers))
            slanted_surface_skin_enabled = bool(
                slicing_data.get("slanted_surface_skin_enabled", True))
            roof_hatch_track_width = max(
                0.01, float(slicing_data.get(
                    "nominal_vector_track_width", 0.2) or 0.2))
            if "flat_surface_skin_hatch_overlap" in slicing_data:
                flat_surface_skin_hatch_overlap = max(
                    -1.0, min(0.95, float(slicing_data.get(
                        "flat_surface_skin_hatch_overlap", 0.0) or 0.0)))
                flat_surface_skin_hatch_spacing = max(
                    0.01,
                    roof_hatch_track_width
                    * (1.0 - flat_surface_skin_hatch_overlap))
            else:
                flat_surface_skin_hatch_overlap = None
                flat_surface_skin_hatch_spacing = max(
                    0.01, float(slicing_data.get(
                        "flat_surface_skin_hatch_spacing",
                        surface_skin_hatch_spacing) or surface_skin_hatch_spacing))
            slanted_surface_skin_hatch_spacing = max(
                0.01, float(slicing_data.get(
                    "slanted_surface_skin_hatch_spacing",
                    surface_skin_hatch_spacing) or surface_skin_hatch_spacing))
            flat_surface_skin_power = int(max(
                0, min(100, float(slicing_data.get(
                    "flat_surface_skin_power", surface_skin_power) or surface_skin_power))))
            flat_surface_skin_speed = int(max(
                1, float(slicing_data.get(
                    "flat_surface_skin_speed", surface_skin_speed) or surface_skin_speed)))
            flat_surface_skin_raster_direction = str(slicing_data.get(
                "flat_surface_skin_raster_direction",
                "x") or "x").strip().lower()
            if flat_surface_skin_raster_direction not in ("x", "y", "diagonal"):
                flat_surface_skin_raster_direction = "x"
            flat_surface_skin_cross_hatch = bool(slicing_data.get(
                "flat_surface_skin_cross_hatch", False))
            slanted_surface_skin_raster_direction = str(slicing_data.get(
                "slanted_surface_skin_raster_direction",
                "diagonal") or "diagonal").strip().lower()
            if slanted_surface_skin_raster_direction not in (
                "x", "y", "diagonal"
            ):
                slanted_surface_skin_raster_direction = "diagonal"
            slanted_surface_skin_cross_hatch = bool(slicing_data.get(
                "slanted_surface_skin_cross_hatch", False))
            slanted_surface_skin_power = int(max(
                0, min(100, float(slicing_data.get(
                    "slanted_surface_skin_power", surface_skin_power) or surface_skin_power))))
            slanted_surface_skin_speed = int(max(
                1, float(slicing_data.get(
                    "slanted_surface_skin_speed", surface_skin_speed) or surface_skin_speed)))
            flat_surface_skin_pattern = str(slicing_data.get(
                "flat_surface_skin_pattern", "hatch") or "hatch").strip().lower()
            if flat_surface_skin_pattern not in ("hatch", "spiral"):
                flat_surface_skin_pattern = "hatch"
            flat_surface_skin_spiral_direction = str(slicing_data.get(
                "flat_surface_skin_spiral_direction",
                "outside-in") or "outside-in").strip().lower()
            if flat_surface_skin_spiral_direction not in ("outside-in", "inside-out"):
                flat_surface_skin_spiral_direction = "outside-in"
            flat_surface_skin_boundary_mode = str(slicing_data.get(
                "flat_surface_skin_boundary_mode",
                "engrave") or "engrave").strip().lower()
            if flat_surface_skin_boundary_mode == "vectoring":
                flat_surface_skin_boundary_mode = "vector"
            if flat_surface_skin_boundary_mode in (
                "raster", "raster-only", "no-shell", "none"):
                flat_surface_skin_boundary_mode = "none"
            if flat_surface_skin_boundary_mode in ("outline", "contour-only"):
                flat_surface_skin_boundary_mode = "contour"
            if flat_surface_skin_boundary_mode not in (
                "engrave", "vector", "contour", "none"):
                flat_surface_skin_boundary_mode = "engrave"
            flat_surface_skin_contour_line_count = max(
                1, int(slicing_data.get(
                    "flat_surface_skin_contour_line_count",
                    slicing_data.get("contour_line_count", 3)) or 3))
            flat_surface_skin_contour_overlap = max(
                -1.0, min(1.0, float(slicing_data.get(
                    "flat_surface_skin_contour_overlap",
                    slicing_data.get("contour_infill_overlap", 0.15)) or 0.0)))
            flat_surface_skin_second_pass_enabled = bool(slicing_data.get(
                "flat_surface_skin_second_pass_enabled", False))
            flat_surface_skin_second_pass_power = int(max(
                0, min(100, float(slicing_data.get(
                    "flat_surface_skin_second_pass_power",
                    flat_surface_skin_power) or flat_surface_skin_power))))
            flat_surface_skin_second_pass_speed = int(max(
                1, float(slicing_data.get(
                    "flat_surface_skin_second_pass_speed",
                    flat_surface_skin_speed) or flat_surface_skin_speed)))
            flat_surface_skin_second_pass_hatch_overlap = max(
                -1.0, min(0.95, float(slicing_data.get(
                    "flat_surface_skin_second_pass_hatch_overlap",
                    flat_surface_skin_hatch_overlap or 0.0) or 0.0)))
            flat_surface_skin_second_pass_hatch_spacing = max(
                0.01,
                roof_hatch_track_width
                * (1.0 - flat_surface_skin_second_pass_hatch_overlap))
            flat_surface_skin_second_pass_raster_direction = str(
                slicing_data.get(
                    "flat_surface_skin_second_pass_raster_direction",
                    flat_surface_skin_raster_direction)
                or flat_surface_skin_raster_direction).strip().lower()
            if flat_surface_skin_second_pass_raster_direction not in (
                "x", "y", "diagonal"
            ):
                flat_surface_skin_second_pass_raster_direction = (
                    flat_surface_skin_raster_direction)
            flat_surface_skin_second_pass_cross_hatch = bool(slicing_data.get(
                "flat_surface_skin_second_pass_cross_hatch",
                flat_surface_skin_cross_hatch))
            flat_surface_skin_second_pass_boundary_mode = str(
                slicing_data.get(
                    "flat_surface_skin_second_pass_boundary_mode",
                    flat_surface_skin_boundary_mode)
                or flat_surface_skin_boundary_mode).strip().lower()
            if flat_surface_skin_second_pass_boundary_mode == "vectoring":
                flat_surface_skin_second_pass_boundary_mode = "vector"
            if flat_surface_skin_second_pass_boundary_mode in (
                "raster", "raster-only", "no-shell", "none"):
                flat_surface_skin_second_pass_boundary_mode = "none"
            if flat_surface_skin_second_pass_boundary_mode in (
                "outline", "contour-only"):
                flat_surface_skin_second_pass_boundary_mode = "contour"
            if flat_surface_skin_second_pass_boundary_mode not in (
                "engrave", "vector", "contour", "none"):
                flat_surface_skin_second_pass_boundary_mode = (
                    flat_surface_skin_boundary_mode)
            flat_surface_skin_second_pass_contour_line_count = max(
                1, int(slicing_data.get(
                    "flat_surface_skin_second_pass_contour_line_count",
                    flat_surface_skin_contour_line_count)
                    or flat_surface_skin_contour_line_count))
            flat_surface_skin_second_pass_contour_overlap = max(
                -1.0, min(1.0, float(slicing_data.get(
                    "flat_surface_skin_second_pass_contour_overlap",
                    flat_surface_skin_contour_overlap)
                    or flat_surface_skin_contour_overlap)))
            flat_surface_skin_roof_only_layers = bool(
                slicing_data.get("flat_surface_skin_roof_only_layers", False))
            flat_surface_skin_max_run_layers = max(
                flat_surface_skin_layers,
                int(slicing_data.get(
                    "flat_surface_skin_max_run_layers",
                    flat_surface_skin_layers + 2)
                    or (flat_surface_skin_layers + 2)))
            roof_hollow_infill_enabled = bool(
                slicing_data.get("roof_hollow_infill_enabled", False))
            roof_hollow_infill_overlap = max(
                -1.0, min(1.0, float(slicing_data.get(
                    "roof_hollow_infill_overlap",
                    slicing_data.get("contour_infill_overlap", 0.15)) or 0.0)))
            internal_floor_support_enabled = bool(
                slicing_data.get("internal_floor_support_enabled", False))
            internal_floor_support_mode = str(slicing_data.get(
                "internal_floor_support_mode", "vectoring")
                or "vectoring").strip().lower()
            if internal_floor_support_mode not in {"vectoring", "engraving"}:
                internal_floor_support_mode = "vectoring"
            internal_floor_support_type = str(slicing_data.get(
                "internal_floor_support_type", "Lines") or "Lines").strip()
            if internal_floor_support_type.lower() not in {"lines", "grid"}:
                internal_floor_support_type = "Lines"
            internal_floor_support_type = (
                "Grid"
                if internal_floor_support_type.lower() == "grid"
                else "Lines")
            internal_floor_support_power = max(
                0.0, min(100.0, float(slicing_data.get(
                    "internal_floor_support_power",
                    slicing_data.get("infill_power", 50)) or 0)))
            internal_floor_support_speed = max(
                1.0, float(slicing_data.get(
                    "internal_floor_support_speed",
                    slicing_data.get("infill_speed", 3000)) or 3000))
            internal_floor_support_track_width = max(
                0.01, float(slicing_data.get(
                    "internal_floor_support_track_width",
                    slicing_data.get("nominal_vector_track_width", 0.2))
                    or 0.2))
            internal_floor_support_line_overlap = max(
                -1.0, min(0.95, float(slicing_data.get(
                    "internal_floor_support_line_overlap", 0) or 0)))
            internal_floor_support_line_spacing = max(
                0.01,
                internal_floor_support_track_width
                * (1.0 - internal_floor_support_line_overlap))
            internal_floor_support_line_count = max(
                1, int(slicing_data.get(
                    "internal_floor_support_line_count",
                    slicing_data.get("infill_line_count", 3)) or 3))
            internal_floor_support_hatch_spacing = max(
                0.01, float(slicing_data.get(
                    "internal_floor_support_hatch_spacing",
                    slicing_data.get("infill_hatch_spacing", 1)) or 1))
            internal_floor_support_grid_spacing = max(
                0.01, float(slicing_data.get(
                    "internal_floor_support_grid_spacing",
                    slicing_data.get("grid_spacing", 2)) or 2))
            internal_floor_support_grid_clearance = max(
                0.0, float(slicing_data.get(
                    "internal_floor_support_grid_clearance",
                    slicing_data.get("grid_clearance", 0.2)) or 0))
            internal_floor_support_angle = float(slicing_data.get(
                "internal_floor_support_angle",
                slicing_data.get("infill_angle", 45)) or 0)
            internal_floor_support_rotation_per_layer = float(
                slicing_data.get(
                    "internal_floor_support_rotation_per_layer",
                    slicing_data.get("hatch_rotation_per_layer", 67)) or 0)
            internal_floor_support_grouping_enabled = bool(slicing_data.get(
                "internal_floor_support_grouping_enabled", False))
            internal_floor_support_group_layers = max(
                1, int(slicing_data.get(
                    "internal_floor_support_group_layers", 1) or 1))
            internal_floor_support_boundary_clearance = max(
                0.0, float(slicing_data.get(
                    "internal_floor_support_boundary_clearance", 0) or 0))
            internal_floor_support_minimum_area = max(
                0.0, float(slicing_data.get(
                    "internal_floor_support_minimum_area", 0.1) or 0))
            internal_floor_support_optimize_scan_order = bool(
                slicing_data.get(
                    "internal_floor_support_optimize_scan_order", True))
            variable_grid_infill_enabled = bool(
                slicing_data.get("variable_grid_infill_enabled", False))
            variable_grid_target = str(
                slicing_data.get("variable_grid_target", "hollow")
                or "hollow").strip().lower()
            if variable_grid_target not in {"hollow", "both"}:
                variable_grid_target = "hollow"
            variable_grid_layers_before_roof = max(
                1, int(slicing_data.get(
                    "variable_grid_infill_layers_before_roof", 3) or 3))
            variable_grid_spacing = max(
                0.01, float(slicing_data.get(
                    "variable_grid_spacing",
                    slicing_data.get("grid_spacing", 2)) or 2))
            variable_grid_hatch_spacing = max(
                0.0, float(slicing_data.get(
                    "variable_grid_hatch_spacing",
                    slicing_data.get("infill_hatch_spacing", 0)) or 0))
            variable_grid_hatch_rotation_per_layer = float(slicing_data.get(
                "variable_grid_hatch_rotation_per_layer",
                slicing_data.get("hatch_rotation_per_layer", 67)) or 0)
            variable_grid_clearance = max(
                0.0, float(slicing_data.get(
                    "variable_grid_clearance",
                    slicing_data.get("grid_clearance", 0.2)) or 0))
            variable_grid_line_count = max(
                1, int(slicing_data.get(
                    "variable_grid_line_count",
                    slicing_data.get("infill_line_count", 1)) or 1))
            variable_engraving_grid_spacing = max(
                0.01, float(slicing_data.get(
                    "variable_engraving_grid_spacing",
                    slicing_data.get("engraving_grid_spacing", 2)) or 2))
            variable_engraving_grid_line_thickness = max(
                0.01, float(slicing_data.get(
                    "variable_engraving_grid_line_thickness",
                    slicing_data.get("engraving_grid_line_thickness", 0.8)) or 0.8))
            multi_variable_grid_enabled = bool(slicing_data.get(
                "multi_variable_grid_enabled", False))
            secondary_variable_grid_layers_before_roof = max(
                1, int(slicing_data.get(
                    "secondary_variable_grid_layers_before_roof",
                    variable_grid_layers_before_roof) or variable_grid_layers_before_roof))
            secondary_variable_grid_spacing = max(
                0.01, float(slicing_data.get(
                    "secondary_variable_grid_spacing",
                    variable_grid_spacing) or variable_grid_spacing))
            secondary_variable_grid_hatch_spacing = max(
                0.0, float(slicing_data.get(
                    "secondary_variable_grid_hatch_spacing",
                    variable_grid_hatch_spacing) or variable_grid_hatch_spacing))
            secondary_variable_grid_clearance = max(
                0.0, float(slicing_data.get(
                    "secondary_variable_grid_clearance",
                    variable_grid_clearance) or variable_grid_clearance))
            secondary_variable_grid_line_count = max(
                1, int(slicing_data.get(
                    "secondary_variable_grid_line_count",
                    variable_grid_line_count) or variable_grid_line_count))
            secondary_variable_engraving_grid_spacing = max(
                0.01, float(slicing_data.get(
                    "secondary_variable_engraving_grid_spacing",
                    variable_engraving_grid_spacing) or variable_engraving_grid_spacing))
            secondary_variable_engraving_grid_line_thickness = max(
                0.01, float(slicing_data.get(
                    "secondary_variable_engraving_grid_line_thickness",
                    variable_engraving_grid_line_thickness)
                    or variable_engraving_grid_line_thickness))
            tertiary_variable_grid_enabled = bool(slicing_data.get(
                "tertiary_variable_grid_enabled", False))
            tertiary_variable_grid_layers_before_roof = max(
                1, int(slicing_data.get(
                    "tertiary_variable_grid_layers_before_roof",
                    secondary_variable_grid_layers_before_roof)
                    or secondary_variable_grid_layers_before_roof))
            tertiary_variable_grid_spacing = max(
                0.01, float(slicing_data.get(
                    "tertiary_variable_grid_spacing",
                    secondary_variable_grid_spacing)
                    or secondary_variable_grid_spacing))
            tertiary_variable_grid_hatch_spacing = max(
                0.0, float(slicing_data.get(
                    "tertiary_variable_grid_hatch_spacing",
                    secondary_variable_grid_hatch_spacing)
                    or secondary_variable_grid_hatch_spacing))
            tertiary_variable_grid_clearance = max(
                0.0, float(slicing_data.get(
                    "tertiary_variable_grid_clearance",
                    secondary_variable_grid_clearance)
                    or secondary_variable_grid_clearance))
            tertiary_variable_grid_line_count = max(
                1, int(slicing_data.get(
                    "tertiary_variable_grid_line_count",
                    secondary_variable_grid_line_count)
                    or secondary_variable_grid_line_count))
            tertiary_variable_engraving_grid_spacing = max(
                0.01, float(slicing_data.get(
                    "tertiary_variable_engraving_grid_spacing",
                    secondary_variable_engraving_grid_spacing)
                    or secondary_variable_engraving_grid_spacing))
            tertiary_variable_engraving_grid_line_thickness = max(
                0.01, float(slicing_data.get(
                    "tertiary_variable_engraving_grid_line_thickness",
                    secondary_variable_engraving_grid_line_thickness)
                    or secondary_variable_engraving_grid_line_thickness))
            engraving_contour_thickness = max(
                0.01, float(slicing_data.get(
                    "engraving_contour_thickness", 0.8) or 0.8))
            vector_line_spacing = max(
                0.0, float(slicing_data.get(
                    "line_bundle_spacing", 0.2) or 0.2))
            nominal_vector_track_width = max(
                0.01, float(slicing_data.get(
                    "nominal_vector_track_width", 0.2) or 0.2))
            solid_mode_enabled = bool(
                slicing_data.get("solid_mode_enabled", False))
            solid_merge_objects_by_layer = bool(slicing_data.get(
                "solid_merge_objects_by_layer", True))
            solid_track_width = max(
                0.01, float(slicing_data.get(
                    "solid_track_width", nominal_vector_track_width)
                    or nominal_vector_track_width))
            solid_anchor_pad_enabled = bool(slicing_data.get(
                "solid_anchor_pad_enabled", False))
            solid_anchor_pad_layers = max(1, int(slicing_data.get(
                "solid_anchor_pad_layers", 1) or 1))
            solid_anchor_pad_size = max(
                0.01, float(slicing_data.get(
                    "solid_anchor_pad_size", 20) or 20))
            solid_anchor_pad_power = int(max(
                0, min(100, float(slicing_data.get(
                    "solid_anchor_pad_power", 50) or 0))))
            solid_anchor_pad_speed = int(max(
                1, float(slicing_data.get(
                    "solid_anchor_pad_speed", 3000) or 3000)))
            solid_anchor_pad_line_overlap = max(
                -1.0, min(0.95, float(slicing_data.get(
                    "solid_anchor_pad_line_overlap", 0.0) or 0.0)))
            solid_anchor_pad_hatch_pitch = max(
                0.0005,
                solid_track_width * (1.0 - solid_anchor_pad_line_overlap))
            solid_anchor_pad_rotate_each_layer = bool(slicing_data.get(
                "solid_anchor_pad_rotate_each_layer", False))
            solid_anchor_pad_rotation_per_layer = max(
                -180.0, min(180.0, float(slicing_data.get(
                    "solid_anchor_pad_rotation_per_layer", 90) or 0)))
            solid_transition_enabled = bool(slicing_data.get(
                "solid_transition_enabled", False))
            solid_transition_layers = max(
                1, min(1000, int(slicing_data.get(
                    "solid_transition_layers", 1) or 1)))
            solid_transition_pattern = str(slicing_data.get(
                "solid_transition_pattern", "lines") or "lines").lower()
            if solid_transition_pattern not in {"lines", "grid"}:
                solid_transition_pattern = "lines"
            solid_transition_power = int(max(
                0, min(100, float(slicing_data.get(
                    "solid_transition_power", 50) or 0))))
            solid_transition_speed = int(max(
                1, float(slicing_data.get(
                    "solid_transition_speed", 3000) or 3000)))
            solid_transition_track_width = max(
                0.01, float(slicing_data.get(
                    "solid_transition_track_width", solid_track_width)
                    or solid_track_width))
            solid_transition_line_overlap = max(
                -1.0, min(0.95, float(slicing_data.get(
                    "solid_transition_line_overlap", 0.0) or 0.0)))
            solid_transition_line_spacing = max(
                0.0005,
                solid_transition_track_width
                * (1.0 - solid_transition_line_overlap))
            solid_transition_line_count = max(
                1, min(25, int(slicing_data.get(
                    "solid_transition_line_count", 1) or 1)))
            solid_transition_hatch_spacing = max(
                0.0005, float(slicing_data.get(
                    "solid_transition_hatch_spacing", 1.0) or 1.0))
            solid_transition_grid_spacing = max(
                0.0005, float(slicing_data.get(
                    "solid_transition_grid_spacing", 1.0) or 1.0))
            solid_transition_grid_clearance = max(
                0.0, float(slicing_data.get(
                    "solid_transition_grid_clearance", 0.2) or 0.0))
            solid_transition_raster_direction = str(slicing_data.get(
                "solid_transition_raster_direction", "x") or "x").lower()
            if solid_transition_raster_direction not in {
                    "x", "y", "diagonal"}:
                solid_transition_raster_direction = "x"
            solid_transition_rotate_each_layer = bool(slicing_data.get(
                "solid_transition_rotate_each_layer", False))
            solid_transition_rotation_per_layer = max(
                -180.0, min(180.0, float(slicing_data.get(
                    "solid_transition_rotation_per_layer", 90) or 0)))
            solid_transition_contour_enabled = bool(slicing_data.get(
                "solid_transition_contour_enabled", True))
            solid_transition_contour_power = int(max(
                0, min(100, float(slicing_data.get(
                    "solid_transition_contour_power", 50) or 0))))
            solid_transition_contour_speed = int(max(
                1, float(slicing_data.get(
                    "solid_transition_contour_speed", 3000) or 3000)))
            solid_transition_contour_line_count = max(
                1, min(25, int(slicing_data.get(
                    "solid_transition_contour_line_count", 1) or 1)))
            solid_transition_contour_overlap = max(
                -1.0, min(1.0, float(slicing_data.get(
                    "solid_transition_contour_overlap", 0.0) or 0.0)))
            normal_transition_floor_support_enabled = (
                not solid_mode_enabled
                and solid_transition_enabled
                and internal_floor_support_enabled)
            if not solid_mode_enabled:
                internal_floor_support_enabled = (
                    normal_transition_floor_support_enabled)
            if normal_transition_floor_support_enabled:
                internal_floor_support_mode = "engraving"
                internal_floor_support_type = (
                    "Grid"
                    if solid_transition_pattern == "grid"
                    else "Lines")
                internal_floor_support_power = solid_transition_power
                internal_floor_support_speed = solid_transition_speed
                internal_floor_support_track_width = (
                    solid_transition_track_width)
                internal_floor_support_line_overlap = (
                    solid_transition_line_overlap)
                internal_floor_support_line_spacing = (
                    solid_transition_line_spacing)
                internal_floor_support_line_count = (
                    solid_transition_line_count)
                internal_floor_support_hatch_spacing = (
                    solid_transition_hatch_spacing)
                internal_floor_support_grid_spacing = (
                    solid_transition_grid_spacing)
                internal_floor_support_grid_clearance = (
                    solid_transition_grid_clearance)
                internal_floor_support_angle = {
                    "x": 0.0,
                    "y": 90.0,
                    "diagonal": 45.0,
                }[solid_transition_raster_direction]
                internal_floor_support_rotation_per_layer = (
                    solid_transition_rotation_per_layer
                    if solid_transition_rotate_each_layer else 0.0)
                internal_floor_support_grouping_enabled = False
                internal_floor_support_boundary_clearance = (
                    max(
                        0.0,
                        (
                            solid_transition_contour_line_count - 1
                        ) * solid_transition_line_spacing
                        - solid_transition_contour_overlap
                        * solid_transition_line_spacing,
                    )
                    if solid_transition_contour_enabled else 0.0
                )
                internal_floor_support_optimize_scan_order = True
            solid_close_top_bottom_enabled = bool(slicing_data.get(
                "solid_close_top_bottom_enabled", False))
            solid_close_top_bottom_layers = max(1, int(slicing_data.get(
                "solid_close_top_bottom_layers", 3) or 3))
            solid_line_overlap = max(
                -1.0, min(0.95, float(slicing_data.get(
                    "solid_line_overlap", 0.0) or 0.0)))
            solid_hatch_pitch = max(
                0.0005, solid_track_width * (1.0 - solid_line_overlap))
            solid_close_top_bottom_line_overlap = max(
                -1.0, min(0.95, float(slicing_data.get(
                    "solid_close_top_bottom_line_overlap",
                    solid_line_overlap))))
            solid_close_top_bottom_hatch_pitch = max(
                0.0005,
                solid_track_width
                * (1.0 - solid_close_top_bottom_line_overlap))
            solid_first_zone_enabled = bool(slicing_data.get(
                "solid_first_zone_enabled", False))
            solid_first_zone_engraving_enabled = bool(slicing_data.get(
                "solid_first_zone_engraving_enabled", True))
            solid_first_layers_count = max(1, int(slicing_data.get(
                "solid_first_layers_count", 1) or 1))
            solid_first_power = int(max(
                0, min(100, float(slicing_data.get(
                    "solid_first_power", 50) or 0))))
            solid_first_speed = int(max(
                1, float(slicing_data.get(
                    "solid_first_speed", 3000) or 3000)))
            solid_first_engraving_hatch_spacing = max(
                0.0005, float(slicing_data.get(
                    "solid_first_engraving_hatch_spacing", 0.2) or 0.2))
            solid_intermediate_zone_enabled = bool(slicing_data.get(
                "solid_intermediate_zone_enabled", False))
            solid_intermediate_zone_engraving_enabled = bool(
                slicing_data.get(
                    "solid_intermediate_zone_engraving_enabled", True))
            solid_intermediate_layers_count = max(1, int(slicing_data.get(
                "solid_intermediate_layers_count", 1) or 1))
            solid_intermediate_power = int(max(
                0, min(100, float(slicing_data.get(
                    "solid_intermediate_power", 50) or 0))))
            solid_intermediate_speed = int(max(
                1, float(slicing_data.get(
                    "solid_intermediate_speed", 3000) or 3000)))
            solid_intermediate_engraving_hatch_spacing = max(
                0.0005, float(slicing_data.get(
                    "solid_intermediate_engraving_hatch_spacing", 0.2)
                    or 0.2))
            solid_raster_direction = str(slicing_data.get(
                "solid_raster_direction", "x") or "x").strip().lower()
            if solid_raster_direction not in ("x", "y", "diagonal"):
                solid_raster_direction = "x"
            solid_cross_hatch = bool(slicing_data.get(
                "solid_cross_hatch", False))
            solid_alternate_lines = bool(slicing_data.get(
                "solid_alternate_lines", True))
            solid_alternate_mode = str(slicing_data.get(
                "solid_alternate_mode",
                "edge-interleave") or "edge-interleave").strip().lower()
            if solid_alternate_mode not in (
                    "two-pass", "edge-interleave"):
                solid_alternate_mode = "edge-interleave"
            solid_island_scanning_enabled = bool(slicing_data.get(
                "solid_island_scanning_enabled", False))
            solid_island_size = max(
                0.01, float(slicing_data.get(
                    "solid_island_size", 3) or 3))
            solid_island_scan_strategy = str(slicing_data.get(
                "solid_island_scan_strategy",
                "edge-interleave") or "edge-interleave").strip().lower()
            if solid_island_scan_strategy not in (
                    "normal", "two-pass", "edge-interleave"):
                solid_island_scan_strategy = "edge-interleave"
            solid_contour_overlap = max(
                -1.0, min(1.0, float(slicing_data.get(
                    "solid_contour_overlap", 0.0) or 0.0)))
            solid_contour_enabled = bool(slicing_data.get(
                "solid_contour_enabled", True))
            solid_power = int(max(
                0, min(100, float(slicing_data.get(
                    "solid_power", 50) or 0))))
            solid_speed = int(max(
                1, float(slicing_data.get("solid_speed", 3000) or 3000)))
            solid_close_top_bottom_power = int(max(
                0, min(100, float(slicing_data.get(
                    "solid_close_top_bottom_power", solid_power)))))
            solid_close_top_bottom_speed = int(max(
                1, float(slicing_data.get(
                    "solid_close_top_bottom_speed", solid_speed))))
            solid_contour_power = int(max(
                0, min(100, float(slicing_data.get(
                    "solid_contour_power", 50) or 0))))
            solid_contour_speed = int(max(
                1, float(slicing_data.get(
                    "solid_contour_speed", 3000) or 3000)))
            solid_contour_line_count = max(
                1, int(slicing_data.get(
                    "solid_contour_line_count", 3) or 3))
            solid_contour_width = slicer._calculate_vector_wall_width(
                solid_contour_line_count,
                solid_track_width,
                solid_track_width)
            configured_contour_line_count = max(
                1, int(slicing_data.get("contour_line_count", 1) or 1))
            auto_contour_line_count_enabled = bool(slicing_data.get(
                "auto_contour_line_count_enabled", False))
            calculated_vector_wall_width = (
                slicer._calculate_vector_wall_width(
                    configured_contour_line_count,
                    vector_line_spacing,
                    nominal_vector_track_width))
            flat_surface_skin_contour_width = (
                slicer._calculate_vector_wall_width(
                    flat_surface_skin_contour_line_count,
                    vector_line_spacing,
                    nominal_vector_track_width))
            flat_surface_skin_second_pass_contour_width = (
                slicer._calculate_vector_wall_width(
                    flat_surface_skin_second_pass_contour_line_count,
                    vector_line_spacing,
                    nominal_vector_track_width))
            surface_skin_edge_clearance = max(
                0.6,
                float(vector_line_spacing) * 3.0,
                float(nominal_vector_track_width) * 3.0,
                float(surface_skin_hatch_spacing) * 3.0,
                float(flat_surface_skin_hatch_spacing) * 3.0,
                float(slanted_surface_skin_hatch_spacing) * 3.0)
            match_engraved_shell = bool(slicing_data.get(
                "match_engraved_shell_to_vector_wall", False))
            if match_engraved_shell:
                engraving_contour_thickness = calculated_vector_wall_width
            engraving_raster_direction = str(slicing_data.get(
                "engraving_raster_direction", "x") or "x").strip().lower()
            if engraving_raster_direction not in ("x", "y"):
                engraving_raster_direction = "x"
            fill_enclosed_hollows = bool(
                slicing_data.get("fill_enclosed_hollows", False))
            first_zone_engraving = bool(
                slicing_data.get("first_zone_engraving_enabled", False))
            intermediate_zone_engraving = bool(
                slicing_data.get(
                    "intermediate_zone_engraving_enabled", False))
            body_engraving_enabled = bool(
                slicing_data.get("body_engraving_enabled", False))
            intermediate_zone_enabled = bool(slicing_data.get(
                "intermediate_zone_enabled", False))
            has_engraving_layers = (
                base_engraving_mode == "engraving"
                or first_zone_engraving
                or (intermediate_zone_enabled and intermediate_zone_engraving)
            )
            has_surface_skin_engraving = (
                surface_skin_enabled and surface_skin_engraving_enabled)
            has_engraving_header = (
                has_engraving_layers or has_surface_skin_engraving)
            first_zone_label = (
                "ENGRAVING"
                if first_zone_engraving
                else "VECTORING")
            intermediate_zone_label = (
                "ENGRAVING"
                if intermediate_zone_enabled and intermediate_zone_engraving
                else "VECTORING")
            remaining_zone_label = (
                base_engraving_mode.upper()
                if base_engraving_mode == "engraving"
                else "VECTORING"
            )
            flat_surface_skin_hatch_overlap_line = ""
            if flat_surface_skin_hatch_overlap is not None:
                flat_surface_skin_hatch_overlap_line = (
                    f";FLAT SURFACE SKIN HATCH OVERLAP: "
                    f"{flat_surface_skin_hatch_overlap * 100.0:.2f}%\n"
                )
            flat_surface_skin_second_pass_header = ""
            if flat_surface_skin_second_pass_enabled:
                flat_surface_skin_second_pass_header = (
                    f";FLAT SURFACE SKIN SECOND PASS: ENABLED\n"
                    f";FLAT SURFACE SKIN SECOND PASS HATCH SPACING: "
                    f"{flat_surface_skin_second_pass_hatch_spacing:.4f} mm\n"
                    f";FLAT SURFACE SKIN SECOND PASS HATCH OVERLAP: "
                    f"{flat_surface_skin_second_pass_hatch_overlap * 100.0:.2f}%\n"
                    f";FLAT SURFACE SKIN SECOND PASS POWER: "
                    f"{flat_surface_skin_second_pass_power}\n"
                    f";FLAT SURFACE SKIN SECOND PASS FEED: "
                    f"{flat_surface_skin_second_pass_speed} mm/min\n"
                    f";FLAT SURFACE SKIN SECOND PASS RASTER DIRECTION: "
                    f"{flat_surface_skin_second_pass_raster_direction.upper()}\n"
                    f";FLAT SURFACE SKIN SECOND PASS CROSS HATCH: "
                    f"{'ENABLED' if flat_surface_skin_second_pass_cross_hatch else 'DISABLED'}\n"
                    f";FLAT SURFACE SKIN SECOND PASS BOUNDARY MODE: "
                    f"{flat_surface_skin_second_pass_boundary_mode.upper()}\n"
                    f";FLAT SURFACE SKIN SECOND PASS CONTOUR LINE COUNT: "
                    f"{flat_surface_skin_second_pass_contour_line_count}\n"
                    f";FLAT SURFACE SKIN SECOND PASS CONTOUR WIDTH: "
                    f"{flat_surface_skin_second_pass_contour_width:.4f} mm\n"
                    f";FLAT SURFACE SKIN SECOND PASS CONTOUR OVERLAP: "
                    f"{flat_surface_skin_second_pass_contour_overlap * 100.0:.2f}%\n"
                )
            else:
                flat_surface_skin_second_pass_header = (
                    ";FLAT SURFACE SKIN SECOND PASS: DISABLED\n")

            # PATCH (Issue 5): write OLSK init block with bed offset
            gcode_file.write(generate_init_block(slicing_data))
            gcode_file.write(
                f";ESTIMATE ACCELERATION: "
                f"{engraving_acceleration:.3f} mm/s^2\n"
                f";ROUTINE RECOATER FEED: {recoater_feed} mm/min\n\n"
            )
            gcode_file.write(
                f";TRANSITION INTERNAL FLANGE SUPPORT: "
                f"{'ENABLED' if internal_floor_support_enabled and not solid_mode_enabled else 'DISABLED'}\n"
                f";INTERNAL FLOOR SUPPORT SOURCE: "
                f"{'TRANSITION PREPARATION' if normal_transition_floor_support_enabled else 'DISABLED'}\n"
                f";INTERNAL FLOOR SUPPORT EXTENT: "
                f"ENCLOSED HOLLOW TO ONE LAYER BELOW DETECTED FLOOR OR FLANGE\n"
                f";INTERNAL FLOOR SUPPORT MODE: "
                f"{internal_floor_support_mode.upper()}\n"
                f";INTERNAL FLOOR SUPPORT PATTERN: "
                f"{internal_floor_support_type.upper()}\n"
                f";INTERNAL FLOOR SUPPORT POWER: "
                f"{internal_floor_support_power}\n"
                f";INTERNAL FLOOR SUPPORT FEED: "
                f"{internal_floor_support_speed} mm/min\n"
                f";INTERNAL FLOOR SUPPORT TRACK WIDTH: "
                f"{internal_floor_support_track_width:.4f} mm\n"
                f";INTERNAL FLOOR SUPPORT LINE OVERLAP: "
                f"{internal_floor_support_line_overlap * 100.0:.2f}%\n"
                f";INTERNAL FLOOR SUPPORT LINE SPACING: "
                f"{internal_floor_support_line_spacing:.4f} mm\n"
                f";INTERNAL FLOOR SUPPORT LINES PER HATCH: "
                f"{internal_floor_support_line_count}\n"
                f";INTERNAL FLOOR SUPPORT HATCH SPACING: "
                f"{internal_floor_support_hatch_spacing:.4f} mm\n"
                f";INTERNAL FLOOR SUPPORT GRID SPACING: "
                f"{internal_floor_support_grid_spacing:.4f} mm\n"
                f";INTERNAL FLOOR SUPPORT GRID CLEARANCE: "
                f"{internal_floor_support_grid_clearance:.4f} mm\n"
                f";INTERNAL FLOOR SUPPORT BASE ANGLE: "
                f"{internal_floor_support_angle:.4f} deg\n"
                f";INTERNAL FLOOR SUPPORT ROTATION PER LAYER: "
                f"{internal_floor_support_rotation_per_layer:.4f} deg\n"
                f";INTERNAL FLOOR SUPPORT BOUNDARY CLEARANCE: "
                f"{internal_floor_support_boundary_clearance:.4f} mm\n"
                f";INTERNAL FLOOR SUPPORT MINIMUM FLOOR AREA: "
                f"{internal_floor_support_minimum_area:.4f} mm^2\n\n"
            )
            if (
                not solid_mode_enabled
                and (solid_anchor_pad_enabled or solid_transition_enabled)
            ):
                gcode_file.write(
                    ";NORMAL OBJECT PREPARATION: ENABLED\n"
                    f";SACRIFICIAL ANCHOR PAD: "
                    f"{'ENABLED' if solid_anchor_pad_enabled else 'DISABLED'}\n"
                    f";ANCHOR PAD LAYERS: {solid_anchor_pad_layers}\n"
                    f";ANCHOR PAD SIZE: {solid_anchor_pad_size:.4f} mm x "
                    f"{solid_anchor_pad_size:.4f} mm\n"
                    f";ANCHOR PAD POWER: {solid_anchor_pad_power}\n"
                    f";ANCHOR PAD FEED: {solid_anchor_pad_speed} mm/min\n"
                    f";ANCHOR PAD LINE OVERLAP: "
                    f"{solid_anchor_pad_line_overlap * 100.0:.2f}%\n"
                    f";TRANSITION LAYERS: "
                    f"{solid_transition_layers if solid_transition_enabled else 0}\n"
                    f";TRANSITION BASE HEIGHT: "
                    f"{solid_transition_layers * slicing_data['layer_height'] if solid_transition_enabled else 0:.4f} mm\n"
                    f";TRANSITION INTERNAL FLANGE SUPPORT: "
                    f"{'ENABLED' if solid_transition_enabled else 'DISABLED'}\n"
                    f";TRANSITION PATTERN: "
                    f"{solid_transition_pattern.upper()}\n"
                    f";TRANSITION POWER: {solid_transition_power}\n"
                    f";TRANSITION FEED: "
                    f"{solid_transition_speed} mm/min\n\n"
                )
            if solid_mode_enabled:
                gcode_file.write(
                    ";SOLID OBJECT MODE: ENABLED\n"
                    f";SOLID MERGE OBJECTS BY LAYER: "
                    f"{'ENABLED' if solid_merge_objects_by_layer else 'DISABLED'}\n"
                    f";SOLID SACRIFICIAL ANCHOR PAD: "
                    f"{'ENABLED' if solid_anchor_pad_enabled else 'DISABLED'}\n"
                    f";SOLID ANCHOR PAD LAYERS: {solid_anchor_pad_layers}\n"
                    f";SOLID ANCHOR PAD SIZE: "
                    f"{solid_anchor_pad_size:.4f} mm x "
                    f"{solid_anchor_pad_size:.4f} mm\n"
                    f";SOLID ANCHOR PAD POWER: {solid_anchor_pad_power}\n"
                    f";SOLID ANCHOR PAD FEED: "
                    f"{solid_anchor_pad_speed} mm/min\n"
                    f";SOLID ANCHOR PAD LINE OVERLAP: "
                    f"{solid_anchor_pad_line_overlap * 100.0:.2f}%\n"
                    f";SOLID ANCHOR PAD HATCH PITCH: "
                    f"{solid_anchor_pad_hatch_pitch:.4f} mm\n"
                    f";SOLID ANCHOR PAD ROTATE EACH LAYER: "
                    f"{'ENABLED' if solid_anchor_pad_rotate_each_layer else 'DISABLED'}\n"
                    f";SOLID ANCHOR PAD ROTATION PER LAYER: "
                    f"{solid_anchor_pad_rotation_per_layer:.4f} deg\n"
                    f";SOLID TRANSITION LAYER: "
                    f"{'ENABLED' if solid_transition_enabled else 'DISABLED'}\n"
                    f";SOLID TRANSITION LAYERS: "
                    f"{solid_transition_layers}\n"
                    f";SOLID TRANSITION PATTERN: "
                    f"{solid_transition_pattern.upper()}\n"
                    f";SOLID TRANSITION POWER: "
                    f"{solid_transition_power}\n"
                    f";SOLID TRANSITION FEED: "
                    f"{solid_transition_speed} mm/min\n"
                    f";SOLID TRANSITION TRACK WIDTH: "
                    f"{solid_transition_track_width:.4f} mm\n"
                    f";SOLID TRANSITION LINE SPACING: "
                    f"{solid_transition_line_spacing:.4f} mm\n"
                    f";SOLID TRANSITION LINES PER HATCH: "
                    f"{solid_transition_line_count}\n"
                    f";SOLID TRANSITION HATCH SPACING: "
                    f"{solid_transition_hatch_spacing:.4f} mm\n"
                    f";SOLID TRANSITION GRID SPACING: "
                    f"{solid_transition_grid_spacing:.4f} mm\n"
                    f";SOLID TRANSITION GRID CLEARANCE: "
                    f"{solid_transition_grid_clearance:.4f} mm\n"
                    f";SOLID TRANSITION DIRECTION: "
                    f"{solid_transition_raster_direction.upper()}\n"
                    f";SOLID TRANSITION ROTATE EACH LAYER: "
                    f"{'ENABLED' if solid_transition_rotate_each_layer else 'DISABLED'}\n"
                    f";SOLID TRANSITION ROTATION PER LAYER: "
                    f"{solid_transition_rotation_per_layer:.4f} deg\n"
                    f";SOLID TRANSITION CONTOUR: "
                    f"{'ENABLED' if solid_transition_contour_enabled else 'DISABLED'}\n"
                    f";SOLID TRANSITION CONTOUR POWER: "
                    f"{solid_transition_contour_power}\n"
                    f";SOLID TRANSITION CONTOUR FEED: "
                    f"{solid_transition_contour_speed} mm/min\n"
                    f";SOLID TRANSITION CONTOUR LINES: "
                    f"{solid_transition_contour_line_count}\n"
                    f";SOLID TRANSITION CONTOUR OVERLAP: "
                    f"{solid_transition_contour_overlap * 100.0:.2f}%\n"
                    f";SOLID TRACK WIDTH: {solid_track_width:.4f} mm\n"
                    f";SOLID CLOSE TOP AND BOTTOM: "
                    f"{'ENABLED' if solid_close_top_bottom_enabled else 'DISABLED'}\n"
                    f";SOLID LINE-ONLY LAYERS PER FACE: "
                    f"{solid_close_top_bottom_layers}\n"
                    f";SOLID FACE CLOSURE POWER: "
                    f"{solid_close_top_bottom_power}\n"
                    f";SOLID FACE CLOSURE FEED: "
                    f"{solid_close_top_bottom_speed} mm/min\n"
                    f";SOLID FACE CLOSURE LINE OVERLAP: "
                    f"{solid_close_top_bottom_line_overlap * 100.0:.2f}%\n"
                    f";SOLID FACE CLOSURE HATCH PITCH: "
                    f"{solid_close_top_bottom_hatch_pitch:.4f} mm\n"
                    f";SOLID LINE OVERLAP: "
                    f"{solid_line_overlap * 100.0:.2f}%\n"
                    f";SOLID HATCH PITCH: {solid_hatch_pitch:.4f} mm\n"
                    f";SOLID FIRST ZONE: "
                    f"{'ENABLED' if solid_first_zone_enabled else 'DISABLED'}\n"
                    f";SOLID FIRST ZONE LAYERS: {solid_first_layers_count}\n"
                    f";SOLID FIRST ZONE MODE: "
                    f"{'ENGRAVING' if solid_first_zone_engraving_enabled else 'VECTORING'}\n"
                    f";SOLID FIRST ZONE POWER: {solid_first_power}\n"
                    f";SOLID FIRST ZONE FEED: "
                    f"{solid_first_speed} mm/min\n"
                    f";SOLID FIRST ZONE HATCH SPACING: "
                    f"{solid_first_engraving_hatch_spacing:.4f} mm\n"
                    f";SOLID INTERMEDIATE ZONE: "
                    f"{'ENABLED' if solid_intermediate_zone_enabled else 'DISABLED'}\n"
                    f";SOLID INTERMEDIATE ZONE LAYERS: "
                    f"{solid_intermediate_layers_count}\n"
                    f";SOLID INTERMEDIATE ZONE MODE: "
                    f"{'ENGRAVING' if solid_intermediate_zone_engraving_enabled else 'VECTORING'}\n"
                    f";SOLID INTERMEDIATE ZONE POWER: "
                    f"{solid_intermediate_power}\n"
                    f";SOLID INTERMEDIATE ZONE FEED: "
                    f"{solid_intermediate_speed} mm/min\n"
                    f";SOLID INTERMEDIATE ZONE HATCH SPACING: "
                    f"{solid_intermediate_engraving_hatch_spacing:.4f} mm\n"
                    f";SOLID ALTERNATE PATHS: "
                    f"{'ENABLED' if solid_alternate_lines else 'DISABLED'}\n"
                    f";SOLID ALTERNATE MODE: "
                    f"{solid_alternate_mode.upper()}\n"
                    f";SOLID ISLAND SCANNING: "
                    f"{'ENABLED' if solid_island_scanning_enabled else 'DISABLED'}\n"
                    f";SOLID REQUESTED ISLAND SIZE: "
                    f"{solid_island_size:.4f} mm x "
                    f"{solid_island_size:.4f} mm\n"
                    f";SOLID ISLAND LINE STRATEGY: "
                    f"{solid_island_scan_strategy.upper()}\n"
                    f";SOLID RASTER DIRECTION: "
                    f"{solid_raster_direction.upper()}\n"
                    f";SOLID CROSS HATCH: "
                    f"{'ENABLED' if solid_cross_hatch else 'DISABLED'}\n"
                    f";SOLID POWER: {solid_power}\n"
                    f";SOLID FEED: {solid_speed} mm/min\n"
                    f";SOLID CONTOUR: "
                    f"{'ENABLED' if solid_contour_enabled else 'DISABLED'}\n"
                    f";SOLID CONTOUR OVERLAP: "
                    f"{solid_contour_overlap * 100.0:.2f}%\n"
                    f";SOLID CONTOUR POWER: {solid_contour_power}\n"
                    f";SOLID CONTOUR FEED: "
                    f"{solid_contour_speed} mm/min\n"
                    f";SOLID CONTOUR LINE COUNT: "
                    f"{solid_contour_line_count}\n\n"
                )
            gcode_file.write(
                f";SURFACE SOLID SKIN: "
                f"{'ENABLED' if surface_skin_enabled else 'DISABLED'}\n"
                f";SURFACE SKIN LAYERS: {surface_skin_layers}\n"
                f";SURFACE SKIN FLAT ROOF ENGRAVING: "
                f"{'ENABLED' if surface_skin_engraving_enabled else 'DISABLED'}\n"
                f";SURFACE SKIN HATCH SPACING: "
                f"{surface_skin_hatch_spacing:.4f} mm\n"
                f";SURFACE SKIN POWER: {surface_skin_power}\n"
                f";SURFACE SKIN FEED: {surface_skin_speed} mm/min\n"
                f";SURFACE SKIN ALTERNATE LINES: "
                f"{'ENABLED' if surface_skin_alternate_lines else 'DISABLED'}\n"
                f";SURFACE SKIN ALTERNATE MODE: "
                f"{surface_skin_alternate_mode.upper()}\n"
                f";FLAT SURFACE SKIN LAYERS: {flat_surface_skin_layers}\n"
                f";FLAT SURFACE SKIN HATCH SPACING: "
                f"{flat_surface_skin_hatch_spacing:.4f} mm\n"
                f"{flat_surface_skin_hatch_overlap_line}"
                f";FLAT SURFACE SKIN POWER: {flat_surface_skin_power}\n"
                f";FLAT SURFACE SKIN FEED: {flat_surface_skin_speed} mm/min\n"
                f";FLAT SURFACE SKIN RASTER DIRECTION: "
                f"{flat_surface_skin_raster_direction.upper()}\n"
                f";FLAT SURFACE SKIN CROSS HATCH: "
                f"{'ENABLED' if flat_surface_skin_cross_hatch else 'DISABLED'}\n"
                f";FLAT SURFACE SKIN PATTERN: {flat_surface_skin_pattern.upper()}\n"
                f";FLAT SURFACE SKIN SPIRAL DIRECTION: "
                f"{flat_surface_skin_spiral_direction.upper()}\n"
                f";FLAT SURFACE SKIN BOUNDARY MODE: "
                f"{flat_surface_skin_boundary_mode.upper()}\n"
                f";FLAT SURFACE SKIN CONTOUR LINE COUNT: "
                f"{flat_surface_skin_contour_line_count}\n"
                f";FLAT SURFACE SKIN CONTOUR WIDTH: "
                f"{flat_surface_skin_contour_width:.4f} mm\n"
                f";FLAT SURFACE SKIN CONTOUR OVERLAP: "
                f"{flat_surface_skin_contour_overlap * 100.0:.2f}%\n"
                f"{flat_surface_skin_second_pass_header}"
                f";SURFACE SKIN EDGE CLEARANCE: "
                f"{surface_skin_edge_clearance:.4f} mm\n"
                f";ROOF HOLLOW INFILL: "
                f"{'ENABLED' if roof_hollow_infill_enabled else 'DISABLED'}\n"
                f";ROOF HOLLOW INFILL OVERLAP: "
                f"{roof_hollow_infill_overlap * 100.0:.2f}%\n"
                f";AUTO BODY CONTOUR SHELL FILL: "
                f"{'ENABLED' if auto_contour_line_count_enabled else 'DISABLED'}\n"
                f";BASE BODY CONTOUR LINE COUNT: "
                f"{configured_contour_line_count}\n"
                f";VARIABLE GRID INFILL BEFORE ROOF: "
                f"{'ENABLED' if variable_grid_infill_enabled else 'DISABLED'}\n"
                f";VARIABLE GRID TARGET: {variable_grid_target.upper()}\n"
                f";VARIABLE GRID LAYERS BEFORE ROOF: "
                f"{variable_grid_layers_before_roof}\n"
                f";VARIABLE GRID SPACING: {variable_grid_spacing:.4f} mm\n"
                f";VARIABLE GRID HATCH SPACING: "
                f"{variable_grid_hatch_spacing:.4f} mm\n"
                f";VARIABLE GRID HATCH ROTATION PER LAYER: "
                f"{variable_grid_hatch_rotation_per_layer:.4f} deg\n"
                f";VARIABLE GRID CLEARANCE: "
                f"{variable_grid_clearance:.4f} mm\n"
                f";VARIABLE GRID LINE COUNT: {variable_grid_line_count}\n"
                f";VARIABLE ENGRAVED GRID SPACING: "
                f"{variable_engraving_grid_spacing:.4f} mm\n"
                f";VARIABLE ENGRAVED GRID LINE WIDTH: "
                f"{variable_engraving_grid_line_thickness:.4f} mm\n"
                f";MULTI VARIABLE GRID BEFORE ROOF: "
                f"{'ENABLED' if multi_variable_grid_enabled else 'DISABLED'}\n"
                f";SECONDARY VARIABLE GRID LAYERS BEFORE ROOF: "
                f"{secondary_variable_grid_layers_before_roof}\n"
                f";SECONDARY VARIABLE GRID SPACING: "
                f"{secondary_variable_grid_spacing:.4f} mm\n"
                f";SECONDARY VARIABLE GRID HATCH SPACING: "
                f"{secondary_variable_grid_hatch_spacing:.4f} mm\n"
                f";SECONDARY VARIABLE GRID CLEARANCE: "
                f"{secondary_variable_grid_clearance:.4f} mm\n"
                f";SECONDARY VARIABLE GRID LINE COUNT: "
                f"{secondary_variable_grid_line_count}\n"
                f";SECONDARY VARIABLE ENGRAVED GRID SPACING: "
                f"{secondary_variable_engraving_grid_spacing:.4f} mm\n"
                f";SECONDARY VARIABLE ENGRAVED GRID LINE WIDTH: "
                f"{secondary_variable_engraving_grid_line_thickness:.4f} mm\n"
                f";TERTIARY VARIABLE GRID: "
                f"{'ENABLED' if tertiary_variable_grid_enabled else 'DISABLED'}\n"
                f";TERTIARY VARIABLE GRID LAYERS BEFORE ROOF: "
                f"{tertiary_variable_grid_layers_before_roof}\n"
                f";TERTIARY VARIABLE GRID SPACING: "
                f"{tertiary_variable_grid_spacing:.4f} mm\n"
                f";TERTIARY VARIABLE GRID HATCH SPACING: "
                f"{tertiary_variable_grid_hatch_spacing:.4f} mm\n"
                f";TERTIARY VARIABLE GRID CLEARANCE: "
                f"{tertiary_variable_grid_clearance:.4f} mm\n"
                f";TERTIARY VARIABLE GRID LINE COUNT: "
                f"{tertiary_variable_grid_line_count}\n"
                f";TERTIARY VARIABLE ENGRAVED GRID SPACING: "
                f"{tertiary_variable_engraving_grid_spacing:.4f} mm\n"
                f";TERTIARY VARIABLE ENGRAVED GRID LINE WIDTH: "
                f"{tertiary_variable_engraving_grid_line_thickness:.4f} mm\n"
                f";SLANTED SURFACE SKIN LAYERS: {slanted_surface_skin_layers}\n"
                f";SLANTED SURFACE SKIN: "
                f"{'ENABLED' if slanted_surface_skin_enabled else 'DISABLED'}\n"
                f";SLANTED SURFACE SKIN HATCH SPACING: "
                f"{slanted_surface_skin_hatch_spacing:.4f} mm\n"
                f";SLANTED SURFACE SKIN RASTER DIRECTION: "
                f"{slanted_surface_skin_raster_direction.upper()}\n"
                f";SLANTED SURFACE SKIN CROSS HATCH: "
                f"{'ENABLED' if slanted_surface_skin_cross_hatch else 'DISABLED'}\n"
                f";SLANTED SURFACE SKIN POWER: {slanted_surface_skin_power}\n"
                f";SLANTED SURFACE SKIN FEED: {slanted_surface_skin_speed} mm/min\n\n"
            )
            if has_engraving_header:
                gcode_file.write(
                    ";SCAN MODE: HYBRID PER-ZONE\n"
                    f";FIRST LAYERS: {first_zone_label}\n"
                    f";INTERMEDIATE LAYERS: {intermediate_zone_label}\n"
                    f";REMAINING LAYERS: {remaining_zone_label}\n"
                    ";BODY GEOMETRY: VECTOR HATCH + VECTOR CONTOUR\n"
                    ";SURFACE SKIN GEOMETRY: OPTIONAL ROOF ENGRAVING BLOCKS\n"
                    ";ENGRAVING CONTOURS: RASTERED WITH ENGRAVED INFILL\n"
                    f";ENGRAVING RASTER DIRECTION: "
                    f"{engraving_raster_direction.upper()}\n"
                    f";VECTOR TRACK WIDTH: "
                    f"{nominal_vector_track_width:.4f} mm\n"
                    f";CALCULATED VECTOR WALL WIDTH: "
                    f"{calculated_vector_wall_width:.4f} mm\n"
                    f";MATCH ENGRAVED SHELL TO VECTOR WALL: "
                    f"{'ENABLED' if match_engraved_shell else 'DISABLED'}\n"
                    ";COMPENSATION: velocity_mm_s = feedrate_mm_min / 60\n"
                    ";COMPENSATION: lead_mm = velocity_mm_s^2 / "
                    "(2 * acceleration_mm_s2)\n\n"
                )
            gcode_file.write(
                ";ENGRAVING INTERIOR GRID: "
                f"{'ENABLED' if fill_enclosed_hollows else 'DISABLED'}\n\n"
            )

            anchor_pad_layer_count = (
                solid_anchor_pad_layers
                if solid_anchor_pad_enabled else 0)
            transition_layer_count = (
                solid_transition_layers
                if solid_transition_enabled else 0)
            foundation_layer_count = (
                anchor_pad_layer_count + transition_layer_count)
            total_output_layers = total_layers + foundation_layer_count
            anchor_pad_regions = []
            printable_layer_regions = []
            anchor_source_region = GeometryCollection()
            if foundation_layer_count:
                printable_layer_regions = [
                    candidate
                    for candidate in layer_regions
                    if candidate is not None and not candidate.is_empty
                ]
                if not printable_layer_regions:
                    raise ValueError(
                        "Cannot create preparation layers because the "
                        "model has no non-empty layers.")
                anchor_source_region = unary_union(printable_layer_regions)
                if not anchor_source_region.is_valid:
                    anchor_source_region = anchor_source_region.buffer(0)

            if anchor_pad_layer_count:
                model_footprints = [
                    polygon
                    for polygon in slicer._extract_polygons(
                        anchor_source_region)
                    if not polygon.is_empty and polygon.area > 1e-9
                ]
                model_footprints = sorted(
                    model_footprints,
                    key=lambda polygon: (
                        round(polygon.centroid.y, 6),
                        round(polygon.centroid.x, 6),
                        -polygon.area,
                    ),
                )
                if not model_footprints:
                    raise ValueError(
                        "Cannot create sacrificial anchor pads because no "
                        "model footprints could be resolved.")
                pad_half_size = solid_anchor_pad_size / 2.0
                pad_squares = []
                for footprint in model_footprints:
                    min_x, min_y, max_x, max_y = footprint.bounds
                    center_x = (min_x + max_x) / 2.0
                    center_y = (min_y + max_y) / 2.0
                    pad_squares.append(box(
                        center_x - pad_half_size,
                        center_y - pad_half_size,
                        center_x + pad_half_size,
                        center_y + pad_half_size,
                    ))

                merged_anchor_pads = unary_union(pad_squares)
                if not merged_anchor_pads.is_valid:
                    merged_anchor_pads = merged_anchor_pads.buffer(0)
                anchor_pad_regions = sorted(
                    (
                        polygon
                        for polygon in slicer._extract_polygons(
                            merged_anchor_pads)
                        if not polygon.is_empty and polygon.area > 1e-9
                    ),
                    key=lambda polygon: (
                        round(polygon.centroid.y, 6),
                        round(polygon.centroid.x, 6),
                        -polygon.area,
                    ),
                )
                for anchor_pad_region in anchor_pad_regions:
                    slicer._validate_region_on_bed(
                        anchor_pad_region, machine_bounds)

                gcode_file.write(
                    f";SOLID ANCHOR PAD MODEL FOOTPRINTS: "
                    f"{len(model_footprints)}\n"
                    f";SOLID ANCHOR PAD EXPOSED REGIONS: "
                    f"{len(anchor_pad_regions)}\n"
                    ";SOLID ANCHOR PAD OVERLAPPING SQUARES: "
                    "UNIONED TO PREVENT DOUBLE EXPOSURE\n\n"
                )

                for anchor_layer_index in range(anchor_pad_layer_count):
                    anchor_raster_direction = solid_raster_direction
                    anchor_raster_angle = None
                    if solid_anchor_pad_rotate_each_layer:
                        anchor_base_angle = {
                            "x": 0.0,
                            "y": 90.0,
                            "diagonal": 45.0,
                        }[solid_raster_direction]
                        anchor_raster_angle = (
                            anchor_base_angle
                            + anchor_layer_index
                            * solid_anchor_pad_rotation_per_layer
                        ) % 180.0
                    elif solid_cross_hatch and anchor_layer_index % 2 == 1:
                        anchor_raster_direction = {
                            "x": "y",
                            "y": "x",
                            "diagonal": "anti-diagonal",
                        }[solid_raster_direction]
                    anchor_effective_angle = (
                        anchor_raster_angle
                        if anchor_raster_angle is not None
                        else {
                            "x": 0.0,
                            "y": 90.0,
                            "diagonal": 45.0,
                            "anti-diagonal": 135.0,
                        }[anchor_raster_direction]
                    )
                    anchor_cross_hatch_active = (
                        solid_cross_hatch
                        and not solid_anchor_pad_rotate_each_layer
                    )

                    anchor_infill_settings = prepare_infill_settings(
                        slicing_data=slicing_data,
                        image_size=model_image_size,
                        user_image_size=user_image_size,
                        layer_index=0,
                        total_layers=total_layers,
                    )
                    anchor_infill_settings["power"] = solid_anchor_pad_power
                    anchor_infill_settings["speed"] = solid_anchor_pad_speed
                    anchor_infill_settings["boundary_clearance"] = 0.0
                    anchor_contour_settings = prepare_contour_settings(
                        slicing_data=slicing_data,
                        layer_index=0,
                        total_layers=total_layers,
                    )

                    anchor_pattern_parts = []
                    anchor_region_count = len(anchor_pad_regions)
                    for object_index, anchor_pad_region in enumerate(
                            anchor_pad_regions, start=1):
                        anchor_pattern_body = (
                            slicer._generate_surface_skin_engraving_gcode(
                                region=anchor_pad_region,
                                infill_settings=anchor_infill_settings,
                                scanline_spacing=(
                                    solid_anchor_pad_hatch_pitch),
                                boundary_mode="engrave",
                                reverse_scan=bool(anchor_layer_index % 2),
                                optimize_scan_order=True,
                                engraving_acceleration=(
                                    engraving_acceleration),
                                raster_direction=anchor_raster_direction,
                                raster_angle=anchor_raster_angle,
                                machine_bounds=machine_bounds,
                                alternate_lines=True,
                                alternate_mode="edge-interleave",
                            )
                        )
                        anchor_pattern_parts.append(
                            f";SOLID ANCHOR PAD REGION {object_index}/"
                            f"{anchor_region_count} START\n"
                            f"{anchor_pattern_body}\n"
                            f";SOLID ANCHOR PAD REGION {object_index}/"
                            f"{anchor_region_count} END"
                        )

                    anchor_infill_gcode = (
                        f";SOLID ANCHOR PAD START LAYER "
                        f"{anchor_layer_index + 1}\n"
                        ";ENGRAVED LINE-ONLY SQUARES\n"
                        f";SOLID ANCHOR PAD REGION COUNT "
                        f"{anchor_region_count}\n"
                        f";RASTER DIRECTION "
                        f"{anchor_raster_direction.upper()} "
                        f"CROSS HATCH "
                        f"{'ENABLED' if anchor_cross_hatch_active else 'DISABLED'}\n"
                        f";RASTER ANGLE "
                        f"{anchor_effective_angle:.4f} deg "
                        f"ROTATE EACH LAYER "
                        f"{'ENABLED' if solid_anchor_pad_rotate_each_layer else 'DISABLED'}\n"
                        f";TRACK WIDTH {solid_track_width:.4f} mm "
                        f"PITCH {solid_anchor_pad_hatch_pitch:.4f} mm "
                        f"OVERLAP "
                        f"{solid_anchor_pad_line_overlap * 100.0:.2f}%\n"
                        + "\n".join(anchor_pattern_parts)
                        + f"\n;SOLID ANCHOR PAD END LAYER "
                        f"{anchor_layer_index + 1}"
                    )
                    anchor_contour_gcode = (
                        f";SOLID ANCHOR PAD CONTOUR OMITTED LAYER "
                        f"{anchor_layer_index + 1}"
                    )

                    gcode_file.write(
                        f";LAYER {anchor_layer_index + 1} SCAN MODE: "
                        "ENGRAVING (SACRIFICIAL ANCHOR PAD)\n")
                    anchor_gcode_content = generate_gcode_for_layer(
                        image_to_gcode_helper=(
                            slicer.image_to_gcode_helper),
                        img_infill=None,
                        svg_contour=None,
                        contour_settings=anchor_contour_settings,
                        infill_settings=anchor_infill_settings,
                        z=z,
                        layer_speed=slicing_data["layer_speed"],
                        bounds=model_bounds,
                        slicing_data=slicing_data,
                        layer_index=anchor_layer_index,
                        infill_gcode_override=anchor_infill_gcode,
                        contour_gcode_override=anchor_contour_gcode,
                    )
                    z += slicing_data["layer_height"]
                    gcode_file.write(anchor_gcode_content)
                    send_slicing_progress_to_interface(
                        websocket_write_queue=websocket_write_queue,
                        layer_index=anchor_layer_index + 1,
                        total_layers=total_output_layers,
                    )

            if transition_layer_count:
                transition_region = printable_layer_regions[0]
                if not transition_region.is_valid:
                    transition_region = transition_region.buffer(0)
                slicer._validate_region_on_bed(
                    transition_region, machine_bounds)

                transition_infill_settings = prepare_infill_settings(
                    slicing_data=slicing_data,
                    image_size=model_image_size,
                    user_image_size=user_image_size,
                    layer_index=0,
                    total_layers=total_layers,
                )
                transition_infill_settings["power"] = (
                    solid_transition_power)
                transition_infill_settings["speed"] = (
                    solid_transition_speed)
                transition_infill_settings["boundary_clearance"] = (
                    max(
                        0.0,
                        (
                            solid_transition_contour_line_count - 1
                        ) * solid_transition_line_spacing
                        - solid_transition_contour_overlap
                        * solid_transition_line_spacing,
                    )
                    if solid_transition_contour_enabled else 0.0
                )
                transition_contour_settings = prepare_contour_settings(
                    slicing_data=slicing_data,
                    layer_index=0,
                    total_layers=total_layers,
                )
                transition_contour_settings["power"] = (
                    solid_transition_contour_power)
                transition_contour_settings["speed"] = (
                    solid_transition_contour_speed)
                transition_base_angle = {
                    "x": 0.0,
                    "y": 90.0,
                    "diagonal": 45.0,
                }[solid_transition_raster_direction]

                for transition_layer_index in range(transition_layer_count):
                    transition_angle = (
                        transition_base_angle
                        + transition_layer_index
                        * solid_transition_rotation_per_layer
                        if solid_transition_rotate_each_layer
                        else transition_base_angle
                    ) % 180.0
                    transition_pattern_gcode = (
                        slicer._generate_infill_vector_gcode(
                            region=transition_region,
                            infill_settings=transition_infill_settings,
                            angle=transition_angle,
                            line_count=solid_transition_line_count,
                            spacing=solid_transition_line_spacing,
                            hatch_spacing=solid_transition_hatch_spacing,
                            infill_type=solid_transition_pattern,
                            grid_spacing=solid_transition_grid_spacing,
                            grid_clearance=solid_transition_grid_clearance,
                            contour_line_count=(
                                solid_transition_contour_line_count
                                if solid_transition_contour_enabled else 0),
                            allow_boundary_fallback=True,
                            reverse_scan=False,
                            optimize_scan_order=True,
                            engraving_mode="engraving",
                            engraving_acceleration=engraving_acceleration,
                            machine_bounds=machine_bounds,
                            preserve_vector_pattern_in_engraving=True,
                        )
                    )
                    transition_output_layer = (
                        anchor_pad_layer_count + transition_layer_index + 1)
                    transition_infill_gcode = (
                        f";SOLID TRANSITION PATTERN START LAYER "
                        f"{transition_output_layer}\n"
                        f";SOLID TRANSITION INDEX "
                        f"{transition_layer_index + 1}/"
                        f"{transition_layer_count}\n"
                        f";PATTERN {solid_transition_pattern.upper()} "
                        f"DIRECTION "
                        f"{solid_transition_raster_direction.upper()} "
                        f"ANGLE {transition_angle:.4f} DEG\n"
                        f";LINES PER HATCH {solid_transition_line_count} "
                        f"TRACK WIDTH "
                        f"{solid_transition_track_width:.4f} mm "
                        f"LINE SPACING "
                        f"{solid_transition_line_spacing:.4f} mm\n"
                        f";HATCH SPACING "
                        f"{solid_transition_hatch_spacing:.4f} mm "
                        f"GRID SPACING "
                        f"{solid_transition_grid_spacing:.4f} mm "
                        f"GRID CLEARANCE "
                        f"{solid_transition_grid_clearance:.4f} mm\n"
                        f"{transition_pattern_gcode}\n"
                        f";SOLID TRANSITION PATTERN END LAYER "
                        f"{transition_output_layer}"
                    )
                    if solid_transition_contour_enabled:
                        transition_contour_body = (
                            slicer._generate_contour_vector_gcode(
                                region=transition_region,
                                contour_settings=transition_contour_settings,
                                line_count=(
                                    solid_transition_contour_line_count),
                                spacing=solid_transition_line_spacing,
                                engraving_mode="engraving",
                                engraving_acceleration=engraving_acceleration,
                                machine_bounds=machine_bounds,
                            )
                        )
                        transition_contour_gcode = (
                            f";SOLID TRANSITION CONTOUR START LAYER "
                            f"{transition_output_layer} "
                            f"LINES "
                            f"{solid_transition_contour_line_count}\n"
                            f"{transition_contour_body}\n"
                            f";SOLID TRANSITION CONTOUR END LAYER "
                            f"{transition_output_layer}"
                        )
                    else:
                        transition_contour_gcode = (
                            f";SOLID TRANSITION CONTOUR DISABLED LAYER "
                            f"{transition_output_layer}"
                        )

                    gcode_file.write(
                        f";LAYER {transition_output_layer} SCAN MODE: "
                        f"ENGRAVING (SOLID TRANSITION LAYER "
                        f"{transition_layer_index + 1}/"
                        f"{transition_layer_count})\n")
                    transition_gcode_content = generate_gcode_for_layer(
                        image_to_gcode_helper=slicer.image_to_gcode_helper,
                        img_infill=None,
                        svg_contour=None,
                        contour_settings=transition_contour_settings,
                        infill_settings=transition_infill_settings,
                        z=z,
                        layer_speed=slicing_data["layer_speed"],
                        bounds=model_bounds,
                        slicing_data=slicing_data,
                        layer_index=transition_output_layer - 1,
                        infill_gcode_override=transition_infill_gcode,
                        contour_gcode_override=transition_contour_gcode,
                    )
                    z += slicing_data["layer_height"]
                    gcode_file.write(transition_gcode_content)
                    send_slicing_progress_to_interface(
                        websocket_write_queue=websocket_write_queue,
                        layer_index=transition_output_layer,
                        total_layers=total_output_layers,
                    )

            flat_surface_skin_layer_count = 0
            slanted_surface_skin_layer_count = 0
            flat_surface_skin_active_regions = []
            slanted_surface_skin_active_regions = []
            internal_floor_support_regions = [
                GeometryCollection() for _ in layer_regions
            ]
            internal_floor_support_source_layers = [
                set() for _ in layer_regions
            ]
            if internal_floor_support_enabled and not solid_mode_enabled:
                (
                    internal_floor_support_regions,
                    internal_floor_support_source_layers,
                ) = slicer._internal_floor_support_regions(
                    layer_regions,
                    minimum_area=internal_floor_support_minimum_area,
                )
            for i in range(len(png_files)):
                output_layer_index = i
                gcode_layer_index = (
                    output_layer_index + foundation_layer_count)
                source_layer_index = i + layer_index_offset
                layer_engraving_mode = get_zone_scan_mode(
                    output_layer_index, slicing_data)
                if (
                    not body_engraving_enabled
                    and layer_engraving_mode == base_engraving_mode
                    and base_engraving_mode != "engraving"
                ):
                    layer_engraving_mode = "vectoring"
                is_engraving = layer_engraving_mode == "engraving"
                if solid_mode_enabled:
                    first_zone_end = (
                        solid_first_layers_count
                        if solid_first_zone_enabled else 0)
                    intermediate_zone_end = first_zone_end + (
                        solid_intermediate_layers_count
                        if solid_intermediate_zone_enabled else 0)
                    if output_layer_index < first_zone_end:
                        solid_stage = "FIRST"
                        solid_pattern_power = solid_first_power
                        solid_pattern_speed = solid_first_speed
                        solid_pattern_spacing = (
                            solid_first_engraving_hatch_spacing
                            if solid_first_zone_engraving_enabled
                            else solid_hatch_pitch)
                        is_engraving = solid_first_zone_engraving_enabled
                    elif output_layer_index < intermediate_zone_end:
                        solid_stage = "INTERMEDIATE"
                        solid_pattern_power = solid_intermediate_power
                        solid_pattern_speed = solid_intermediate_speed
                        solid_pattern_spacing = (
                            solid_intermediate_engraving_hatch_spacing
                            if solid_intermediate_zone_engraving_enabled
                            else solid_hatch_pitch)
                        is_engraving = (
                            solid_intermediate_zone_engraving_enabled)
                    else:
                        solid_stage = "BULK"
                        solid_pattern_power = solid_power
                        solid_pattern_speed = solid_speed
                        solid_pattern_spacing = solid_hatch_pitch
                        is_engraving = True
                    solid_pattern_overlap = solid_line_overlap
                    solid_face_closure_layer = (
                        solid_close_top_bottom_enabled
                        and (
                            output_layer_index < solid_close_top_bottom_layers
                            or output_layer_index >= (
                                total_layers - solid_close_top_bottom_layers)
                        )
                    )
                    if solid_face_closure_layer:
                        solid_pattern_power = solid_close_top_bottom_power
                        solid_pattern_speed = solid_close_top_bottom_speed
                        solid_pattern_spacing = (
                            solid_close_top_bottom_hatch_pitch)
                        solid_pattern_overlap = (
                            solid_close_top_bottom_line_overlap)
                    layer_engraving_mode = (
                        "engraving" if is_engraving else "vectoring")

                    solid_layer_raster_direction = solid_raster_direction
                    if solid_cross_hatch and output_layer_index % 2 == 1:
                        solid_layer_raster_direction = {
                            "x": "y",
                            "y": "x",
                            "diagonal": "anti-diagonal",
                        }[solid_raster_direction]
                    solid_layer_angle = {
                        "x": 0.0,
                        "y": 90.0,
                        "diagonal": 45.0,
                        "anti-diagonal": 135.0,
                    }[solid_layer_raster_direction]

                contour_settings = prepare_contour_settings(
                    slicing_data=slicing_data,
                    layer_index=output_layer_index,
                    total_layers=total_layers)
                infill_settings = prepare_infill_settings(
                    slicing_data=slicing_data, image_size=model_image_size,
                    user_image_size=user_image_size,
                    layer_index=output_layer_index,
                    total_layers=total_layers)
                spacing = float(slicing_data.get("line_bundle_spacing", 0.2))
                track_width = max(
                    0.01, float(slicing_data.get(
                        "infill_line_thickness", 0.8) or 0.8))
                hatch_spacing = float(slicing_data.get(
                    "infill_hatch_spacing", 0) or 0)
                main_hatch_spacing = hatch_spacing
                scanline_spacing = float(slicing_data.get(
                    "scanline_spacing", 0.2) or 0.2)
                infill_type = str(slicing_data.get(
                    "infill_type", "Lines") or "Lines")
                main_infill_type = infill_type
                grid_spacing = float(slicing_data.get(
                    "grid_spacing", 2) or 2)
                main_grid_spacing = grid_spacing
                engraving_grid_spacing = float(slicing_data.get(
                    "engraving_grid_spacing", grid_spacing) or grid_spacing)
                engraving_grid_line_thickness = max(
                    0.01, float(slicing_data.get(
                        "engraving_grid_line_thickness", 0.8) or 0.8))
                contour_line_count = max(
                    1, int(slicing_data.get("contour_line_count", 1) or 1))
                infill_line_count = max(
                    1, int(slicing_data.get("infill_line_count", 1) or 1))
                main_infill_line_count = infill_line_count
                region = layer_regions[i]
                if is_engraving:
                    contour_line_count = 1
                    infill_line_count = 1
                body_contour_line_count = contour_line_count
                if auto_contour_line_count_enabled and not is_engraving:
                    body_contour_line_count = (
                        slicer._auto_contour_line_count_for_region(
                            region=region,
                            base_count=contour_line_count,
                            spacing=spacing,
                            track_width=nominal_vector_track_width))
                contour_overlap = max(
                    -1.0, min(1.0, float(slicing_data.get(
                        "contour_infill_overlap", 0.15) or 0.0)))
                if is_engraving:
                    scanline_spacing = slicer._engraving_hatch_spacing_for_layer(
                        output_layer_index, slicing_data)
                if bool(slicing_data.get(
                        "use_custom_grid_clearance", False)):
                    grid_clearance = max(0.0, float(slicing_data.get(
                        "grid_clearance", 0.2) or 0.0))
                elif is_engraving:
                    grid_clearance = scanline_spacing
                elif (
                    str(infill_type).strip().lower() == "grid"
                    or bool(slicing_data.get(
                        "hatch_rotation_grouping_enabled", False))
                ):
                    grid_clearance = max(
                        0.0, float(slicing_data.get(
                            "grid_clearance", 0.2) or 0.2))
                else:
                    grid_clearance = abs(contour_overlap) * spacing
                main_grid_clearance = grid_clearance
                infill_settings["boundary_clearance"] = (
                    max(0.0, (contour_line_count - 1) * spacing
                        - contour_overlap * spacing))
                body_infill_settings = dict(infill_settings)
                body_infill_settings["boundary_clearance"] = (
                    max(0.0, (body_contour_line_count - 1) * spacing
                        - contour_overlap * spacing))
                if auto_contour_line_count_enabled and not is_engraving:
                    body_infill_settings["boundary_clearance"] = max(
                        body_infill_settings["boundary_clearance"],
                        slicer._calculate_vector_wall_width(
                            body_contour_line_count,
                            spacing,
                            nominal_vector_track_width)
                        - contour_overlap * spacing)
                if bool(slicing_data.get(
                        "hatch_rotation_grouping_enabled", False)):
                    group_layers = max(1, int(slicing_data.get(
                        "hatch_rotation_group_layers", 5) or 5))
                    rotation_index = output_layer_index // group_layers
                else:
                    group_layers = 1
                    rotation_index = output_layer_index
                base_infill_angle = float(
                    slicing_data.get("infill_angle", 0) or 0)
                rotation_step = float(slicing_data.get(
                    "hatch_rotation_per_layer", 67) or 0)
                vector_layer_angle = (
                    base_infill_angle + rotation_index * rotation_step
                ) % 180
                variable_grid_layer_angle = (
                    base_infill_angle
                    + output_layer_index * variable_grid_hatch_rotation_per_layer
                ) % 180
                support_margin_for_surface_skin = max(
                    spacing, surface_skin_hatch_spacing)
                variable_grid_wall_clearance = float(
                    infill_settings.get("boundary_clearance", 0.0) or 0.0)
                current_surface_skin_exclusion_region = GeometryCollection()
                if surface_skin_enabled:
                    current_surface_skin_exclusion_region = (
                        slicer._current_surface_skin_exclusion_region(
                            layer_regions=layer_regions,
                            layer_index=i,
                            flat_skin_layers=flat_surface_skin_layers,
                            slanted_skin_layers=slanted_surface_skin_layers,
                            flat_hatch_spacing=flat_surface_skin_hatch_spacing,
                            slanted_hatch_spacing=slanted_surface_skin_hatch_spacing,
                            slanted_enabled=slanted_surface_skin_enabled,
                            shell_clearance=surface_skin_edge_clearance,
                            support_margin=support_margin_for_surface_skin,
                            flat_active_regions=flat_surface_skin_active_regions,
                            slanted_active_regions=slanted_surface_skin_active_regions))
                has_carried_surface_skin_layer = (
                    surface_skin_enabled
                    and slicer._has_carried_surface_skin_on_layer(
                        region,
                        flat_surface_skin_active_regions
                        + slanted_surface_skin_active_regions,
                        support_margin_for_surface_skin)
                )
                is_current_surface_skin_layer = (
                    surface_skin_enabled
                    and (
                        has_carried_surface_skin_layer
                        or slicer._has_current_surface_skin_candidate(
                            layer_regions=layer_regions,
                            layer_index=i,
                            flat_skin_layers=flat_surface_skin_layers,
                            slanted_skin_layers=slanted_surface_skin_layers,
                            flat_hatch_spacing=flat_surface_skin_hatch_spacing,
                            slanted_hatch_spacing=slanted_surface_skin_hatch_spacing,
                            slanted_enabled=slanted_surface_skin_enabled,
                            shell_clearance=surface_skin_edge_clearance,
                            support_margin=support_margin_for_surface_skin)
                    )
                )
                variable_grid_stage_regions = []
                variable_grid_stage = 0
                variable_grid_support_region = GeometryCollection()
                variable_grid_roof_kind = "ROOF"

                def variable_grid_stage_spec(stage):
                    if stage == 1:
                        return {
                            "stage": 1,
                            "lookahead": variable_grid_layers_before_roof,
                            "grid_spacing": variable_grid_spacing,
                            "hatch_spacing": variable_grid_hatch_spacing,
                            "grid_clearance": variable_grid_clearance,
                            "line_count": variable_grid_line_count,
                        }
                    if stage == 2:
                        return {
                            "stage": 2,
                            "lookahead": (
                                variable_grid_layers_before_roof
                                + secondary_variable_grid_layers_before_roof),
                            "grid_spacing": secondary_variable_grid_spacing,
                            "hatch_spacing": (
                                secondary_variable_grid_hatch_spacing),
                            "grid_clearance": (
                                secondary_variable_grid_clearance),
                            "line_count": (
                                secondary_variable_grid_line_count),
                        }
                    return {
                        "stage": 3,
                        "lookahead": (
                            variable_grid_layers_before_roof
                            + secondary_variable_grid_layers_before_roof
                            + tertiary_variable_grid_layers_before_roof),
                        "grid_spacing": tertiary_variable_grid_spacing,
                        "hatch_spacing": tertiary_variable_grid_hatch_spacing,
                        "grid_clearance": tertiary_variable_grid_clearance,
                        "line_count": tertiary_variable_grid_line_count,
                    }

                if (
                    variable_grid_infill_enabled
                    and surface_skin_enabled
                ):
                    assigned_variable_region = GeometryCollection()
                    stages_to_project = [1]
                    if multi_variable_grid_enabled:
                        stages_to_project.append(2)
                    if (
                        multi_variable_grid_enabled
                        and tertiary_variable_grid_enabled
                    ):
                        stages_to_project.append(3)

                    for stage in stages_to_project:
                        stage_spec = variable_grid_stage_spec(stage)
                        projection_margin = max(
                            support_margin_for_surface_skin,
                            float(spacing) * float(stage_spec["line_count"]))
                        total_stage_region = (
                            slicer._roof_skin_support_region(
                                layer_regions=layer_regions,
                                layer_index=i,
                                lookahead_layers=stage_spec["lookahead"],
                                flat_skin_layers=flat_surface_skin_layers,
                                slanted_skin_layers=(
                                    slanted_surface_skin_layers),
                                flat_hatch_spacing=(
                                    flat_surface_skin_hatch_spacing),
                                slanted_hatch_spacing=(
                                    slanted_surface_skin_hatch_spacing),
                                slanted_enabled=(
                                    slanted_surface_skin_enabled),
                                shell_clearance=(
                                    surface_skin_edge_clearance),
                                support_margin=(
                                    support_margin_for_surface_skin),
                                projection_margin=projection_margin))
                        if (
                            assigned_variable_region is not None
                            and not assigned_variable_region.is_empty
                            and total_stage_region is not None
                            and not total_stage_region.is_empty
                        ):
                            try:
                                total_stage_region = (
                                    total_stage_region.difference(
                                        assigned_variable_region))
                            except Exception:
                                total_stage_region = GeometryCollection()
                        total_stage_region = (
                            slicer._clean_surface_skin_region(
                                total_stage_region,
                                support_margin_for_surface_skin))
                        if (
                            variable_grid_wall_clearance > 0
                            and total_stage_region is not None
                            and not total_stage_region.is_empty
                        ):
                            try:
                                layer_core_region = region.buffer(
                                    -variable_grid_wall_clearance,
                                    join_style=2,
                                    mitre_limit=5)
                                total_stage_region = (
                                    total_stage_region.intersection(
                                        layer_core_region))
                            except Exception:
                                total_stage_region = GeometryCollection()
                            total_stage_region = (
                                slicer._clean_surface_skin_region(
                                    total_stage_region,
                                    support_margin_for_surface_skin))
                        if (
                            total_stage_region is None
                            or total_stage_region.is_empty
                        ):
                            continue
                        stage_spec["region"] = total_stage_region
                        variable_grid_stage_regions.append(stage_spec)
                        try:
                            assigned_variable_region = unary_union([
                                assigned_variable_region,
                                total_stage_region])
                        except Exception:
                            assigned_variable_region = total_stage_region

                if variable_grid_stage_regions:
                    variable_grid_stage = variable_grid_stage_regions[0]["stage"]
                    variable_grid_support_region = unary_union([
                        stage_spec["region"]
                        for stage_spec in variable_grid_stage_regions
                    ])
                    variable_grid_lookahead = max(
                        stage_spec["lookahead"]
                        for stage_spec in variable_grid_stage_regions)
                    variable_grid_roof_kind = (
                        "FLAT ROOF"
                        if slicer._is_before_flat_roof_skin(
                            layer_regions=layer_regions,
                            layer_index=i,
                            lookahead_layers=variable_grid_lookahead,
                            skin_layers=flat_surface_skin_layers,
                            hatch_spacing=flat_surface_skin_hatch_spacing)
                        else "SLANTED ROOF"
                    )
                if variable_grid_stage == 1:
                    engraving_grid_spacing = variable_engraving_grid_spacing
                    engraving_grid_line_thickness = (
                        variable_engraving_grid_line_thickness)
                    engraving_grid_line_count = variable_grid_line_count
                elif variable_grid_stage == 2:
                    engraving_grid_spacing = (
                        secondary_variable_engraving_grid_spacing)
                    engraving_grid_line_thickness = (
                        secondary_variable_engraving_grid_line_thickness)
                    engraving_grid_line_count = (
                        secondary_variable_grid_line_count)
                elif variable_grid_stage == 3:
                    engraving_grid_spacing = (
                        tertiary_variable_engraving_grid_spacing)
                    engraving_grid_line_thickness = (
                        tertiary_variable_engraving_grid_line_thickness)
                    engraving_grid_line_count = (
                        tertiary_variable_grid_line_count)
                else:
                    engraving_grid_line_count = main_infill_line_count
                hollow_infill_type = main_infill_type
                hollow_grid_spacing = main_grid_spacing
                hollow_hatch_spacing = main_hatch_spacing
                hollow_grid_clearance = main_grid_clearance
                hollow_infill_line_count = main_infill_line_count
                variable_grid_applies_to_main = (
                    variable_grid_stage
                    and (
                        variable_grid_target == "both"
                        or not roof_hollow_infill_enabled
                    )
                )
                variable_grid_applies_to_hollow = (
                    variable_grid_stage
                    and roof_hollow_infill_enabled
                    and variable_grid_target in {"hollow", "both"}
                )
                base_main_infill_type = main_infill_type
                base_main_grid_spacing = main_grid_spacing
                base_main_hatch_spacing = main_hatch_spacing
                base_main_grid_clearance = main_grid_clearance
                base_main_infill_line_count = main_infill_line_count
                if variable_grid_stage == 1:
                    hollow_infill_type = "Grid"
                    hollow_grid_spacing = variable_grid_spacing
                    hollow_hatch_spacing = variable_grid_hatch_spacing
                    hollow_grid_clearance = variable_grid_clearance
                    hollow_infill_line_count = variable_grid_line_count
                    if variable_grid_applies_to_main:
                        main_infill_type = "Grid"
                        main_grid_spacing = variable_grid_spacing
                        main_hatch_spacing = variable_grid_hatch_spacing
                        main_grid_clearance = variable_grid_clearance
                        main_infill_line_count = variable_grid_line_count
                elif variable_grid_stage == 2:
                    hollow_infill_type = "Grid"
                    hollow_grid_spacing = secondary_variable_grid_spacing
                    hollow_hatch_spacing = secondary_variable_grid_hatch_spacing
                    hollow_grid_clearance = secondary_variable_grid_clearance
                    hollow_infill_line_count = (
                        secondary_variable_grid_line_count)
                    if variable_grid_applies_to_main:
                        main_infill_type = "Grid"
                        main_grid_spacing = secondary_variable_grid_spacing
                        main_hatch_spacing = (
                            secondary_variable_grid_hatch_spacing)
                        main_grid_clearance = secondary_variable_grid_clearance
                        main_infill_line_count = (
                            secondary_variable_grid_line_count)
                elif variable_grid_stage == 3:
                    hollow_infill_type = "Grid"
                    hollow_grid_spacing = tertiary_variable_grid_spacing
                    hollow_hatch_spacing = tertiary_variable_grid_hatch_spacing
                    hollow_grid_clearance = tertiary_variable_grid_clearance
                    hollow_infill_line_count = (
                        tertiary_variable_grid_line_count)
                    if variable_grid_applies_to_main:
                        main_infill_type = "Grid"
                        main_grid_spacing = tertiary_variable_grid_spacing
                        main_hatch_spacing = (
                            tertiary_variable_grid_hatch_spacing)
                        main_grid_clearance = tertiary_variable_grid_clearance
                        main_infill_line_count = (
                            tertiary_variable_grid_line_count)
                if is_engraving:
                    # Raster scan: always 0° (horizontal) — lines scan left to
                    # right, naturally forming the cross-section profile.
                    # Per-layer rotation and infill_angle are vectoring concepts
                    # and have no meaning in line-by-line raster mode.
                    layer_angle = 0.0
                    engraving_inner_grid_angle = (
                        variable_grid_layer_angle
                        if variable_grid_stage else vector_layer_angle)
                    hatch_exclusion_angles = []
                else:
                    layer_angle = vector_layer_angle
                    # Grouping controls how long a hatch direction is held.
                    # Previous layers must not cut holes into the current
                    # layer's hatches; doing so fragmented variable grids into
                    # isolated dashes and made the exclusion geometry explode.
                    hatch_exclusion_angles = []
                reverse_scan = (
                    bool(slicing_data.get("optimize_scan_order", True))
                    and i % 2 == 1)
                optimize_scan_order = bool(
                    slicing_data.get("optimize_scan_order", True))
                if is_engraving:
                    # Raster only the perimeter shell. The inset material core
                    # remains empty unless the interior-grid option is enabled.
                    scan_region, core_region = (
                        slicer._engraving_shell_and_core_regions(
                            region, engraving_contour_thickness))
                    if not current_surface_skin_exclusion_region.is_empty:
                        try:
                            scan_region = scan_region.difference(
                                current_surface_skin_exclusion_region)
                            core_region = core_region.difference(
                                current_surface_skin_exclusion_region)
                        except Exception:
                            pass
                        scan_region = slicer._clean_geometry_for_surface_skin(
                            scan_region)
                        core_region = slicer._clean_geometry_for_surface_skin(
                            core_region)
                    slicer._validate_region_on_bed(region, machine_bounds)

                    grid_band_region = GeometryCollection()
                    vector_engraving_grid_region = GeometryCollection()
                    engraving_hollow_infill_region = GeometryCollection()
                    if roof_hollow_infill_enabled:
                        roof_hollow_overlap_distance = (
                            roof_hollow_infill_overlap * spacing)
                        engraving_hollow_infill_region = (
                            slicer._roof_hollow_infill_region(
                                region,
                                overlap_distance=roof_hollow_overlap_distance))
                        if (
                            engraving_hollow_infill_region is not None
                            and not engraving_hollow_infill_region.is_empty
                            and not current_surface_skin_exclusion_region.is_empty
                        ):
                            try:
                                engraving_hollow_infill_region = (
                                    engraving_hollow_infill_region.difference(
                                        current_surface_skin_exclusion_region))
                            except Exception:
                                engraving_hollow_infill_region = (
                                    GeometryCollection())
                        engraving_hollow_infill_region = (
                            slicer._clean_surface_skin_region(
                                engraving_hollow_infill_region,
                                support_margin_for_surface_skin))
                    engraving_hollow_infill_active = (
                        engraving_hollow_infill_region is not None
                        and not engraving_hollow_infill_region.is_empty
                        and engraving_hollow_infill_region.area > 1e-6)
                    if (
                        fill_enclosed_hollows
                        or variable_grid_stage
                        or engraving_hollow_infill_active
                    ):
                        overlap_distance = contour_overlap * track_width
                        if abs(overlap_distance) > 1e-9:
                            core_region = core_region.buffer(
                                overlap_distance,
                                join_style=2,
                                mitre_limit=5)
                            core_region = core_region.intersection(region)
                        slicer._validate_region_on_bed(
                            core_region, machine_bounds)
                        vector_engraving_grid_region = core_region
                        if (
                            engraving_hollow_infill_active
                            and not fill_enclosed_hollows
                        ):
                            vector_engraving_grid_region = (
                                engraving_hollow_infill_region)
                        if (
                            variable_grid_stage
                            and not fill_enclosed_hollows
                            and not engraving_hollow_infill_active
                            and variable_grid_support_region is not None
                            and not variable_grid_support_region.is_empty
                        ):
                            try:
                                vector_engraving_grid_region = (
                                    vector_engraving_grid_region.intersection(
                                        variable_grid_support_region))
                            except Exception:
                                vector_engraving_grid_region = (
                                    GeometryCollection())
                        vector_engraving_grid_region = (
                            slicer._clean_surface_skin_region(
                                vector_engraving_grid_region,
                                support_margin_for_surface_skin))
                        if (
                            variable_grid_stage
                            and (
                                vector_engraving_grid_region is None
                                or vector_engraving_grid_region.is_empty
                            )
                        ):
                            vector_engraving_grid_region = core_region
                        if (
                            vector_engraving_grid_region is not None
                            and not vector_engraving_grid_region.is_empty
                        ):
                            grid_band_region = (
                                slicer._engraving_grid_band_region(
                                    vector_engraving_grid_region,
                                    engraving_inner_grid_angle,
                                    engraving_grid_spacing,
                                    engraving_grid_line_thickness,
                                    0.0))
                            grid_band_region = (
                                slicer._clean_geometry_for_surface_skin(
                                    grid_band_region))

                    combined_gcode = slicer._generate_combined_engraving_rows(
                        shell_region=scan_region,
                        grid_region=grid_band_region,
                        infill_settings=infill_settings,
                        scanline_spacing=scanline_spacing,
                        reverse_scan=reverse_scan,
                        optimize_scan_order=optimize_scan_order,
                        engraving_acceleration=engraving_acceleration,
                        raster_direction=engraving_raster_direction,
                        machine_bounds=machine_bounds)

                    contour_gcode = (
                        ";ENGRAVING CONTOUR SHELL IS RASTERED WITH INFILL\n"
                        ";NO SEPARATE VECTOR CONTOUR FOR THIS ENGRAVED LAYER\n"
                    )
                    infill_lead = slicer._compensation_distance(
                        infill_settings["speed"], engraving_acceleration)
                    infill_velocity = slicer._feedrate_to_velocity(
                        infill_settings["speed"])
                    fill_label = "EMPTY INTERIOR CORE"
                    if (
                        fill_enclosed_hollows
                        or variable_grid_stage
                        or engraving_hollow_infill_active
                    ):
                        fill_label = "RASTER GRID INTERIOR CORE"
                    infill_gcode = (
                        f";ENGRAVING CONTOUR SHELL RASTER "
                        f"F {infill_settings['speed']} mm/min "
                        f"V {infill_velocity:.4f} mm/s "
                        f"LEAD {infill_lead:.4f} mm "
                        f"ACCEL {engraving_acceleration:.3f} mm/s^2 "
                        f"TRACK {track_width:.4f} mm "
                        f"SHELL_WIDTH {engraving_contour_thickness:.4f} mm "
                        f"PITCH {scanline_spacing:.4f} mm "
                        f"DIRECTION {engraving_raster_direction.upper()} "
                        f"PATTERN {main_infill_type.upper()} "
                        f"FILL {fill_label}\n"
                        ";ENGRAVING COMBINED ROWS START\n"
                        f"{combined_gcode}\n"
                        ";ENGRAVING COMBINED ROWS END\n"
                    )
                    engraving_vector_grid_gcode = ""
                    if (
                        vector_engraving_grid_region is not None
                        and not vector_engraving_grid_region.is_empty
                        and grid_band_region.is_empty
                    ):
                        engraving_grid_settings = dict(infill_settings)
                        engraving_grid_settings["boundary_clearance"] = 0.0
                        engraving_vector_grid_gcode = (
                            slicer._generate_sparse_grid_vector_gcode(
                                region=vector_engraving_grid_region,
                                infill_settings=engraving_grid_settings,
                                angle=engraving_inner_grid_angle,
                                line_count=engraving_grid_line_count,
                                spacing=spacing,
                                grid_spacing=engraving_grid_spacing,
                                grid_clearance=0.0,
                                reverse_scan=reverse_scan,
                                optimize_scan_order=optimize_scan_order,
                                engraving_acceleration=engraving_acceleration,
                                engraving_mode=layer_engraving_mode,
                                machine_bounds=machine_bounds))
                    if engraving_vector_grid_gcode:
                        if variable_grid_stage:
                            infill_gcode += (
                                "\n"
                                f";VARIABLE GRID STAGE "
                                f"{variable_grid_stage} "
                                f"ACTIVE LAYER {i + 1} "
                                f"BEFORE {variable_grid_roof_kind} "
                                f"GRID {engraving_grid_spacing:.4f} mm "
                                f"WIDTH {engraving_grid_line_thickness:.4f} mm "
                                f"ANGLE {engraving_inner_grid_angle:.4f} deg "
                                f"ROTATION "
                                f"{variable_grid_hatch_rotation_per_layer:.4f} deg/layer "
                                "MODE ENGRAVING\n"
                                ";ENGRAVING VARIABLE GRID START\n"
                                f"{engraving_vector_grid_gcode}\n"
                                ";ENGRAVING VARIABLE GRID END\n"
                                ";VARIABLE GRID END\n"
                            )
                        else:
                            infill_gcode += (
                                "\n"
                                f";ENGRAVING INTERIOR GRID START "
                                f"GRID {engraving_grid_spacing:.4f} mm "
                                f"WIDTH {engraving_grid_line_thickness:.4f} mm\n"
                                f"{engraving_vector_grid_gcode}\n"
                                ";ENGRAVING INTERIOR GRID END\n"
                            )
                    if not grid_band_region.is_empty:
                        infill_gcode += (
                            f";ENGRAVING GRID INFILL START "
                            f"WIDTH {engraving_grid_line_thickness:.4f} mm "
                            f"ANGLE {engraving_inner_grid_angle:.4f} deg "
                            f"RASTER_PITCH {scanline_spacing:.4f} mm "
                            "PREVIEW_ONLY\n"
                            + slicer._generate_preview_grid_comments(
                                grid_band_region, scanline_spacing)
                            + "\n;ENGRAVING GRID INFILL END\n"
                        )
                else:
                    infill_gcode = ""
                    def clean_vector_infill_region(candidate_region):
                        if (
                            candidate_region is None
                            or candidate_region.is_empty
                        ):
                            return GeometryCollection()
                        candidate_region = slicer._clean_surface_skin_region(
                            candidate_region,
                            support_margin_for_surface_skin)
                        if current_surface_skin_exclusion_region.is_empty:
                            return candidate_region
                        try:
                            candidate_region = candidate_region.difference(
                                current_surface_skin_exclusion_region)
                        except Exception:
                            candidate_region = GeometryCollection()
                        return slicer._clean_surface_skin_region(
                            candidate_region,
                            support_margin_for_surface_skin)

                    if variable_grid_applies_to_main:
                        if (
                            variable_grid_support_region is None
                            or variable_grid_support_region.is_empty
                        ):
                            normal_infill_region = region
                            variable_infill_region = GeometryCollection()
                        else:
                            try:
                                # The normal/variable split creates an internal
                                # boundary. Generic contour clearance still
                                # needs to protect the outer model wall, but at
                                # this split the normal infill should meet the
                                # variable-grid contour according to the user's
                                # overlap value. Pre-offset the split boundary
                                # so the later clearance buffer lands at the
                                # requested interface distance instead of
                                # leaving a full contour-shell gap.
                                interface_offset = (
                                    -contour_overlap * spacing
                                    - variable_grid_wall_clearance)
                                adjusted_variable_split_region = (
                                    variable_grid_support_region.buffer(
                                        interface_offset,
                                        join_style=2,
                                        mitre_limit=5))
                                if adjusted_variable_split_region.is_empty:
                                    adjusted_variable_split_region = (
                                        variable_grid_support_region)
                                normal_infill_region = region.difference(
                                    adjusted_variable_split_region)
                                variable_infill_region = region.intersection(
                                    variable_grid_support_region)
                            except Exception:
                                normal_infill_region = region
                                variable_infill_region = GeometryCollection()

                        normal_infill_region = clean_vector_infill_region(
                            normal_infill_region)
                        variable_infill_region = clean_vector_infill_region(
                            variable_infill_region)

                        infill_parts = []
                        slicer._validate_region_on_bed(
                            normal_infill_region, machine_bounds)
                        normal_infill_gcode = (
                            slicer._generate_infill_vector_gcode(
                                region=normal_infill_region,
                                infill_settings=body_infill_settings,
                                angle=layer_angle,
                                line_count=base_main_infill_line_count,
                                spacing=spacing,
                                hatch_spacing=base_main_hatch_spacing,
                                infill_type=base_main_infill_type,
                                grid_spacing=base_main_grid_spacing,
                                grid_clearance=base_main_grid_clearance,
                                contour_line_count=body_contour_line_count,
                                allow_boundary_fallback=(
                                    contour_overlap >= 0),
                                reverse_scan=reverse_scan,
                                optimize_scan_order=optimize_scan_order,
                                engraving_mode=layer_engraving_mode,
                                engraving_acceleration=(
                                    engraving_acceleration),
                                machine_bounds=machine_bounds,
                                hatch_exclusion_angles=(
                                    hatch_exclusion_angles)))
                        if normal_infill_gcode:
                            infill_parts.append(normal_infill_gcode)

                        for stage_spec in sorted(
                                variable_grid_stage_regions,
                                key=lambda spec: int(spec["stage"]),
                                reverse=True):
                            try:
                                stage_infill_region = region.intersection(
                                    stage_spec["region"])
                            except Exception:
                                stage_infill_region = GeometryCollection()
                            stage_infill_region = clean_vector_infill_region(
                                stage_infill_region)
                            slicer._validate_region_on_bed(
                                stage_infill_region, machine_bounds)
                            stage = int(stage_spec["stage"])
                            variable_contour_gcode = ""
                            if variable_grid_roof_kind == "FLAT ROOF":
                                variable_contour_gcode = (
                                    slicer._generate_contour_vector_gcode(
                                        region=stage_infill_region,
                                        contour_settings=contour_settings,
                                        line_count=contour_line_count,
                                        spacing=spacing,
                                        engraving_mode=layer_engraving_mode,
                                        engraving_acceleration=(
                                            engraving_acceleration),
                                        machine_bounds=machine_bounds,
                                        include_holes=False))
                            variable_stage_infill_settings = infill_settings
                            if variable_grid_roof_kind == "SLANTED ROOF":
                                variable_stage_infill_settings = dict(
                                    infill_settings)
                                variable_stage_infill_settings[
                                    "boundary_clearance"] = 0.0
                            variable_infill_gcode = (
                                slicer._generate_infill_vector_gcode(
                                    region=stage_infill_region,
                                    infill_settings=(
                                        variable_stage_infill_settings),
                                    angle=variable_grid_layer_angle,
                                    line_count=stage_spec["line_count"],
                                    spacing=spacing,
                                    hatch_spacing=(
                                        stage_spec["hatch_spacing"]),
                                    infill_type="Grid",
                                    grid_spacing=stage_spec["grid_spacing"],
                                    grid_clearance=(
                                        stage_spec["grid_clearance"]),
                                    contour_line_count=(
                                        contour_line_count),
                                    allow_boundary_fallback=(
                                        contour_overlap >= 0),
                                    reverse_scan=reverse_scan,
                                    optimize_scan_order=optimize_scan_order,
                                    engraving_mode=layer_engraving_mode,
                                    engraving_acceleration=(
                                        engraving_acceleration),
                                    machine_bounds=machine_bounds,
                                    hatch_exclusion_angles=(
                                        hatch_exclusion_angles),
                                    hatch_phase_fraction=(
                                        0.25 if stage == 2
                                        else 0.75 if stage == 3
                                        else 0.5)))
                            variable_stage_parts = []
                            if variable_contour_gcode:
                                variable_stage_parts.append(
                                    f";VARIABLE GRID CONTOUR START "
                                    f"STAGE {stage} "
                                    f"LINES {contour_line_count} "
                                    f"OVERLAP {contour_overlap * 100.0:.2f}%\n"
                                    f"{variable_contour_gcode}\n"
                                    f";VARIABLE GRID CONTOUR END")
                            if variable_infill_gcode:
                                variable_stage_parts.append(
                                    variable_infill_gcode)
                            if variable_stage_parts:
                                variable_stage_gcode = "\n".join(
                                    variable_stage_parts)
                                infill_parts.append(
                                    f";VARIABLE GRID STAGE "
                                    f"{stage} "
                                    f"ACTIVE MAIN INFILL LAYER {i + 1} "
                                    f"BEFORE {variable_grid_roof_kind} "
                                    f"GRID "
                                    f"{stage_spec['grid_spacing']:.4f} mm "
                                    f"HATCH "
                                    f"{stage_spec['hatch_spacing']:.4f} mm "
                                    f"ANGLE "
                                    f"{variable_grid_layer_angle:.4f} deg "
                                    f"CLEARANCE "
                                    f"{stage_spec['grid_clearance']:.4f} mm "
                                    f"LINES {stage_spec['line_count']}\n"
                                    f"{variable_stage_gcode}\n"
                                    f";VARIABLE GRID END\n")
                        infill_gcode = "\n".join(infill_parts)
                    else:
                        infill_region = clean_vector_infill_region(region)
                        slicer._validate_region_on_bed(
                            infill_region, machine_bounds)
                        infill_gcode = slicer._generate_infill_vector_gcode(
                            region=infill_region,
                            infill_settings=body_infill_settings,
                            angle=layer_angle,
                            line_count=main_infill_line_count,
                            spacing=spacing,
                            hatch_spacing=main_hatch_spacing,
                            infill_type=main_infill_type,
                            grid_spacing=main_grid_spacing,
                            grid_clearance=main_grid_clearance,
                            contour_line_count=body_contour_line_count,
                            allow_boundary_fallback=(contour_overlap >= 0),
                            reverse_scan=reverse_scan,
                            optimize_scan_order=optimize_scan_order,
                            engraving_mode=layer_engraving_mode,
                            engraving_acceleration=engraving_acceleration,
                            machine_bounds=machine_bounds,
                            hatch_exclusion_angles=hatch_exclusion_angles)
                    contour_gcode = slicer._generate_contour_vector_gcode(
                        region=region,
                        contour_settings=contour_settings,
                        line_count=body_contour_line_count,
                        spacing=spacing,
                        engraving_mode=layer_engraving_mode,
                        engraving_acceleration=engraving_acceleration,
                        machine_bounds=machine_bounds)
                    if (
                        auto_contour_line_count_enabled
                        and body_contour_line_count != contour_line_count
                    ):
                        body_contour_width = slicer._calculate_vector_wall_width(
                            body_contour_line_count,
                            spacing,
                            nominal_vector_track_width)
                        contour_gcode = (
                            f";AUTO BODY CONTOUR SHELL FILL LAYER {i + 1} "
                            f"LINES {body_contour_line_count} "
                            f"EFFECTIVE SIDE WIDTH "
                            f"{body_contour_width:.4f} mm "
                            f"APPROX TWO-SIDED COVERAGE "
                            f"{body_contour_width * 2.0:.4f} mm "
                            f"BASE LINES {contour_line_count}\n"
                            f"{contour_gcode}")
                if (
                    internal_floor_support_enabled
                    and not solid_mode_enabled
                    and i < len(internal_floor_support_regions)
                ):
                    floor_support_region = internal_floor_support_regions[i]
                    if (
                        floor_support_region is not None
                        and not floor_support_region.is_empty
                        and floor_support_region.area > 1e-9
                    ):
                        support_rotation_index = output_layer_index
                        if internal_floor_support_grouping_enabled:
                            support_rotation_index = (
                                output_layer_index
                                // internal_floor_support_group_layers)
                        floor_support_angle = (
                            internal_floor_support_angle
                            + support_rotation_index
                            * internal_floor_support_rotation_per_layer
                        ) % 180.0
                        floor_support_settings = dict(infill_settings)
                        floor_support_settings["power"] = (
                            internal_floor_support_power)
                        floor_support_settings["speed"] = (
                            internal_floor_support_speed)
                        floor_support_settings["boundary_clearance"] = (
                            internal_floor_support_boundary_clearance)
                        slicer._validate_region_on_bed(
                            floor_support_region, machine_bounds)
                        floor_support_gcode = (
                            slicer._generate_infill_vector_gcode(
                                region=floor_support_region,
                                infill_settings=floor_support_settings,
                                angle=floor_support_angle,
                                line_count=(
                                    internal_floor_support_line_count),
                                spacing=(
                                    internal_floor_support_line_spacing),
                                hatch_spacing=(
                                    internal_floor_support_hatch_spacing),
                                infill_type=internal_floor_support_type,
                                grid_spacing=(
                                    internal_floor_support_grid_spacing),
                                grid_clearance=(
                                    internal_floor_support_grid_clearance),
                                contour_line_count=0,
                                allow_boundary_fallback=True,
                                reverse_scan=reverse_scan,
                                optimize_scan_order=(
                                    internal_floor_support_optimize_scan_order),
                                engraving_mode=internal_floor_support_mode,
                                engraving_acceleration=(
                                    engraving_acceleration),
                                machine_bounds=machine_bounds,
                                hatch_exclusion_angles=[],
                                preserve_vector_pattern_in_engraving=True,
                            )
                        )
                        if (
                            normal_transition_floor_support_enabled
                            and solid_transition_contour_enabled
                        ):
                            floor_support_contour_settings = dict(
                                contour_settings)
                            floor_support_contour_settings["power"] = (
                                solid_transition_contour_power)
                            floor_support_contour_settings["speed"] = (
                                solid_transition_contour_speed)
                            floor_support_contour_gcode = (
                                slicer._generate_contour_vector_gcode(
                                    region=floor_support_region,
                                    contour_settings=(
                                        floor_support_contour_settings),
                                    line_count=(
                                        solid_transition_contour_line_count),
                                    spacing=(
                                        solid_transition_line_spacing),
                                    engraving_mode="engraving",
                                    engraving_acceleration=(
                                        engraving_acceleration),
                                    machine_bounds=machine_bounds,
                                )
                            )
                            if floor_support_contour_gcode:
                                floor_support_gcode = (
                                    f"{floor_support_gcode}\n"
                                    ";TRANSITION FLANGE SUPPORT CONTOUR "
                                    f"START LAYER {i + 1}\n"
                                    f"{floor_support_contour_gcode}\n"
                                    ";TRANSITION FLANGE SUPPORT CONTOUR END"
                                )
                        if floor_support_gcode:
                            source_layers = ", ".join(
                                str(layer + 1)
                                for layer in sorted(
                                    internal_floor_support_source_layers[i]))
                            min_x, min_y, max_x, max_y = (
                                floor_support_region.bounds)
                            infill_gcode += (
                                "\n"
                                f";INTERNAL FLOOR SUPPORT START LAYER "
                                f"{i + 1}\n"
                                f";SUPPORTS DETECTED FLOOR LAYER(S): "
                                f"{source_layers}\n"
                                f";INTERNAL FLOOR SUPPORT BOUNDS "
                                f"X {min_x:.4f} to {max_x:.4f} "
                                f"Y {min_y:.4f} to {max_y:.4f}\n"
                                f";INTERNAL FLOOR SUPPORT MODE "
                                f"{internal_floor_support_mode.upper()} "
                                f"PATTERN "
                                f"{internal_floor_support_type.upper()} "
                                f"ANGLE {floor_support_angle:.4f} deg\n"
                                f";TRANSITION PREPARATION DRIVEN "
                                f"{'YES' if normal_transition_floor_support_enabled else 'NO'}\n"
                                f"{floor_support_gcode}\n"
                                ";INTERNAL FLOOR SUPPORT END\n"
                            )
                if roof_hollow_infill_enabled and not is_engraving:
                    roof_hollow_overlap_distance = (
                        roof_hollow_infill_overlap * spacing)
                    roof_hollow_region = slicer._roof_hollow_infill_region(
                        region,
                        overlap_distance=roof_hollow_overlap_distance)
                    if (
                        roof_hollow_region is not None
                        and not roof_hollow_region.is_empty
                        and not current_surface_skin_exclusion_region.is_empty
                    ):
                        try:
                            roof_hollow_region = roof_hollow_region.difference(
                                current_surface_skin_exclusion_region)
                        except Exception:
                            roof_hollow_region = GeometryCollection()
                        roof_hollow_region = slicer._clean_surface_skin_region(
                            roof_hollow_region,
                            support_margin_for_surface_skin)
                    if (
                        roof_hollow_region is not None
                        and not roof_hollow_region.is_empty
                        and roof_hollow_region.area > 1e-6
                    ):
                        slicer._validate_region_on_bed(
                            roof_hollow_region, machine_bounds)
                        roof_hollow_layer_angle = (
                            variable_grid_layer_angle
                            if variable_grid_applies_to_hollow
                            else layer_angle)
                        roof_hollow_gcode = slicer._generate_infill_vector_gcode(
                            region=roof_hollow_region,
                            infill_settings=infill_settings,
                            angle=roof_hollow_layer_angle,
                            line_count=hollow_infill_line_count,
                            spacing=spacing,
                            hatch_spacing=hollow_hatch_spacing,
                            infill_type=hollow_infill_type,
                            grid_spacing=hollow_grid_spacing,
                            grid_clearance=hollow_grid_clearance,
                            contour_line_count=0,
                            allow_boundary_fallback=False,
                            reverse_scan=reverse_scan,
                            optimize_scan_order=optimize_scan_order,
                            engraving_mode="vectoring",
                            engraving_acceleration=engraving_acceleration,
                            machine_bounds=machine_bounds,
                            hatch_exclusion_angles=[])
                        if roof_hollow_gcode:
                            min_x, min_y, max_x, max_y = (
                                roof_hollow_region.bounds)
                            infill_gcode += (
                                "\n"
                                f";ROOF HOLLOW INFILL START LAYER {i + 1} "
                                f"X {min_x:.4f} to {max_x:.4f} "
                                f"Y {min_y:.4f} to {max_y:.4f}\n"
                                f";ROOF HOLLOW INFILL OVERLAP "
                                f"{roof_hollow_infill_overlap * 100.0:.2f}% "
                                f"({roof_hollow_overlap_distance:.4f} mm)\n"
                                f";ROOF HOLLOW INFILL ANGLE "
                                f"{roof_hollow_layer_angle:.4f} deg\n"
                                + (
                                    f";ROOF HOLLOW VARIABLE GRID ACTIVE "
                                    f"STAGE {variable_grid_stage} "
                                    f"GRID {hollow_grid_spacing:.4f} mm "
                                    f"HATCH {hollow_hatch_spacing:.4f} mm "
                                    f"ROTATION "
                                    f"{variable_grid_hatch_rotation_per_layer:.4f} deg/layer "
                                    f"CLEARANCE {hollow_grid_clearance:.4f} mm "
                                    f"LINES {hollow_infill_line_count}\n"
                                    if variable_grid_stage else ""
                                )
                                +
                                f"{roof_hollow_gcode}\n"
                                ";ROOF HOLLOW INFILL END\n"
                            )

                if surface_skin_enabled:
                    support_margin = max(
                        spacing, surface_skin_hatch_spacing)

                    flat_skin_region = GeometryCollection()
                    slanted_skin_region = GeometryCollection()
                    carried_flat_skin_regions = []
                    next_flat_surface_skin_active_regions = []
                    for active_region, remaining in flat_surface_skin_active_regions:
                        remaining = int(remaining) - 1
                        if remaining <= 0:
                            continue
                        try:
                            clipped_region = active_region.intersection(region)
                        except Exception:
                            continue
                        clipped_region = slicer._clean_surface_skin_region(
                            clipped_region, support_margin)
                        if not clipped_region.is_empty:
                            carried_flat_skin_regions.append(clipped_region)
                            if remaining > 0:
                                next_flat_surface_skin_active_regions.append(
                                    (clipped_region, remaining))

                    flat_skin_region = slicer._flat_roof_skin_region(
                        layer_regions,
                        i,
                        flat_surface_skin_layers,
                        flat_surface_skin_hatch_spacing)
                    flat_skin_region = slicer._surface_skin_interior_region(
                        flat_skin_region,
                        region,
                        surface_skin_edge_clearance)
                    flat_skin_region = slicer._clean_surface_skin_region(
                        flat_skin_region, support_margin)
                    new_flat_skin_region = flat_skin_region
                    if (
                        not new_flat_skin_region.is_empty
                        and flat_surface_skin_layers > 1
                    ):
                        next_flat_surface_skin_active_regions.append(
                            (new_flat_skin_region,
                             flat_surface_skin_layers - 1))
                    if carried_flat_skin_regions:
                        flat_skin_region = unary_union(
                            [new_flat_skin_region]
                            + carried_flat_skin_regions)
                        flat_skin_region = slicer._clean_surface_skin_region(
                            flat_skin_region, support_margin)
                    flat_skin_region = slicer._flat_surface_skin_region(
                        flat_skin_region,
                        flat_surface_skin_hatch_spacing)
                    flat_skin_region = slicer._clean_surface_skin_region(
                        flat_skin_region, support_margin)
                    flat_surface_skin_active_regions = [
                        (candidate_region, remaining)
                        for candidate_region, remaining
                        in next_flat_surface_skin_active_regions
                        if not slicer._flat_surface_skin_region(
                            candidate_region,
                            flat_surface_skin_hatch_spacing).is_empty
                    ]

                    if slanted_surface_skin_enabled:
                        carried_slanted_skin_regions = []
                        next_slanted_surface_skin_active_regions = []
                        for active_region, remaining in slanted_surface_skin_active_regions:
                            remaining_for_current = int(remaining)
                            if remaining_for_current <= 0:
                                continue
                            try:
                                clipped_region = active_region.intersection(
                                    region)
                            except Exception:
                                continue
                            clipped_region = slicer._clean_surface_skin_region(
                                clipped_region, support_margin)
                            if not clipped_region.is_empty:
                                carried_slanted_skin_regions.append(
                                    clipped_region)
                                remaining_after_current = (
                                    remaining_for_current - 1)
                                if remaining_after_current > 0:
                                    next_slanted_surface_skin_active_regions.append(
                                        (clipped_region,
                                         remaining_after_current))

                        slanted_comparison_region = (
                            layer_regions[i + slanted_surface_skin_layers]
                            if i + slanted_surface_skin_layers < len(layer_regions)
                            else None)
                        slanted_skin_region = (
                            slicer._new_surface_upskin_region(
                                region,
                                slanted_comparison_region,
                                support_margin))
                        slanted_skin_region = (
                            slicer._interior_surface_skin_region(
                                slanted_skin_region,
                                region,
                                slanted_surface_skin_hatch_spacing,
                                surface_skin_edge_clearance))
                        slanted_skin_region = (
                            slicer._clean_surface_skin_region(
                                slanted_skin_region, support_margin))
                        if carried_slanted_skin_regions:
                            slanted_skin_region = unary_union(
                                [slanted_skin_region]
                                + carried_slanted_skin_regions)
                            slanted_skin_region = (
                                slicer._clean_surface_skin_region(
                                    slanted_skin_region, support_margin))
                        slanted_skin_region = (
                            slicer._surface_skin_interior_region(
                                slanted_skin_region,
                                region,
                                surface_skin_edge_clearance))
                        slanted_skin_region = (
                            slicer._clean_surface_skin_region(
                                slanted_skin_region, support_margin))
                        if not slanted_skin_region.is_empty:
                            try:
                                slanted_skin_region = (
                                    slanted_skin_region.difference(
                                        new_flat_skin_region))
                                slanted_skin_region = (
                                    slicer._clean_surface_skin_region(
                                        slanted_skin_region, support_margin))
                            except Exception:
                                pass
                        if (
                            not slanted_skin_region.is_empty
                            and slanted_surface_skin_layers > 1
                        ):
                            next_slanted_surface_skin_active_regions.append(
                                (slanted_skin_region,
                                 slanted_surface_skin_layers - 1))
                        slanted_surface_skin_active_regions = (
                            [
                                (candidate_region, remaining)
                                for candidate_region, remaining
                                in next_slanted_surface_skin_active_regions
                                if not slicer._surface_skin_interior_region(
                                    candidate_region,
                                    region,
                                    surface_skin_edge_clearance).is_empty
                            ])
                    else:
                        slanted_surface_skin_active_regions = []

                    slicer._validate_region_on_bed(
                        flat_skin_region, machine_bounds)
                    slicer._validate_region_on_bed(
                        slanted_skin_region, machine_bounds)

                    if (
                        roof_hollow_infill_enabled
                        and flat_skin_region is not None
                        and not flat_skin_region.is_empty
                    ):
                        flat_skin_region = slicer._fill_enclosed_hollows(
                            flat_skin_region)

                    current_flat_raster_direction = flat_surface_skin_raster_direction
                    if (
                        flat_surface_skin_cross_hatch
                        and flat_surface_skin_pattern == "hatch"
                    ):
                        if flat_surface_skin_raster_direction == "diagonal":
                            current_flat_raster_direction = (
                                "diagonal"
                                if flat_surface_skin_layer_count % 2 == 0
                                else "anti-diagonal")
                        else:
                            current_flat_raster_direction = (
                                "x" if flat_surface_skin_layer_count % 2 == 0
                                else "y")
                    current_flat_angle = (
                        0.0 if current_flat_raster_direction == "x"
                        else 90.0 if current_flat_raster_direction == "y"
                        else 135.0 if current_flat_raster_direction == "anti-diagonal"
                        else 45.0
                    )
                    current_second_pass_raster_direction = (
                        flat_surface_skin_second_pass_raster_direction)
                    if (
                        flat_surface_skin_second_pass_cross_hatch
                        and flat_surface_skin_pattern == "hatch"
                    ):
                        if flat_surface_skin_second_pass_raster_direction == "diagonal":
                            current_second_pass_raster_direction = (
                                "diagonal"
                                if flat_surface_skin_layer_count % 2 == 0
                                else "anti-diagonal")
                        else:
                            current_second_pass_raster_direction = (
                                "x" if flat_surface_skin_layer_count % 2 == 0
                                else "y")
                    current_second_pass_angle = (
                        0.0 if current_second_pass_raster_direction == "x"
                        else 90.0 if current_second_pass_raster_direction == "y"
                        else 135.0 if current_second_pass_raster_direction == "anti-diagonal"
                        else 45.0
                    )

                    skin_specs = [
                        {
                            "kind": "FLAT",
                            "pass": 1,
                            "region": flat_skin_region,
                            "spacing": flat_surface_skin_hatch_spacing,
                            "power": flat_surface_skin_power,
                            "speed": flat_surface_skin_speed,
                            "pattern": flat_surface_skin_pattern,
                            "layers": flat_surface_skin_layers,
                            "angle": current_flat_angle,
                            "raster_direction": current_flat_raster_direction,
                            "boundary_mode": flat_surface_skin_boundary_mode,
                            "contour_line_count": flat_surface_skin_contour_line_count,
                            "contour_width": flat_surface_skin_contour_width,
                            "contour_overlap": flat_surface_skin_contour_overlap,
                            "cross_hatch": flat_surface_skin_cross_hatch,
                        },
                    ]
                    if flat_surface_skin_second_pass_enabled:
                        skin_specs.append({
                            "kind": "FLAT",
                            "pass": 2,
                            "region": flat_skin_region,
                            "spacing": flat_surface_skin_second_pass_hatch_spacing,
                            "power": flat_surface_skin_second_pass_power,
                            "speed": flat_surface_skin_second_pass_speed,
                            "pattern": flat_surface_skin_pattern,
                            "layers": flat_surface_skin_layers,
                            "angle": current_second_pass_angle,
                            "raster_direction": current_second_pass_raster_direction,
                            "boundary_mode": flat_surface_skin_second_pass_boundary_mode,
                            "contour_line_count": (
                                flat_surface_skin_second_pass_contour_line_count),
                            "contour_width": (
                                flat_surface_skin_second_pass_contour_width),
                            "contour_overlap": (
                                flat_surface_skin_second_pass_contour_overlap),
                            "cross_hatch": (
                                flat_surface_skin_second_pass_cross_hatch),
                        })
                    if slanted_surface_skin_enabled:
                        current_slanted_raster_direction = (
                            slanted_surface_skin_raster_direction)
                        if slanted_surface_skin_cross_hatch:
                            if slanted_surface_skin_layer_count % 2 == 1:
                                current_slanted_raster_direction = (
                                    "y" if slanted_surface_skin_raster_direction == "x"
                                    else "x" if slanted_surface_skin_raster_direction == "y"
                                    else "anti-diagonal")
                        current_slanted_angle = (
                            0.0 if current_slanted_raster_direction == "x"
                            else 90.0 if current_slanted_raster_direction == "y"
                            else 135.0 if current_slanted_raster_direction == "anti-diagonal"
                            else 45.0)
                        skin_specs.append({
                            "kind": "SLANTED",
                            "region": slanted_skin_region,
                            "spacing": slanted_surface_skin_hatch_spacing,
                            "power": slanted_surface_skin_power,
                            "speed": slanted_surface_skin_speed,
                            "pattern": "hatch",
                            "layers": slanted_surface_skin_layers,
                            "angle": current_slanted_angle,
                            "raster_direction": current_slanted_raster_direction,
                            "pass": 1,
                            # Slanted skin advances as a partial hatch on each
                            # layer and already follows the model contour. A
                            # separate roof boundary creates moving partitions
                            # around features such as the chimney.
                            "boundary_mode": "none",
                            "contour_line_count": flat_surface_skin_contour_line_count,
                            "contour_width": flat_surface_skin_contour_width,
                            "contour_overlap": flat_surface_skin_contour_overlap,
                            "cross_hatch": slanted_surface_skin_cross_hatch,
                        })

                    for skin_spec in skin_specs:
                        skin_region = skin_spec["region"]
                        if (
                            skin_region is None
                            or skin_region.is_empty
                            or skin_region.area <= 1e-6
                        ):
                            continue

                        is_flat_skin = skin_spec["kind"] == "FLAT"
                        engrave_surface_skin = (
                            is_flat_skin
                            and surface_skin_engraving_enabled
                            and skin_spec["raster_direction"] in (
                                "x", "y", "diagonal", "anti-diagonal")
                            and skin_spec["pattern"] != "spiral"
                        )
                        surface_skin_settings = dict(infill_settings)
                        surface_skin_settings["power"] = skin_spec["power"]
                        surface_skin_settings["speed"] = skin_spec["speed"]
                        surface_skin_settings["boundary_clearance"] = 0.0

                        if skin_spec["pattern"] == "spiral":
                            skin_gcode = slicer._generate_surface_skin_spiral_gcode(
                                region=skin_region,
                                infill_settings=surface_skin_settings,
                                spacing=skin_spec["spacing"],
                                direction=flat_surface_skin_spiral_direction,
                                alternate_lines=surface_skin_alternate_lines,
                                alternate_mode=surface_skin_alternate_mode)
                        elif engrave_surface_skin:
                            skin_gcode = slicer._generate_surface_skin_engraving_gcode(
                                region=skin_region,
                                infill_settings=surface_skin_settings,
                                scanline_spacing=skin_spec["spacing"],
                                shell_thickness=skin_spec["contour_width"],
                                inner_grid_spacing=skin_spec["spacing"],
                                inner_grid_line_thickness=track_width,
                                contour_overlap=skin_spec["contour_overlap"],
                                crossing_clearance=grid_clearance,
                                inner_grid_angle=skin_spec["angle"],
                                boundary_mode=skin_spec["boundary_mode"],
                                contour_line_count=skin_spec["contour_line_count"],
                                contour_spacing=vector_line_spacing,
                                vector_contour_settings=contour_settings,
                                reverse_scan=reverse_scan,
                                optimize_scan_order=optimize_scan_order,
                                engraving_acceleration=engraving_acceleration,
                                raster_direction=skin_spec["raster_direction"],
                                machine_bounds=machine_bounds,
                                alternate_lines=surface_skin_alternate_lines,
                                alternate_mode=surface_skin_alternate_mode)
                        else:
                            skin_gcode = slicer._generate_surface_skin_gcode(
                                region=skin_region,
                                infill_settings=surface_skin_settings,
                                angle=skin_spec["angle"],
                                hatch_spacing=skin_spec["spacing"],
                                reverse_scan=reverse_scan,
                                optimize_scan_order=optimize_scan_order,
                                engraving_acceleration=engraving_acceleration,
                                machine_bounds=machine_bounds,
                                alternate_lines=surface_skin_alternate_lines,
                                alternate_mode=surface_skin_alternate_mode)
                        if not skin_gcode:
                            continue

                        min_x, min_y, max_x, max_y = skin_region.bounds
                        preview_region = skin_region
                        roof_contour_preview_comments = ""
                        if engrave_surface_skin:
                            shell_preview_region, grid_preview_region = (
                                slicer._surface_skin_engraving_regions(
                                    region=skin_region,
                                    shell_thickness=skin_spec["contour_width"],
                                    inner_grid_spacing=skin_spec["spacing"],
                                    inner_grid_line_thickness=track_width,
                                    contour_overlap=skin_spec["contour_overlap"],
                                    crossing_clearance=grid_clearance,
                                    inner_grid_angle=skin_spec["angle"]))
                            if skin_spec["boundary_mode"] in (
                                "contour",
                                "vector",
                                "none",
                            ):
                                # The preview skin is raster-only for vector,
                                # contour, and no-boundary modes. Vector/contour
                                # shell moves are represented by normal preview
                                # contour paths instead of fake hatch bands.
                                preview_region = grid_preview_region
                                if skin_spec["boundary_mode"] in (
                                    "contour",
                                    "vector",
                                ):
                                    roof_contour_preview_comments = (
                                        slicer._generate_preview_contour_lines_comments(
                                            skin_region,
                                            skin_spec["contour_line_count"],
                                            vector_line_spacing))
                            else:
                                preview_region = unary_union([
                                    geometry
                                    for geometry in (
                                        shell_preview_region,
                                        grid_preview_region)
                                    if (
                                        geometry is not None
                                        and not geometry.is_empty
                                    )
                                ])
                        preview_comments = (
                            slicer._generate_preview_surface_skin_spiral_comments(
                                preview_region,
                                skin_spec["spacing"],
                                flat_surface_skin_spiral_direction,
                                surface_skin_alternate_lines,
                                surface_skin_alternate_mode)
                            if skin_spec["pattern"] == "spiral"
                            else slicer._generate_preview_surface_skin_comments(
                                preview_region,
                                skin_spec["spacing"],
                                skin_spec["angle"],
                                surface_skin_alternate_lines,
                                surface_skin_alternate_mode)
                        )
                        skin_block = (
                            "\n"
                            f";SURFACE SKIN START LAYER {i + 1} "
                            f"TYPE {skin_spec['kind']} "
                            f"X {min_x:.4f} to {max_x:.4f} "
                            f"Y {min_y:.4f} to {max_y:.4f}\n"
                            f";SURFACE SKIN PASS {skin_spec.get('pass', 1)}\n"
                            f";SURFACE SKIN MODE "
                            f"{'ENGRAVING' if engrave_surface_skin else 'VECTORING'}\n"
                            f";SURFACE SKIN PATTERN {skin_spec['pattern'].upper()}\n"
                            f";SURFACE SKIN RASTER DIRECTION "
                            f"{skin_spec['raster_direction'].upper()}\n"
                            f";SURFACE SKIN CROSS HATCH "
                            f"{'ENABLED' if skin_spec.get('cross_hatch', False) else 'DISABLED'}\n"
                            + (
                                f";SURFACE SKIN BOUNDARY MODE "
                                f"{skin_spec['boundary_mode'].upper()}\n"
                                f";SURFACE SKIN CONTOUR LINE COUNT "
                                f"{skin_spec['contour_line_count']}\n"
                                f";SURFACE SKIN CONTOUR WIDTH "
                                f"{skin_spec['contour_width']:.4f} mm\n"
                                f";SURFACE SKIN CONTOUR OVERLAP "
                                f"{skin_spec['contour_overlap'] * 100.0:.2f}%\n"
                                if engrave_surface_skin else ""
                            )
                            +
                            f";SURFACE SKIN LAYERS {skin_spec['layers']}\n"
                            f";SURFACE SKIN HATCH SPACING "
                            f"{skin_spec['spacing']:.4f} mm\n"
                            f";SURFACE SKIN POWER {skin_spec['power']}\n"
                            f";SURFACE SKIN FEED {skin_spec['speed']} mm/min\n"
                            f";SURFACE SKIN ALTERNATE LINES "
                            f"{'ENABLED' if surface_skin_alternate_lines else 'DISABLED'}\n"
                            f";SURFACE SKIN ALTERNATE MODE "
                            f"{surface_skin_alternate_mode.upper()}\n"
                            f"{skin_gcode}\n"
                            ";SURFACE SKIN PREVIEW START\n"
                            f"{preview_comments}\n"
                            ";SURFACE SKIN PREVIEW END\n"
                            ";SURFACE SKIN END\n"
                        )
                        if is_flat_skin:
                            if flat_surface_skin_roof_only_layers:
                                existing_skin_blocks = ""
                                if ";SURFACE SKIN START" in infill_gcode:
                                    skin_start = infill_gcode.find(
                                        ";SURFACE SKIN START")
                                    existing_skin_blocks = infill_gcode[skin_start:]
                                infill_gcode = (
                                    f"{existing_skin_blocks}\n{skin_block}"
                                    if existing_skin_blocks else skin_block)
                                contour_gcode = (
                                    ";SURFACE SKIN ROOF-ONLY LAYER "
                                    "CONTOUR PREVIEW ONLY\n"
                                    ";NOT EXECUTED BY CONTROLLER\n"
                                    + slicer._generate_preview_contour_comments(
                                        region)
                                )
                            else:
                                infill_gcode += skin_block
                            if roof_contour_preview_comments:
                                contour_gcode += (
                                    "\n;ROOF CONTOUR SHELL PREVIEW ONLY\n"
                                    ";NOT EXECUTED BY CONTROLLER\n"
                                    + roof_contour_preview_comments
                                    + "\n"
                                )
                            if int(skin_spec.get("pass", 1)) == 1:
                                flat_surface_skin_layer_count += 1
                        else:
                            infill_gcode += skin_block
                            slanted_surface_skin_layer_count += 1

                if solid_mode_enabled:
                    solid_layer_contour_enabled = (
                        solid_contour_enabled
                        and not solid_face_closure_layer)
                    solid_infill_settings = dict(infill_settings)
                    solid_infill_settings["power"] = solid_pattern_power
                    solid_infill_settings["speed"] = solid_pattern_speed
                    solid_boundary_clearance = max(
                        0.0,
                        solid_contour_width
                        - solid_contour_overlap * solid_track_width,
                    ) if solid_layer_contour_enabled else 0.0
                    solid_infill_settings["boundary_clearance"] = (
                        solid_boundary_clearance)
                    solid_contour_settings = dict(contour_settings)
                    solid_contour_settings["power"] = solid_contour_power
                    solid_contour_settings["speed"] = solid_contour_speed
                    slicer._validate_region_on_bed(region, machine_bounds)
                    solid_layer_regions = [region]
                    if solid_merge_objects_by_layer:
                        # The combined slice has no model IDs; disconnected
                        # polygons are the stable per-layer object boundary.
                        disconnected_regions = [
                            polygon
                            for polygon in slicer._extract_polygons(region)
                            if not polygon.is_empty and polygon.area > 1e-9
                        ]
                        if disconnected_regions:
                            solid_layer_regions = sorted(
                                disconnected_regions,
                                key=lambda polygon: (
                                    round(polygon.centroid.y, 6),
                                    round(polygon.centroid.x, 6),
                                    -polygon.area,
                                ),
                            )

                    if solid_island_scanning_enabled:
                        solid_line_strategy = solid_island_scan_strategy
                    elif solid_alternate_lines:
                        solid_line_strategy = solid_alternate_mode
                    else:
                        solid_line_strategy = "normal"
                    solid_alternate_lines_active = (
                        solid_line_strategy != "normal")
                    solid_alternate_mode_active = (
                        solid_line_strategy
                        if solid_alternate_lines_active else "two-pass")

                    solid_pattern_parts = []
                    solid_island_count = 0
                    solid_object_count = len(solid_layer_regions)
                    for object_index, solid_object_region in enumerate(
                            solid_layer_regions, start=1):
                        solid_pattern_region = solid_object_region
                        if solid_boundary_clearance > 1e-9:
                            solid_pattern_region = solid_object_region.buffer(
                                -solid_boundary_clearance,
                                join_style=2,
                                mitre_limit=5)
                            if not solid_pattern_region.is_valid:
                                solid_pattern_region = (
                                    solid_pattern_region.buffer(0))

                        if is_engraving and solid_island_scanning_enabled:
                            (
                                island_cells,
                                island_columns,
                                island_rows,
                                actual_island_width,
                                actual_island_height,
                            ) = slicer._solid_island_cells(
                                solid_pattern_region, solid_island_size)
                            island_parts = []
                            object_island_count = len(island_cells)
                            solid_island_count += object_island_count
                            for island_index, island_cell in enumerate(
                                    island_cells, start=1):
                                island_body = (
                                    slicer
                                    ._generate_surface_skin_engraving_gcode(
                                        region=island_cell["region"],
                                        infill_settings=(
                                            solid_infill_settings),
                                        scanline_spacing=(
                                            solid_pattern_spacing),
                                        boundary_mode="engrave",
                                        reverse_scan=bool(
                                            (
                                                output_layer_index
                                                + island_index - 1
                                            ) % 2),
                                        optimize_scan_order=True,
                                        engraving_acceleration=(
                                            engraving_acceleration),
                                        raster_direction=(
                                            solid_layer_raster_direction),
                                        machine_bounds=machine_bounds,
                                        alternate_lines=(
                                            solid_alternate_lines_active),
                                        alternate_mode=(
                                            solid_alternate_mode_active),
                                    )
                                )
                                island_parts.append(
                                    f";SOLID ISLAND {island_index}/"
                                    f"{object_island_count} "
                                    f"ROW {island_cell['row']} "
                                    f"COLUMN {island_cell['column']} "
                                    f"STRATEGY "
                                    f"{solid_line_strategy.upper()} START\n"
                                    f"{island_body}\n"
                                    f";SOLID ISLAND {island_index}/"
                                    f"{object_island_count} END"
                                )
                            solid_pattern_body = (
                                f";SOLID ISLAND GRID "
                                f"{island_columns}x{island_rows} "
                                f"CELLS {object_island_count}\n"
                                f";SOLID ISLAND REQUESTED SIZE "
                                f"{solid_island_size:.4f} mm x "
                                f"{solid_island_size:.4f} mm\n"
                                f";SOLID ISLAND ACTUAL CELL SIZE "
                                f"{actual_island_width:.4f} mm x "
                                f"{actual_island_height:.4f} mm\n"
                                + "\n".join(island_parts)
                            )
                        elif is_engraving:
                            solid_pattern_body = (
                                slicer._generate_surface_skin_engraving_gcode(
                                    region=solid_pattern_region,
                                    infill_settings=solid_infill_settings,
                                    scanline_spacing=solid_pattern_spacing,
                                    boundary_mode="engrave",
                                    reverse_scan=bool(output_layer_index % 2),
                                    optimize_scan_order=True,
                                    engraving_acceleration=(
                                        engraving_acceleration),
                                    raster_direction=(
                                        solid_layer_raster_direction),
                                    machine_bounds=machine_bounds,
                                    alternate_lines=(
                                        solid_alternate_lines_active),
                                    alternate_mode=(
                                        solid_alternate_mode_active),
                                )
                            )
                        else:
                            solid_pattern_body = (
                                slicer._generate_infill_vector_gcode(
                                    region=solid_object_region,
                                    infill_settings=solid_infill_settings,
                                    angle=solid_layer_angle,
                                    line_count=1,
                                    spacing=solid_pattern_spacing,
                                    hatch_spacing=solid_pattern_spacing,
                                    infill_type="Lines",
                                    grid_spacing=solid_pattern_spacing,
                                    grid_clearance=0.0,
                                    contour_line_count=(
                                        solid_contour_line_count
                                        if solid_layer_contour_enabled else 0),
                                    allow_boundary_fallback=False,
                                    reverse_scan=bool(
                                        output_layer_index % 2),
                                    optimize_scan_order=True,
                                    engraving_mode="vectoring",
                                    engraving_acceleration=(
                                        engraving_acceleration),
                                    machine_bounds=machine_bounds,
                                )
                            )
                        solid_pattern_parts.append(
                            f";SOLID OBJECT {object_index}/"
                            f"{solid_object_count} PATTERN START\n"
                            f"{solid_pattern_body}\n"
                            f";SOLID OBJECT {object_index}/"
                            f"{solid_object_count} PATTERN END"
                        )
                    infill_gcode = (
                        f";SOLID PATTERN START LAYER "
                        f"{gcode_layer_index + 1} "
                        f"STAGE {solid_stage} "
                        f"MODE {layer_engraving_mode.upper()}\n"
                        f";SOLID OBJECT COUNT {solid_object_count}\n"
                        f";FACE CLOSURE: "
                        f"{'LINE-ONLY' if solid_face_closure_layer else 'STANDARD'}\n"
                        + (
                            ";ENGRAVING MATERIAL RASTER SOLID ROOF PATTERN\n"
                            if is_engraving else "")
                        + f";RASTER DIRECTION "
                        f"{solid_layer_raster_direction.upper()} "
                        f"CROSS HATCH "
                        f"{'ENABLED' if solid_cross_hatch else 'DISABLED'}\n"
                        f";LINE STRATEGY "
                        f"{solid_line_strategy.upper() if is_engraving else 'NOT APPLICABLE'}\n"
                        f";ISLAND SCANNING "
                        f"{'ENABLED' if is_engraving and solid_island_scanning_enabled else 'DISABLED'}\n"
                        f";SOLID ISLAND COUNT {solid_island_count}\n"
                        f";TRACK WIDTH {solid_track_width:.4f} mm "
                        f"PITCH {solid_pattern_spacing:.4f} mm "
                        f"OVERLAP {solid_pattern_overlap * 100.0:.2f}%\n"
                        + "\n".join(solid_pattern_parts)
                        + f"\n;SOLID PATTERN END LAYER "
                        f"{gcode_layer_index + 1}"
                    )
                    if solid_layer_contour_enabled:
                        solid_contour_parts = []
                        for object_index, solid_object_region in enumerate(
                                solid_layer_regions, start=1):
                            solid_contour_parts.append(
                                f";SOLID OBJECT {object_index}/"
                                f"{solid_object_count} CONTOUR START\n"
                                + slicer._generate_contour_vector_gcode(
                                    region=solid_object_region,
                                    contour_settings=solid_contour_settings,
                                    line_count=solid_contour_line_count,
                                    spacing=solid_track_width,
                                    engraving_mode="vectoring",
                                    engraving_acceleration=(
                                        engraving_acceleration),
                                    machine_bounds=machine_bounds,
                                )
                                + f"\n;SOLID OBJECT {object_index}/"
                                f"{solid_object_count} CONTOUR END"
                            )
                        contour_gcode = (
                            f";SOLID CONTOUR START LAYER "
                            f"{gcode_layer_index + 1} "
                            f"LINES {solid_contour_line_count} "
                            f"WIDTH {solid_contour_width:.4f} mm\n"
                            + "\n".join(solid_contour_parts)
                            + f"\n;SOLID CONTOUR END LAYER "
                            f"{gcode_layer_index + 1}"
                        )
                    elif solid_face_closure_layer:
                        contour_gcode = (
                            f";SOLID CONTOUR OMITTED FOR FACE CLOSURE "
                            f"LAYER {gcode_layer_index + 1}"
                        )
                    else:
                        contour_gcode = (
                            f";SOLID CONTOUR DISABLED LAYER "
                            f"{gcode_layer_index + 1}"
                        )

                gcode_file.write(
                        f";LAYER {gcode_layer_index + 1} SCAN MODE: "
                    f"{layer_engraving_mode.upper()} "
                    f"(SOURCE LAYER {source_layer_index + 1})\n")
                gcode_content = generate_gcode_for_layer(
                    image_to_gcode_helper=slicer.image_to_gcode_helper,
                    img_infill=None, svg_contour=None,
                    contour_settings=contour_settings, infill_settings=infill_settings,
                    z=z, layer_speed=slicing_data["layer_speed"],
                    bounds=model_bounds,
                    slicing_data=slicing_data,
                    layer_index=gcode_layer_index,
                    infill_gcode_override=infill_gcode,
                    contour_gcode_override=contour_gcode)

                z += slicing_data["layer_height"]
                gcode_file.write(gcode_content)
                send_slicing_progress_to_interface(
                    websocket_write_queue=websocket_write_queue,
                    layer_index=gcode_layer_index + 1,
                    total_layers=total_output_layers)

            if not slicing_data.get("visicut_style", False):
                gcode_file.write(
                    slicer.image_to_gcode_helper.add_end_of_job_commands())

        zip_ref.close()
        slicer._normalise_prusa_travel_moves(
            slicing_data["gcode_file_path"],
            slicing_data.get("visicut_style", False))
        slicer._validate_generated_gcode_file(
            slicing_data["gcode_file_path"], slicing_data)

    @staticmethod
    def _layer_start_index(print_height_lower_limit, layer_height):
        """Return the 0-based source layer where the selected height band starts."""
        lower_height = max(0.0, float(print_height_lower_limit or 0.0))
        step = max(1e-9, float(layer_height))
        return int(np.floor(lower_height / step + 1e-9))

    @staticmethod
    def _limit_layer_files(layer_files, print_height_limit,
                           print_height_lower_limit, layer_height):
        """Keeps complete layers inside the selected bed-relative height band."""
        if print_height_limit is None and not print_height_lower_limit:
            return layer_files

        lower_height = max(0.0, float(print_height_lower_limit or 0.0))
        upper_height = (
            float("inf")
            if print_height_limit is None
            else max(0.0, float(print_height_limit))
        )
        if upper_height <= lower_height:
            return []

        step = max(1e-9, float(layer_height))
        first_layer = PrusaSlicer._layer_start_index(
            print_height_lower_limit, layer_height)
        last_layer = int(np.floor(upper_height / step + 1e-9))
        return layer_files[first_layer:last_layer]

    def _slice_padding_for_model(self, model_size, slicing_data):
        if "slice_padding_mm" in slicing_data:
            return max(0.0, float(slicing_data.get("slice_padding_mm") or 0))

        smaller_dimension = max(
            1.0, min(float(model_size.x), float(model_size.y)))
        return max(2.0, min(5.0, smaller_dimension * 0.12))

    def _display_pixels_for_model(self, model_size, slicing_data, padding=0.0):
        pixels_per_mm = float(slicing_data.get("slice_pixels_per_mm", 15) or 15)
        max_pixels = int(slicing_data.get("slice_max_pixels", 4096) or 4096)
        min_pixels = int(slicing_data.get("slice_min_pixels", 256) or 256)

        def scale(value):
            physical_mm = max(1.0, float(value))
            pixels = int(np.ceil(physical_mm * pixels_per_mm))
            return max(min_pixels, min(max_pixels, pixels))

        return (
            scale(float(model_size.y) + 2 * padding),
            scale(float(model_size.x) + 2 * padding))

    def _remove_slice_padding(self, region, padding, model_image_size):
        if region is None or region.is_empty or padding <= 0:
            return region

        shifted_region = translate(region, xoff=-padding, yoff=-padding)
        model_region = box(0, 0, model_image_size[0], model_image_size[1])
        clipped_region = shifted_region.intersection(model_region)
        if not clipped_region.is_empty:
            return clipped_region
        return shifted_region

    def _machine_bed_size(self, slicing_data):
        width = float(slicing_data.get("bed_width", 96) or 96)
        height = float(slicing_data.get("bed_height", width) or width)
        return width, height

    @staticmethod
    def _normalise_engraving_mode(value):
        mode = str(value or "vectoring").strip().lower()
        if mode not in {"vectoring", "engraving"}:
            raise ValueError(
                f"Unsupported Prusa scan mode '{value}'. "
                "Expected 'vectoring' or 'engraving'.")
        return mode

    @staticmethod
    def _engraving_hatch_spacing_for_layer(layer_index, slicing_data):
        first_count = max(
            0, int(slicing_data.get("first_layers_count", 0) or 0))
        intermediate_count = max(
            0, int(slicing_data.get("intermediate_layers_count", 0) or 0))
        intermediate_enabled = bool(
            slicing_data.get("intermediate_zone_enabled", False))
        default_spacing = float(slicing_data.get("scanline_spacing", 0.2) or 0.2)

        if (
            first_count > 0
            and layer_index < first_count
            and bool(slicing_data.get("first_zone_engraving_enabled", False))
        ):
            return max(
                1e-6,
                float(slicing_data.get(
                    "first_engraving_hatch_spacing",
                    default_spacing) or default_spacing))

        intermediate_start = first_count
        intermediate_end = first_count + intermediate_count
        if (
            intermediate_enabled
            and intermediate_count > 0
            and intermediate_start <= layer_index < intermediate_end
            and bool(slicing_data.get(
                "intermediate_zone_engraving_enabled", False))
        ):
            return max(
                1e-6,
                float(slicing_data.get(
                    "intermediate_engraving_hatch_spacing",
                    default_spacing) or default_spacing))

        return max(1e-6, default_spacing)

    @staticmethod
    def _validate_acceleration(value):
        acceleration = float(value)
        if not np.isfinite(acceleration) or acceleration <= 0:
            raise ValueError(
                "Engraving acceleration must be greater than 0 mm/s^2.")
        return acceleration

    @staticmethod
    def _validate_generated_gcode_file(gcode_file_path, slicing_data):
        """
        Reject malformed or unsafe laser-state commands before the file is
        returned to the interface.
        """
        word_pattern = re.compile(
            r"[A-Za-z][-+]?(?:\d+(?:\.\d*)?|\.\d+)")
        laser_enabled = False
        bed_width = float(slicing_data.get("bed_width", 96) or 96)
        bed_height = float(slicing_data.get("bed_height", bed_width) or bed_width)
        expected_origin_x = float(slicing_data.get("work_origin_x", 0) or 0)
        expected_origin_y = float(slicing_data.get("work_origin_y", 0) or 0)
        compact_visicut_style = bool(slicing_data.get("visicut_style", False))
        expected_origin_seen = False
        previous_command = ""

        with open(gcode_file_path, mode="r", errors="strict") as gcode_file:
            for line_number, raw_line in enumerate(gcode_file, 1):
                code = raw_line.split(";", 1)[0].strip()
                if not code:
                    continue

                compact = re.sub(r"\s+", "", code)
                words = word_pattern.findall(compact)
                if not words or "".join(words).upper() != compact.upper():
                    raise ValueError(
                        "Generated G-code contains a malformed word at "
                        f"line {line_number}: {code}")

                command = words[0].upper()
                if command == "M3":
                    laser_enabled = True
                    previous_command = command
                    continue
                if command == "M5":
                    laser_enabled = False
                    previous_command = command
                    continue

                values = {
                    word[0].upper(): float(word[1:])
                    for word in words[1:]
                }
                is_machine_xy_travel = (
                    command == "G53" and "X" in values and "Y" in values)
                is_origin_return = (
                    command in {"G1", "G01"}
                    and "X" in values
                    and "Y" in values
                    and values.get("S", 0) <= 0
                    and abs(values["X"] - expected_origin_x) <= 1e-6
                    and abs(values["Y"] - expected_origin_y) <= 1e-6
                )
                if (
                    (is_machine_xy_travel or is_origin_return)
                    and not compact_visicut_style
                ):
                    if "S" in values and values.get("S", 0) <= 0:
                        raise ValueError(
                            "Generated long travel depends on S0 instead of "
                            f"M5 at line {line_number}: {code}")
                    if previous_command != "M5":
                        raise ValueError(
                            "Generated long travel is not immediately "
                            f"guarded by M5 at line {line_number}: {code}")
                if command == "G92" and "X" in values and "Y" in values:
                    if (
                        abs(values["X"] - expected_origin_x) <= 1e-6
                        and abs(values["Y"] - expected_origin_y) <= 1e-6
                    ):
                        expected_origin_seen = True
                if command in {"G0", "G00"} and values.get("S", 0) > 0:
                    raise ValueError(
                        "Generated G-code attempted a powered rapid move at "
                        f"line {line_number}: {code}")
                if (
                    command in {"G1", "G01"}
                    and values.get("S", 0) > 0
                    and not laser_enabled
                ):
                    raise ValueError(
                        "Generated G-code attempted powered motion while the "
                        f"laser was disabled at line {line_number}: {code}")
                previous_command = command

        if not expected_origin_seen:
            raise ValueError(
                "Generated Prusa G-code does not establish the configured "
                f"work origin ({expected_origin_x:g}, "
                f"{expected_origin_y:g}).")

    @staticmethod
    def _normalise_prusa_travel_moves(gcode_file_path,
                                      compact_visicut_style=False):
        """Convert known laser-off origin returns from linear cuts to rapids."""
        if compact_visicut_style:
            return

        with open(gcode_file_path, mode="r", errors="strict") as gcode_file:
            lines = gcode_file.readlines()

        changed = False
        normalised_lines = []
        for line in lines:
            compact = re.sub(r"\s+", "", line.split(";", 1)[0]).upper()
            if re.fullmatch(r"G0?1X0(?:\.0+)?Y0(?:\.0+)?F\d+(?:\.\d+)?", compact):
                line = re.sub(r"^\s*G0?1", "G0", line, count=1, flags=re.I)
                changed = True
            normalised_lines.append(line)

        if changed:
            with open(gcode_file_path, mode="w", errors="strict") as gcode_file:
                gcode_file.writelines(normalised_lines)

    @staticmethod
    def _validate_region_on_bed(region, machine_bounds):
        if region is None or region.is_empty or not machine_bounds:
            return
        min_x, min_y, max_x, max_y = machine_bounds[:4]
        bed_shape = machine_bounds[4] if len(machine_bounds) > 4 else "rect"
        if bed_shape == "circle":
            center_x = (float(min_x) + float(max_x)) / 2.0
            center_y = (float(min_y) + float(max_y)) / 2.0
            radius = min(
                float(max_x) - float(min_x),
                float(max_y) - float(min_y)) / 2.0
            bed = Point(center_x, center_y).buffer(radius, resolution=128)
        else:
            bed = box(min_x, min_y, max_x, max_y)
        outside = region.difference(bed)
        if not outside.is_empty and outside.area > 1e-6:
            raise ValueError(
                "The sliced model extends outside the 96 mm circular bed.")

    def _center_region_on_bed(self, region, model_image_size, bed_width, bed_height):
        if region is None or region.is_empty:
            return region

        model_center_x = float(model_image_size[0]) / 2
        model_center_y = float(model_image_size[1]) / 2
        bed_center_x = float(bed_width) / 2
        bed_center_y = float(bed_height) / 2
        return translate(
            region,
            xoff=bed_center_x - model_center_x,
            yoff=bed_center_y - model_center_y)

    def _fill_enclosed_hollows(self, region):
        """
        Fill polygon interior rings for material-raster generation only.
        With this disabled, the original region preserves enclosed holes.
        """
        if region is None or region.is_empty:
            return region

        filled_polygons = []
        for polygon in self._extract_polygons(region):
            filled = Polygon(polygon.exterior)
            if not filled.is_valid:
                filled = filled.buffer(0)
            if not filled.is_empty and filled.area > 0:
                filled_polygons.append(filled)

        if not filled_polygons:
            return region
        return unary_union(filled_polygons)

    def _enclosed_hollow_region(self, region):
        """Return only enclosed polygon holes, excluding printable material."""
        if region is None or region.is_empty:
            return GeometryCollection()
        filled_region = self._fill_enclosed_hollows(region)
        hollow_region = filled_region.difference(region)
        if not hollow_region.is_valid:
            hollow_region = hollow_region.buffer(0)
        return hollow_region

    def _internal_floor_support_regions(self, layer_regions,
                                        minimum_area=0.1):
        """
        Detect material that first appears inside a previous layer's enclosed
        hollow, then project only that footprint through connected hollow space
        below it.

        The detected floor/flange layer remains ordinary model material.
        Generated support stops one layer below it and terminates wherever the
        downward projection meets existing material or an open cavity.
        """
        region_count = len(layer_regions or [])
        support_regions = [
            GeometryCollection() for _ in range(region_count)
        ]
        support_source_layers = [set() for _ in range(region_count)]
        if region_count < 2:
            return support_regions, support_source_layers

        cleaned_regions = []
        hollow_regions = []
        for region in layer_regions:
            cleaned = region
            if cleaned is None or cleaned.is_empty:
                cleaned = GeometryCollection()
            elif not cleaned.is_valid:
                cleaned = cleaned.buffer(0)
            cleaned_regions.append(cleaned)
            hollow_regions.append(self._enclosed_hollow_region(cleaned))

        area_threshold = max(0.0, float(minimum_area or 0.0))
        for floor_index in range(1, region_count):
            previous_hollow = hollow_regions[floor_index - 1]
            current_region = cleaned_regions[floor_index]
            if previous_hollow.is_empty or current_region.is_empty:
                continue

            newly_spanned = current_region.intersection(previous_hollow)
            if not newly_spanned.is_valid:
                newly_spanned = newly_spanned.buffer(0)
            floor_parts = [
                polygon
                for polygon in self._extract_polygons(newly_spanned)
                if not polygon.is_empty
                and polygon.area + 1e-9 >= area_threshold
            ]
            if not floor_parts:
                continue

            active_projection = unary_union(floor_parts)
            for support_index in range(floor_index - 1, -1, -1):
                hollow = hollow_regions[support_index]
                if hollow.is_empty or active_projection.is_empty:
                    break
                active_projection = active_projection.intersection(hollow)
                if not active_projection.is_valid:
                    active_projection = active_projection.buffer(0)
                if active_projection.is_empty:
                    break
                projected_parts = [
                    polygon
                    for polygon in self._extract_polygons(active_projection)
                    if not polygon.is_empty
                    and polygon.area + 1e-9 >= area_threshold
                ]
                if not projected_parts:
                    break
                active_projection = unary_union(projected_parts)
                existing_support = support_regions[support_index]
                support_regions[support_index] = (
                    active_projection
                    if existing_support.is_empty
                    else unary_union(
                        [existing_support, active_projection]))
                if not support_regions[support_index].is_valid:
                    support_regions[support_index] = (
                        support_regions[support_index].buffer(0))
                support_source_layers[support_index].add(floor_index)

        return support_regions, support_source_layers

    def _roof_hollow_infill_region(self, region, overlap_distance=0.0):
        """
        Fill enclosed hollow area with support infill for later roof skin.

        The toggle is intentionally explicit: when enabled, enclosed polygon
        holes in the current slice receive infill instead of being left open.
        """
        hollow_region = self._enclosed_hollow_region(region)
        if hollow_region is None or hollow_region.is_empty:
            return GeometryCollection()

        fill_region = hollow_region
        if abs(float(overlap_distance)) > 1e-9:
            fill_region = fill_region.buffer(
                float(overlap_distance), join_style=2, mitre_limit=5)
        if not fill_region.is_valid:
            fill_region = fill_region.buffer(0)
        return fill_region

    def _engraving_shell_and_core_regions(self, region, shell_thickness):
        """
        Split printable material into a dense rastered perimeter shell and an
        inset core that may receive grid infill.

        Buffering inward applies to exterior and interior boundaries, so the
        result works for solid sections, rings, and complex open interiors
        without relying on polygon-hole detection.
        """
        if region is None or region.is_empty:
            return GeometryCollection(), GeometryCollection()

        thickness = max(float(shell_thickness), 1e-6)
        core_region = region.buffer(
            -thickness, join_style=2, mitre_limit=5)
        if not core_region.is_valid:
            core_region = core_region.buffer(0)
        if core_region.is_empty:
            return region, GeometryCollection()

        core_region = core_region.intersection(region)
        shell_region = region.difference(core_region)
        if not shell_region.is_valid:
            shell_region = shell_region.buffer(0)
        return shell_region, core_region

    def _new_surface_downskin_region(
            self, region, previous_region, support_margin):
        """Detect current material that is unsupported by the layer below."""
        if (
            region is None
            or region.is_empty
            or previous_region is None
            or previous_region.is_empty
        ):
            return GeometryCollection()

        supported_below = previous_region.buffer(
            float(support_margin), join_style=2, mitre_limit=5)
        unsupported_material = region.difference(supported_below)
        unsupported_material = unsupported_material.intersection(region)
        return self._clean_surface_skin_region(
            unsupported_material, support_margin)

    def _new_surface_upskin_region(
            self, region, next_region, support_margin):
        """Detect current material exposed because the next layer is smaller."""
        if region is None or region.is_empty:
            return GeometryCollection()
        if next_region is None or next_region.is_empty:
            return GeometryCollection()

        region = self._clean_geometry_for_surface_skin(region)
        next_region = self._clean_geometry_for_surface_skin(next_region)
        if region.is_empty or next_region.is_empty:
            return GeometryCollection()

        exposure_margin = max(
            0.01, min(0.05, float(support_margin) * 0.25))
        try:
            covered_above = next_region.buffer(
                exposure_margin, join_style=2, mitre_limit=5)
            covered_above = self._clean_geometry_for_surface_skin(
                covered_above)
            exposed_material = region.difference(covered_above)
            exposed_material = exposed_material.intersection(region)
        except Exception:
            return GeometryCollection()
        return self._clean_surface_skin_region(
            exposed_material, support_margin)

    def _surface_skin_region_for_layer(
            self, region, previous_region, next_region, active_regions,
            support_margin):
        regions = [
            active_region.intersection(region)
            for active_region, remaining in active_regions
            if remaining > 0
            and active_region is not None
            and not active_region.is_empty
        ]

        upskin_region = self._new_surface_upskin_region(
            region, next_region, support_margin)
        if not upskin_region.is_empty:
            regions.append(upskin_region)

        if not regions:
            return GeometryCollection()
        return self._clean_surface_skin_region(
            unary_union(regions), support_margin)

    def _advance_surface_skin_regions(
            self, current_region, previous_region, active_regions,
            next_region, support_margin, surface_skin_layers):
        next_regions = []
        for active_region, remaining in active_regions:
            remaining_after_current = int(remaining) - 1
            if remaining_after_current <= 0:
                continue
            clipped = active_region.intersection(current_region)
            clipped = self._clean_surface_skin_region(
                clipped, support_margin)
            if not clipped.is_empty:
                next_regions.append((clipped, remaining_after_current))

        new_regions = self._new_surface_upskin_region(
            current_region, next_region, support_margin)
        new_regions = self._clean_surface_skin_region(
            new_regions, support_margin)
        remaining_after_current = int(surface_skin_layers) - 1
        if not new_regions.is_empty and remaining_after_current > 0:
            next_regions.append((new_regions, remaining_after_current))
        return next_regions

    def _new_roof_skin_region(self, region, previous_region, support_margin):
        return self._new_surface_downskin_region(
            region, previous_region, support_margin)

    def _clean_surface_skin_region(self, geometry, support_margin):
        if geometry is None or geometry.is_empty:
            return GeometryCollection()
        geometry = self._clean_geometry_for_surface_skin(geometry)
        if geometry.is_empty:
            return GeometryCollection()
        min_area = max(
            3.0, float(support_margin) * float(support_margin) * 20.0)
        polygons = [
            polygon
            for polygon in self._extract_polygons(geometry)
            if polygon.area >= min_area
        ]
        return unary_union(polygons) if polygons else GeometryCollection()

    @staticmethod
    def _clean_geometry_for_surface_skin(geometry):
        if geometry is None or geometry.is_empty:
            return GeometryCollection()
        try:
            cleaned = geometry.buffer(0)
        except Exception:
            return GeometryCollection()
        if cleaned is None or cleaned.is_empty:
            return GeometryCollection()
        return cleaned

    def _interior_surface_skin_region(
            self, skin_region, layer_region, hatch_spacing, shell_clearance):
        if (
            skin_region is None
            or skin_region.is_empty
            or layer_region is None
            or layer_region.is_empty
        ):
            return GeometryCollection()

        pitch = max(float(hatch_spacing), 1e-6)
        clearance = max(float(shell_clearance or 0.0), pitch, 0.2)
        layer_region = self._clean_geometry_for_surface_skin(layer_region)
        if layer_region.is_empty:
            return GeometryCollection()
        try:
            interior_region = layer_region.buffer(
                -clearance, join_style=2, mitre_limit=5)
        except Exception:
            return GeometryCollection()
        interior_region = self._clean_geometry_for_surface_skin(
            interior_region)
        if interior_region.is_empty:
            return GeometryCollection()

        skin_region = self._clean_geometry_for_surface_skin(skin_region)
        if skin_region.is_empty:
            return GeometryCollection()
        try:
            skin_region = skin_region.intersection(interior_region)
        except Exception:
            return GeometryCollection()
        skin_region = self._clean_geometry_for_surface_skin(skin_region)
        if skin_region.is_empty:
            return GeometryCollection()

        polygons = [
            polygon
            for polygon in self._extract_polygons(skin_region)
            if self._is_slanted_surface_skin_region(polygon, pitch)
        ]
        return unary_union(polygons) if polygons else GeometryCollection()

    def _surface_skin_interior_region(
            self, skin_region, layer_region, edge_clearance):
        """Keep roof skin away from existing vector contour/wall edges."""
        if (
            skin_region is None
            or skin_region.is_empty
            or layer_region is None
            or layer_region.is_empty
        ):
            return GeometryCollection()

        clearance = max(0.0, float(edge_clearance or 0.0))
        if clearance <= 1e-9:
            return self._clean_geometry_for_surface_skin(skin_region)

        layer_region = self._clean_geometry_for_surface_skin(layer_region)
        if layer_region.is_empty:
            return GeometryCollection()
        try:
            interior_region = layer_region.buffer(
                -clearance, join_style=2, mitre_limit=5)
        except Exception:
            return GeometryCollection()
        interior_region = self._clean_geometry_for_surface_skin(
            interior_region)
        if interior_region.is_empty:
            return GeometryCollection()

        skin_region = self._clean_geometry_for_surface_skin(skin_region)
        if skin_region.is_empty:
            return GeometryCollection()
        try:
            skin_region = skin_region.intersection(interior_region)
        except Exception:
            return GeometryCollection()
        return self._clean_geometry_for_surface_skin(skin_region)

    def _has_carried_surface_skin_on_layer(
            self, layer_region, active_regions, support_margin):
        if (
            layer_region is None
            or layer_region.is_empty
            or not active_regions
        ):
            return False
        for active_region, remaining in active_regions:
            if int(remaining) <= 0:
                continue
            if active_region is None or active_region.is_empty:
                continue
            try:
                clipped_region = active_region.intersection(layer_region)
            except Exception:
                continue
            clipped_region = self._clean_surface_skin_region(
                clipped_region, support_margin)
            if not clipped_region.is_empty and clipped_region.area > 1e-6:
                return True
        return False

    def _current_surface_skin_exclusion_region(
            self, layer_regions, layer_index, flat_skin_layers,
            slanted_skin_layers, flat_hatch_spacing, slanted_hatch_spacing,
            slanted_enabled, shell_clearance, support_margin,
            flat_active_regions=None, slanted_active_regions=None):
        """Roof area on this layer that must be reserved for skin only."""
        if layer_index < 0 or layer_index >= len(layer_regions):
            return GeometryCollection()

        region = layer_regions[layer_index]
        if region is None or region.is_empty:
            return GeometryCollection()

        regions = []
        for active_region, remaining in (
                (flat_active_regions or []) + (slanted_active_regions or [])):
            if int(remaining) <= 0:
                continue
            if active_region is None or active_region.is_empty:
                continue
            try:
                clipped_region = active_region.intersection(region)
            except Exception:
                continue
            clipped_region = self._clean_surface_skin_region(
                clipped_region,
                support_margin)
            if not clipped_region.is_empty:
                regions.append(clipped_region)

        flat_skin_region = self._flat_roof_skin_region(
            layer_regions,
            layer_index,
            flat_skin_layers,
            flat_hatch_spacing)
        flat_skin_region = self._surface_skin_interior_region(
            flat_skin_region,
            region,
            shell_clearance)
        flat_skin_region = self._clean_surface_skin_region(
            flat_skin_region,
            support_margin)
        flat_skin_region = self._flat_surface_skin_region(
            flat_skin_region,
            flat_hatch_spacing)
        flat_skin_region = self._clean_surface_skin_region(
            flat_skin_region,
            support_margin)
        if flat_skin_region is not None and not flat_skin_region.is_empty:
            regions.append(flat_skin_region)

        if slanted_enabled:
            slanted_skin_region = self._slanted_surface_skin_candidate(
                layer_regions,
                layer_index,
                slanted_skin_layers,
                slanted_hatch_spacing,
                shell_clearance,
                support_margin)
            if (
                slanted_skin_region is not None
                and not slanted_skin_region.is_empty
            ):
                regions.append(slanted_skin_region)

        if not regions:
            return GeometryCollection()

        try:
            exclusion_region = unary_union(regions)
            exclusion_region = exclusion_region.intersection(region)
        except Exception:
            return GeometryCollection()
        return self._clean_surface_skin_region(
            exclusion_region,
            support_margin)

    def _has_current_surface_skin_candidate(
            self, layer_regions, layer_index, flat_skin_layers,
            slanted_skin_layers, flat_hatch_spacing, slanted_hatch_spacing,
            slanted_enabled, shell_clearance, support_margin):
        self._prepare_surface_skin_caches(layer_regions)
        cache_key = (
            int(layer_index), int(flat_skin_layers), int(slanted_skin_layers),
            float(flat_hatch_spacing),
            float(slanted_hatch_spacing), bool(slanted_enabled),
            float(shell_clearance), float(support_margin))
        cached = self._current_surface_skin_candidate_cache.get(cache_key)
        if cached is not None:
            return cached

        if layer_index < 0 or layer_index >= len(layer_regions):
            self._current_surface_skin_candidate_cache[cache_key] = False
            return False

        region = layer_regions[layer_index]
        if region is None or region.is_empty:
            self._current_surface_skin_candidate_cache[cache_key] = False
            return False

        flat_skin_region = self._flat_roof_skin_region(
            layer_regions, layer_index, flat_skin_layers, flat_hatch_spacing)
        flat_skin_region = self._surface_skin_interior_region(
            flat_skin_region, region, shell_clearance)
        flat_skin_region = self._clean_surface_skin_region(
            flat_skin_region, support_margin)
        if (
            flat_skin_region is not None
            and not flat_skin_region.is_empty
            and flat_skin_region.area > 1e-6
        ):
            self._current_surface_skin_candidate_cache[cache_key] = True
            return True

        if not slanted_enabled:
            self._current_surface_skin_candidate_cache[cache_key] = False
            return False

        slanted_comparison_index = layer_index + int(slanted_skin_layers)
        slanted_comparison_region = (
            layer_regions[slanted_comparison_index]
            if slanted_comparison_index < len(layer_regions)
            else None)
        slanted_skin_region = self._new_surface_upskin_region(
            region, slanted_comparison_region, support_margin)
        slanted_skin_region = self._interior_surface_skin_region(
            slanted_skin_region,
            region,
            slanted_hatch_spacing,
            shell_clearance)
        slanted_skin_region = self._clean_surface_skin_region(
            slanted_skin_region, support_margin)
        if (
            slanted_skin_region is None
            or slanted_skin_region.is_empty
            or slanted_skin_region.area <= 1e-6
        ):
            self._current_surface_skin_candidate_cache[cache_key] = False
            return False

        result = (
            self._slanted_surface_skin_run_length(
                layer_regions=layer_regions,
                layer_index=layer_index,
                skin_layers=slanted_skin_layers,
                hatch_spacing=slanted_hatch_spacing,
                shell_clearance=shell_clearance,
                support_margin=support_margin,
                stop_after=4)
            >= 4
        )
        self._current_surface_skin_candidate_cache[cache_key] = result
        return result

    def _slanted_surface_skin_candidate(
            self, layer_regions, layer_index, skin_layers, hatch_spacing,
            shell_clearance, support_margin):
        self._prepare_surface_skin_caches(layer_regions)
        cache_key = (
            int(layer_index), int(skin_layers), float(hatch_spacing),
            float(shell_clearance), float(support_margin))
        if cache_key in self._slanted_surface_skin_candidate_cache:
            return self._slanted_surface_skin_candidate_cache[cache_key]

        if layer_index < 0 or layer_index >= len(layer_regions):
            result = GeometryCollection()
            self._slanted_surface_skin_candidate_cache[cache_key] = result
            return result

        comparison_index = layer_index + int(skin_layers)
        if comparison_index >= len(layer_regions):
            result = GeometryCollection()
            self._slanted_surface_skin_candidate_cache[cache_key] = result
            return result

        region = layer_regions[layer_index]
        comparison_region = layer_regions[comparison_index]
        candidate = self._new_surface_upskin_region(
            region, comparison_region, support_margin)
        candidate = self._interior_surface_skin_region(
            candidate, region, hatch_spacing, shell_clearance)
        result = self._clean_surface_skin_region(candidate, support_margin)
        self._slanted_surface_skin_candidate_cache[cache_key] = result
        return result

    def _has_slanted_surface_skin_candidate(
            self, layer_regions, layer_index, skin_layers, hatch_spacing,
            shell_clearance, support_margin):
        candidate = self._slanted_surface_skin_candidate(
            layer_regions,
            layer_index,
            skin_layers,
            hatch_spacing,
            shell_clearance,
            support_margin)
        return (
            candidate is not None
            and not candidate.is_empty
            and candidate.area > 1e-6
        )

    def _slanted_surface_skin_run_length(
            self, layer_regions, layer_index, skin_layers, hatch_spacing,
            shell_clearance, support_margin, stop_after):
        if not self._has_slanted_surface_skin_candidate(
                layer_regions, layer_index, skin_layers, hatch_spacing,
                shell_clearance, support_margin):
            return 0

        count = 1
        previous_index = layer_index - 1
        while (
            count <= stop_after
            and self._has_slanted_surface_skin_candidate(
                layer_regions,
                previous_index,
                skin_layers,
                hatch_spacing,
                shell_clearance,
                support_margin)
        ):
            count += 1
            previous_index -= 1

        next_index = layer_index + 1
        while (
            count <= stop_after
            and self._has_slanted_surface_skin_candidate(
                layer_regions,
                next_index,
                skin_layers,
                hatch_spacing,
                shell_clearance,
                support_margin)
        ):
            count += 1
            next_index += 1

        return count

    def _surface_skin_region(self, layer_regions, layer_index, skin_layers):
        """Return material within the configured top-skin depth.

        A top skin depth of N should mark the visible roof layer and the
        N - 1 layers below it. Comparing only with the layer N steps above
        catches those lower skin layers even when the immediately-above layer
        has the same footprint.
        """
        if layer_index >= len(layer_regions):
            return GeometryCollection()

        region = layer_regions[layer_index]
        if region is None or region.is_empty:
            return GeometryCollection()

        comparison_index = layer_index + int(skin_layers)
        if comparison_index >= len(layer_regions):
            return region

        covered_above = layer_regions[comparison_index]
        if covered_above is None or covered_above.is_empty:
            return region

        skin_region = region.difference(covered_above)
        if not skin_region.is_valid:
            skin_region = skin_region.buffer(0)
        return skin_region

    def _slanted_surface_skin_region(
            self, layer_regions, layer_index, skin_layers, hatch_spacing,
            shell_clearance):
        """Return broad interior slanted roof areas only.

        Generic top-skin detection catches every surface that disappears
        within the lookahead depth, including exterior hull sides, tiny wall
        caps, and final chimney caps. Slanted roof skin is intended for
        supported interior roof/deck slopes, so keep only candidate material
        that remains after an inward shell clearance.
        """
        if layer_index < 0 or layer_index >= len(layer_regions):
            return GeometryCollection()

        comparison_index = layer_index + int(skin_layers)
        if comparison_index >= len(layer_regions):
            return GeometryCollection()

        region = layer_regions[layer_index]
        covered_above = layer_regions[comparison_index]
        if (
            region is None
            or region.is_empty
            or covered_above is None
            or covered_above.is_empty
        ):
            return GeometryCollection()

        skin_region = region.difference(covered_above)
        if not skin_region.is_valid:
            skin_region = skin_region.buffer(0)
        if skin_region.is_empty:
            return GeometryCollection()

        pitch = max(float(hatch_spacing), 1e-6)
        clearance = max(float(shell_clearance or 0.0), pitch * 4.0, 0.8)
        interior_region = region.buffer(
            -clearance, join_style=2, mitre_limit=5)
        if not interior_region.is_valid:
            interior_region = interior_region.buffer(0)
        if interior_region.is_empty:
            return GeometryCollection()

        skin_region = skin_region.intersection(interior_region)
        if not skin_region.is_valid:
            skin_region = skin_region.buffer(0)
        if skin_region.is_empty:
            return GeometryCollection()

        slanted_polygons = [
            polygon
            for polygon in self._extract_polygons(skin_region)
            if self._is_slanted_surface_skin_region(polygon, pitch)
        ]
        return unary_union(slanted_polygons) if slanted_polygons else GeometryCollection()

    def _is_before_flat_roof_skin(
            self, layer_regions, layer_index, lookahead_layers,
            skin_layers, hatch_spacing):
        """True when a flat roof skin region appears soon above this layer."""
        maximum = min(
            len(layer_regions),
            layer_index + int(lookahead_layers) + 1)
        for future_index in range(layer_index + 1, maximum):
            flat_region = self._flat_roof_skin_region(
                layer_regions, future_index, skin_layers, hatch_spacing)
            if (
                flat_region is not None
                and not flat_region.is_empty
                and flat_region.area > 1e-6
            ):
                return True
        return False

    def _is_before_roof_skin(
            self, layer_regions, layer_index, lookahead_layers,
            flat_skin_layers, slanted_skin_layers, flat_hatch_spacing,
            slanted_hatch_spacing, slanted_enabled, shell_clearance,
            support_margin):
        """True when either flat or slanted roof skin appears soon above."""
        maximum = min(
            len(layer_regions),
            layer_index + int(lookahead_layers) + 1)
        for future_index in range(layer_index + 1, maximum):
            if self._has_current_surface_skin_candidate(
                    layer_regions=layer_regions,
                    layer_index=future_index,
                    flat_skin_layers=flat_skin_layers,
                    slanted_skin_layers=slanted_skin_layers,
                    flat_hatch_spacing=flat_hatch_spacing,
                    slanted_hatch_spacing=slanted_hatch_spacing,
                    slanted_enabled=slanted_enabled,
                    shell_clearance=shell_clearance,
                    support_margin=support_margin):
                return True
        return False

    def _roof_skin_support_region(
            self, layer_regions, layer_index, lookahead_layers,
            flat_skin_layers, slanted_skin_layers, flat_hatch_spacing,
            slanted_hatch_spacing, slanted_enabled, shell_clearance,
            support_margin, projection_margin=0.0):
        """Projected footprint of roof skin that appears soon above a layer."""
        if layer_index < 0 or layer_index >= len(layer_regions):
            return GeometryCollection()

        current_region = layer_regions[layer_index]
        if current_region is None or current_region.is_empty:
            return GeometryCollection()

        regions = []
        maximum = min(
            len(layer_regions),
            layer_index + int(lookahead_layers) + 1)
        for future_index in range(layer_index + 1, maximum):
            future_region = layer_regions[future_index]
            if future_region is None or future_region.is_empty:
                continue

            flat_region = self._flat_roof_skin_region(
                layer_regions,
                future_index,
                flat_skin_layers,
                flat_hatch_spacing)
            flat_region = self._surface_skin_interior_region(
                flat_region,
                future_region,
                shell_clearance)
            flat_region = self._clean_surface_skin_region(
                flat_region,
                support_margin)
            if flat_region is not None and not flat_region.is_empty:
                regions.append(flat_region)

            if slanted_enabled:
                slanted_region = self._slanted_surface_skin_candidate(
                    layer_regions,
                    future_index,
                    slanted_skin_layers,
                    slanted_hatch_spacing,
                    shell_clearance,
                    support_margin)
                if slanted_region is not None and not slanted_region.is_empty:
                    regions.append(slanted_region)

        if not regions:
            return GeometryCollection()

        try:
            support_region = unary_union(regions)
            projection_margin = max(0.0, float(projection_margin or 0.0))
            if projection_margin > 0:
                support_region = support_region.buffer(
                    projection_margin,
                    join_style=2,
                    mitre_limit=5)
            support_region = support_region.intersection(current_region)
        except Exception:
            return GeometryCollection()
        return self._clean_surface_skin_region(
            support_region,
            support_margin)

    def _generate_surface_skin_gcode(
            self, region, infill_settings, angle, hatch_spacing,
            reverse_scan=False, optimize_scan_order=True,
            engraving_acceleration=500, machine_bounds=None,
            alternate_lines=False, alternate_mode="two-pass"):
        if region is None or region.is_empty:
            return ""

        pitch = max(float(hatch_spacing), 1e-6)
        hatch_lines = self._angled_hatch_lines(
            region, angle, pitch, 1, pitch,
            require_complete_bundle=False)
        hatch_lines = self._order_hatch_lines(
            hatch_lines, reverse_scan, optimize_scan_order)
        commands = []
        if alternate_lines and alternate_mode == "edge-interleave":
            commands.append(";SURFACE SKIN EDGE INTERLEAVE ORDER")
            self._append_hatch_commands(
                commands, self._edge_interleave_order(hatch_lines),
                infill_settings, "vectoring",
                engraving_acceleration, machine_bounds)
        elif alternate_lines:
            first_pass = hatch_lines[::2]
            second_pass = hatch_lines[1::2]
            if first_pass:
                commands.append(";SURFACE SKIN ALTERNATE PASS 1")
                self._append_hatch_commands(
                    commands, first_pass, infill_settings, "vectoring",
                    engraving_acceleration, machine_bounds)
            if second_pass:
                commands.append(";SURFACE SKIN ALTERNATE PASS 2")
                self._append_hatch_commands(
                    commands, second_pass, infill_settings, "vectoring",
                    engraving_acceleration, machine_bounds)
        else:
            self._append_hatch_commands(
                commands, hatch_lines, infill_settings, "vectoring",
                engraving_acceleration, machine_bounds)
        return "\n".join(commands)

    def _flat_surface_skin_region(self, region, hatch_spacing):
        """Return broad surface-skin patches treated as flat roofs/decks."""
        if region is None or region.is_empty:
            return GeometryCollection()

        flat_polygons = [
            polygon
            for polygon in self._extract_polygons(region)
            if self._is_flat_surface_skin_region(polygon, hatch_spacing)
        ]
        return unary_union(flat_polygons) if flat_polygons else GeometryCollection()

    def _flat_roof_skin_region(
            self, layer_regions, layer_index, skin_layers, hatch_spacing):
        """Return stable horizontal cap/deck regions for flat roof skin.

        This intentionally differs from generic top-skin detection. A Benchy
        hull side can disappear over the next N layers and look like top skin,
        but it is not a flat cap. For flat roofs/decks we require the footprint
        to remain present through the skin stack, then disappear above it.
        """
        self._prepare_surface_skin_caches(layer_regions)
        cache_key = (
            int(layer_index), int(skin_layers), float(hatch_spacing))
        if cache_key in self._flat_roof_skin_region_cache:
            return self._flat_roof_skin_region_cache[cache_key]

        if layer_index < 0 or layer_index >= len(layer_regions):
            result = GeometryCollection()
            self._flat_roof_skin_region_cache[cache_key] = result
            return result

        stable_region = layer_regions[layer_index]
        if stable_region is None or stable_region.is_empty:
            result = GeometryCollection()
            self._flat_roof_skin_region_cache[cache_key] = result
            return result

        layers_to_intersect = max(1, int(skin_layers))
        for offset in range(1, layers_to_intersect):
            next_index = layer_index + offset
            if next_index >= len(layer_regions):
                break
            next_region = layer_regions[next_index]
            if next_region is None or next_region.is_empty:
                stable_region = GeometryCollection()
                break
            stable_region = stable_region.intersection(next_region)
            if not stable_region.is_valid:
                stable_region = stable_region.buffer(0)
            if stable_region.is_empty:
                break

        if stable_region is None or stable_region.is_empty:
            result = GeometryCollection()
            self._flat_roof_skin_region_cache[cache_key] = result
            return result

        comparison_index = layer_index + layers_to_intersect
        if comparison_index >= len(layer_regions):
            # A simple box/cap can end exactly at the top of the model, so
            # there is no layer above the configured skin stack to compare
            # against. In that case the stable stack itself is the visible
            # flat roof.
            result = self._flat_surface_skin_region(
                stable_region, hatch_spacing)
            self._flat_roof_skin_region_cache[cache_key] = result
            return result

        covered_above = layer_regions[comparison_index]
        if covered_above is not None and not covered_above.is_empty:
            stable_region = stable_region.difference(covered_above)
            if not stable_region.is_valid:
                stable_region = stable_region.buffer(0)

        result = self._flat_surface_skin_region(
            stable_region, hatch_spacing)
        self._flat_roof_skin_region_cache[cache_key] = result
        return result

    def _has_flat_surface_skin_candidate(
            self, layer_regions, layer_index, skin_layers, hatch_spacing):
        if layer_index < 0 or layer_index >= len(layer_regions):
            return False
        flat_region = self._flat_roof_skin_region(
            layer_regions, layer_index, skin_layers, hatch_spacing)
        return (
            flat_region is not None
            and not flat_region.is_empty
            and flat_region.area > 1e-6
        )

    def _flat_surface_skin_run_length(
            self, layer_regions, layer_index, skin_layers, hatch_spacing,
            stop_after):
        """Count adjacent flat-skin candidate layers around layer_index.

        True flat roof/deck bands usually last roughly the configured skin
        depth. Long continuous runs are usually curved/sloped Benchy surfaces
        being mistaken for flat roof skin.
        """
        if not self._has_flat_surface_skin_candidate(
                layer_regions, layer_index, skin_layers, hatch_spacing):
            return 0

        count = 1
        previous_index = layer_index - 1
        while (
            count <= stop_after
            and self._has_flat_surface_skin_candidate(
                layer_regions, previous_index, skin_layers, hatch_spacing)
        ):
            count += 1
            previous_index -= 1

        next_index = layer_index + 1
        while (
            count <= stop_after
            and self._has_flat_surface_skin_candidate(
                layer_regions, next_index, skin_layers, hatch_spacing)
        ):
            count += 1
            next_index += 1

        return count

    def _generate_surface_skin_spiral_gcode(
            self, region, infill_settings, spacing,
            direction="outside-in", alternate_lines=False,
            alternate_mode="two-pass"):
        if region is None or region.is_empty:
            return ""

        contour_lines = self._surface_skin_spiral_lines(region, spacing)
        if str(direction).lower() == "inside-out":
            contour_lines = list(reversed(contour_lines))

        commands = [";SURFACE SKIN FLAT ROOF SPIRAL VECTOR START"]
        if alternate_lines and alternate_mode == "edge-interleave":
            passes = [("EDGE INTERLEAVE", self._edge_interleave_order(contour_lines))]
        elif alternate_lines:
            passes = [
                ("1", contour_lines[::2]),
                ("2", contour_lines[1::2]),
            ]
        else:
            passes = [(None, contour_lines)]

        for pass_label, pass_lines in passes:
            if not pass_lines:
                continue
            if pass_label is not None:
                commands.append(
                    f";SURFACE SKIN SPIRAL ALTERNATE PASS {pass_label}")
            for coords in pass_lines:
                self._append_vector_polyline_commands(
                    commands, coords, infill_settings)
        commands.append(";SURFACE SKIN FLAT ROOF SPIRAL VECTOR END")
        return "\n".join(commands)

    def _surface_skin_spiral_lines(self, region, spacing):
        pitch = max(float(spacing), 1e-6)
        if region is None or region.is_empty:
            return []

        min_x, min_y, max_x, max_y = region.bounds
        max_offsets = max(1, int(np.ceil(
            max(max_x - min_x, max_y - min_y) / (2.0 * pitch))) + 2)
        lines = []
        for index in range(max_offsets):
            offset_region = region.buffer(
                -index * pitch, join_style=2, mitre_limit=5)
            if offset_region is None or offset_region.is_empty:
                break
            for polygon in self._extract_polygons(offset_region):
                coords = list(polygon.exterior.coords)
                if len(coords) > 2:
                    lines.append(coords)
                for interior in polygon.interiors:
                    coords = list(interior.coords)
                    if len(coords) > 2:
                        lines.append(coords)
        return lines

    @staticmethod
    def _edge_interleave_order(items):
        ordered = []
        left = 0
        right = len(items) - 1
        while left <= right:
            ordered.append(items[left])
            if left != right:
                ordered.append(items[right])
            left += 1
            right -= 1
        return ordered

    def _append_vector_polyline_commands(self, commands, coords, settings):
        if len(coords) < 2:
            return

        start = coords[0]
        commands.append("M5")
        commands.append(f"G0X{start[0]:.4f}Y{start[1]:.4f}S0")
        first_powered_move = True
        for point in coords[1:]:
            if first_powered_move:
                commands.append("M3")
                commands.append(
                    f"G1X{point[0]:.4f}Y{point[1]:.4f}"
                    f"S{int(settings['power'])}F{int(settings['speed'])}")
                first_powered_move = False
                continue
            commands.append(f"G1X{point[0]:.4f}Y{point[1]:.4f}")
        commands.append("S0")
        commands.append("M5")

    def _is_flat_surface_skin_region(self, region, hatch_spacing):
        """Conservatively identify broad deck/flat-roof skin patches."""
        if region is None or region.is_empty:
            return False

        min_x, min_y, max_x, max_y = region.bounds
        width = float(max_x - min_x)
        height = float(max_y - min_y)
        pitch = max(float(hatch_spacing), 1e-6)
        minimum_span = max(4.0, pitch * 12.0)
        minimum_area = max(8.0, pitch * pitch * 80.0)

        # A rim around an open hole is a wall/contour top, not a roof cap.
        # Benchy has this behind the cabin: the lower deck cover should get
        # skin, but the later rectangular rim around the hollow should not.
        hole_area = 0.0
        for interior in getattr(region, "interiors", []):
            try:
                hole_area += abs(Polygon(interior).area)
            except Exception:
                continue
        if (
            float(region.area) < 150.0
            and hole_area > max(pitch * pitch * 4.0, float(region.area) * 0.15)
        ):
            return False

        return (
            width >= minimum_span
            and height >= minimum_span
            and float(region.area) >= minimum_area
        )

    def _is_slanted_surface_skin_region(self, region, hatch_spacing):
        """Reject tiny wall/rim caps and keep usable slanted roof strips."""
        if region is None or region.is_empty:
            return False

        min_x, min_y, max_x, max_y = region.bounds
        width = float(max_x - min_x)
        height = float(max_y - min_y)
        short_span = min(width, height)
        long_span = max(width, height)
        pitch = max(float(hatch_spacing), 1e-6)
        minimum_short_span = max(0.45, pitch * 2.0)
        minimum_long_span = max(8.0, pitch * 20.0)
        minimum_area = max(6.0, pitch * pitch * 120.0)
        bounding_area = max(width * height, 1e-9)

        hole_area = 0.0
        for interior in getattr(region, "interiors", []):
            try:
                hole_area += abs(Polygon(interior).area)
            except Exception:
                continue
        if hole_area > max(pitch * pitch * 4.0, float(region.area) * 0.15):
            return False
        if float(region.area) / bounding_area < 0.12:
            return False

        return (
            short_span >= minimum_short_span
            and long_span >= minimum_long_span
            and float(region.area) >= minimum_area
        )

    def _surface_skin_engraving_regions(
            self, region,
            shell_thickness=None, inner_grid_spacing=None,
            inner_grid_line_thickness=None, contour_overlap=0.0,
            crossing_clearance=0.0, inner_grid_angle=0.0):
        if region is None or region.is_empty:
            return GeometryCollection(), GeometryCollection()

        shell_region = region
        grid_region = GeometryCollection()
        if shell_thickness is None or inner_grid_spacing is None:
            return shell_region, grid_region

        shell_region, core_region = self._engraving_shell_and_core_regions(
            region, shell_thickness)
        if core_region is None or core_region.is_empty:
            return shell_region, grid_region

        overlap_distance = (
            float(contour_overlap)
            * max(float(inner_grid_line_thickness or 0.0), 1e-6)
        )
        if abs(overlap_distance) > 1e-9:
            core_region = core_region.buffer(
                overlap_distance,
                join_style=2,
                mitre_limit=5)
            core_region = core_region.intersection(region)
            if not core_region.is_valid:
                core_region = core_region.buffer(0)
        grid_region = self._engraving_grid_band_region(
            region=core_region,
            angle=float(inner_grid_angle or 0.0),
            grid_spacing=float(inner_grid_spacing),
            line_thickness=max(
                float(inner_grid_line_thickness or inner_grid_spacing),
                1e-6),
            crossing_clearance=max(float(crossing_clearance), 0.0))
        return shell_region, grid_region

    def _generate_surface_skin_engraving_gcode(
            self, region, infill_settings, scanline_spacing,
            shell_thickness=None, inner_grid_spacing=None,
            inner_grid_line_thickness=None, contour_overlap=0.0,
            crossing_clearance=0.0, inner_grid_angle=0.0,
            boundary_mode="engrave", contour_line_count=1,
            contour_spacing=0.2, vector_contour_settings=None,
            reverse_scan=False, optimize_scan_order=True,
            engraving_acceleration=500, raster_direction="x",
            raster_angle=None,
            machine_bounds=None, alternate_lines=False,
            alternate_mode="two-pass"):
        if region is None or region.is_empty:
            return ""

        shell_region, grid_region = self._surface_skin_engraving_regions(
            region=region,
            shell_thickness=shell_thickness,
            inner_grid_spacing=inner_grid_spacing,
            inner_grid_line_thickness=inner_grid_line_thickness,
            contour_overlap=contour_overlap,
            crossing_clearance=crossing_clearance,
            inner_grid_angle=inner_grid_angle)

        mode = str(boundary_mode or "engrave").strip().lower()
        if mode == "vectoring":
            mode = "vector"
        if mode in ("raster", "raster-only", "no-shell"):
            mode = "none"
        if mode in ("outline", "contour-only"):
            mode = "contour"
        if mode not in ("engrave", "vector", "contour", "none"):
            mode = "engrave"

        if mode == "none":
            raster_gcode = self._generate_combined_engraving_rows(
                shell_region=GeometryCollection(),
                grid_region=grid_region,
                infill_settings=infill_settings,
                scanline_spacing=scanline_spacing,
                reverse_scan=reverse_scan,
                optimize_scan_order=optimize_scan_order,
                engraving_acceleration=engraving_acceleration,
                raster_direction=raster_direction,
                raster_angle=raster_angle,
                machine_bounds=machine_bounds,
                alternate_lines=alternate_lines,
                alternate_mode=alternate_mode,
                comment_label="SURFACE SKIN")
            if not raster_gcode:
                return ""
            return (
                ";SURFACE SKIN FLAT ROOF INNER RASTER ONLY\n"
                f";SURFACE SKIN FLAT ROOF NO EXTRA SHELL\n"
                f";SURFACE SKIN FLAT ROOF INNER RASTER POWER "
                f"{int(infill_settings['power'])} FEED "
                f"{int(infill_settings['speed'])} mm/min\n"
                ";SURFACE SKIN FLAT ROOF INNER RASTER START\n"
                f"{raster_gcode}\n"
                ";SURFACE SKIN FLAT ROOF INNER RASTER END"
            )

        if mode == "vector":
            contour_settings = (
                vector_contour_settings
                if vector_contour_settings is not None
                else infill_settings)
            shell_gcode = self._generate_contour_vector_gcode(
                region=region,
                contour_settings=contour_settings,
                line_count=contour_line_count,
                spacing=contour_spacing,
                engraving_mode="vectoring",
                engraving_acceleration=engraving_acceleration,
                machine_bounds=machine_bounds)
            raster_gcode = self._generate_combined_engraving_rows(
                shell_region=GeometryCollection(),
                grid_region=grid_region,
                infill_settings=infill_settings,
                scanline_spacing=scanline_spacing,
                reverse_scan=reverse_scan,
                optimize_scan_order=optimize_scan_order,
                engraving_acceleration=engraving_acceleration,
                raster_direction=raster_direction,
                raster_angle=raster_angle,
                machine_bounds=machine_bounds,
                alternate_lines=alternate_lines,
                alternate_mode=alternate_mode,
                comment_label="SURFACE SKIN")
            skin_parts = [
                ";SURFACE SKIN FLAT ROOF VECTOR SHELL START",
                shell_gcode,
                ";SURFACE SKIN FLAT ROOF VECTOR SHELL END",
            ]
            if raster_gcode:
                skin_parts.extend([
                    ";SURFACE SKIN FLAT ROOF INNER RASTER START",
                    raster_gcode,
                    ";SURFACE SKIN FLAT ROOF INNER RASTER END",
                ])
            skin_gcode = "\n".join(part for part in skin_parts if part)
            if not shell_gcode and not raster_gcode:
                return ""
            return (
                ";SURFACE SKIN FLAT ROOF VECTOR SHELL + INNER RASTER\n"
                f";SURFACE SKIN FLAT ROOF CONTOUR LINES "
                f"{int(contour_line_count)} OVERLAP "
                f"{float(contour_overlap) * 100.0:.2f}%\n"
                f";SURFACE SKIN FLAT ROOF VECTOR SHELL POWER "
                f"{int(contour_settings['power'])} FEED "
                f"{int(contour_settings['speed'])} mm/min\n"
                f";SURFACE SKIN FLAT ROOF INNER RASTER POWER "
                f"{int(infill_settings['power'])} FEED "
                f"{int(infill_settings['speed'])} mm/min\n"
                f"{skin_gcode}"
            )

        if mode == "contour":
            # Trace a clean contour outline around the roof (just lines, like
            # the body contour) that is independent of how the roof interior is
            # filled. The interior grid raster is emitted unchanged so the roof
            # still forms exactly as it does in the other shell modes; only the
            # perimeter switches from a rastered/cross-hatched band to plain
            # contour lines.
            contour_settings = (
                vector_contour_settings
                if vector_contour_settings is not None
                else infill_settings)
            shell_gcode = self._generate_contour_vector_gcode(
                region=region,
                contour_settings=contour_settings,
                line_count=contour_line_count,
                spacing=contour_spacing,
                engraving_mode="vectoring",
                engraving_acceleration=engraving_acceleration,
                machine_bounds=machine_bounds)
            raster_gcode = self._generate_combined_engraving_rows(
                shell_region=GeometryCollection(),
                grid_region=grid_region,
                infill_settings=infill_settings,
                scanline_spacing=scanline_spacing,
                reverse_scan=reverse_scan,
                optimize_scan_order=optimize_scan_order,
                engraving_acceleration=engraving_acceleration,
                raster_direction=raster_direction,
                raster_angle=raster_angle,
                machine_bounds=machine_bounds,
                alternate_lines=alternate_lines,
                alternate_mode=alternate_mode,
                comment_label="SURFACE SKIN")
            skin_parts = [
                ";SURFACE SKIN FLAT ROOF CONTOUR SHELL START",
                shell_gcode,
                ";SURFACE SKIN FLAT ROOF CONTOUR SHELL END",
            ]
            if raster_gcode:
                skin_parts.extend([
                    ";SURFACE SKIN FLAT ROOF INNER RASTER START",
                    raster_gcode,
                    ";SURFACE SKIN FLAT ROOF INNER RASTER END",
                ])
            skin_gcode = "\n".join(part for part in skin_parts if part)
            if not shell_gcode and not raster_gcode:
                return ""
            return (
                ";SURFACE SKIN FLAT ROOF CONTOUR SHELL + INNER RASTER\n"
                f";SURFACE SKIN FLAT ROOF CONTOUR LINES "
                f"{int(contour_line_count)} OVERLAP "
                f"{float(contour_overlap) * 100.0:.2f}%\n"
                f";SURFACE SKIN FLAT ROOF CONTOUR SHELL POWER "
                f"{int(contour_settings['power'])} FEED "
                f"{int(contour_settings['speed'])} mm/min\n"
                f";SURFACE SKIN FLAT ROOF INNER RASTER POWER "
                f"{int(infill_settings['power'])} FEED "
                f"{int(infill_settings['speed'])} mm/min\n"
                f"{skin_gcode}"
            )

        skin_gcode = self._generate_combined_engraving_rows(
            shell_region=shell_region,
            grid_region=grid_region,
            infill_settings=infill_settings,
            scanline_spacing=scanline_spacing,
            reverse_scan=reverse_scan,
            optimize_scan_order=optimize_scan_order,
            engraving_acceleration=engraving_acceleration,
            raster_direction=raster_direction,
            raster_angle=raster_angle,
            machine_bounds=machine_bounds,
            alternate_lines=alternate_lines,
            alternate_mode=alternate_mode,
            comment_label="SURFACE SKIN")
        if not skin_gcode:
            return ""
        return (
            ";SURFACE SKIN FLAT ROOF COMBINED SHELL INFILL RASTER\n"
            f";SURFACE SKIN FLAT ROOF CONTOUR LINES "
            f"{int(contour_line_count)} OVERLAP "
            f"{float(contour_overlap) * 100.0:.2f}%\n"
            ";SURFACE SKIN FLAT ROOF RASTER ROWS START\n"
            f"{skin_gcode}\n"
            ";SURFACE SKIN FLAT ROOF RASTER ROWS END"
        )

    def _solid_island_cells(self, region, requested_size):
        """Fit whole, equal cells to a solid region and clip them to it."""
        if region is None or region.is_empty:
            return [], 0, 0, 0.0, 0.0

        requested_size = max(float(requested_size), 1e-6)
        min_x, min_y, max_x, max_y = region.bounds
        width = max(0.0, max_x - min_x)
        height = max(0.0, max_y - min_y)
        if width <= 1e-9 or height <= 1e-9:
            return [], 0, 0, 0.0, 0.0

        # Use complete requested-size cells as the count, then distribute the
        # full span evenly. For example, 8 / 3 produces two 4 mm cells.
        columns = max(
            1, int(np.floor((width + 1e-9) / requested_size)))
        rows = max(
            1, int(np.floor((height + 1e-9) / requested_size)))
        actual_width = width / columns
        actual_height = height / rows
        cells = []

        for row_index in range(rows):
            column_indices = list(range(columns))
            if row_index % 2 == 1:
                column_indices.reverse()
            y0 = min_y + row_index * actual_height
            y1 = max_y if row_index == rows - 1 else y0 + actual_height
            for column_index in column_indices:
                x0 = min_x + column_index * actual_width
                x1 = (
                    max_x
                    if column_index == columns - 1
                    else x0 + actual_width)
                clipped = region.intersection(box(x0, y0, x1, y1))
                if not clipped.is_valid:
                    clipped = clipped.buffer(0)
                if clipped.is_empty or clipped.area <= 1e-9:
                    continue
                cells.append({
                    "region": clipped,
                    "row": row_index + 1,
                    "column": column_index + 1,
                })

        return cells, columns, rows, actual_width, actual_height

    def _extract_polygons(self, geometry):
        if geometry is None or geometry.is_empty:
            return []
        if isinstance(geometry, Polygon):
            return [geometry]
        if isinstance(geometry, MultiPolygon):
            return list(geometry.geoms)
        if isinstance(geometry, GeometryCollection):
            polygons = []
            for child in geometry.geoms:
                polygons.extend(self._extract_polygons(child))
            return polygons
        return []

    def _mask_region_from_png(self, img_infill, image_size):
        img = Image.open(io.BytesIO(img_infill)).convert("L")
        arr = np.array(img)
        # Prusa SL1 masks use bright pixels for exposed/printable regions.
        # The old raster path inverted these images before power mapping, so
        # keep the same convention here for vector hatches.
        mask = arr > 5
        mask_u8 = (mask.astype(np.uint8)) * 255
        contours, hierarchy = cv2.findContours(
            mask_u8, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_NONE)
        if hierarchy is None:
            return None

        hierarchy = hierarchy[0]
        height, width = mask.shape
        scale_x = float(image_size[0]) / max(1, width)
        scale_y = float(image_size[1]) / max(1, height)
        polygons = []

        for index, contour in enumerate(contours):
            if hierarchy[index][3] != -1 or len(contour) < 3:
                continue
            exterior = self._contour_to_points(contour, height, scale_x, scale_y)
            holes = []
            child = hierarchy[index][2]
            while child != -1:
                if len(contours[child]) >= 3:
                    holes.append(self._contour_to_points(
                        contours[child], height, scale_x, scale_y))
                child = hierarchy[child][0]

            polygon = Polygon(exterior, holes)
            if not polygon.is_valid:
                polygon = polygon.buffer(0)
            if not polygon.is_empty and polygon.area > 0:
                polygons.append(polygon)

        if not polygons:
            return None
        region = unary_union(polygons)
        simplify_tolerance = min(
            0.05, max(0.01, min(scale_x, scale_y) * 0.75))
        return region.simplify(simplify_tolerance, preserve_topology=True)

    def _contour_to_points(self, contour, image_height, scale_x, scale_y):
        pts = contour.reshape(-1, 2)
        return [(float(x) * scale_x, (image_height - float(y)) * scale_y)
                for x, y in pts]

    def _generate_infill_vector_gcode(self, region, infill_settings, angle,
                                      line_count, spacing, hatch_spacing,
                                      infill_type, grid_spacing,
                                      grid_clearance,
                                      contour_line_count,
                                      allow_boundary_fallback=True,
                                      reverse_scan=False,
                                      optimize_scan_order=True,
                                      engraving_mode="vectoring",
                                      engraving_acceleration=500,
                                      machine_bounds=None,
                                      hatch_exclusion_angles=None,
                                      require_complete_bundle=None,
                                      hatch_phase_fraction=0.0,
                                      preserve_vector_pattern_in_engraving=False):
        if region is None or region.is_empty:
            return ""

        if (
            str(engraving_mode).lower() == "engraving"
            and not preserve_vector_pattern_in_engraving
        ):
            scanline_pitch = max(
                float(hatch_spacing) if hatch_spacing and hatch_spacing > 0
                else float(spacing),
                1e-6,
            )
            scanlines = self._horizontal_scanline_segments(
                region,
                scanline_pitch,
                reverse_first_row=reverse_scan,
                alternate_direction=optimize_scan_order,
            )
            commands = []
            self._append_hatch_commands(
                commands,
                scanlines,
                infill_settings,
                engraving_mode,
                engraving_acceleration,
                machine_bounds,
            )
            return "\n".join(commands)

        line_count = self._normalise_line_count(line_count)
        spacing = float(spacing)
        if hatch_spacing and hatch_spacing > 0:
            pitch = max(float(hatch_spacing), spacing * line_count)
        else:
            pitch = max(float(infill_settings["pixel_size"][0]),
                        float(infill_settings["pixel_size"][1]),
                        spacing * line_count)
        clearance = float(infill_settings.get(
            "boundary_clearance",
            max(0, int(contour_line_count or 1) - 1) * spacing))
        clip_region = region.buffer(-clearance, join_style=2, mitre_limit=5)
        has_clearance_region = not clip_region.is_empty
        if not has_clearance_region:
            clip_region = region

        commands = []
        is_grid = str(infill_type).strip().lower() == "grid"
        active_pitch = (
            max(float(grid_spacing), spacing * line_count)
            if is_grid else pitch)
        hatch_phase_offset = (
            active_pitch * float(hatch_phase_fraction or 0.0))
        hatch_require_complete_bundle = (
            has_clearance_region
            if require_complete_bundle is None
            else bool(require_complete_bundle))
        primary_lines = self._angled_hatch_lines(
            clip_region, angle, active_pitch, line_count, spacing,
            require_complete_bundle=hatch_require_complete_bundle,
            phase_offset=hatch_phase_offset)
        exclusion_lines = []
        for exclusion_angle in hatch_exclusion_angles or []:
            exclusion_lines.extend(self._angled_hatch_lines(
                clip_region, exclusion_angle, active_pitch, line_count,
                spacing,
                require_complete_bundle=hatch_require_complete_bundle,
                phase_offset=hatch_phase_offset))
        if exclusion_lines:
            primary_lines = self._exclude_primary_hatches_from_grid(
                primary_lines, exclusion_lines, spacing, grid_clearance,
                line_count)
        hatch_lines = list(primary_lines)
        if is_grid:
            secondary_lines = self._angled_hatch_lines(
                clip_region, angle + 90.0, active_pitch, line_count, spacing,
                require_complete_bundle=hatch_require_complete_bundle,
                phase_offset=hatch_phase_offset)
            if exclusion_lines:
                secondary_lines = self._exclude_primary_hatches_from_grid(
                    secondary_lines, exclusion_lines, spacing, grid_clearance,
                    line_count)
            hatch_lines.extend(self._exclude_primary_hatches_from_grid(
                secondary_lines, primary_lines, spacing, grid_clearance,
                line_count))
        hatch_lines = self._order_hatch_lines(
            hatch_lines, reverse_scan, optimize_scan_order)
        self._append_hatch_commands(
            commands, hatch_lines, infill_settings, engraving_mode,
            engraving_acceleration, machine_bounds)

        if allow_boundary_fallback and has_clearance_region and line_count > 1:
            loose_clearance = max(spacing * 0.25, clearance * 0.5)
            loose_region = region.buffer(
                -loose_clearance, join_style=2, mitre_limit=5)
            if not loose_region.is_empty:
                fallback_region = loose_region.difference(
                    clip_region.buffer(spacing * 0.1))
                fallback_lines = self._angled_hatch_lines(
                    fallback_region, angle, active_pitch, line_count, spacing,
                    require_complete_bundle=False,
                    phase_offset=hatch_phase_offset)
                fallback_exclusion_lines = []
                for exclusion_angle in hatch_exclusion_angles or []:
                    fallback_exclusion_lines.extend(self._angled_hatch_lines(
                        fallback_region, exclusion_angle, active_pitch,
                        line_count, spacing, require_complete_bundle=False,
                        phase_offset=hatch_phase_offset))
                if fallback_exclusion_lines:
                    fallback_lines = self._exclude_primary_hatches_from_grid(
                        fallback_lines, fallback_exclusion_lines, spacing,
                        grid_clearance, line_count)
                if is_grid:
                    fallback_secondary = self._angled_hatch_lines(
                        fallback_region, angle + 90.0, active_pitch,
                        line_count, spacing, require_complete_bundle=False,
                        phase_offset=hatch_phase_offset)
                    if fallback_exclusion_lines:
                        fallback_secondary = (
                            self._exclude_primary_hatches_from_grid(
                                fallback_secondary, fallback_exclusion_lines,
                                spacing, grid_clearance, line_count))
                    fallback_lines.extend(
                        self._exclude_primary_hatches_from_grid(
                            fallback_secondary, fallback_lines, spacing,
                            grid_clearance, line_count))
                fallback_lines = self._order_hatch_lines(
                    fallback_lines, reverse_scan, optimize_scan_order)
                self._append_hatch_commands(
                    commands, fallback_lines, infill_settings, engraving_mode,
                    engraving_acceleration, machine_bounds)
        return "\n".join(commands)

    def _generate_sparse_grid_vector_gcode(
            self, region, infill_settings, angle, line_count, spacing,
            grid_spacing, grid_clearance, reverse_scan=False,
            optimize_scan_order=True, engraving_acceleration=500,
            engraving_mode="engraving", machine_bounds=None):
        """Emit sparse grid centerlines for engraving interiors."""
        if region is None or region.is_empty:
            return ""

        line_count = self._normalise_line_count(line_count)
        spacing = float(spacing)
        active_pitch = max(float(grid_spacing), spacing * line_count)
        primary_lines = self._angled_hatch_lines(
            region,
            angle,
            active_pitch,
            line_count,
            spacing,
            require_complete_bundle=False)
        secondary_lines = self._angled_hatch_lines(
            region,
            angle + 90.0,
            active_pitch,
            line_count,
            spacing,
            require_complete_bundle=False)
        secondary_lines = self._exclude_primary_hatches_from_grid(
            secondary_lines,
            primary_lines,
            spacing,
            grid_clearance,
            line_count)
        hatch_lines = list(primary_lines) + list(secondary_lines)

        if not hatch_lines:
            hatch_lines = self._fallback_cross_grid_lines(
                region,
                angle,
                max(spacing * 0.25, 1e-6))

        hatch_lines = self._order_hatch_lines(
            hatch_lines,
            reverse_scan,
            optimize_scan_order)
        commands = []
        self._append_hatch_commands(
            commands,
            hatch_lines,
            infill_settings,
            engraving_mode,
            engraving_acceleration,
            machine_bounds)
        return "\n".join(commands)

    def _fallback_cross_grid_lines(self, region, angle, min_length):
        if region is None or region.is_empty:
            return []
        try:
            centroid = region.representative_point()
            center = np.array([float(centroid.x), float(centroid.y)])
            min_x, min_y, max_x, max_y = region.bounds
            line_length = float(np.hypot(max_x - min_x, max_y - min_y)) * 2.0
            line_length = max(line_length, float(min_length) * 8.0)
        except Exception:
            return []

        lines = []
        for candidate_angle in (float(angle), float(angle) + 90.0):
            theta = np.radians(candidate_angle)
            direction = np.array([np.cos(theta), np.sin(theta)])
            raw_line = LineString([
                tuple(center - direction * line_length),
                tuple(center + direction * line_length),
            ])
            try:
                clipped = raw_line.intersection(region)
            except Exception:
                continue
            lines.extend([
                line for line in self._extract_lines(clipped)
                if line.length >= float(min_length)
            ])
        return lines

    def _horizontal_scanline_segments(
            self, region, pitch, reverse_first_row=False,
            alternate_direction=True):
        """
        Clip horizontal top-to-bottom scanlines against the layer geometry.

        A row can produce several independent segments when it crosses holes,
        cavities, or disconnected islands. Segment direction is consistent
        within each row and may alternate between rows for shorter travel.
        """
        if region is None or region.is_empty:
            return []

        pitch = max(float(pitch), 1e-6)
        min_x, min_y, max_x, max_y = region.bounds
        margin = max(pitch, max_x - min_x) + 1.0
        row_count = max(1, int(np.floor((max_y - min_y) / pitch)) + 1)
        first_y = max_y - min(pitch / 2.0, (max_y - min_y) / 2.0)
        minimum_length = max(pitch * 0.01, 1e-6)
        ordered_segments = []

        for row_index in range(row_count):
            y = first_y - row_index * pitch
            if y < min_y - 1e-9:
                break

            raw_line = LineString([
                (min_x - margin, y),
                (max_x + margin, y),
            ])
            row_segments = [
                segment
                for segment in self._extract_lines(
                    raw_line.intersection(region))
                if segment.length >= minimum_length
            ]
            row_segments = [
                LineString(
                    coords
                    if coords[0][0] <= coords[-1][0]
                    else list(reversed(coords)))
                for segment in row_segments
                if len(coords := list(segment.coords)) >= 2
            ]
            row_segments.sort(key=lambda segment: segment.bounds[0])
            if not row_segments:
                continue

            reverse_row = bool(reverse_first_row)
            if alternate_direction and row_index % 2 == 1:
                reverse_row = not reverse_row

            if reverse_row:
                row_segments.reverse()
                row_segments = [
                    LineString(list(reversed(segment.coords)))
                    for segment in row_segments
                ]

            ordered_segments.extend(row_segments)

        return ordered_segments

    def _vertical_scanline_segments(
            self, region, pitch, reverse_first_column=False,
            alternate_direction=True):
        """
        Clip vertical left-to-right scanlines against the layer geometry.

        Each column travels along Y. Direction may alternate between columns
        while preserving the same segment grouping used for compensated rows.
        """
        if region is None or region.is_empty:
            return []

        pitch = max(float(pitch), 1e-6)
        min_x, min_y, max_x, max_y = region.bounds
        margin = max(pitch, max_y - min_y) + 1.0
        column_count = max(
            1, int(np.floor((max_x - min_x) / pitch)) + 1)
        first_x = min_x + min(pitch / 2.0, (max_x - min_x) / 2.0)
        minimum_length = max(pitch * 0.01, 1e-6)
        ordered_segments = []

        for column_index in range(column_count):
            x = first_x + column_index * pitch
            if x > max_x + 1e-9:
                break

            raw_line = LineString([
                (x, min_y - margin),
                (x, max_y + margin),
            ])
            column_segments = [
                segment
                for segment in self._extract_lines(
                    raw_line.intersection(region))
                if segment.length >= minimum_length
            ]
            column_segments = [
                LineString(
                    coords
                    if coords[0][1] <= coords[-1][1]
                    else list(reversed(coords)))
                for segment in column_segments
                if len(coords := list(segment.coords)) >= 2
            ]
            column_segments.sort(key=lambda segment: segment.bounds[1])
            if not column_segments:
                continue

            reverse_column = bool(reverse_first_column)
            if alternate_direction and column_index % 2 == 1:
                reverse_column = not reverse_column

            if reverse_column:
                column_segments.reverse()
                column_segments = [
                    LineString(list(reversed(segment.coords)))
                    for segment in column_segments
                ]

            ordered_segments.extend(column_segments)

        return ordered_segments

    def _engraving_grid_band_region(
            self, region, angle, grid_spacing, line_thickness,
            crossing_clearance):
        """Return clipped thick grid members before horizontal rasterization."""
        if region is None or region.is_empty:
            return GeometryCollection()

        pitch = max(float(grid_spacing), 1e-6)
        thickness = max(float(line_thickness), 1e-6)
        primary_lines = self._angled_hatch_lines(
            region, angle, pitch, 1, pitch,
            require_complete_bundle=False)
        secondary_lines = self._angled_hatch_lines(
            region, float(angle) + 90.0, pitch, 1, pitch,
            require_complete_bundle=False)
        primary_bands = self._buffer_grid_lines(
            primary_lines, thickness).intersection(region)
        secondary_bands = self._buffer_grid_lines(
            secondary_lines, thickness).intersection(region)
        if crossing_clearance > 0 and not primary_bands.is_empty:
            secondary_bands = secondary_bands.difference(
                primary_bands.buffer(
                    float(crossing_clearance),
                    cap_style=2,
                    join_style=2))
        return unary_union([primary_bands, secondary_bands])

    def _generate_combined_engraving_rows(
            self, shell_region, grid_region, infill_settings,
            scanline_spacing, reverse_scan=False,
            optimize_scan_order=True, engraving_acceleration=500,
            raster_direction="x", raster_angle=None,
            machine_bounds=None, alternate_lines=False,
            alternate_mode="two-pass", comment_label="ENGRAVING"):
        """
        Emit one compensated motion sequence for each scan row or column.

        A scan can contain shell and grid dashes. S0 moves bridge every gap
        without restarting acceleration.
        """
        geometries = [
            geometry for geometry in (shell_region, grid_region)
            if geometry is not None and not geometry.is_empty
        ]
        if not geometries:
            return ""

        combined_region = unary_union(geometries)
        direction = str(raster_direction or "x").strip().lower()
        pitch = max(float(scanline_spacing), 1e-6)
        if raster_angle is not None:
            active_raster_angle = float(raster_angle) % 180.0
            raster_lines = self._angled_hatch_lines(
                combined_region,
                active_raster_angle,
                pitch,
                1,
                pitch,
                require_complete_bundle=False)
            raster_lines = self._order_hatch_lines(
                raster_lines,
                reverse_scan,
                optimize_scan_order)
            rows = [(index, [segment])
                    for index, segment in enumerate(raster_lines)]
            scan_label = f"ANGLE {active_raster_angle:.4f} DEG"
        elif direction in ("diagonal", "anti-diagonal"):
            active_raster_angle = (
                45.0 if direction == "diagonal" else 135.0)
            raster_lines = self._angled_hatch_lines(
                combined_region,
                active_raster_angle,
                pitch,
                1,
                pitch,
                require_complete_bundle=False)
            raster_lines = self._order_hatch_lines(
                raster_lines,
                reverse_scan,
                optimize_scan_order)
            rows = [(index, [segment])
                    for index, segment in enumerate(raster_lines)]
            scan_label = "DIAGONAL"
        elif direction == "y":
            raster_lines = self._vertical_scanline_segments(
                combined_region,
                pitch,
                reverse_first_column=reverse_scan,
                alternate_direction=optimize_scan_order)
            grouping_axis = 0
            scan_label = "COLUMN"
            rows = []
            for segment in raster_lines:
                coords = list(segment.coords)
                if len(coords) < 2:
                    continue
                position = float(coords[0][grouping_axis])
                if not rows or abs(rows[-1][0] - position) > 1e-6:
                    rows.append((position, [segment]))
                else:
                    rows[-1][1].append(segment)
        else:
            raster_lines = self._horizontal_scanline_segments(
                combined_region,
                pitch,
                reverse_first_row=reverse_scan,
                alternate_direction=optimize_scan_order)
            grouping_axis = 1
            scan_label = "ROW"
            rows = []
            for segment in raster_lines:
                coords = list(segment.coords)
                if len(coords) < 2:
                    continue
                position = float(coords[0][grouping_axis])
                if not rows or abs(rows[-1][0] - position) > 1e-6:
                    rows.append((position, [segment]))
                else:
                    rows[-1][1].append(segment)

        commands = []
        if alternate_lines and alternate_mode == "edge-interleave":
            ordered_rows = [("EDGE INTERLEAVE", self._edge_interleave_order(rows))]
        elif alternate_lines:
            ordered_rows = [
                (1, rows[::2]),
                (2, rows[1::2]),
            ]
        else:
            ordered_rows = [(None, rows)]

        for pass_index, pass_rows in ordered_rows:
            if not pass_rows:
                continue
            if pass_index is not None:
                commands.append(
                    f";{comment_label} ALTERNATE PASS {pass_index}")
            commands.append("M3")
            for row_index, (_, segments) in enumerate(pass_rows, 1):
                if pass_index is None:
                    label = f";{comment_label} {scan_label} {row_index}"
                elif pass_index == "EDGE INTERLEAVE":
                    label = (
                        f";{comment_label} {scan_label} EDGE-INTERLEAVE "
                        f"{row_index}"
                    )
                else:
                    source_index = (row_index * 2 - 1
                                    if pass_index == 1
                                    else row_index * 2)
                    label = (
                        f";{comment_label} {scan_label} {source_index} "
                        f"PASS {pass_index}"
                    )
                commands.append(label)
                self._append_compensated_row_commands(
                    commands, segments, infill_settings,
                    engraving_acceleration, machine_bounds)
            commands.append("S0")
            commands.append("M5")
        return "\n".join(commands)

    def _append_compensated_row_commands(
            self, commands, segments, settings, acceleration,
            machine_bounds=None):
        """Apply lead-in/out once around every powered/gap sequence in a row."""
        if not segments:
            return

        speed = int(settings["speed"])
        power = int(settings["power"])
        first_coords = list(segments[0].coords)
        last_coords = list(segments[-1].coords)
        if len(first_coords) < 2 or len(last_coords) < 2:
            return

        first_start, first_end = first_coords[0], first_coords[-1]
        last_start, last_end = last_coords[0], last_coords[-1]
        lead_in, _ = self._lead_points(
            first_start, first_end, speed, acceleration, machine_bounds)
        _, lead_out = self._lead_points(
            last_start, last_end, speed, acceleration, machine_bounds)

        commands.append(
            f"G0X{lead_in[0]:.4f}Y{lead_in[1]:.4f}S0")
        commands.append(
            f"G1X{first_start[0]:.4f}Y{first_start[1]:.4f}S0F{speed}")

        current = first_start
        for segment in segments:
            coords = list(segment.coords)
            if len(coords) < 2:
                continue
            start, end = coords[0], coords[-1]
            if (
                abs(float(current[0]) - float(start[0])) > 1e-6
                or abs(float(current[1]) - float(start[1])) > 1e-6
            ):
                commands.append(
                    f"G1X{start[0]:.4f}Y{start[1]:.4f}S0F{speed}")
            commands.append(
                f"G1X{end[0]:.4f}Y{end[1]:.4f}S{power}F{speed}")
            current = end

        commands.append(
            f"G1X{lead_out[0]:.4f}Y{lead_out[1]:.4f}S0F{speed}")

    @staticmethod
    def _buffer_grid_lines(lines, thickness):
        """Convert grid centerlines into flat-ended printable bands."""
        bands = [
            line.buffer(
                float(thickness) / 2.0,
                cap_style=2,
                join_style=2)
            for line in lines
            if line is not None and not line.is_empty and line.length > 1e-9
        ]
        return unary_union(bands) if bands else GeometryCollection()

    def _append_hatch_commands(self, commands, hatch_lines, infill_settings,
                               engraving_mode="vectoring",
                               engraving_acceleration=500,
                               machine_bounds=None):
        valid_segments = []
        for line in hatch_lines:
            coords = list(line.coords)
            if len(coords) < 2:
                continue
            start = coords[0]
            end = coords[-1]
            if start == end:
                continue
            valid_segments.append((start, end))

        if not valid_segments:
            return

        if str(engraving_mode).lower() == "engraving":
            speed = int(infill_settings["speed"])
            power = int(infill_settings["power"])
            commands.append("M3")
            for start, end in valid_segments:
                lead_in, lead_out = self._lead_points(
                    start, end, speed, engraving_acceleration,
                    machine_bounds)
                commands.append(
                    f"G0X{lead_in[0]:.4f}Y{lead_in[1]:.4f}S0")
                commands.append(
                    f"G1X{start[0]:.4f}Y{start[1]:.4f}S0F{speed}")
                commands.append(
                    f"G1X{end[0]:.4f}Y{end[1]:.4f}S{power}F{speed}")
                commands.append(
                    f"G1X{lead_out[0]:.4f}Y{lead_out[1]:.4f}S0F{speed}")
            commands.append("S0")
            commands.append("M5")
            return

        speed = int(infill_settings["speed"])
        power = int(infill_settings["power"])

        commands.append("M3")
        for start, end in valid_segments:
            commands.append(
                f"G0X{start[0]:.4f}Y{start[1]:.4f}S0")
            commands.append(
                f"G1X{end[0]:.4f}Y{end[1]:.4f}S{power}F{speed}")

        commands.append("S0")
        commands.append("M5")

    def _exclude_primary_hatches_from_grid(self, secondary_lines,
                                           primary_lines, spacing,
                                           clearance, line_count=1):
        """Remove powered secondary motion around every primary hatch crossing."""
        if not secondary_lines or not primary_lines:
            return list(secondary_lines)

        bundle_half_width = (
            max(0, self._normalise_line_count(line_count) - 1)
            * float(spacing)
            * 0.5)
        exclusion_radius = max(
            float(clearance) + bundle_half_width,
            float(spacing) * 0.25,
            1e-6)
        # Buffering one fully-noded union of many crossing hatch directions can
        # become extremely expensive. Buffering each source line creates the
        # same exclusion area; the spatial index then unions only the buffers
        # that can actually intersect the secondary line being clipped.
        exclusion_zones = [
            line.buffer(exclusion_radius, cap_style=1, join_style=1)
            for line in primary_lines
        ]
        exclusion_tree = STRtree(exclusion_zones)
        min_segment_length = max(float(spacing) * 0.25, 1e-6)
        clipped_lines = []
        for line in secondary_lines:
            zone_indices = exclusion_tree.query(
                line, predicate="intersects")
            if len(zone_indices) == 0:
                if line.length >= min_segment_length:
                    clipped_lines.append(line)
                continue
            local_zones = [
                exclusion_zones[int(index)] for index in zone_indices
            ]
            exclusion_zone = (
                local_zones[0]
                if len(local_zones) == 1
                else unary_union(local_zones)
            )
            parts = self._extract_lines(line.difference(exclusion_zone))
            clipped_lines.extend(
                part for part in parts
                if part.length >= min_segment_length)
        return clipped_lines

    def _generate_contour_vector_gcode(self, region, contour_settings,
                                       line_count, spacing,
                                       engraving_mode="vectoring",
                                       engraving_acceleration=500,
                                       machine_bounds=None,
                                       include_holes=True):
        if region is None or region.is_empty:
            return ""

        commands = []
        for contour in self._offset_contour_lines(
                region, line_count, spacing, include_holes=include_holes):
            if len(contour) < 2:
                continue
            start = contour[0]
            if str(engraving_mode).lower() == "engraving":
                self._append_compensated_polyline_commands(
                    commands, contour, contour_settings,
                    engraving_acceleration, machine_bounds)
                continue
            commands.append("M5")
            commands.append(
                f"G0X{start[0]:.4f}Y{start[1]:.4f}S0")
            first_powered_move = True
            for pt in contour[1:]:
                if first_powered_move:
                    commands.append("M3")
                    commands.append(
                        f"G1X{pt[0]:.4f}Y{pt[1]:.4f}S{int(contour_settings['power'])}F{int(contour_settings['speed'])}")
                    first_powered_move = False
                    continue
                commands.append(
                    f"G1X{pt[0]:.4f}Y{pt[1]:.4f}")
            commands.append("S0")
            commands.append("M5")
        return "\n".join(commands)

    def _generate_preview_contour_comments(self, region):
        """Encode slice boundaries in comments for frontend rendering only."""
        if region is None or region.is_empty:
            return ""

        commands = []
        for polygon in self._extract_polygons(region):
            rings = [polygon.exterior, *polygon.interiors]
            for ring in rings:
                points = list(ring.coords)
                if len(points) < 2:
                    continue
                start = points[0]
                commands.append(
                    f";PREVIEW_CONTOUR G0X{start[0]:.4f}Y{start[1]:.4f}")
                for point in points[1:]:
                    commands.append(
                        f";PREVIEW_CONTOUR "
                        f"G1X{point[0]:.4f}Y{point[1]:.4f}S1F1")
        return "\n".join(commands)

    def _generate_preview_contour_lines_comments(
            self, region, line_count, spacing):
        """Encode roof contour-shell offset lines for blue contour preview.

        Used by the flat-roof "contour" boundary mode so the perimeter renders
        as plain contour lines (like the body contour) instead of a green
        cross-hatched skin band. Emitted as ;PREVIEW_CONTOUR comments so the
        frontend draws them in the same colour as every other slice contour.
        """
        if region is None or region.is_empty:
            return ""

        commands = []
        for contour in self._offset_contour_lines(region, line_count, spacing):
            if len(contour) < 2:
                continue
            start = contour[0]
            commands.append(
                f";PREVIEW_CONTOUR G0X{start[0]:.4f}Y{start[1]:.4f}")
            for point in contour[1:]:
                commands.append(
                    f";PREVIEW_CONTOUR "
                    f"G1X{point[0]:.4f}Y{point[1]:.4f}S1F1")
        return "\n".join(commands)

    def _generate_preview_grid_comments(self, region, scanline_spacing):
        """Encode rasterized grid bands as comments for green preview only."""
        commands = []
        for segment in self._horizontal_scanline_segments(
                region, scanline_spacing,
                reverse_first_row=False,
                alternate_direction=False):
            coords = list(segment.coords)
            if len(coords) < 2:
                continue
            start, end = coords[0], coords[-1]
            commands.append(
                f";PREVIEW_GRID G0X{start[0]:.4f}Y{start[1]:.4f}")
            commands.append(
                f";PREVIEW_GRID "
                f"G1X{end[0]:.4f}Y{end[1]:.4f}S1F1")
        return "\n".join(commands)

    def _generate_preview_surface_skin_comments(
            self, region, hatch_spacing, angle,
            alternate_lines=False, alternate_mode="two-pass"):
        """Encode roof/deck skin hatch as comments for highlighted preview."""
        commands = []
        pitch = max(float(hatch_spacing), 1e-6)
        hatch_lines = self._angled_hatch_lines(
                region, angle, pitch, 1, pitch,
                require_complete_bundle=False)
        if alternate_lines and alternate_mode == "edge-interleave":
            hatch_lines = self._edge_interleave_order(hatch_lines)
        elif alternate_lines:
            hatch_lines = hatch_lines[::2] + hatch_lines[1::2]

        for segment in hatch_lines:
            coords = list(segment.coords)
            if len(coords) < 2:
                continue
            start, end = coords[0], coords[-1]
            commands.append(
                f";PREVIEW_SURFACE_SKIN "
                f"G0X{start[0]:.4f}Y{start[1]:.4f}")
            commands.append(
                f";PREVIEW_SURFACE_SKIN "
                f"G1X{end[0]:.4f}Y{end[1]:.4f}S1F1")
        return "\n".join(commands)

    def _generate_preview_surface_skin_spiral_comments(
            self, region, spacing, direction="outside-in",
            alternate_lines=False, alternate_mode="two-pass"):
        """Encode flat-roof spiral vectors as highlighted preview comments."""
        commands = []
        contour_lines = self._surface_skin_spiral_lines(region, spacing)
        if str(direction).lower() == "inside-out":
            contour_lines = list(reversed(contour_lines))
        if alternate_lines and alternate_mode == "edge-interleave":
            contour_lines = self._edge_interleave_order(contour_lines)
        elif alternate_lines:
            contour_lines = contour_lines[::2] + contour_lines[1::2]

        for coords in contour_lines:
            if len(coords) < 2:
                continue
            start = coords[0]
            commands.append(
                f";PREVIEW_SURFACE_SKIN "
                f"G0X{start[0]:.4f}Y{start[1]:.4f}")
            for point in coords[1:]:
                commands.append(
                    f";PREVIEW_SURFACE_SKIN "
                    f"G1X{point[0]:.4f}Y{point[1]:.4f}S1F1")
        return "\n".join(commands)

    @staticmethod
    def _feedrate_to_velocity(feedrate):
        """Convert controller feedrate from mm/min to velocity in mm/s."""
        feedrate = float(feedrate)
        if not np.isfinite(feedrate) or feedrate <= 0:
            raise ValueError("Engraving feedrate must be greater than 0 mm/min.")
        return feedrate / 60.0

    @classmethod
    def _compensation_distance(cls, feedrate, acceleration):
        """Return d=v^2/(2a), after converting mm/min feedrate to mm/s."""
        velocity = cls._feedrate_to_velocity(feedrate)
        acceleration = cls._validate_acceleration(acceleration)
        return (velocity * velocity) / (2.0 * acceleration)

    @staticmethod
    def _point_within_bounds(point, machine_bounds, tolerance=1e-6):
        if not machine_bounds:
            return True
        min_x, min_y, max_x, max_y = machine_bounds[:4]
        x = float(point[0])
        y = float(point[1])
        if (
            x < float(min_x) - tolerance
            or x > float(max_x) + tolerance
            or y < float(min_y) - tolerance
            or y > float(max_y) + tolerance
        ):
            return False
        bed_shape = machine_bounds[4] if len(machine_bounds) > 4 else "rect"
        if bed_shape != "circle":
            return True

        center_x = (float(min_x) + float(max_x)) / 2.0
        center_y = (float(min_y) + float(max_y)) / 2.0
        radius = min(
            float(max_x) - float(min_x),
            float(max_y) - float(min_y)) / 2.0
        return float(np.hypot(x - center_x, y - center_y)) <= (
            radius + tolerance)

    def _lead_points(self, start, end, feedrate, acceleration,
                     machine_bounds=None):
        dx = float(end[0]) - float(start[0])
        dy = float(end[1]) - float(start[1])
        length = float(np.hypot(dx, dy))
        if length <= 1e-9:
            return start, end
        distance = self._compensation_distance(feedrate, acceleration)
        ux, uy = dx / length, dy / length
        lead_in = (float(start[0]) - ux * distance,
                   float(start[1]) - uy * distance)
        lead_out = (float(end[0]) + ux * distance,
                    float(end[1]) + uy * distance)
        if (
            not self._point_within_bounds(lead_in, machine_bounds)
            or not self._point_within_bounds(lead_out, machine_bounds)
        ):
            raise ValueError(
                "Engraving compensation move exceeds the 96 mm circular "
                f"bed: F{float(feedrate):g} mm/min, "
                f"a={float(acceleration):g} mm/s^2 requires "
                f"{distance:.4f} mm lead-in and lead-out. Reposition or "
                "reduce the model, reduce feedrate, or use the measured "
                "machine acceleration.")
        return lead_in, lead_out

    def _append_compensated_segment_commands(
            self, commands, start, end, settings, acceleration,
            machine_bounds=None):
        speed = int(settings["speed"])
        power = int(settings["power"])
        lead_in, lead_out = self._lead_points(
            start, end, speed, acceleration, machine_bounds)
        commands.append(
            f"G0X{lead_in[0]:.4f}Y{lead_in[1]:.4f}S0")
        commands.append(
            f"G1X{start[0]:.4f}Y{start[1]:.4f}S0F{speed}")
        commands.append("M3")
        commands.append(
            f"G1X{end[0]:.4f}Y{end[1]:.4f}S{power}F{speed}")
        commands.append("S0")
        commands.append(
            f"G1X{lead_out[0]:.4f}Y{lead_out[1]:.4f}S0F{speed}")
        commands.append("M5")

    def _append_compensated_polyline_commands(
            self, commands, points, settings, acceleration,
            machine_bounds=None):
        if len(points) < 2:
            return
        speed = int(settings["speed"])
        power = int(settings["power"])
        lead_in, _ = self._lead_points(
            points[0], points[1], speed, acceleration, machine_bounds)
        _, lead_out = self._lead_points(
            points[-2], points[-1], speed, acceleration, machine_bounds)
        commands.append(
            f"G0X{lead_in[0]:.4f}Y{lead_in[1]:.4f}S0")
        commands.append(
            f"G1X{points[0][0]:.4f}Y{points[0][1]:.4f}S0F{speed}")
        commands.append("M3")
        commands.append(
            f"G1X{points[1][0]:.4f}Y{points[1][1]:.4f}S{power}F{speed}")
        for point in points[2:]:
            commands.append(
                f"G1X{point[0]:.4f}Y{point[1]:.4f}")
        commands.append("S0")
        commands.append(
            f"G1X{lead_out[0]:.4f}Y{lead_out[1]:.4f}S0F{speed}")
        commands.append("M5")

    def _angled_hatch_lines(self, region, angle, pitch, line_count, spacing,
                            require_complete_bundle=True,
                            phase_offset=0.0):
        if region is None or region.is_empty:
            return []

        pitch = max(float(pitch), 1e-6)
        phase_offset = float(phase_offset or 0.0) % pitch
        theta = np.radians(float(angle))
        direction = np.array([np.cos(theta), np.sin(theta)])
        normal = np.array([-direction[1], direction[0]])
        min_x, min_y, max_x, max_y = region.bounds
        corners = np.array([
            [min_x, min_y], [min_x, max_y],
            [max_x, min_y], [max_x, max_y],
        ])
        projections = corners @ normal
        line_length = float(np.hypot(max_x - min_x, max_y - min_y)) + pitch * 4
        center = np.array([(min_x + max_x) / 2, (min_y + max_y) / 2])
        center_projection = float(center @ normal)
        first_offset = (
            np.floor((projections.min() - phase_offset) / pitch)
            * pitch
            + phase_offset)
        last_offset = (
            np.ceil((projections.max() - phase_offset) / pitch)
            * pitch
            + phase_offset)
        base_offsets = np.arange(
            first_offset,
            last_offset + pitch,
            pitch)
        lines = []
        bundle_offsets = self._line_offsets(line_count, spacing)
        min_segment_length = max(float(spacing) * 0.25, 1e-6)

        for base_offset in base_offsets:
            bundle_lines = []
            is_complete_bundle = True

            for bundle_offset in bundle_offsets:
                target_projection = base_offset + bundle_offset
                point = center + normal * (
                    target_projection - center_projection)
                raw_line = LineString([
                    tuple(point - direction * line_length),
                    tuple(point + direction * line_length),
                ])
                clipped_lines = [
                    line for line in self._extract_lines(
                        raw_line.intersection(region))
                    if line.length >= min_segment_length
                ]
                if not clipped_lines:
                    is_complete_bundle = False
                    if require_complete_bundle:
                        break
                    continue
                bundle_lines.extend(clipped_lines)

            if bundle_lines and (is_complete_bundle or not require_complete_bundle):
                lines.extend(bundle_lines)

        return lines

    def _order_hatch_lines(self, lines, reverse_scan=False,
                           optimize_scan_order=True):
        if not lines:
            return []

        ordered = list(reversed(lines)) if reverse_scan else list(lines)
        if not optimize_scan_order:
            return ordered

        optimized = []
        for index, line in enumerate(ordered):
            coords = list(line.coords)
            if len(coords) < 2:
                optimized.append(line)
                continue
            should_reverse = (index % 2 == 1) ^ bool(reverse_scan)
            optimized.append(
                LineString(list(reversed(coords))) if should_reverse else line)
        return optimized

    def _offset_contour_lines(
            self, region, line_count, spacing, include_holes=True):
        out = []
        for index in range(self._normalise_line_count(line_count)):
            offset_region = region.buffer(
                -index * float(spacing), join_style=2, mitre_limit=5)
            for poly in self._extract_polygons(offset_region):
                coords = list(poly.exterior.coords)
                if len(coords) > 2:
                    out.append(coords)
                if not include_holes:
                    continue
                for interior in poly.interiors:
                    coords = list(interior.coords)
                    if len(coords) > 2:
                        out.append(coords)
        return out

    def _extract_lines(self, geometry):
        if geometry.is_empty:
            return []
        if isinstance(geometry, LineString):
            return [geometry]
        if isinstance(geometry, MultiLineString):
            return list(geometry.geoms)
        if isinstance(geometry, GeometryCollection):
            lines = []
            for geom in geometry.geoms:
                lines.extend(self._extract_lines(geom))
            return lines
        return []

    def _extract_polygons(self, geometry):
        if geometry.is_empty:
            return []
        if isinstance(geometry, Polygon):
            return [geometry]
        if isinstance(geometry, MultiPolygon):
            return list(geometry.geoms)
        if isinstance(geometry, GeometryCollection):
            polygons = []
            for geom in geometry.geoms:
                polygons.extend(self._extract_polygons(geom))
            return polygons
        return []

    def _normalise_line_count(self, value):
        return max(1, min(25, int(value or 1)))

    def _auto_contour_line_count_for_region(
            self, region, base_count, spacing, track_width):
        base_count = self._normalise_line_count(base_count)
        if region is None or region.is_empty:
            return base_count

        polygons = [
            polygon for polygon in self._extract_polygons(region)
            if getattr(polygon, "interiors", None)
            and len(polygon.interiors) > 0
        ]
        if not polygons:
            return base_count

        required_count = base_count
        for polygon in polygons:
            for candidate_count in range(base_count, 26):
                contour_width = self._calculate_vector_wall_width(
                    candidate_count,
                    spacing,
                    track_width)
                try:
                    uncovered_shell = polygon.buffer(
                        -contour_width,
                        join_style=2,
                        mitre_limit=5)
                except Exception:
                    continue
                if uncovered_shell is None or uncovered_shell.is_empty:
                    required_count = max(required_count, candidate_count)
                    break
            else:
                required_count = 25

        return self._normalise_line_count(required_count)

    @staticmethod
    def _calculate_vector_wall_width(line_count, spacing, track_width):
        """Return the outer-edge width of parallel vector contour tracks."""
        count = max(1, min(25, int(line_count or 1)))
        center_spacing = max(0.0, float(spacing or 0.0))
        physical_track_width = max(0.01, float(track_width or 0.01))
        return ((count - 1) * center_spacing) + physical_track_width

    def _line_offsets(self, line_count, spacing):
        return [(index - (line_count - 1) / 2) * float(spacing)
                for index in range(self._normalise_line_count(line_count))]

    def _generate_svg_contour(self, img_infill):
        # Unreachable in this build: kept so an upstream revision that starts
        # calling it again still applies cleanly. Imports here, not at module
        # scope -- see the note where the module imports would have been.
        from autotrace import Bitmap, Color, VectorFormat

        img = Image.open(io.BytesIO(img_infill))
        bitmap = Bitmap(np.asarray(img.convert('RGB')))
        vector1 = bitmap.trace(
            error_threshold=0.001, corner_threshold=100, filter_iterations=1,
            remove_adjacent_corners=True, noise_removal=1, color_count=2,
            background_color=Color(0, 0, 0))
        root = ET.fromstring(vector1.encode(VectorFormat.SVG))
        ns = {'svg': 'http://www.w3.org/2000/svg'}
        path_element = root.find(".//svg:path", ns)
        if path_element is not None:
            del path_element.attrib['style']
            path_element.set('stroke', 'black')
            path_element.set('fill', 'none')
        return ET.tostring(root)
