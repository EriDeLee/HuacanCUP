"""网表 → .kicad_pcb：放封装、连网络、画板框和安装孔，按通用规则一行行排开（M1）。

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


# 安装孔（board.mount_holes，如 M3x4）：封装名，孔中心离板边的距离（mm）
HOLES = {
    "M2": ("MountingHole_2.2mm_M2", 3.5),
    "M2.5": ("MountingHole_2.7mm_M2.5", 4.0),
    "M3": ("MountingHole_3.2mm_M3", 4.5),
}


def _move_to(fp, box, left, top):
    fp.Move(pcbnew.VECTOR2I(int(left - box.GetLeft()), int(top - box.GetTop())))


def add_mount_holes(board, design, footprint_dir, taken):
    """四角（x4）或左上、右下两角（x2）各放一个安装孔。

    返回每个角留出的正方形边长（mm），没有孔返回 0。
    安装孔只在板子上、不在原理图里：标成 board only，
    DRC 的原理图一致性检查不会把它当成多出来的元件。
    """
    spec = design["board"].get("mount_holes")
    if not spec:
        return 0.0
    size, count = spec.split("x")
    name, inset = HOLES[size]
    width_mm, height_mm = design["board"]["size_mm"]
    corners = [(inset, inset), (width_mm - inset, height_mm - inset)]
    if count == "4":
        corners = [
            (inset, inset),
            (width_mm - inset, inset),
            (width_mm - inset, height_mm - inset),
            (inset, height_mm - inset),
        ]
    number = 1
    for x, y in corners:
        while f"H{number}" in taken:
            number += 1
        fp = pcbnew.FootprintLoad(str(footprint_dir / "MountingHole.pretty"), name)
        if fp is None:
            raise SystemExit(f"找不到安装孔封装 MountingHole:{name}")
        fp.SetFPID(pcbnew.LIB_ID("MountingHole", name))
        fp.SetReference(f"H{number}")
        fp.SetValue(f"MountingHole_{size}")
        fp.SetBoardOnly(True)
        fp.SetExcludedFromBOM(True)
        fp.SetExcludedFromPosFiles(True)
        fp.SetPosition(pcbnew.VECTOR2I(mm(ORIGIN_MM[0] + x), mm(ORIGIN_MM[1] + y)))
        board.Add(fp)
        number += 1
    return 2 * inset


def _edge_column(refs, footprints, boxes, x_left, x_right, top, bottom, at_left):
    """板边连接器贴着板边竖着排一列，整列竖直居中。返回这一列的宽度（内部单位）。"""
    if not refs:
        return 0
    total = sum(boxes[r].GetHeight() for r in refs) + mm(MIN_GAP_MM) * (len(refs) - 1)
    if total > bottom - top:
        raise SystemExit(
            f"板边连接器 {refs} 竖着排一列有 {pcbnew.ToMM(total):.1f} mm 高，板子放不下"
        )
    y = (top + bottom - total) // 2
    for r in refs:
        box = boxes[r]
        x = x_left if at_left else x_right - box.GetWidth()
        _move_to(footprints[r], box, x, y)
        y += box.GetHeight() + mm(MIN_GAP_MM)
    return max(boxes[r].GetWidth() for r in refs)


def place_parts(design, footprints, corner):
    """板边连接器贴着指定的一边竖排；其余元件按 IR 顺序一行行排（放满一行换下一行），
    每行内和行与行之间的空隙都均匀分开。corner 是安装孔在四角留出的正方形边长（mm）。"""
    width_mm, height_mm = design["board"]["size_mm"]
    sides = {c["ref"]: c["side"] for c in design["board"].get("edge_connectors", [])}
    order = [p["ref"] for p in design["parts"] if p["ref"] in footprints]
    boxes = {r: bbox(footprints[r]) for r in order}
    x0, x1 = mm(ORIGIN_MM[0] + MARGIN_MM), mm(ORIGIN_MM[0] + width_mm - MARGIN_MM)
    y0, y1 = mm(ORIGIN_MM[1] + MARGIN_MM), mm(ORIGIN_MM[1] + height_mm - MARGIN_MM)
    keep = mm(corner) if corner else 0
    left = [r for r in order if sides.get(r) == "left"]
    right = [r for r in order if sides.get(r) == "right"]
    left_w = _edge_column(left, footprints, boxes, x0, x1, y0 + keep, y1 - keep, True)
    right_w = _edge_column(right, footprints, boxes, x0, x1, y0 + keep, y1 - keep, False)
    ix0 = x0 + (left_w + mm(MIN_GAP_MM) if left else 0)
    ix1 = x1 - (right_w + mm(MIN_GAP_MM) if right else 0)
    iy0, iy1 = y0 + keep, y1 - keep
    middle = [r for r in order if r not in sides]
    rows, row, row_w = [], [], 0
    for r in middle:
        w = boxes[r].GetWidth()
        if w > ix1 - ix0:
            raise SystemExit(
                f"{r} 宽 {pcbnew.ToMM(w):.1f} mm，{width_mm}×{height_mm} mm 的板子放不下"
            )
        if row and row_w + mm(MIN_GAP_MM) + w > ix1 - ix0:
            rows.append(row)
            row, row_w = [], 0
        row_w += (mm(MIN_GAP_MM) if row else 0) + w
        row.append(r)
    if row:
        rows.append(row)
    heights = [max(boxes[r].GetHeight() for r in rw) for rw in rows]
    free_y = (iy1 - iy0) - sum(heights)
    if free_y < mm(MIN_GAP_MM) * (len(rows) - 1):
        total = pcbnew.ToMM(sum(heights))
        raise SystemExit(
            f"{width_mm}×{height_mm} mm 的板子排不下：{len(rows)} 行元件共 {total:.1f} mm 高"
        )
    gap_y = free_y // (len(rows) + 1)
    y = iy0 + gap_y
    for rw, h in zip(rows, heights, strict=True):
        free_x = (ix1 - ix0) - sum(boxes[r].GetWidth() for r in rw)
        gap_x = free_x // (len(rw) + 1)
        x = ix0 + gap_x
        for r in rw:
            box = boxes[r]
            _move_to(footprints[r], box, x, y + (h - box.GetHeight()) // 2)
            x += box.GetWidth() + gap_x
        y += h + gap_y


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
    corner = add_mount_holes(board, design, footprint_dir, set(footprints))
    place_parts(design, footprints, corner)
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
