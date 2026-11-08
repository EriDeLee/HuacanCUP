# 原样取自 2026-11-06 会话；只把写死的 /home/claude 路径改成了位置参数，逻辑没动。
# 用法：python run.py <repo根目录> <design.json> <plan.json> <labels.txt>
import json, subprocess, sys
from pathlib import Path
REPO = Path(sys.argv[1]); HERE = Path(__file__).parent
sys.path.insert(0, str(REPO))
from eda_agent.smoke import Smoke
PLAN = Path(sys.argv[3]); LABELS = Path(sys.argv[4]).read_text()
def kpy(*a):
    out = subprocess.run(["/usr/bin/python3", *map(str, a)], capture_output=True, text=True)
    print(out.stdout[-500:], out.stderr[-1500:]); out.check_returncode()
class Placed(Smoke):
    def board(self, sch, netlist):
        pcb = super().board(sch, netlist)
        kpy(HERE / "apply_place.py", pcb, PLAN)
        return pcb
    def route(self, pcb, router):
        t = super().route(pcb, router)
        kpy(HERE / "finish.py", t, LABELS)
        return t
s = Placed(Path(sys.argv[2]), REPO / "runs")
r = s.run(("fastroute",), "fastroute")
print(s.run_dir)
print({k: (v["passed"], v.get("counts")) for k, v in r["gates"].items() if "quality" not in k}, r.get("error", "")[-800:], r.get("router_errors"))
