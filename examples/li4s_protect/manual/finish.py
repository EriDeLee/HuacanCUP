"""布线后：双面 GND 铺铜、丝印标注（KiCad 自带 Python 运行）。"""
import sys, pcbnew
pcb = sys.argv[1]
b = pcbnew.LoadBoard(pcb)
mm = pcbnew.FromMM
box = b.GetBoardEdgesBoundingBox()
x0, y0, x1, y1 = box.GetLeft(), box.GetTop(), box.GetRight(), box.GetBottom()
gnd = b.FindNet("GND")
for layer in (pcbnew.F_Cu, pcbnew.B_Cu):
    z = pcbnew.ZONE(b)
    z.SetLayer(layer); z.SetNet(gnd)
    o = z.Outline(); o.NewOutline()
    for px, py in ((x0, y0), (x1, y0), (x1, y1), (x0, y1)):
        o.Append(px, py)
    z.SetLocalClearance(mm(0.4)); z.SetMinThickness(mm(0.25))
    z.SetPadConnection(pcbnew.ZONE_CONNECTION_THERMAL)
    z.SetThermalReliefGap(mm(0.4)); z.SetThermalReliefSpokeWidth(mm(0.5))
    b.Add(z)
def text(s, x, y, size=1.2, bold=False, layer=pcbnew.F_SilkS):
    t = pcbnew.PCB_TEXT(b); t.SetText(s); t.SetLayer(layer)
    t.SetPosition(pcbnew.VECTOR2I(mm(x), mm(y)))
    t.SetTextSize(pcbnew.VECTOR2I(mm(size), mm(size))); t.SetTextThickness(mm(size * (0.2 if bold else 0.15)))
    b.Add(t)
for s, x, y, sz, bd in [(l[0], *l[1:]) for l in eval(sys.argv[2])]:
    text(s, x, y, sz, bd)
pcbnew.ZONE_FILLER(b).Fill(b.Zones())
b.Save(pcb)
print("ok")
