"""网表 → .kicad_pcb：放封装、连网络、画板框，按通用规则排成一排（M0）。

在 KiCad 自带 Python 里运行：

    python board_build.py --netlist led.net --design design.json \
        --footprints <封装库目录> --out led.kicad_pcb

每个封装都写上原理图符号的 UUID 路径；DRC 的原理图一致性检查靠它把封装和符号对上。
加 --omit-paths 则不写路径，模拟 kinet2pcb 1.1.4 的行为，只用于对照实验。
"""

import argparse
import json
import sys
from pathlib import Path

import pcbnew
import sexpr

ORIGIN_MM = (100.0, 100.0)
MARGIN_MM = 1.0
MIN_GAP_MM = 1.0


def mm(value):
    return pcbnew.FromMM(float(value))


def read_netlist(path):
    export = sexpr.parse(path.read_text(encoding="utf-8"))[0]
    comps = []
    for comp in sexpr.children(sexpr.child(export, "components"), "comp"):
        sheet = sexpr.child(comp, "sheetpath")
        stamps = sexpr.child(comp, "tstamps")
        props = {
            sexpr.value(p, "name"): sexpr.value(p, "value")
            for p in sexpr.children(comp, "property")
        }
        fields_node = sexpr.child(comp, "fields")
        fields = {}
        for field in sexpr.children(fields_node, "field") if fields_node else []:
            name = sexpr.value(field, "name")
            if name and name != "Footprint":
                fields[name] = field[2] if len(field) > 2 and isinstance(field[2], str) else ""
        comps.append(
            {
                "ref": sexpr.value(comp, "ref"),
                "value": sexpr.value(comp, "value"),
                "footprint": sexpr.value(comp, "footprint"),
                "path": (sexpr.value(sheet, "tstamps", "/") if sheet else "/")
                + (stamps[1] if stamps and len(stamps) > 1 else ""),
                "sheetname": props.get("Sheetname", ""),
                "sheetfile": props.get("Sheetfile", ""),
                "fields": fields,
            }
        )
    nets = []
    for net in sexpr.children(sexpr.child(export, "nets"), "net"):
        nodes = [
            (sexpr.value(n, "ref"), sexpr.value(n, "pin")) for n in sexpr.children(net, "node")
        ]
        nets.append({"name": sexpr.value(net, "name"), "nodes": nodes})
    return comps, nets


def bbox(fp):
    try:
        return fp.GetBoundingBox(False)
    except TypeError:
        return fp.GetBoundingBox(False, False)


def draw_outline(board, width_mm, height_mm):
    x0, y0 = ORIGIN_MM
    corners = [(x0, y0), (x0 + width_mm, y0), (x0 + width_mm, y0 + height_mm), (x0, y0 + height_mm)]
    for (ax, ay), (bx, by) in zip(corners, corners[1:] + corners[:1], strict=True):
        seg = pcbnew.PCB_SHAPE(board)
        seg.SetShape(pcbnew.SHAPE_T_SEGMENT)
        seg.SetStart(pcbnew.VECTOR2I(mm(ax), mm(ay)))
        seg.SetEnd(pcbnew.VECTOR2I(mm(bx), mm(by)))
        seg.SetLayer(pcbnew.Edge_Cuts)
        seg.SetWidth(mm(0.1))
        board.Add(seg)


def place_in_row(design, footprints):
    """板边连接器放在指定的一侧，其余元件按 IR 顺序从左到右等间距排成一排，竖直居中。"""
    width_mm, height_mm = design["board"]["size_mm"]
    sides = {c["ref"]: c["side"] for c in design["board"].get("edge_connectors", [])}
    order = [p["ref"] for p in design["parts"]]
    row = (
        [r for r in order if sides.get(r) == "left"]
        + [r for r in order if r not in sides]
        + [r for r in order if sides.get(r) == "right"]
    )
    boxes = {r: bbox(footprints[r]) for r in row}
    total = sum(b.GetWidth() for b in boxes.values())
    free = mm(width_mm) - 2 * mm(MARGIN_MM) - total
    gap = free / (len(row) - 1) if len(row) > 1 else 0
    if gap < mm(MIN_GAP_MM):
        raise SystemExit(
            f"{width_mm}×{height_mm} mm 的板子排不下这一排元件"
            f"（元件总宽 {pcbnew.ToMM(total):.1f} mm）"
        )
    x = mm(ORIGIN_MM[0] + MARGIN_MM)
    cy = mm(ORIGIN_MM[1] + height_mm / 2)
    for ref in row:
        box = boxes[ref]
        if box.GetHeight() > mm(height_mm) - 2 * mm(MARGIN_MM):
            raise SystemExit(f"{ref} 高 {pcbnew.ToMM(box.GetHeight()):.1f} mm，板子放不下")
        footprints[ref].Move(pcbnew.VECTOR2I(int(x - box.GetLeft()), int(cy - box.GetCenter().y)))
        x += box.GetWidth() + gap


def build(netlist, design, footprint_dir, out, omit_paths):
    comps, nets = read_netlist(netlist)
    board = pcbnew.NewBoard(str(out))
    board.SetCopperLayerCount(design["board"]["layers"])

    footprints = {}
    for comp in comps:
        lib, name = comp["footprint"].split(":", 1)
        fp = pcbnew.FootprintLoad(str(footprint_dir / f"{lib}.pretty"), name)
        if fp is None:
            raise SystemExit(f"找不到封装 {comp['footprint']}")
        fp.SetFPID(pcbnew.LIB_ID(lib, name))
        fp.SetReference(comp["ref"])
        fp.SetValue(comp["value"])
        # 和 KiCad 的「从原理图更新 PCB」一样把符号字段（Datasheet、Description…）抄到封装上，
        # 否则原理图一致性检查会对每个字段报不一致。
        for name, text in comp["fields"].items():
            fp.SetField(name, text)
        if not omit_paths:
            fp.SetPath(pcbnew.KIID_PATH(comp["path"]))
            fp.SetSheetname(comp["sheetname"])
            fp.SetSheetfile(comp["sheetfile"])
        board.Add(fp)
        footprints[comp["ref"]] = fp

    for net in nets:
        info = pcbnew.NETINFO_ITEM(board, net["name"])
        board.Add(info)
        for ref, pin in net["nodes"]:
            pads = [p for p in footprints[ref].Pads() if p.GetNumber() == pin]
            if not pads:
                raise SystemExit(f"{ref} 没有编号为 {pin} 的焊盘（网络 {net['name']}）")
            for pad in pads:
                pad.SetNet(info)

    width_mm, height_mm = design["board"]["size_mm"]
    draw_outline(board, width_mm, height_mm)
    place_in_row(design, footprints)
    board.BuildListOfNets()
    if not pcbnew.SaveBoard(str(out), board):
        raise SystemExit(f"保存 {out} 失败")
    print(json.dumps({"footprints": len(footprints), "nets": len(nets), "out": str(out)}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--netlist", type=Path, required=True)
    parser.add_argument("--design", type=Path, required=True)
    parser.add_argument("--footprints", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--omit-paths", action="store_true")
    args = parser.parse_args()
    design = json.loads(args.design.read_text(encoding="utf-8"))
    build(args.netlist, design, args.footprints, args.out, args.omit_paths)
    return 0


if __name__ == "__main__":
    sys.exit(main())
