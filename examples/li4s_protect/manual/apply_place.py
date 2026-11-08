"""手工布局：按 placement.json 把封装挪到指定位置和角度（KiCad 自带 Python 运行）。"""
import json, sys, pcbnew
pcb, plan = sys.argv[1], json.load(open(sys.argv[2]))
b = pcbnew.LoadBoard(pcb)
fps = {f.GetReference(): f for f in b.GetFootprints()}
for ref, (x, y, rot) in plan.items():
    f = fps[ref]
    f.SetOrientationDegrees(rot)
    f.SetPosition(pcbnew.VECTOR2I(pcbnew.FromMM(x), pcbnew.FromMM(y)))
b.Save(pcb)
print(json.dumps({"placed": len(plan)}))
