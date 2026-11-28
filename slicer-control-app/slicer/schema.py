"""
slicer/schema.py
The slicing parameters as a table the interface builds itself from.

``params.py`` says what the engine reads and what the values are.  This says
what each one *is*: its label, the group it belongs in, whether it is a number,
a toggle or a choice, its unit, its range, and what it does.  One table, two
slicers -- the gantry and galvo panels are the same widget built twice over the
same rows, which is the only way 220 settings get an interface without being
typed out twice and drifting apart.

**It is extracted, not written.**  Hand-authoring 220 labels would mean
inventing names for machine behaviour someone else already named, and getting
some of them subtly wrong.  So the table is generated from OLOS's own interface:

* **Labels** from the ``<span class="text-bold text-caption">`` captions and
  ``label=`` attributes in ``PrusaSettings.vue`` -- 162 of them.
* **Groups** from the ``q-expansion-item`` each control sits inside, so the
  sections are the ones OLOS's operators already know.
* **Units, ranges and steps** from the ``suffix``, ``min``, ``max`` and ``step``
  attributes on each input.  ``step`` is also what decides whether a field is a
  whole number or a decimal: it is upstream's own statement of intent, and more
  reliable than guessing from whether a default happens to be ``1`` or ``1.0``.
* **Choices** from the ``q-btn-toggle`` option arrays, which carry both the
  value the engine wants and the text to show -- ``{label: '45 deg', value:
  'diagonal'}``.  Where the engine compares against a value the form does not
  offer, that value is added: ``variable_grid_target`` accepts ``both``, and the
  form only ever offered ``hollow``.
* **Help text** from ``PRINTING_SETTINGS_ASSISTANT_MESSAGES`` in
  ``constants.ts``.

Three groups of keys needed work beyond that:

**14 are ours.**  The bed, the parking position, the powder reservoir, the
recoater and the raster resolution.  OLOS hardcodes these in its handler and
never puts them on screen -- which is understandable for one fixed machine, and
useless here, because they are exactly the numbers that differ between its
printer and this one.  They are marked ``origin="ours"``.

**32 are exposed nowhere.**  ``surface_skin_*``, ``internal_floor_support_*``
and a few derived values are read by the engine, defined in ``constants.ts``,
and absent from ``PrusaSettings.vue``.  They are grouped under *Advanced* and
marked ``origin="default"``: the honest statement is that these defaults are the
only values they have ever run with.

**3 are hidden on purpose.**  ``is_hollowing_enabled``, ``is_support_enabled``
and ``is_pad_enabled`` live inside ``v-if="false"`` sections upstream, so they
are always off, and the engine passes them to PrusaSlicer as
``--hollowing-enable=0 --supports-enable=0 --pad-enable=0``.  That is right for
powder-bed fusion -- loose powder supports the part, so SLA supports and pads
have nothing to do here -- and the detail settings behind those flags never
reach the engine at all.  They are kept in the table with ``hidden=True`` so the
reason is recorded rather than the keys just being missing.

One inherited default is deliberately *not* corrected here: ``layer_height`` is
1 mm upstream.  See ``params.LAYER_HEIGHT_MM_RANGE``.
"""

from dataclasses import dataclass, field as _field


@dataclass(frozen=True)
class Field:
    """One setting, and everything the interface needs to render it."""

    #: The engine's key in ``slicing_data``.
    key: str
    #: What to show next to the control.
    label: str
    #: Which section it belongs in. See :data:`GROUPS`.
    group: str
    #: "int", "float", "bool", "choice" or "tuple".
    kind: str
    #: The value it starts at, in the engine's units.
    default: object
    unit: str = None
    minimum: float = None
    maximum: float = None
    step: float = None
    #: For "choice": a tuple of ``(value, label)`` pairs.
    choices: tuple = None
    tooltip: str = None
    #: True for the settings OLOS deliberately hides. Not for the interface.
    hidden: bool = False
    #: True where the engine wants a fraction and operators think in percent:
    #: show ``value * 100`` with a % suffix and divide on the way back.
    percent: bool = False
    #: Where this row came from: "olos" (a labelled control upstream),
    #: "derived" (a control with no caption, so named from its key), "ours"
    #: (written here, because upstream hardcodes it), or "default" (exposed
    #: nowhere upstream either).
    origin: str = "olos"

    @property
    def editable(self) -> bool:
        return not self.hidden


#: Sections in the order the interface should lay them out: the machine first,
#: because on this printer those are the numbers that are not yet trusted, then
#: the settings that shape a part, then the ones nobody should need.
GROUPS = (
    ('General',
     'Top-level switches, which decide what the rest of the settings mean. '
     '"Solid Object" is the big one: it replaces contour-plus-infill with '
     'scanning the part as filled geometry, so Model Settings applies instead '
     'of Infill Settings.'),
    ('Machine',
     'The bed the part is placed on. Scan coordinates are centred on it.'),
    ('Machine Routine',
     'What happens between layers: park, feed powder, step the piston, sweep the blade.'),
    ('Layer Settings',
     'Layer height and the speed the piston moves between layers.'),
    ('Contour/Wall Settings',
     'The outline of each layer.'),
    ('Infill Settings',
     'Filling the interior: hatching, spacing, rotation, skins.'),
    ('Engraving Infill',
     'Raster-style interior fill, as an alternative to vector hatching.'),
    ('Per-Zone Exposure',
     'Different power and speed for the first layers and an optional middle band.'),
    ('Preparation',
     'Anchor pad and the transition from it into the body.'),
    ('Model Settings',
     'Solid mode: scanning the part as filled geometry rather than contour-plus-infill.'),
    ('Raster',
     'How finely each layer is rendered before its paths are traced.'),
    ('Advanced',
     'Read by the engine but never exposed by OLOS either. Defaults only.'),
    ('Hollowing Settings',
     "PrusaSlicer's own hollowing. Hidden: only the on/off flag reaches it."),
    ('Support Settings',
     "PrusaSlicer's own supports. Hidden: the powder bed supports the part."),
    ('Pad Settings',
     "PrusaSlicer's own pad. Hidden: only the on/off flag reaches it."),
)

#: Every parameter, in key order.
FIELDS = (
    Field(key='auto_contour_line_count_enabled', label='Auto body contour shell fill', group='Contour/Wall Settings', kind='bool', default=False),
    Field(key='bed_height', label='Bed Depth', group='Machine', kind='float', default=None, unit='mm', tooltip="The bed's Y extent. Left empty, the engine uses the width, giving a square bed.", origin='ours'),
    Field(key='bed_offset_x', label='Bed Offset X', group='Machine Routine', kind='float', default=-238.5, unit='mm'),
    Field(key='bed_offset_y', label='Bed Offset Y', group='Machine Routine', kind='float', default=-158, unit='mm'),
    Field(key='bed_width', label='Bed Width', group='Machine', kind='float', default=96, unit='mm', tooltip="The powder bed's X extent. Scan paths are centred on it, and the galvo retargeting centres this width inside the 250 mm field.", origin='ours'),
    Field(key='body_engraving_enabled', label='Body Engraving Enabled', group='Advanced', kind='bool', default=False, tooltip="Not exposed in OLOS's own interface either, so this default is the only value it has ever run with.", origin='default'),
    Field(key='contour_infill_overlap', label='Contour/Infill Overlap', group='Infill Settings', kind='float', default=0.15, unit='%', minimum=-100, maximum=100, percent=True),
    Field(key='contour_line_count', label='Contour Line Count', group='Contour/Wall Settings', kind='int', default=3, minimum=1, maximum=25),
    Field(key='contour_power', label='Contour/Wall Power', group='Contour/Wall Settings', kind='int', default=50, unit='%', minimum=0, tooltip='Laser power for the contour/wall'),
    Field(key='contour_speed', label='Contour/Wall Speed', group='Contour/Wall Settings', kind='float', default=3000, unit='mm/min', minimum=0, tooltip='The feed rate/speed for the machine to execute the contour/wall'),
    Field(key='engraving_acceleration', label='XY Gantry Acceleration', group='General', kind='int', default=500, unit='mm/s^2', minimum=1, step=10, tooltip='Used to calculate laser-off lead-in and lead-out distance: d = (F / 60)^2 / (2a).'),
    Field(key='engraving_contour_thickness', label='Manual Contour Shell Width', group='Engraving Infill', kind='float', default=0.8, unit='mm', minimum=0.1, maximum=10, step=0.1),
    Field(key='engraving_grid_line_thickness', label='Engraved grid member width', group='Engraving Infill', kind='float', default=0.8, unit='mm', minimum=0.1, step=0.1),
    Field(key='engraving_grid_spacing', label='Engraved grid pitch', group='Engraving Infill', kind='float', default=2, unit='mm', minimum=0.2, step=0.1),
    Field(key='engraving_mode', label='Remaining Layers Mode', group='General', kind='choice', default='vectoring', choices=(('vectoring', 'Vectoring'), ('engraving', 'Engraving')), tooltip='Selects the mode used after the first and intermediate zones. Those zones can independently override this mode below.'),
    Field(key='engraving_raster_direction', label='Engraving Raster Direction', group='Engraving Infill', kind='choice', default='x', choices=(('x', 'Raster X'), ('y', 'Raster Y')), origin='derived'),
    Field(key='fill_enclosed_hollows', label='Engraved interior grid fill', group='Engraving Infill', kind='bool', default=False),
    Field(key='first_contour_power', label='First Layer Contour Power', group='Per-Zone Exposure', kind='int', default=55, unit='%'),
    Field(key='first_contour_speed', label='First Layer Contour Speed', group='Per-Zone Exposure', kind='float', default=2000, unit='mm/min'),
    Field(key='first_engraving_hatch_spacing', label='First-layer engraving hatch spacing', group='Engraving Infill', kind='float', default=0.2, unit='mm', minimum=0.01, step=0.05),
    Field(key='first_infill_power', label='First Infill Power', group='Per-Zone Exposure', kind='int', default=55, unit='%', origin='derived'),
    Field(key='first_infill_speed', label='First Infill Speed', group='Per-Zone Exposure', kind='float', default=2000, unit='mm/min', origin='derived'),
    Field(key='first_layers_count', label='Number of First Layers', group='Per-Zone Exposure', kind='int', default=1, minimum=0, tooltip='Number of bottom layers using the FIRST exposure values. Set to 0 to disable this zone.'),
    Field(key='first_zone_engraving_enabled', label='Engrave First Layers', group='Per-Zone Exposure', kind='bool', default=False),
    Field(key='flat_surface_skin_boundary_mode', label='Flat Surface Skin Boundary Mode', group='Infill Settings', kind='choice', default='engrave', choices=(('engrave', 'Engrave Shell'), ('vector', 'Vector Shell'), ('contour', 'Contour Shell'), ('none', 'No Shell')), origin='derived'),
    Field(key='flat_surface_skin_contour_line_count', label='Roof contour lines', group='Infill Settings', kind='int', default=3, minimum=1, step=1),
    Field(key='flat_surface_skin_contour_overlap', label='Roof contour overlap', group='Infill Settings', kind='float', default=0.15, unit='%', minimum=-100, maximum=100, step=1, percent=True),
    Field(key='flat_surface_skin_cross_hatch', label='Cross-hatch flat roof layers', group='Infill Settings', kind='bool', default=False),
    Field(key='flat_surface_skin_hatch_overlap', label='Roof line overlap', group='Infill Settings', kind='float', default=0.0, unit='%', minimum=-100, maximum=95, step=1, percent=True),
    Field(key='flat_surface_skin_hatch_spacing', label='Flat Surface Skin Hatch Spacing', group='Advanced', kind='float', default=0.2, tooltip="Not exposed in OLOS's own interface either, so this default is the only value it has ever run with.", origin='default'),
    Field(key='flat_surface_skin_layers', label='Flat layers', group='Infill Settings', kind='int', default=3, minimum=1, step=1),
    Field(key='flat_surface_skin_pattern', label='Flat Surface Skin Pattern', group='Infill Settings', kind='choice', default='hatch', choices=(('hatch', 'Hatch'), ('spiral', 'Spiral')), origin='derived'),
    Field(key='flat_surface_skin_power', label='Flat roof hatch power', group='Infill Settings', kind='int', default=50, unit='%', minimum=0, maximum=100),
    Field(key='flat_surface_skin_raster_direction', label='Flat Roof Hatch', group='Infill Settings', kind='choice', default='x', choices=(('x', 'X'), ('y', 'Y'), ('diagonal', '45 deg'))),
    Field(key='flat_surface_skin_roof_only_layers', label='Flat Surface Skin Roof Only Layers', group='Advanced', kind='bool', default=False, tooltip="Not exposed in OLOS's own interface either, so this default is the only value it has ever run with.", origin='default'),
    Field(key='flat_surface_skin_second_pass_boundary_mode', label='Flat Surface Skin Second Pass Boundary Mode', group='Infill Settings', kind='choice', default='engrave', choices=(('engrave', 'Pass 2 Engrave Shell'), ('vector', 'Pass 2 Vector Shell'), ('contour', 'Pass 2 Contour Shell'), ('none', 'Pass 2 No Shell')), origin='derived'),
    Field(key='flat_surface_skin_second_pass_contour_line_count', label='Pass 2 contour lines', group='Infill Settings', kind='int', default=3, minimum=1, step=1),
    Field(key='flat_surface_skin_second_pass_contour_overlap', label='Pass 2 contour overlap', group='Infill Settings', kind='float', default=0.15, unit='%', minimum=-100, maximum=100, step=1, percent=True),
    Field(key='flat_surface_skin_second_pass_cross_hatch', label='Cross-hatch flat roof second pass', group='Infill Settings', kind='bool', default=False),
    Field(key='flat_surface_skin_second_pass_enabled', label='Flat roof second pass', group='Infill Settings', kind='bool', default=False),
    Field(key='flat_surface_skin_second_pass_hatch_overlap', label='Pass 2 line overlap', group='Infill Settings', kind='float', default=0.0, unit='%', minimum=-100, maximum=95, step=1, percent=True),
    Field(key='flat_surface_skin_second_pass_power', label='Pass 2 power', group='Infill Settings', kind='int', default=50, unit='%', minimum=0, maximum=100),
    Field(key='flat_surface_skin_second_pass_raster_direction', label='Flat Surface Skin Second Pass Raster Direction', group='Infill Settings', kind='choice', default='x', choices=(('x', 'Pass 2 X'), ('y', 'Pass 2 Y'), ('diagonal', 'Pass 2 45 deg')), origin='derived'),
    Field(key='flat_surface_skin_second_pass_speed', label='Pass 2 feed rate', group='Infill Settings', kind='int', default=3000, unit='mm/min', minimum=1, step=100),
    Field(key='flat_surface_skin_speed', label='Flat roof hatch feedrate', group='Infill Settings', kind='int', default=3000, unit='mm/min', minimum=1, step=100),
    Field(key='flat_surface_skin_spiral_direction', label='Flat Surface Skin Spiral Direction', group='Infill Settings', kind='choice', default='outside-in', choices=(('outside-in', 'Outside In'), ('inside-out', 'Inside Out')), origin='derived'),
    Field(key='grid_clearance', label='Shared grid crossing gap', group='Engraving Infill', kind='float', default=0.2, unit='mm', minimum=0, step=0.01),
    Field(key='grid_spacing', label='Normal Grid Pitch', group='Infill Settings', kind='float', default=2, unit='mm', minimum=0.2, step=0.1),
    Field(key='hatch_rotation_group_layers', label='Layers per Direction', group='Infill Settings', kind='choice', default=5, choices=((1, '1'), (2, '2'), (3, '3'), (4, '4'), (5, '5'), (6, '6'), (8, '8'), (10, '10'))),
    Field(key='hatch_rotation_grouping_enabled', label='Group Hatch Directions', group='Infill Settings', kind='bool', default=False),
    Field(key='hatch_rotation_per_layer', label='Normal Hatch Rotation per Layer', group='Infill Settings', kind='float', default=67, unit='deg', minimum=0),
    Field(key='infill_angle', label='Normal Hatch Angle', group='Infill Settings', kind='float', default=0, unit='deg', minimum=0),
    Field(key='infill_hatch_spacing', label='Normal Hatch Bundle Spacing', group='Infill Settings', kind='float', default=1, unit='mm', minimum=0.2, step=0.1),
    Field(key='infill_image_pixel_size', label='Infill Image Pixel Size', group='Advanced', kind='tuple', default=(1, 1), tooltip="Not exposed in OLOS's own interface either, so this default is the only value it has ever run with.", origin='default'),
    Field(key='infill_image_size', label='Infill Image Size', group='Advanced', kind='tuple', default=(1, 1), tooltip="Not exposed in OLOS's own interface either, so this default is the only value it has ever run with.", origin='default'),
    Field(key='infill_line_count', label='Lines per Hatch Bundle', group='Infill Settings', kind='int', default=3, minimum=1, maximum=10),
    Field(key='infill_line_thickness', label='Infill Line Thickness', group='Advanced', kind='float', default=0.8, tooltip="Not exposed in OLOS's own interface either, so this default is the only value it has ever run with.", origin='default'),
    Field(key='infill_noise', label='Infill Noise', group='Advanced', kind='int', default=5, tooltip="Not exposed in OLOS's own interface either, so this default is the only value it has ever run with.", origin='default'),
    Field(key='infill_power', label='Infill Power', group='Infill Settings', kind='int', default=50, unit='%', minimum=0, tooltip="Maximum laser power for layer's infill", origin='derived'),
    Field(key='infill_speed', label='Infill image pixel size', group='Infill Settings', kind='float', default=3000, unit='mm/min', minimum=0, tooltip='The feed rate/speed for the machine to execute the infill'),
    Field(key='infill_type', label='Infill Type', group='Infill Settings', kind='choice', default='Lines', choices=(('Lines', 'Lines'), ('Grid', 'Grid'))),
    Field(key='intermediate_contour_power', label='Intermediate Contour Power', group='Per-Zone Exposure', kind='int', default=45, unit='%'),
    Field(key='intermediate_contour_speed', label='Intermediate Contour Speed', group='Per-Zone Exposure', kind='float', default=2500, unit='mm/min'),
    Field(key='intermediate_engraving_hatch_spacing', label='Intermediate engraving hatch spacing', group='Engraving Infill', kind='float', default=0.2, unit='mm', minimum=0.01, step=0.05),
    Field(key='intermediate_infill_power', label='Intermediate Infill Power', group='Per-Zone Exposure', kind='int', default=45, unit='%', origin='derived'),
    Field(key='intermediate_infill_speed', label='Intermediate Infill Speed', group='Per-Zone Exposure', kind='float', default=2500, unit='mm/min', origin='derived'),
    Field(key='intermediate_layers_count', label='Number of Intermediate Layers', group='Per-Zone Exposure', kind='int', default=2, minimum=0),
    Field(key='intermediate_zone_enabled', label='Enable', group='Per-Zone Exposure', kind='bool', default=False),
    Field(key='intermediate_zone_engraving_enabled', label='Engrave Intermediate Layers', group='Per-Zone Exposure', kind='bool', default=False),
    Field(key='internal_floor_support_angle', label='Internal Floor Support Angle', group='Advanced', kind='int', default=45, tooltip="Not exposed in OLOS's own interface either, so this default is the only value it has ever run with.", origin='default'),
    Field(key='internal_floor_support_boundary_clearance', label='Internal Floor Support Boundary Clearance', group='Advanced', kind='int', default=0, tooltip="Not exposed in OLOS's own interface either, so this default is the only value it has ever run with.", origin='default'),
    Field(key='internal_floor_support_enabled', label='Internal Floor Support Enabled', group='Preparation', kind='bool', default=False, origin='derived'),
    Field(key='internal_floor_support_grid_clearance', label='Internal Floor Support Grid Clearance', group='Advanced', kind='float', default=0.2, tooltip="Not exposed in OLOS's own interface either, so this default is the only value it has ever run with.", origin='default'),
    Field(key='internal_floor_support_grid_spacing', label='Internal Floor Support Grid Spacing', group='Advanced', kind='int', default=2, tooltip="Not exposed in OLOS's own interface either, so this default is the only value it has ever run with.", origin='default'),
    Field(key='internal_floor_support_group_layers', label='Internal Floor Support Group Layers', group='Advanced', kind='int', default=1, tooltip="Not exposed in OLOS's own interface either, so this default is the only value it has ever run with.", origin='default'),
    Field(key='internal_floor_support_grouping_enabled', label='Internal Floor Support Grouping Enabled', group='Advanced', kind='bool', default=False, tooltip="Not exposed in OLOS's own interface either, so this default is the only value it has ever run with.", origin='default'),
    Field(key='internal_floor_support_hatch_spacing', label='Internal Floor Support Hatch Spacing', group='Advanced', kind='int', default=1, tooltip="Not exposed in OLOS's own interface either, so this default is the only value it has ever run with.", origin='default'),
    Field(key='internal_floor_support_line_count', label='Internal Floor Support Line Count', group='Advanced', kind='int', default=3, tooltip="Not exposed in OLOS's own interface either, so this default is the only value it has ever run with.", origin='default'),
    Field(key='internal_floor_support_line_overlap', label='Internal Floor Support Line Overlap', group='Advanced', kind='int', default=0, tooltip="Not exposed in OLOS's own interface either, so this default is the only value it has ever run with.", origin='default'),
    Field(key='internal_floor_support_minimum_area', label='Internal Floor Support Minimum Area', group='Advanced', kind='float', default=0.1, tooltip="Not exposed in OLOS's own interface either, so this default is the only value it has ever run with.", origin='default'),
    Field(key='internal_floor_support_mode', label='Internal Floor Support Mode', group='Advanced', kind='choice', default='vectoring', choices=(('vectoring', 'Vectoring'), ('engraving', 'Engraving')), tooltip="Not exposed in OLOS's own interface either, so this default is the only value it has ever run with.", origin='default'),
    Field(key='internal_floor_support_optimize_scan_order', label='Internal Floor Support Optimize Scan Order', group='Advanced', kind='bool', default=True, tooltip="Not exposed in OLOS's own interface either, so this default is the only value it has ever run with.", origin='default'),
    Field(key='internal_floor_support_power', label='Internal Floor Support Power', group='Advanced', kind='int', default=50, tooltip="Not exposed in OLOS's own interface either, so this default is the only value it has ever run with.", origin='default'),
    Field(key='internal_floor_support_rotation_per_layer', label='Internal Floor Support Rotation Per Layer', group='Advanced', kind='int', default=67, tooltip="Not exposed in OLOS's own interface either, so this default is the only value it has ever run with.", origin='default'),
    Field(key='internal_floor_support_speed', label='Internal Floor Support Speed', group='Advanced', kind='int', default=3000, tooltip="Not exposed in OLOS's own interface either, so this default is the only value it has ever run with.", origin='default'),
    Field(key='internal_floor_support_track_width', label='Internal Floor Support Track Width', group='Advanced', kind='float', default=0.2, tooltip="Not exposed in OLOS's own interface either, so this default is the only value it has ever run with.", origin='default'),
    Field(key='internal_floor_support_type', label='Internal Floor Support Type', group='Advanced', kind='choice', default='Lines', choices=(('Lines', 'Lines'), ('Grid', 'Grid')), tooltip="Not exposed in OLOS's own interface either, so this default is the only value it has ever run with.", origin='default'),
    Field(key='is_hollowing_enabled', label='Hollowing', group='Hollowing Settings', kind='bool', default=False, hidden=True),
    Field(key='is_pad_enabled', label='Pad', group='Pad Settings', kind='bool', default=False, hidden=True),
    Field(key='is_support_enabled', label='Support', group='Support Settings', kind='bool', default=False, hidden=True),
    Field(key='layer_height', label='Layer Height', group='Layer Settings', kind='float', default=1, unit='mm', minimum=0, step=0.1, tooltip='Layer height to slice the 3D model'),
    Field(key='layer_speed', label='Layer Z Travel Speed', group='Layer Settings', kind='float', default=1000, unit='mm/min', minimum=0, tooltip='Navigating feed rate/speed between layers'),
    Field(key='line_bundle_spacing', label='Line Bundle Spacing', group='Infill Settings', kind='float', default=0.2, unit='mm', minimum=0.01, maximum=5, step=0.01, origin='derived'),
    Field(key='match_engraved_shell_to_vector_wall', label='Use calculated bundle width for engraved shell', group='Infill Settings', kind='bool', default=True),
    Field(key='multi_variable_grid_enabled', label='Add second variable grid band', group='Infill Settings', kind='bool', default=False),
    Field(key='no_routine_first_layers', label='No-Routine First Layers', group='Machine Routine', kind='int', default=0, minimum=0, tooltip='How many of the first N layers should be printed WITHOUT the powder-feed / recoater routine in between. Used to repeatedly expose the first layer for better adhesion. Set to 0 to apply the routine between every layer.'),
    Field(key='nominal_vector_track_width', label='Nominal Vector Track Width', group='Infill Settings', kind='float', default=0.2, unit='mm', minimum=0.01, maximum=2, step=0.01, origin='derived'),
    Field(key='optimize_scan_order', label='Optimize Hatch Scan Order', group='Infill Settings', kind='bool', default=True),
    Field(key='parking_feed', label='Parking Feed Rate', group='Machine Routine', kind='float', default=10000, unit='mm/min', tooltip='How fast the gantry travels to the parking position and back.', origin='ours'),
    Field(key='parking_x', label='Parking X', group='Machine Routine', kind='float', default=-395.5, unit='mm', tooltip='Where the gantry parks before each recoat, in machine coordinates. Negative, because machine zero sits at the far limit.', origin='ours'),
    Field(key='parking_y', label='Parking Y', group='Machine Routine', kind='float', default=-4.5, unit='mm', tooltip="The parking position's Y, in machine coordinates.", origin='ours'),
    Field(key='powder_feed', label='Reservoir Lift per Layer', group='Machine Routine', kind='float', default=-0.3, unit='mm', tooltip='How far the powder reservoir rises each layer to present fresh powder. Negative is up on the A axis.', origin='ours'),
    Field(key='powder_feed_rate', label='Reservoir Feed Rate', group='Machine Routine', kind='float', default=50, unit='mm/min', tooltip='How fast the reservoir axis moves. Slow by design: it is lifting loose powder.', origin='ours'),
    Field(key='recoater_feed', label='Recoater Feed Rate', group='Machine Routine', kind='float', default=3000, unit='mm/min', tooltip='How fast the blade sweeps powder across the bed.', origin='ours'),
    Field(key='recoater_home', label='Recoater Home', group='Machine Routine', kind='float', default=-54.5, unit='mm', tooltip='Where the blade returns to after a sweep, in machine coordinates.', origin='ours'),
    Field(key='recoater_out', label='Recoater Throw', group='Machine Routine', kind='float', default=-405, unit='mm', tooltip='How far out the blade travels, in machine coordinates, to spread a layer.', origin='ours'),
    Field(key='recoater_return_bed_drop_distance', label='Bed Dip Distance', group='Machine Routine', kind='float', default=0.2, unit='mm', minimum=0.001, step=0.01),
    Field(key='recoater_return_bed_drop_enabled', label='Return-Pass Bed Clearance', group='Machine Routine', kind='bool', default=False, tooltip='After the forward recoater stroke, dip the build bed for the return stroke, then restore scan height with a backlash-safe final approach.'),
    Field(key='recoater_return_bed_feed_rate', label='Bed Z Feed Rate', group='Machine Routine', kind='int', default=50, unit='mm/min', minimum=1, step=10),
    Field(key='roof_hollow_infill_enabled', label='Fill roofed internal hollows', group='Infill Settings', kind='bool', default=False),
    Field(key='roof_hollow_infill_overlap', label='Hollow infill overlap', group='Infill Settings', kind='float', default=0.15, unit='%', minimum=-100, maximum=100, step=1, percent=True),
    Field(key='scanline_spacing', label='Scanline Spacing', group='Infill Settings', kind='float', default=0.2, unit='mm', minimum=0.01, step=0.05, origin='derived'),
    Field(key='secondary_variable_engraving_grid_line_thickness', label='Secondary Variable Engraving Grid Line Thickness', group='Advanced', kind='float', default=0.8, tooltip="Not exposed in OLOS's own interface either, so this default is the only value it has ever run with.", origin='default'),
    Field(key='secondary_variable_engraving_grid_spacing', label='Band 2 engraved/hollow grid pitch', group='Infill Settings', kind='float', default=1, unit='mm', minimum=0.2, step=0.1),
    Field(key='secondary_variable_grid_clearance', label='Band 2 grid crossing gap', group='Infill Settings', kind='float', default=0.2, unit='mm', minimum=0, step=0.01),
    Field(key='secondary_variable_grid_hatch_spacing', label='Band 2 vector hatch spacing', group='Infill Settings', kind='float', default=1, unit='mm', minimum=0.2, step=0.1),
    Field(key='secondary_variable_grid_layers_before_roof', label='Band 2 layers before roof', group='Infill Settings', kind='int', default=6, minimum=1, step=1),
    Field(key='secondary_variable_grid_line_count', label='Band 2 lines per grid member', group='Infill Settings', kind='int', default=3, minimum=1, step=1),
    Field(key='secondary_variable_grid_spacing', label='Band 2 vector grid pitch', group='Infill Settings', kind='float', default=1, unit='mm', minimum=0.2, step=0.1),
    Field(key='slanted_surface_skin_cross_hatch', label='Cross-hatch slanted roof layers', group='Infill Settings', kind='bool', default=False),
    Field(key='slanted_surface_skin_enabled', label='Enable slanted roof hatch', group='Infill Settings', kind='bool', default=True),
    Field(key='slanted_surface_skin_hatch_spacing', label='Slanted hatch line spacing', group='Infill Settings', kind='float', default=0.2, unit='mm', minimum=0.01, step=0.05),
    Field(key='slanted_surface_skin_layers', label='Slanted layers', group='Infill Settings', kind='int', default=3, minimum=1, step=1),
    Field(key='slanted_surface_skin_power', label='Slanted hatch power', group='Infill Settings', kind='int', default=50, unit='%', minimum=0, maximum=100),
    Field(key='slanted_surface_skin_raster_direction', label='Slanted Surface Skin Raster Direction', group='Infill Settings', kind='choice', default='diagonal', choices=(('x', 'X'), ('y', 'Y'), ('diagonal', '45 deg')), origin='derived'),
    Field(key='slanted_surface_skin_speed', label='Slanted hatch feedrate', group='Infill Settings', kind='int', default=3000, unit='mm/min', minimum=1, step=100),
    Field(key='slice_max_pixels', label='Maximum Mask Size', group='Raster', kind='int', default=4096, unit='px', tooltip='A ceiling on the rendered bitmap, bounding memory and time on a large part.', origin='ours'),
    Field(key='slice_min_pixels', label='Minimum Mask Size', group='Raster', kind='int', default=256, unit='px', tooltip='A floor on the rendered bitmap, so a small part is not traced from too few pixels.', origin='ours'),
    Field(key='slice_padding_mm', label='Mask Padding', group='Raster', kind='float', default=None, unit='mm', tooltip='Blank margin left around the part in the bitmap, so geometry never touches the edge where contour tracing would lose the boundary. Empty means the engine computes it.', origin='ours'),
    Field(key='slice_pixels_per_mm', label='Mask Resolution', group='Raster', kind='float', default=15, unit='px/mm', tooltip='The engine renders each layer to a bitmap at this resolution and traces contours in it, so this sets the finest feature it can resolve. Cost rises with the square.', origin='ours'),
    Field(key='solid_alternate_lines', label='Alternate Solid Paths', group='Model Settings', kind='bool', default=True),
    Field(key='solid_alternate_mode', label='Solid Alternate Mode', group='Model Settings', kind='choice', default='edge-interleave', choices=(('two-pass', 'Two Passes'), ('edge-interleave', 'Edge Interleave')), origin='derived'),
    Field(key='solid_anchor_pad_enabled', label='Sacrificial Anchor Pad', group='Preparation', kind='bool', default=False, tooltip='Adds engraved square layers beneath every disconnected model before the model itself begins.'),
    Field(key='solid_anchor_pad_layers', label='Anchor Pad Layers', group='Preparation', kind='int', default=1, minimum=1, step=1),
    Field(key='solid_anchor_pad_line_overlap', label='Anchor Pad Line Overlap', group='Preparation', kind='int', default=0, unit='%', minimum=-100, maximum=95, step=1, tooltip='Zero places adjacent engraved lines one estimated track width apart.'),
    Field(key='solid_anchor_pad_power', label='Anchor Pad Power', group='Preparation', kind='int', default=50, unit='%', minimum=0, maximum=100),
    Field(key='solid_anchor_pad_rotate_each_layer', label='Rotate Each Layer', group='Preparation', kind='bool', default=False, tooltip='Starts from the model pattern direction and rotates each following anchor-pad layer by the selected angle.'),
    Field(key='solid_anchor_pad_rotation_per_layer', label='Solid Anchor Pad Rotation Per Layer', group='Preparation', kind='int', default=90, unit='deg/layer', minimum=-180, maximum=180, step=1, origin='derived'),
    Field(key='solid_anchor_pad_size', label='Square Size', group='Preparation', kind='float', default=20, unit='mm x mm', minimum=0.01, step=0.1),
    Field(key='solid_anchor_pad_speed', label='Anchor Pad Feed Rate', group='Preparation', kind='float', default=3000, unit='mm/min', minimum=1),
    Field(key='solid_close_top_bottom_enabled', label='Close Top & Bottom', group='Model Settings', kind='bool', default=False),
    Field(key='solid_close_top_bottom_layers', label='Line-only Layers per Face', group='Model Settings', kind='int', default=3, minimum=1, step=1, tooltip='The first and final layers fill to the object boundary using lines only. Middle layers keep the configured contour.'),
    Field(key='solid_close_top_bottom_line_overlap', label='Closure Line Overlap', group='Model Settings', kind='int', default=0, unit='%', minimum=-100, maximum=95, step=1, tooltip='Zero places adjacent closure tracks one estimated track width apart.'),
    Field(key='solid_close_top_bottom_power', label='Closure Power', group='Model Settings', kind='int', default=50, unit='%', minimum=0, maximum=100),
    Field(key='solid_close_top_bottom_speed', label='Closure Feed Rate', group='Model Settings', kind='float', default=3000, unit='mm/min', minimum=1),
    Field(key='solid_contour_enabled', label='Enable Vectored Contour', group='Model Settings', kind='bool', default=True),
    Field(key='solid_contour_line_count', label='Number of Contour Lines', group='Model Settings', kind='int', default=3, minimum=1, maximum=25, step=1),
    Field(key='solid_contour_overlap', label='Overlap with Contour', group='Model Settings', kind='int', default=0, unit='%', minimum=-100, maximum=100, step=1),
    Field(key='solid_contour_power', label='Contour Power', group='Model Settings', kind='int', default=50, unit='%', minimum=0, maximum=100),
    Field(key='solid_contour_speed', label='Contour Feed Rate', group='Model Settings', kind='float', default=3000, unit='mm/min', minimum=1),
    Field(key='solid_cross_hatch', label='Cross Hatch Next Layer', group='Model Settings', kind='bool', default=False),
    Field(key='solid_first_engraving_hatch_spacing', label='Distance Between Engraved Lines', group='Model Settings', kind='float', default=0.2, unit='mm', minimum=0.001, step=0.01),
    Field(key='solid_first_layers_count', label='Number of Layers', group='Model Settings', kind='int', default=1, minimum=1, step=1),
    Field(key='solid_first_power', label='Initial Pattern Power', group='Model Settings', kind='int', default=50, unit='%', minimum=0, maximum=100),
    Field(key='solid_first_speed', label='Initial Pattern Feed Rate', group='Model Settings', kind='float', default=3000, unit='mm/min', minimum=1),
    Field(key='solid_first_zone_enabled', label='Enable First Zone', group='Model Settings', kind='bool', default=False),
    Field(key='solid_first_zone_engraving_enabled', label='Method', group='Model Settings', kind='bool', default=True),
    Field(key='solid_intermediate_engraving_hatch_spacing', label='Distance Between Engraved Lines', group='Model Settings', kind='float', default=0.2, unit='mm', minimum=0.001, step=0.01),
    Field(key='solid_intermediate_layers_count', label='Number of Layers', group='Model Settings', kind='int', default=1, minimum=1, step=1),
    Field(key='solid_intermediate_power', label='Intermediate Pattern Power', group='Model Settings', kind='int', default=50, unit='%', minimum=0, maximum=100),
    Field(key='solid_intermediate_speed', label='Intermediate Pattern Feed Rate', group='Model Settings', kind='float', default=3000, unit='mm/min', minimum=1),
    Field(key='solid_intermediate_zone_enabled', label='Enable Intermediate Zone', group='Model Settings', kind='bool', default=False),
    Field(key='solid_intermediate_zone_engraving_enabled', label='Method', group='Model Settings', kind='bool', default=True),
    Field(key='solid_island_scan_strategy', label='Island Line Strategy', group='Model Settings', kind='choice', default='edge-interleave', choices=(('normal', 'Normal'), ('two-pass', 'Two Passes'), ('edge-interleave', 'Edge Interleave'))),
    Field(key='solid_island_scanning_enabled', label='Island Scanning', group='Model Settings', kind='bool', default=False, tooltip='Completes one fitted section of the solid layer before moving to the next. Actual island dimensions are adjusted so a whole number fits along each object axis.'),
    Field(key='solid_island_size', label='Requested Island Size', group='Model Settings', kind='float', default=3, unit='mm x mm', minimum=0.01, step=0.1),
    Field(key='solid_line_overlap', label='Overlap Between Lines', group='Model Settings', kind='int', default=0, unit='%', minimum=-100, maximum=95, step=1, tooltip='Zero places adjacent tracks one estimated track width apart.'),
    Field(key='solid_merge_objects_by_layer', label='Merge Objects by Layer', group='Model Settings', kind='bool', default=True, tooltip='Completes the current layer for every disconnected solid before running the layer-change routine.'),
    Field(key='solid_mode_enabled', label='Solid Object', group='General', kind='bool', default=False),
    Field(key='solid_power', label='Power', group='Model Settings', kind='int', default=50, unit='%', minimum=0, maximum=100),
    Field(key='solid_raster_direction', label='Pattern Direction', group='Model Settings', kind='choice', default='x', choices=(('x', 'X'), ('y', 'Y'), ('diagonal', '45 deg'))),
    Field(key='solid_speed', label='Feed Rate', group='Model Settings', kind='float', default=3000, unit='mm/min', minimum=1),
    Field(key='solid_track_width', label='Estimated Track Width', group='Model Settings', kind='float', default=0.2, unit='mm', minimum=0.01, maximum=2, step=0.01, tooltip='Estimated physical width of one solid-pattern laser track.'),
    Field(key='solid_transition_contour_enabled', label='Transition Contour', group='Preparation', kind='bool', default=True),
    Field(key='solid_transition_contour_line_count', label='Contour Lines', group='Preparation', kind='int', default=1, minimum=1, maximum=25, step=1),
    Field(key='solid_transition_contour_overlap', label='Contour Overlap', group='Preparation', kind='int', default=0, unit='%', minimum=-100, maximum=100, step=1),
    Field(key='solid_transition_contour_power', label='Contour Power', group='Preparation', kind='int', default=50, unit='%', minimum=0, maximum=100),
    Field(key='solid_transition_contour_speed', label='Contour Feed Rate', group='Preparation', kind='float', default=3000, unit='mm/min', minimum=1),
    Field(key='solid_transition_enabled', label='Solid Transition Enabled', group='Preparation', kind='bool', default=False, origin='derived'),
    Field(key='solid_transition_grid_clearance', label='Grid Crossing Clearance', group='Preparation', kind='float', default=0.2, unit='mm', minimum=0, step=0.05),
    Field(key='solid_transition_grid_spacing', label='Grid Spacing', group='Preparation', kind='float', default=1, unit='mm', minimum=0.01, step=0.1),
    Field(key='solid_transition_hatch_spacing', label='Hatch Spacing', group='Preparation', kind='float', default=1, unit='mm', minimum=0.01, step=0.1),
    Field(key='solid_transition_layers', label='Transition Layers', group='Preparation', kind='int', default=1, minimum=1, maximum=1000, step=1),
    Field(key='solid_transition_line_count', label='Lines per Hatch', group='Preparation', kind='int', default=1, minimum=1, maximum=25, step=1),
    Field(key='solid_transition_line_overlap', label='Line Overlap', group='Preparation', kind='int', default=0, unit='%', minimum=-100, maximum=95, step=1, tooltip='Zero places lines in a hatch bundle one estimated track width apart.'),
    Field(key='solid_transition_pattern', label='Transition Pattern', group='Preparation', kind='choice', default='lines', choices=(('lines', 'Lines'), ('grid', 'Grid'))),
    Field(key='solid_transition_power', label='Transition Power', group='Preparation', kind='int', default=50, unit='%', minimum=0, maximum=100),
    Field(key='solid_transition_raster_direction', label='Pattern Direction', group='Preparation', kind='choice', default='x', choices=(('x', 'X'), ('y', 'Y'), ('diagonal', '45 deg'))),
    Field(key='solid_transition_rotate_each_layer', label='Rotate Each Layer', group='Preparation', kind='bool', default=False),
    Field(key='solid_transition_rotation_per_layer', label='Rotation per Layer', group='Preparation', kind='int', default=90, unit='deg/layer', minimum=-180, maximum=180, step=1),
    Field(key='solid_transition_speed', label='Transition Feed Rate', group='Preparation', kind='float', default=3000, unit='mm/min', minimum=1),
    Field(key='solid_transition_track_width', label='Transition Track Width', group='Preparation', kind='float', default=0.2, unit='mm', minimum=0.01, step=0.01),
    Field(key='surface_skin_alternate_lines', label='Alternate roof skin paths', group='Infill Settings', kind='bool', default=False),
    Field(key='surface_skin_alternate_mode', label='Surface Skin Alternate Mode', group='Infill Settings', kind='choice', default='two-pass', choices=(('two-pass', 'Two Passes'), ('edge-interleave', 'Edge Interleave')), origin='derived'),
    Field(key='surface_skin_enabled', label='Roof/deck hatch skin', group='Infill Settings', kind='bool', default=True),
    Field(key='surface_skin_engraving_enabled', label='Raster flat roof hatch', group='Infill Settings', kind='bool', default=False),
    Field(key='surface_skin_hatch_spacing', label='Surface Skin Hatch Spacing', group='Advanced', kind='float', default=0.2, tooltip="Not exposed in OLOS's own interface either, so this default is the only value it has ever run with.", origin='default'),
    Field(key='surface_skin_layers', label='Surface Skin Layers', group='Advanced', kind='int', default=3, tooltip="Not exposed in OLOS's own interface either, so this default is the only value it has ever run with.", origin='default'),
    Field(key='surface_skin_power', label='Surface Skin Power', group='Advanced', kind='int', default=50, tooltip="Not exposed in OLOS's own interface either, so this default is the only value it has ever run with.", origin='default'),
    Field(key='surface_skin_speed', label='Surface Skin Speed', group='Advanced', kind='int', default=3000, tooltip="Not exposed in OLOS's own interface either, so this default is the only value it has ever run with.", origin='default'),
    Field(key='tertiary_variable_engraving_grid_line_thickness', label='Tertiary Variable Engraving Grid Line Thickness', group='Advanced', kind='float', default=0.8, tooltip="Not exposed in OLOS's own interface either, so this default is the only value it has ever run with.", origin='default'),
    Field(key='tertiary_variable_engraving_grid_spacing', label='Band 3 engraved/hollow grid pitch', group='Infill Settings', kind='float', default=1, unit='mm', minimum=0.2, step=0.1),
    Field(key='tertiary_variable_grid_clearance', label='Band 3 grid crossing gap', group='Infill Settings', kind='float', default=0.2, unit='mm', minimum=0, step=0.01),
    Field(key='tertiary_variable_grid_enabled', label='Add third variable grid band', group='Infill Settings', kind='bool', default=False),
    Field(key='tertiary_variable_grid_hatch_spacing', label='Band 3 vector hatch spacing', group='Infill Settings', kind='float', default=1, unit='mm', minimum=0.2, step=0.1),
    Field(key='tertiary_variable_grid_layers_before_roof', label='Band 3 layers before roof', group='Infill Settings', kind='int', default=6, minimum=1, step=1),
    Field(key='tertiary_variable_grid_line_count', label='Band 3 lines per grid member', group='Infill Settings', kind='int', default=3, minimum=1, step=1),
    Field(key='tertiary_variable_grid_spacing', label='Band 3 vector grid pitch', group='Infill Settings', kind='float', default=1, unit='mm', minimum=0.2, step=0.1),
    Field(key='use_custom_grid_clearance', label='Manual grid crossing gap', group='Engraving Infill', kind='bool', default=False),
    Field(key='use_image_size', label='Use Image Size', group='Advanced', kind='bool', default=False, tooltip="Not exposed in OLOS's own interface either, so this default is the only value it has ever run with.", origin='default'),
    Field(key='variable_engraving_grid_line_thickness', label='Variable Engraving Grid Line Thickness', group='Advanced', kind='float', default=0.8, tooltip="Not exposed in OLOS's own interface either, so this default is the only value it has ever run with.", origin='default'),
    Field(key='variable_engraving_grid_spacing', label='Engraved/hollow grid pitch', group='Infill Settings', kind='float', default=1, unit='mm', minimum=0.2, step=0.1),
    Field(key='variable_grid_clearance', label='Grid crossing gap', group='Infill Settings', kind='float', default=0.2, unit='mm', minimum=0, step=0.01),
    Field(key='variable_grid_hatch_rotation_per_layer', label='Variable grid hatch rotation per layer', group='Infill Settings', kind='int', default=67, unit='deg/layer', minimum=-180, maximum=180, step=1),
    Field(key='variable_grid_hatch_spacing', label='Vector hatch spacing', group='Infill Settings', kind='float', default=1, unit='mm', minimum=0.2, step=0.1),
    Field(key='variable_grid_infill_enabled', label='Variable grid support before roof', group='Infill Settings', kind='bool', default=False),
    Field(key='variable_grid_infill_layers_before_roof', label='Closest band layers before roof', group='Infill Settings', kind='int', default=3, minimum=1, step=1),
    Field(key='variable_grid_line_count', label='Lines per grid member', group='Infill Settings', kind='int', default=3, minimum=1, step=1),
    Field(key='variable_grid_spacing', label='Vector grid pitch', group='Infill Settings', kind='float', default=1, unit='mm', minimum=0.2, step=0.1),
    Field(key='variable_grid_target', label='Variable Grid Target', group='Infill Settings', kind='choice', default='hollow', choices=(('hollow', 'Hollow only'), ('both', 'Both')), origin='derived'),
)

#: Keyed lookup, for applying a saved profile or resolving one control.
BY_KEY = {f.key: f for f in FIELDS}

#: Group name -> its one-line description, as given in :data:`GROUPS`.
GROUP_NOTES = dict(GROUPS)

#: The group order, for laying the interface out.
GROUP_ORDER = tuple(name for name, _ in GROUPS)


def fields_in(group, include_hidden=False):
    """The fields of one group, in the order they should be shown."""
    return tuple(f for f in FIELDS
                 if f.group == group and (include_hidden or f.editable))


def groups_with_fields(include_hidden=False):
    """``(group, note, fields)`` for each group that has anything to show,
    in :data:`GROUP_ORDER`. Empty groups are skipped so a section never
    appears as an empty box."""
    out = []
    for name in GROUP_ORDER:
        rows = fields_in(name, include_hidden)
        if rows:
            out.append((name, GROUP_NOTES[name], rows))
    return tuple(out)


def defaults():
    """Every key's default, as one flat dict in the engine's units.

    This is the same mapping ``params.defaults()`` returns, built from the other
    direction. ``test_schema.py`` asserts the two agree, which is what keeps a
    value edited in one file from silently disagreeing with the other.
    """
    return {f.key: f.default for f in FIELDS}


def to_display(f, value):
    """The number to put in the widget. Percent fields are stored as fractions
    and shown as percentages -- see the unit note in ``params.py``."""
    if f.percent and value is not None:
        return value * 100.0
    return value


def from_display(f, value):
    """The inverse of :func:`to_display`: what the engine should receive."""
    if f.percent and value is not None:
        return value / 100.0
    return value


def coerce(f, value):
    """Put a value into the type the engine expects for this field.

    The engine coerces most things itself, but it does so inconsistently -- some
    keys go through ``int(... or default)``, others are used raw -- so a string
    out of a text box must not be allowed to reach it.
    """
    if value is None:
        return None
    if f.kind == "bool":
        return bool(value)
    if f.kind == "int":
        return int(round(float(value)))
    if f.kind == "float":
        return float(value)
    if f.kind == "tuple":
        return tuple(value)
    if f.kind == "choice":
        allowed = [v for v, _ in (f.choices or ())]
        if allowed and value not in allowed:
            raise ValueError(
                f"{f.key}: {value!r} is not one of {allowed}")
        return value
    return value


def clamp(f, value):
    """Hold a value inside the range OLOS's own input declared, where it did.

    Only applied to numbers, and only where a bound exists: most fields declare
    ``min="0"`` and nothing else.
    """
    if value is None or f.kind not in ("int", "float"):
        return value
    if f.minimum is not None:
        value = max(f.minimum, value)
    if f.maximum is not None:
        value = min(f.maximum, value)
    return value
