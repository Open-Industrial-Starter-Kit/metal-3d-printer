# Working with CAM operations (2D/3D tab)

How to add, duplicate, name and isolate operations in the Kiri:Moto tab, and how
to get each one's toolpath onto its own preview toggle.

Everything here is Kiri's own behaviour except the last section, which describes
`0005-per-operation-preview-layers.patch` (see *Patching Kiri:Moto* in
[cam-integration.md](cam-integration.md)).

The **operations list** is the strip of tiles — `rough`, `outline`, `lathe`,
each with a ✕ — that builds up as you add them. Everything below is done there.

## Adding one

Hover **add** and click a name from the list: index, laser on/off, gcode, level,
loop, rough, outline, contour, lathe, register, drill, trace, pocket, helical,
area, flip. The new operation is appended to the end of the list.

Two of them pick an axis for you, and it is not always the one you want:
**contour** and **lathe** check the operations you already have, and if one is
already on X the new one comes in on **Y**. Adding a second lathe from this menu
therefore gives you a Y-axis lathe. To get a second pass on the *same* axis,
duplicate the first one instead (below).

## Opening one's settings

**Hover** the tile — the settings panel pops up beside it. It closes shortly
after the pointer leaves both the tile and the panel; clicking a field inside
makes it sticky so it stays put while you type. On a touchscreen, tap the tile
to open it and tap again to close.

## Duplicating one

**Shift-click** the tile. You get a deep copy of every setting — including the
axis, the tool, the step-down and any note — appended to the end of the list.
Then drag it into position.

This is the one to use for "the same operation again, slightly different": a
roughing pass and a finishing pass off the same lathe setup, for example.

## Reordering, disabling, deleting

* **Reorder** — drag a tile up or down the list. Operations run top to bottom.
* **Disable** — **ctrl-click** (**⌘-click** on a Mac) a tile. It greys out and
  is skipped, but keeps all its settings. Ctrl-click again to re-enable.
* **Solo one** — **ctrl-shift-click** a tile. The one you click flips state, and
  every other one is set to the opposite. So ctrl-shift-clicking a *disabled*
  operation enables it and disables all the rest — one pass on its own, without
  deleting anything. Do it to an enabled one and you get the inverse: that one
  off, everything else on.
* **Delete** — the ✕ on the tile.

## Naming an operation

Every operation can carry a **note**, and the note is what lets you tell two of
the same type apart.

Open the operation's settings panel (hover it) and click the **note strip along
the bottom** of that panel — it shows the current note, or nothing if there
isn't one. An *Edit Note for Operation* prompt opens; type a name and confirm.

Two forms behave differently:

| note | preview layer becomes | tile label becomes |
|---|---|---|
| `finish` | `lathe (finish)` | unchanged |
| `#finish` | `#finish` | `lathe (finish)` |

**Prefer the plain form.** Both give the operation its own preview layer, but
the `#tag` form uses the tag verbatim — `#` and all — as the layer name, and a
note with several tags becomes a comma-joined name. (Area operations are the
exception: they read the tag separately and get the clean `finish`.) The plain
note reads better in the layer list. Use `#tag` when you want the tile itself
relabelled, which is useful once a job has four or five operations in it.

## Seeing one operation at a time

After slicing, the checkboxes with the colour swatches beside the viewport are
the preview layers. **They are keyed by layer name, not by operation**, so two
operations that produce the same layer name share one checkbox and toggle
together.

That is what a note fixes. Un-noted, two lathe operations are both `lathe` and
you cannot separate them. Note them `rough` and `finish` and you get
`lathe (rough)` and `lathe (finish)` — two checkboxes, two colours, either one
on its own.

This applies to the layers an operation cuts in: area (clear / trace / linear),
contour, helical, level, register, lathe and drill. The diagnostic overlays —
`shadow`, `tool shadow`, `base`, `coastline`, `heights`, `clip.*` — stay pooled
under their own names on purpose; they are cross-operation aids and are more
useful together.

**The layer slider** is the other control, and it is a *range* over the whole
stack rather than a per-operation switch. All operations' layers sit in one
stack in list order, so narrowing the range tends to land you inside a single
operation — which is a fine way to step through a job, but it is not the same as
isolating one. Every slice and every preview resets it to the full range, so
anything you scrub to is temporary.

## Two lathe passes, end to end

1. Set up the first lathe operation and note it `rough`.
2. Shift-click its tile — an identical copy appears at the end of the list.
3. Hover the copy, note it `finish`, and change what should differ (step-down,
   leave/offset, feed).
4. Slice. The layer list now shows `lathe (rough)` and `lathe (finish)`, each
   with its own checkbox, and you can look at either alone.
5. Ctrl-shift-click one of them to run only that pass, if you want to cut them
   separately.
