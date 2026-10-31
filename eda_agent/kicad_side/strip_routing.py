"""删掉电路板上的全部走线、过孔和铺铜，元件位置不动（用于布线器对比实验）。

python strip_routing.py --pcb in.kicad_pcb --out out.kicad_pcb
"""

import argparse
import json
import sys
from pathlib import Path

import pcbnew


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pcb", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    board = pcbnew.LoadBoard(str(args.pcb))
    tracks = list(board.GetTracks())
    zones = list(board.Zones())
    for item in tracks + zones:
        board.Delete(item)
    if not pcbnew.SaveBoard(str(args.out), board):
        print(f"保存 {args.out} 失败", file=sys.stderr)
        return 1
    print(json.dumps({"removed_tracks_and_vias": len(tracks), "removed_zones": len(zones)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
