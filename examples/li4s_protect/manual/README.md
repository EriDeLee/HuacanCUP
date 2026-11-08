# li4s_protect 手工摆放（2026-11-06 会话原样整理）

来源：会话 f5663e13 里 `/home/claude/place/` 下的文件。plan.json 是第一版坐标加上后两次修改合并后的最终值
（对应最终那次运行 smoke-li4s_protect-20261106-085513，70×44 板）。

- plan.json：27 个元件的 [x, y, 角度]，毫米，KiCad 板坐标。J1、J2 不在表里，由现有建板程序按 `edge_connectors` 贴边。
- apply_place.py：建板之后、布线之前，把封装挪到 plan.json 的位置。
- finish.py + labels.txt：布线之后加双面 GND 铺铜和丝印文字。
- run.py：把上面两步挂进 Smoke 的 board() 和 route()。

用法（要用 KiCad 自带的 Python 跑 apply_place.py 和 finish.py；run.py 里写的是 /usr/bin/python3，Windows 上要改）：
  python run.py <repo根目录> examples/li4s_protect/design.json plan.json labels.txt

注意：
- 当时没有保存最终的 .kicad_pcb，也没有发给用户。要得到 manual.kicad_pcb 就得按上面再跑一遍。
- M2 的 HPWL 对比只需要元件位置，直接用 plan.json 即可，不用铺铜和丝印。
- 坐标依赖当时的建板程序（板原点、连接器贴边位置）。换了版本要重新核对 J1/J2 和板框。
