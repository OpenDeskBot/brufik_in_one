#!/usr/bin/env python3
"""Generate KiCad project files for Brufik Board1 (JLC EDA import friendly)."""

from __future__ import annotations

import math
import textwrap
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path

OUT = Path(__file__).resolve().parent
PROJECT = "Board1"
ROOT_SCH_UUID = "f62c91bb-4ed1-4930-8d81-727d0a5ea1fe"

# Stable UUIDs so schematic <-> PCB paths match across regenerations.
SYM_UUIDS = {
    "USB1": "11111111-1111-1111-1111-111111111101",
    "R1": "11111111-1111-1111-1111-111111111102",
    "R2": "11111111-1111-1111-1111-111111111103",
    "H3": "11111111-1111-1111-1111-111111111104",
    "H4": "11111111-1111-1111-1111-111111111105",
    "J1": "11111111-1111-1111-1111-111111111106",
    "J2": "11111111-1111-1111-1111-111111111107",
    "J3": "11111111-1111-1111-1111-111111111108",
    "J4": "11111111-1111-1111-1111-111111111109",
}

# Official PickAndPlace (mm). BOM ref -> KiCad ref / footprint / coords.
PANEL_PLACEMENTS = [
    ("J1", "CONN-SMD_8P-P1.25", -0.127, -5.461, 0, "F.Cu", "HEAD_8P", True),
    ("USB1", "TYPE-C-6P-073", -0.15, -17.018, 0, "F.Cu", "TYPE-C_6P", True),
    ("J4", "HDR-TH_3P-P2.54-V-M", -12.478, -15.644, 0, "F.Cu", "SERVO_Y", False),
    ("J3", "HDR-TH_3P-P2.54-H-M-W10.4", 13.227, 19.18, 0, "F.Cu", "SERVO_X", False),
    ("H3", "HDR-TH_7P-P2.54-V-M", 6.477, 12.573, 270, "F.Cu", "XIAO_A", False),
    ("H4", "HDR-TH_7P-P2.54-V-M", -8.763, 12.573, 270, "F.Cu", "XIAO_B", False),
    ("J2", "HDR-TH_7P-P2.54-H-M-W10.4", -12.7, 1.778, 270, "F.Cu", "AMP_7P", False),
    ("R2", "R0805", 7.131, -13.081, 180, "B.Cu", "5.1k", True),
    ("R1", "R0805", 8.382, -16.383, 180, "B.Cu", "5.1k", True),
]

BOARD_OUTLINE = (-18.5, -21.5, 16.0, 22.5)  # xmin, ymin, xmax, ymax (mm)


def uid() -> str:
    return str(uuid.uuid4())


def fx(size: float = 1.27) -> str:
    return f"(font (size {size} {size}))"


def eff(left: bool = False, hide: bool = False) -> str:
    parts = [fx()]
    if left:
        parts.append("(justify left)")
    if hide:
        parts.append("hide")
    return f"(effects {' '.join(parts)})"


def pin_def(num: str, name: str, x: float, y: float, angle: int = 180) -> str:
    return textwrap.dedent(
        f"""
        (pin passive line (at {x} {y} {angle}) (length 2.54)
          (name "{name}" (effects {fx(1.016)} (justify right)))
          (number "{num}" (effects {fx(1.016)} (justify left)))
        )
        """
    ).strip()


@dataclass
class Comp:
    lib_id: str
    ref: str
    value: str
    x: float
    y: float
    angle: int
    footprint: str
    pin_names: list[str]


def pin_abs(comp: Comp, pin_num: int) -> tuple[float, float]:
    """Return absolute schematic coordinates for a 1-based pin index."""
    n = len(comp.pin_names)
    spacing = 2.54
    y0 = (n - 1) * spacing / 2
    local_x = -5.08
    local_y = y0 - (pin_num - 1) * spacing

    if comp.lib_id == "Device:R":
        # vertical resistor symbol; pins at (0, ±3.81)
        pins = {1: (0.0, 3.81), 2: (0.0, -3.81)}
        local_x, local_y = pins[pin_num]
    elif comp.lib_id == "Brufik:USB_C_6P":
        usb = {
            1: (5.08, 5.08),
            2: (5.08, 2.54),
            3: (5.08, 0.0),
            4: (5.08, -2.54),
            5: (5.08, -5.08),
            6: (5.08, -7.62),
        }
        local_x, local_y = usb[pin_num]

    r = math.radians(comp.angle)
    ax = comp.x + local_x * math.cos(r) - local_y * math.sin(r)
    ay = comp.y + local_x * math.sin(r) + local_y * math.cos(r)
    return round(ax, 2), round(ay, 2)


def sym_resistor() -> str:
    return textwrap.dedent(
        """
        (symbol "Device:R"
          (pin_numbers hide)
          (pin_names (offset 0))
          (exclude_from_sim no) (in_bom yes) (on_board yes)
          (property "Reference" "R" (at 2.032 0 90) (effects (font (size 1.27 1.27)) (justify left)))
          (property "Value" "R" (at 0 0 90) (effects (font (size 1.27 1.27))))
          (property "Footprint" "Board1:R0805" (at 0 -3.81 90) (effects (font (size 1.27 1.27))))
          (property "Datasheet" "~" (at 0 0 0) (effects (font (size 1.27 1.27)) hide))
          (property "Description" "Resistor" (at 0 0 0) (effects (font (size 1.27 1.27)) hide))
          (symbol "R_0_1"
            (rectangle (start -1.016 -2.54) (end 1.016 2.54)
              (stroke (width 0.254) (type default)) (fill (type none))))
          (symbol "R_1_1"
            (pin passive line (at 0 3.81 270) (length 1.27)
              (name "~" (effects (font (size 1.27 1.27))))
              (number "1" (effects (font (size 1.27 1.27)))))
            (pin passive line (at 0 -3.81 90) (length 1.27)
              (name "~" (effects (font (size 1.27 1.27))))
              (number "2" (effects (font (size 1.27 1.27)))))
          )
        )
        """
    ).strip()


def sym_usb_c() -> str:
    pins = [
        pin_def("1", "VBUS", 5.08, 5.08, 180),
        pin_def("2", "GND", 5.08, 2.54, 180),
        pin_def("3", "CC1", 5.08, 0, 180),
        pin_def("4", "CC2", 5.08, -2.54, 180),
        pin_def("5", "D+", 5.08, -5.08, 180),
        pin_def("6", "D-", 5.08, -7.62, 180),
    ]
    return textwrap.dedent(
        f"""
        (symbol "Brufik:USB_C_6P"
          (pin_names (offset 1.016))
          (exclude_from_sim no) (in_bom yes) (on_board yes)
          (property "Reference" "USB" (at 0 10.16 0) {eff()})
          (property "Value" "TYPE-C_6P" (at 0 -10.16 0) {eff()})
          (property "Footprint" "Board1:TYPE-C-6P-073" (at 0 0 0) {eff()})
          (property "Datasheet" "~" (at 0 0 0) {eff(hide=True)})
          (symbol "USB_C_6P_0_1"
            (rectangle (start -2.54 -8.89) (end 2.54 8.89)
              (stroke (width 0.254) (type default)) (fill (type background))))
          (symbol "USB_C_6P_1_1"
            {' '.join(pins)}
          )
        )
        """
    ).strip()


def sym_connector(lib_id: str, ref_prefix: str, pin_count: int, footprint: str, pin_names: list[str]) -> str:
    spacing = 2.54
    y0 = (pin_count - 1) * spacing / 2
    pins = []
    for i in range(pin_count):
        y = y0 - i * spacing
        nm = pin_names[i]
        pins.append(pin_def(str(i + 1), nm, -5.08, y, 0))
    h = y0 + 2.54
    name = lib_id.split(":")[1]
    return textwrap.dedent(
        f"""
        (symbol "{lib_id}"
          (pin_names (offset 1.016))
          (exclude_from_sim no) (in_bom yes) (on_board yes)
          (property "Reference" "{ref_prefix}" (at 0 {h + 2.54} 0) {eff()})
          (property "Value" "{name}" (at 0 {-h - 2.54} 0) {eff()})
          (property "Footprint" "{footprint}" (at 0 0 0) {eff()})
          (property "Datasheet" "~" (at 0 0 0) {eff(hide=True)})
          (symbol "{name}_0_1"
            (rectangle (start -2.54 {-h}) (end 2.54 {h})
              (stroke (width 0.254) (type default)) (fill (type background))))
          (symbol "{name}_1_1"
            {' '.join(pins)}
          )
        )
        """
    ).strip()


def place(comp: Comp, sym_uuid: str, root_uuid: str) -> str:
    pin_count = len(comp.pin_names)
    pin_lines = "\n    ".join(f'(pin "{i + 1}" (uuid "{uid()}"))' for i in range(pin_count))
    su = SYM_UUIDS.get(comp.ref, sym_uuid)
    return textwrap.dedent(
        f"""
        (symbol (lib_id "{comp.lib_id}") (at {comp.x} {comp.y} {comp.angle}) (unit 1)
          (exclude_from_sim no) (in_bom yes) (on_board yes)
          (uuid "{sym_uuid}")
          (property "Reference" "{comp.ref}" (at {comp.x} {comp.y - 8} {comp.angle}) {eff()})
          (property "Value" "{comp.value}" (at {comp.x} {comp.y + 8} {comp.angle}) {eff()})
          (property "Footprint" "{comp.footprint}" (at {comp.x} {comp.y + 11} {comp.angle}) {eff()})
          (property "Datasheet" "~" (at {comp.x} {comp.y} {comp.angle}) {eff(hide=True)})
          {pin_lines}
          (instances
            (project "{PROJECT}"
              (path "/{root_uuid}" (reference "{comp.ref}") (unit 1))
            )
          )
        )
        """
    ).strip()


def wire(x1: float, y1: float, x2: float, y2: float) -> str:
    return (
        f'(wire (pts (xy {x1} {y1}) (xy {x2} {y2})) '
        f'(stroke (width 0) (type default)) (uuid "{uid()}"))'
    )


def junction(x: float, y: float) -> str:
    return f'(junction (at {x} {y}) (diameter 0) (color 0 0 0 0) (uuid "{uid()}"))'


def no_connect(x: float, y: float) -> str:
    return f'(no_connect (at {x} {y}) (uuid "{uid()}"))'


def label(name: str, x: float, y: float, angle: int = 0) -> str:
    return textwrap.dedent(
        f"""
        (label "{name}" (at {x} {y} {angle})
          (effects (font (size 1.27 1.27)) (justify left bottom))
          (uuid "{uid()}")
        )
        """
    ).strip()


def connect_net(net_items: list[tuple[Comp, int]], bus_x: float) -> list[str]:
    """Star-connect pins to a vertical bus at bus_x."""
    if not net_items:
        return []
    pts = [pin_abs(c, p) for c, p in net_items]
    ys = [p[1] for p in pts]
    y_min, y_max = min(ys), max(ys)
    out = [wire(bus_x, y_min, bus_x, y_max)]
    if len(pts) > 1:
        out.append(junction(bus_x, y_min))
        out.append(junction(bus_x, y_max))
    for (px, py) in pts:
        out.append(wire(px, py, bus_x, py))
        out.append(junction(bus_x, py))
    return out


def build_schematic() -> str:
    root = ROOT_SCH_UUID

    comps = [
        Comp("Brufik:USB_C_6P", "USB1", "TYPE-C_6P", 30, 50, 0, "Board1:TYPE-C-6P-073", ["VBUS", "GND", "CC1", "CC2", "D+", "D-"]),
        Comp("Device:R", "R1", "5.1k", 70, 35, 0, "Board1:R0805", ["1", "2"]),
        Comp("Device:R", "R2", "5.1k", 70, 65, 0, "Board1:R0805", ["1", "2"]),
        Comp("Brufik:Conn_7P_V", "H3", "XIAO_A", 120, 50, 0, "Board1:HDR-TH_7P-P2.54-V-M", ["D0", "D1", "D2", "D3", "D4", "D5", "GND"]),
        Comp("Brufik:Conn_7P_H", "H4", "XIAO_B", 160, 50, 0, "Board1:HDR-TH_7P-P2.54-H-M-W10.4", ["5V", "3V3", "D6", "D7", "D8", "D9", "D10"]),
        Comp("Brufik:Conn_8P", "J1", "HEAD_8P", 220, 50, 0, "Board1:CONN-SMD_8P-P1.25", ["3V3", "GND", "MOSI", "SCK", "CS", "DC", "SPK+", "SPK-"]),
        Comp("Brufik:Conn_7P_H", "J2", "AMP_7P", 220, 110, 0, "Board1:HDR-TH_7P-P2.54-H-M-W10.4", ["3V3", "GND", "DIN", "BCLK", "LRC", "SPK+", "SPK-"]),
        Comp("Brufik:Conn_3P_H", "J3", "SERVO_X", 120, 130, 0, "Board1:HDR-TH_3P-P2.54-H-M-W10.4", ["GND", "5V", "SIG"]),
        Comp("Brufik:Conn_3P_V", "J4", "SERVO_Y", 160, 130, 0, "Board1:HDR-TH_3P-P2.54-V-M", ["GND", "5V", "SIG"]),
    ]
    by_ref = {c.ref: c for c in comps}

    lib = [
        sym_resistor(),
        sym_usb_c(),
        sym_connector("Brufik:Conn_7P_V", "H", 7, "Board1:HDR-TH_7P-P2.54-V-M", ["D0", "D1", "D2", "D3", "D4", "D5", "GND"]),
        sym_connector("Brufik:Conn_7P_H", "H", 7, "Board1:HDR-TH_7P-P2.54-H-M-W10.4", ["5V", "3V3", "D6", "D7", "D8", "D9", "D10"]),
        sym_connector("Brufik:Conn_8P", "J", 8, "Board1:CONN-SMD_8P-P1.25", ["3V3", "GND", "MOSI", "SCK", "CS", "DC", "SPK+", "SPK-"]),
        sym_connector("Brufik:Conn_3P_H", "J", 3, "Board1:HDR-TH_3P-P2.54-H-M-W10.4", ["GND", "5V", "SIG"]),
        sym_connector("Brufik:Conn_3P_V", "J", 3, "Board1:HDR-TH_3P-P2.54-V-M", ["GND", "5V", "SIG"]),
    ]

    sym_blocks = [place(c, uid(), root) for c in comps]

    def C(ref: str, pin: int) -> tuple[Comp, int]:
        return (by_ref[ref], pin)

    nets: list[str] = []

    # Power / ground buses (vertical at fixed X)
    nets += connect_net([C("USB1", 1), C("H4", 1), C("J3", 2), C("J4", 2)], 95.0)
    nets += [label("+5V", 96, 50)]
    nets += connect_net([C("USB1", 2), C("H3", 7), C("J1", 2), C("J2", 2), C("J3", 1), C("J4", 1), C("R1", 2), C("R2", 2)], 55.0)
    nets += [label("GND", 56, 50)]
    nets += connect_net([C("H4", 2), C("J1", 1), C("J2", 1)], 200.0)
    nets += [label("+3V3", 201, 80)]

    # CC resistors: USB CC pins -> R1/R2 pin1, R1/R2 pin2 already on GND bus
    nets += connect_net([C("USB1", 3), C("R1", 1)], 60.0)
    nets += connect_net([C("USB1", 4), C("R2", 1)], 65.0)

    # Signals
    nets += connect_net([C("H3", 1), C("J2", 3)], 185.0)
    nets += [label("D0/I2S_DIN", 186, 56.46)]
    nets += connect_net([C("H3", 2), C("J1", 5)], 190.0)
    nets += [label("D1/LCD_CS", 191, 52.92)]
    nets += connect_net([C("H3", 3), C("J1", 6)], 195.0)
    nets += [label("D2/LCD_DC", 196, 50.38)]
    nets += connect_net([C("H3", 5), C("J2", 5)], 205.0)
    nets += [label("D4/I2S_LRC", 206, 45.3)]
    nets += connect_net([C("H3", 6), C("J2", 4)], 210.0)
    nets += [label("D5/I2S_BCLK", 211, 42.76)]
    nets += connect_net([C("H4", 3), C("J4", 3)], 175.0)
    nets += [label("D6/SERVO_Y", 176, 45.3)]
    nets += connect_net([C("H4", 4), C("J3", 3)], 170.0)
    nets += [label("D7/SERVO_X", 171, 42.76)]
    nets += connect_net([C("H4", 5), C("J1", 4)], 215.0)
    nets += [label("D8/LCD_SCK", 216, 40.22)]
    nets += connect_net([C("H4", 7), C("J1", 3)], 220.0)
    nets += [label("D10/LCD_MOSI", 221, 35.14)]
    nets += connect_net([C("J1", 7), C("J2", 6)], 225.0)
    nets += [label("SPK+", 226, 37.68)]
    nets += connect_net([C("J1", 8), C("J2", 7)], 230.0)
    nets += [label("SPK-", 231, 35.14)]

    # NC pins
    for ref, pin in [("H3", 4), ("H4", 6), ("USB1", 5), ("USB1", 6)]:
        px, py = pin_abs(by_ref[ref], pin)
        nets.append(no_connect(px, py))

    title = textwrap.dedent(
        f"""
        (text "Brufik Board1 - KiCad for JLC EDA import"
          (at 25 20 0) (effects (font (size 1.5 1.5)) (justify left bottom)) (uuid "{uid()}"))
        (text "Footprints in Board1.pretty; re-import Board1_kicad.zip"
          (at 25 25 0) (effects (font (size 1.016 1.016)) (justify left bottom)) (uuid "{uid()}"))
        """
    ).strip()

    return textwrap.dedent(
        f"""
        (kicad_sch (version 20231120) (generator "brufik_board1_generator") (generator_version "2.0")
          (uuid "{root}")
          (paper "A3")
          (title_block
            (title "Brufik Board1 / PCB1")
            (date "2026-06-16")
            (rev "1.1")
            (company "OpenDeskBot")
            (comment 1 "JLC EDA import - all footprints assigned")
            (comment 2 "See Board1_pinout.csv")
          )
          (lib_symbols
            {' '.join(lib)}
          )
          {title}
          {' '.join(sym_blocks)}
          {' '.join(nets)}
          (sheet_instances
            (path "/" (page "1"))
          )
        )
        """
    ).strip()


def build_project() -> str:
    return textwrap.dedent(
        """
        {
          "board": {
            "3dviewports": [],
            "layer_presets": [],
            "viewports": []
          },
          "boards": ["Board1.kicad_pcb"],
          "meta": {"filename": "Board1.kicad_pro", "version": 1},
          "net_settings": {
            "classes": [{
              "bus_width": 12,
              "clearance": 0.2,
              "diff_pair_gap": 0.25,
              "diff_pair_via_gap": 0.25,
              "diff_pair_width": 0.2,
              "line_style": 0,
              "microvia_diameter": 0.3,
              "microvia_drill": 0.1,
              "name": "Default",
              "pcb_color": "rgba(0, 0, 0, 0.000)",
              "schematic_color": "rgba(0, 0, 0, 0.000)",
              "track_width": 0.25,
              "via_diameter": 0.8,
              "via_drill": 0.4,
              "wire_width": 6
            }],
            "meta": {"version": 3},
            "net_colors": null,
            "netclass_assignments": null,
            "netclass_patterns": []
          },
          "pcbnew": {
            "last_paths": {
              "gencad": "", "idf": "", "netlist": "", "plot": "",
              "pos_files": "", "specctra_dsn": "", "step": "", "svg": "", "vrml": ""
            },
            "page_layout_descr_file": ""
          },
          "schematic": {"legacy_lib_dir": "", "legacy_lib_list": []},
          "sheets": [["a1b2c3d4-e5f6-7890-abcd-ef1234567890", "Board1"]],
          "text_variables": {}
        }
        """
    ).strip()


def build_pcb() -> str:
    xmin, ymin, xmax, ymax = BOARD_OUTLINE
    nets = [
        (0, ""),
        (1, "+5V"),
        (2, "GND"),
        (3, "+3V3"),
        (4, "D0/I2S_DIN"),
        (5, "D1/LCD_CS"),
        (6, "D2/LCD_DC"),
        (7, "D4/I2S_LRC"),
        (8, "D5/I2S_BCLK"),
        (9, "D6/SERVO_Y"),
        (10, "D7/SERVO_X"),
        (11, "D8/LCD_SCK"),
        (12, "D10/LCD_MOSI"),
        (13, "SPK+"),
        (14, "SPK-"),
    ]
    net_lines = " ".join(f'(net {n} "{name}")' for n, name in nets)

    fp_instances = []
    for ref, fp_name, x, y, rot, layer, value, is_smd in PANEL_PLACEMENTS:
        fp_instances.append(
            pcb_place_footprint(ref, fp_name, value, x, y, rot, layer, is_smd)
        )

    return textwrap.dedent(
        f"""
        (kicad_pcb (version 20240108) (generator "brufik_board1_generator") (generator_version "2.1")
          (general (thickness 1.6) (legacy_teardrops no))
          (paper "A4")
          (title_block
            (title "Brufik Board1 / PCB1") (date "2026-06-16") (rev "1.2") (company "OpenDeskBot"))
          (layers
            (0 "F.Cu" signal) (31 "B.Cu" signal)
            (32 "B.Adhes" user "B.Adhesive") (33 "F.Adhes" user "F.Adhesive")
            (34 "B.Paste" user) (35 "F.Paste" user)
            (36 "B.SilkS" user "B.Silkscreen") (37 "F.SilkS" user "F.Silkscreen")
            (38 "B.Mask" user "B.SolderMask") (39 "F.Mask" user "B.SolderMask")
            (44 "Edge.Cuts" user) (45 "Margin" user)
            (46 "B.CrtYd" user "B.Courtyard") (47 "F.CrtYd" user "F.Courtyard")
            (48 "B.Fab" user) (49 "F.Fab" user))
          (setup
            (pad_to_mask_clearance 0)
            (pcbplotparams
              (layerselection 0x00000000_ffffffff)
              (plot_on_all_layers_selection 0x00000000_00000001)
              (disableapertmacros no) (usegerberextensions no)
              (usegerberattributes yes) (usegerberadvancedattributes yes)
              (creategerberjobfile yes) (mode 1) (useauxorigin no)
              (plotreference yes) (plotvalue yes) (outputformat 1) (mirror no)
            )
          )
          {net_lines}
          (gr_rect (start {xmin} {ymin}) (end {xmax} {ymax})
            (stroke (width 0.1) (type default)) (fill none) (layer "Edge.Cuts") (uuid "{uid()}"))
          (gr_text "Brufik Board1" (at {(xmin + xmax) / 2} {ymax - 2}) (layer "F.SilkS")
            (effects (font (size 1.2 1.2) (thickness 0.15)) (justify center bottom)) (uuid "{uid()}"))
          {' '.join(fp_instances)}
        )
        """
    ).strip()


def pcb_pad_layers(layer: str, smd: bool) -> str:
    if smd:
        if layer == "B.Cu":
            return '"B.Cu" "B.Paste" "B.Mask"'
        return '"F.Cu" "F.Paste" "F.Mask"'
    return '"*.Cu" "*.Mask"'


def pcb_fp_pads(fp_name: str, layer: str, smd: bool) -> list[tuple[str, float, float, bool]]:
    """Local pad geometry (name, x, y, smd) before board rotation."""
    pads: list[tuple[str, float, float, bool]] = []
    if fp_name == "R0805":
        pads = [("1", -0.9125, 0, True), ("2", 0.9125, 0, True)]
    elif fp_name == "TYPE-C-6P-073":
        pitch = 1.27
        y0 = 2.5 * pitch
        for i in range(6):
            pads.append((str(i + 1), -2.5, y0 - i * pitch, True))
    elif fp_name == "CONN-SMD_8P-P1.25":
        pitch = 1.25
        y0 = 3.5 * pitch
        for i in range(8):
            pads.append((str(i + 1), -2.5, y0 - i * pitch, True))
    elif "7P" in fp_name:
        pitch = 2.54
        y0 = 3 * pitch
        for i in range(7):
            pads.append((str(i + 1), 0, y0 - i * pitch, False))
    elif "3P" in fp_name:
        pitch = 2.54
        for i in range(3):
            pads.append((str(i + 1), 0, pitch - i * pitch, False))
    return pads


def pcb_place_footprint(
    ref: str, fp_name: str, value: str, x: float, y: float, rot: int, layer: str, smd: bool
) -> str:
    sym_uuid = SYM_UUIDS.get(ref, uid())
    path = f"/{ROOT_SCH_UUID}/{sym_uuid}"
    sil_layer = "B.SilkS" if layer == "B.Cu" else "F.SilkS"
    fab_layer = "B.Fab" if layer == "B.Cu" else "F.Fab"
    attr = "(attr smd)" if smd else ""
    pads = pcb_fp_pads(fp_name, layer, smd)
    pad_lines = []
    for pname, px, py, pad_smd in pads:
        layers = pcb_pad_layers(layer, pad_smd)
        if pad_smd:
            pad_lines.append(
                f'(pad "{pname}" smd rect (at {px} {py}) (size 0.975 1.25) (layers {layers}) (net 0 ""))'
            )
        else:
            pad_lines.append(
                f'(pad "{pname}" thru_hole rect (at {px} {py}) (size 1.7 1.7) (drill 1.0) '
                f'(layers {layers}) (net 0 ""))'
            )

    h = 4.0
    if "7P" in fp_name:
        h = 10.0
    elif fp_name == "CONN-SMD_8P-P1.25":
        h = 5.5
    elif "3P" in fp_name:
        h = 4.0

    return textwrap.dedent(
        f"""
        (footprint "Board1:{fp_name}" (layer "{layer}") (uuid "{uid()}")
          (at {x} {y} {rot})
          (descr "{fp_name}")
          (property "Reference" "{ref}" (at 0 {-h} {rot}) (layer "{sil_layer}")
            (effects (font (size 0.8 0.8) (thickness 0.12))))
          (property "Value" "{value}" (at 0 {h} {rot}) (layer "{fab_layer}")
            (effects (font (size 0.8 0.8) (thickness 0.12)) hide))
          (property "Footprint" "Board1:{fp_name}" (at 0 0 {rot}) (layer "{fab_layer}")
            (effects (font (size 0.8 0.8)) hide))
          (path "{path}")
          {attr}
          (fp_text reference "{ref}" (at 0 {-h - 1} {rot}) (layer "{sil_layer}")
            (effects (font (size 0.8 0.8) (thickness 0.12)))
          (fp_text value "{value}" (at 0 {h + 1} {rot}) (layer "{fab_layer}")
            (effects (font (size 0.8 0.8) (thickness 0.12)) hide))
          (fp_rect (start -3.5 {-h}) (end 3.5 {h})
            (stroke (width 0.12) (type solid)) (fill none) (layer "{sil_layer}"))
          {' '.join(pad_lines)}
        )
        """
    ).strip()


def fp_resistor() -> str:
    return textwrap.dedent(
        """
        (footprint "R0805"
          (version 20240108) (generator "brufik_board1_generator")
          (layer "F.Cu") (descr "0805") (tags "0805 R0805")
          (property "Reference" "R" (at 0 -1.2 0) (layer "F.SilkS") (effects (font (size 0.8 0.8))))
          (property "Value" "R0805" (at 0 1.2 0) (layer "F.Fab") (effects (font (size 0.8 0.8))))
          (attr smd)
          (fp_line (start -1 -0.625) (end 1 -0.625) (stroke (width 0.12) (type solid)) (layer "F.SilkS"))
          (fp_line (start -1 0.625) (end 1 0.625) (stroke (width 0.12) (type solid)) (layer "F.SilkS"))
          (pad "1" smd rect (at -0.9125 0) (size 0.975 1.25) (layers "F.Cu" "F.Paste" "F.Mask"))
          (pad "2" smd rect (at 0.9125 0) (size 0.975 1.25) (layers "F.Cu" "F.Paste" "F.Mask"))
        )
        """
    ).strip()


def fp_conn(name: str, pads: int, pitch: float = 2.54, smd: bool = False) -> str:
    y0 = (pads - 1) * pitch / 2
    pad_lines = []
    for i in range(pads):
        y = y0 - i * pitch
        if smd:
            pad_lines.append(
                f'(pad "{i + 1}" smd rect (at -2.5 {y}) (size 1.5 1.0) (layers "F.Cu" "F.Paste" "F.Mask"))'
            )
        else:
            pad_lines.append(
                f'(pad "{i + 1}" thru_hole rect (at 0 {y}) (size 1.7 1.7) (drill 1.0) (layers "*.Cu" "*.Mask"))'
            )
    h = y0 + 2.0
    return textwrap.dedent(
        f"""
        (footprint "{name}"
          (version 20240108) (generator "brufik_board1_generator")
          (layer "F.Cu") (descr "{name}")
          (property "Reference" "J" (at 0 {h} 0) (layer "F.SilkS") (effects (font (size 0.8 0.8))))
          (property "Value" "{name}" (at 0 {-h} 0) (layer "F.Fab") (effects (font (size 0.8 0.8))))
          {'(attr smd)' if smd else ''}
          (fp_rect (start -3.5 {-h}) (end 3.5 {h}) (stroke (width 0.12) (type solid)) (fill none) (layer "F.SilkS"))
          {' '.join(pad_lines)}
        )
        """
    ).strip()


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "Board1.kicad_sch").write_text(build_schematic() + "\n", encoding="utf-8")
    (OUT / "Board1.kicad_pro").write_text(build_project() + "\n", encoding="utf-8")
    (OUT / "Board1.kicad_pcb").write_text(build_pcb() + "\n", encoding="utf-8")

    fp_dir = OUT / "Board1.pretty"
    fp_dir.mkdir(exist_ok=True)
    footprints = {
        "R0805.kicad_mod": fp_resistor(),
        "TYPE-C-6P-073.kicad_mod": fp_conn("TYPE-C-6P-073", 6, smd=True),
        "HDR-TH_7P-P2.54-V-M.kicad_mod": fp_conn("HDR-TH_7P-P2.54-V-M", 7),
        "HDR-TH_7P-P2.54-H-M-W10.4.kicad_mod": fp_conn("HDR-TH_7P-P2.54-H-M-W10.4", 7),
        "CONN-SMD_8P-P1.25.kicad_mod": fp_conn("CONN-SMD_8P-P1.25", 8, pitch=1.25, smd=True),
        "HDR-TH_3P-P2.54-H-M-W10.4.kicad_mod": fp_conn("HDR-TH_3P-P2.54-H-M-W10.4", 3),
        "HDR-TH_3P-P2.54-V-M.kicad_mod": fp_conn("HDR-TH_3P-P2.54-V-M", 3),
    }
    for fname, body in footprints.items():
        (fp_dir / fname).write_text(body + "\n", encoding="utf-8")

    zip_path = OUT / "Board1_kicad.zip"
    (OUT / "fp-lib-table").write_text(
        textwrap.dedent(
            """
            (fp_lib_table
              (version 7)
              (lib (name "Board1")(type "KiCad")(uri "${KIPRJMOD}/Board1.pretty")(options "")(descr "Brufik Board1 footprints"))
            )
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in ["Board1.kicad_pro", "Board1.kicad_sch", "Board1.kicad_pcb", "fp-lib-table"]:
            zf.write(OUT / name, arcname=f"Board1/{name}")
        for fp in fp_dir.glob("*.kicad_mod"):
            zf.write(fp, arcname=f"Board1/Board1.pretty/{fp.name}")

    print("Regenerated KiCad project v2.1 (PCB layout from PickAndPlace)")
    print(f"  {OUT / 'Board1_kicad.zip'}")


if __name__ == "__main__":
    main()
