#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
符号几何表生成器 —— 编译含全部 stm32tikz 符号的探针文档, 解析它自己写出的侧车,
生成 references/geometry.md。

为什么要有它
------------
画图时要摆器件, 就得知道"这个符号的端子在哪、本体多宽多高"。以前靠临时写探针
.tex 再编译读侧车 —— 那要踩三个坑(裸 xelatex 找不到 sty、`\typeout` 拿不到要读
日志、shell 的 grep 不认交替), 用掉好几次工具调用; 而且**测出来的数还要手抄进
源码**, 下次换符号再测一遍。

更要紧的是: 手抄/估算的间距**门禁查不出来**。排版门只查"重叠/穿体/压线",
两个器件挨得太近但没碰上, 它一条都不报 —— 于是"按记忆估的 0.55cm"能过门禁,
只是刚好没撞上而已。这份表把"需要探测"变成"查表"。

为什么表可以很小（实测结论）
---------------------------
**circuitikz 的元件本体尺寸是常数, 不随两端跨度缩放。** 实测 R 在跨度
28.45 / 34.14 / 56.91pt 时本体恒为 27.31×10.24pt。所以每种符号只要一行。

⚠ 但**最小跨度**必须给: 跨度小于本体宽时, **本体盒会越出两个端点**
(实测 R 跨度 11.4pt 时本体仍占 27.3pt)。排间距要按本体宽算, 不是按跨度。

归一化基准是什么（容易搞错的地方）
---------------------------------
探针把每个符号排在**自己的行**上(绝对 y 越来越负)。若直接读侧车坐标, 得到的是
**行距**(如 -73.98 / -147.95), 那是探针布局, 不是符号几何, 抄进源码全错。

所以必须归一化到"**把符号放上去的那个点**"。这个点探针生成器**本来就知道**
(就是它传给宏的位置参数), 所以不该用启发式去猜 —— 初版用"取端子里 y 最小的
那个当基准", 结果 npn 的基准落到了 E 端, 输出 `b=(0,22.3)` 这种看不懂的值
(正确应是 `b=(0,0), c=(24.3,22.3), e=(24.3,-22.3)`)。

现在每个符号的放置点显式写在 `PROBE_ITEMS` 里, 归一化直接用它。

用法
----
    python <skill>/scripts/gen_geometry.py           # 生成 references/geometry.md
    python <skill>/scripts/gen_geometry.py --check    # 只校验现有表是否过期
    python <skill>/scripts/gen_geometry.py --json     # 输出 JSON

退出码: 0 = 成功/一致; 1 = --check 发现过期; 2 = 环境错误。
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _paths                                        # noqa: E402
from sidecar import parse_sidecar                    # noqa: E402

PT2CM = 1.0 / 28.45274
CM2PT = 28.45274

# 每行的 y 间距(cm): 2.6 够放下最高的符号(npn 高 44.6pt=1.57cm)
ROW_PITCH = 2.6

BS = chr(92)

# (探测名, 报告名, 类型, 唯一键, 符号键, 跨度, 选项)
# 探测名 = 唯一键 = 侧车里的索引名, 三者必须一致且**互不重复**。
PROBE_ITEMS = [
    # ---- 双端元件: 跨度 1.5cm(正常) ----
    ("P_R", "R", "cPart", "P_R", "R", "1.5cm", ""),
    ("P_C", "C", "cPart", "P_C", "C", "1.5cm", ""),
    ("P_D", "D", "cPart", "P_D", "D", "1.5cm", ""),
    ("P_LED", "leD", "cPart", "P_LED", "leD", "1.5cm", ""),
    ("P_L", "L", "cPart", "P_L", "L", "1.5cm", ""),
    ("P_SW", "switch", "cPart", "P_SW", "switch", "1.5cm", ""),
    ("P_PD", "photodiode", "cPart", "P_PD", "photodiode", "1.5cm", ""),
    # ---- 双端元件: 跨度 0.4cm(偏小, 验证本体不随跨度缩小) ----
    ("S_R", "R(跨度小)", "cPart", "S_R", "R", "0.4cm", ""),
    ("S_LED", "leD(跨度小)", "cPart", "S_LED", "leD", "0.4cm", ""),
    ("S_C", "C(跨度小)", "cPart", "S_C", "C", "0.4cm", ""),
    # ---- 带值标签 ----
    ("W_R", "R+值标签", "cPart", "W_R", "R", "1.5cm", "l=10K"),
    ("W_C", "C+值标签", "cPart", "W_C", "C", "1.5cm", "l=100nF"),
    # ---- 单点 / 多点符号(放置点 = 传入的位置; cNPN 是 anchor=B) ----
    ("X_NPN", "npn", "cNPN", "X_NPN", None, None, None),
    ("X_COIL", "继电器线圈", "coil", "X_COIL", None, None, None),
    ("X_CT", "继电器触点", "contacts", "X_CT", None, None, None),
    ("X_GND", "GND", "gnd", "X_GND", None, None, None),
    ("X_VCC", "+5V", "vcc", "X_VCC", None, None, None),
    ("X_MCU", "MCU方块", "mcu", "X_MCU", None, None, None),
]

# 各符号"放置点"的存在性: 用来算 report 里的本体相对偏移
RENDER_BILATERAL = ("P_R", "P_C", "P_D", "P_LED", "P_L", "P_SW", "P_PD")
RENDER_SMALL = ("S_R", "S_LED", "S_C")
RENDER_MULTI = ("X_NPN", "X_COIL", "X_CT", "X_MCU")


def _frag(kind, key, sym, span, opt, y):
    """生成一个符号的 LaTeX 片段; y 是该符号所在行的 y(cm, 字符串)。

    ⚠ 坐标参数**不写外层括号** —— `\\cPart` 内部自己补, 传 `(0,0)` 会变成
    `((0,0))`, pgf 报 `No shape named '(...'`。(见 quickref 的语法规矩。)

    ⚠ `\\cPart{名}{circuitikz键}{起点}{终点}{选项}` —— 参数序别颠倒。
    起点固定在 `0,<y>`, 终点是 `<span>,<y>`(同行, 水平放)。

    ⚠ **每个符号的探测名必须唯一** —— 侧车是"按名字索引"的(T|/B| 记录),
    同名会互相覆盖, 于是只量到最后一个。所以用 P_R / S_R / W_R 这种前缀区分。
    """
    if kind == "cPart":
        return (BS + f"cPart{{{key}}}{{{sym}}}{{0,{y}}}"
                + "{" + span + "," + y + "}{" + (opt or "") + "}")
    if kind == "cNPN":
        return BS + f"cNPN{{{key}}}{{0,{y}}}"
    if kind == "coil":
        return BS + f"CoilNode{{{key}}}{{0,{y}}}{{K}}"
    if kind == "contacts":
        return BS + f"Contacts{{{key}}}{{0,{y}}}"
    if kind == "gnd":
        return BS + f"cGND{{0,{y}}}"
    if kind == "vcc":
        return BS + f"cVCC{{+5V}}{{0,{y}}}"
    if kind == "mcu":
        return (BS + f"MCUauto{{{key}}}{{0}}{{{y}}}{{3.0cm}}{{3.4cm}}"
                + "{cap}{0.9cm}{PA0, PA1}")
    raise ValueError(kind)


def build_probe():
    """生成探针 .tex, 同时返回 {探测名: (基准x_pt, 基准y_pt)}。"""
    body, origins = [], {}
    y = 0.0
    for i, item in enumerate(PROBE_ITEMS):
        name, disp, kind, key, sym, span, opt = item
        if i:
            y -= ROW_PITCH
        ys = f"{y:.4f}cm"
        body.append(f"% ---- {disp} ----")
        body.append(_frag(kind, key, sym, span, opt, ys))
        # ⚠ 带 `l=`/`v=` 的条目必须补 `\LogLabel` —— `\cPart` **不会**自动登记
        #   值标签(只有 `\cRes`/`\cCap` 那类封装带它, 但它们不接受额外选项)。
        #   漏了它, 侧车里就没有 `X|...|L|` 记录, 值标签一格数据都拿不到。
        if opt and ("l=" in opt or "v=" in opt):
            body.append(BS + f"LogLabel{{{key}}}")
        # 归一化基准 = 放置点。双端元件起点在 (0,y), 单点符号的放置点也是 (0,y)
        origins[name] = (0.0, y * CM2PT)
    tex = (BS + "documentclass[border=4pt]{standalone}\n"
           + BS + "usepackage{ctex}\n"
           + BS + "usepackage{stm32tikz}\n"
           + BS + "begin{document}\n"
           + BS + "begin{tikzpicture}\n\n"
           + "\n".join(body) + "\n\n"
           + BS + "DumpCanvas\n"
           + BS + "end{tikzpicture}\n"
           + BS + "end{document}\n")
    return tex, origins


def _find_xelatex():
    """找 xelatex —— 统一走 _paths, 不在这里写死用户目录。"""
    return _paths.find_xelatex()


def compile_probe(workdir, tex):
    with open(os.path.join(workdir, "geom.tex"), "w", encoding="utf-8") as f:
        f.write(tex)
    x = _find_xelatex()
    if not x:
        print("错误: 找不到 xelatex", file=sys.stderr)
        return None
    r = subprocess.run([x, "-interaction=nonstopmode", "geom.tex"],
                       cwd=workdir, env=_paths.texinputs_env(),
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=180)
    net = os.path.join(workdir, "geom.net")
    if not os.path.exists(net):
        print("探针编译失败:", file=sys.stderr)
        for ln in (r.stdout or "").splitlines():
            if ln.startswith("!"):
                print("   " + ln, file=sys.stderr)
        return None
    return net


def measure(net, origins):
    """
    读侧车, 返回 {探测名: {w, h, box_rel, terms_rel}} —— 全部相对**放置点**。
    """
    sc = parse_sidecar(net)
    out = {}
    for name, bb in sc["bodies"]:
        if name not in origins:
            continue
        bx, by = origins[name]
        x0, y0 = min(bb[0], bb[2]), min(bb[1], bb[3])
        x1, y1 = max(bb[0], bb[2]), max(bb[1], bb[3])
        terms = {}
        for tn, (tx, ty) in sc["terms"].items():
            if tn.startswith(name + "."):
                terms[tn[len(name) + 1:]] = (tx - bx, ty - by)
        out[name] = {"w": x1 - x0, "h": y1 - y0,
                     "box": (x0 - bx, y0 - by, x1 - bx, y1 - by),
                     "terms": terms}

    # 电源/地符号: 在 gsyms 里, 按放置点归一化。
    # 探针里 gnd/vcc 各一个, 顺序与 PROBE_ITEMS 一致。
    gs_names = [i[0] for i in PROBE_ITEMS if i[2] in ("gnd", "vcc")]
    gs = sc.get("gsyms", [])
    for i, (nm, gx0, gy0, gx1, gy1) in enumerate(gs):
        if i >= len(gs_names):
            break
        name = gs_names[i]
        bx, by = origins[name]
        x0, y0 = min(gx0, gx1), min(gy0, gy1)
        x1, y1 = max(gx0, gx1), max(gy0, gy1)
        out[name] = {"w": x1 - x0, "h": y1 - y0,
                     "box": (x0 - bx, y0 - by, x1 - bx, y1 - by),
                     "terms": {}, "net": nm, "is_power": True}
    return out, sc


def fmt(v):
    return f"{v:.1f}"


def render_md(geo, sc):
    L = [
        "# 符号几何表（自动生成 —— 不要手改）",
        "",
        "由 `scripts/gen_geometry.py` 编译探针文档、解析侧车生成。",
        "改了符号几何就重跑：`python <skill>/scripts/gen_geometry.py`",
        "",
        "**坐标一律相对「放置点」** —— 即传给宏的那个位置参数（`\\cPart` 用起点，",
        "`\\cNPN` 用 `anchor=B` 的那个点，`\\CoilNode`/`\\Contacts` 用中心）。",
        "所以表里的数可以直接用来算间距，不用再做加减。",
        "",
        "**尺寸是常数，不随两端跨度缩放**（实测：R 在跨度 28.4 / 34.1 / 56.9pt",
        "时本体恒为 27.3×10.2pt）。",
        "",
        "⚠ **跨度小于本体宽时，本体盒会越出两个端点**（见「本体相对起点」列：",
        "x0 为负数就说明本体在起点外侧）。排间距要按**本体宽**算，不是按跨度。",
        "",
        "## 双端元件（跨度 1.5cm）",
        "",
        "| 符号 | 本体宽 | 本体高 | 本体相对起点 x0..x1 | `b` 端 |",
        "|---|---|---|---|---|",
    ]
    for name in RENDER_BILATERAL:
        g = geo.get(name)
        if not g:
            continue
        disp = next(i[1] for i in PROBE_ITEMS if i[0] == name)
        b = g["terms"].get("b", (0, 0))
        x0, _, x1, _ = g["box"]
        L.append(f"| `{disp}` | {fmt(g['w'])}pt ({g['w'] * PT2CM:.2f}cm) "
                 f"| {fmt(g['h'])}pt | {fmt(x0)} .. {fmt(x1)} "
                 f"| ({fmt(b[0])}, {fmt(b[1])}) |")

    L += ["", "## 小跨度实例（本体不随跨度缩小）", "",
          "| 符号 | 端点跨度 | 本体宽 | 本体相对起点 x0..x1 | 越出端点？ |",
          "|---|---|---|---|---|"]
    for name in RENDER_SMALL:
        g = geo.get(name)
        if not g:
            continue
        disp = next(i[1] for i in PROBE_ITEMS if i[0] == name)
        span = 0.4 * CM2PT
        x0, _, x1, _ = g["box"]
        over = "**是**" if (x0 < -0.5 or x1 > span + 0.5) else "否"
        L.append(f"| `{disp}` | {fmt(span)}pt | {fmt(g['w'])}pt "
                 f"| {fmt(x0)} .. {fmt(x1)} | {over} |")

    L += ["", "## 多点符号（相对放置点）", "",
          "| 符号 | 端子 | 本体相对放置点 x0..x1 / y0..y1 |",
          "|---|---|---|"]
    for name in RENDER_MULTI:
        g = geo.get(name)
        if not g:
            continue
        disp = next(i[1] for i in PROBE_ITEMS if i[0] == name)
        ts = ", ".join(f"`{t}`=({fmt(x)},{fmt(y)})"
                       for t, (x, y) in sorted(g["terms"].items()))
        x0, y0, x1, y1 = g["box"]
        L.append(f"| `{disp}` | {ts or '—'} | {fmt(x0)}..{fmt(x1)} / "
                 f"{fmt(y0)}..{fmt(y1)} |")

    L += ["", "## 电源/地符号（图形范围，相对放置点）", "",
          "`P|` 只记**一个连接点**，`GS|` 记**图形范围**（排版障碍物用）。",
          "",
          "| 符号 | 图形宽 × 高 | 相对放置点 x0..x1 / y0..y1 |",
          "|---|---|---|"]
    for name in ("X_GND", "X_VCC"):
        g = geo.get(name)
        if not g:
            continue
        x0, y0, x1, y1 = g["box"]
        L.append(f"| `{g.get('net', name)}` | {fmt(g['w'])} × {fmt(g['h'])} "
                 f"| {fmt(x0)}..{fmt(x1)} / {fmt(y0)}..{fmt(y1)} |")

    L += ["", "## 值标签（`l=`）的排版盒", "",
          "circuitikz 自动摆的节点，**紧贴本体上方、横向与本体重合**：", ""]
    wbox = [(a[4], abs(a[2] - a[0]), abs(a[3] - a[1]))
            for a in sc["annos"] if a[5] == "L"]
    if wbox:
        L += ["| 标签 | 宽 | 高 |", "|---|---|---|"]
        for txt, w, h in wbox:
            L.append(f"| `{txt[:26]}` | {fmt(w)}pt | {fmt(h)}pt |")
    L += [
        "",
        "所以：**横向**排器件时，标签相撞的临界 ≈ 本体相撞的临界（同一判据）；",
        "**纵向**排器件时要按**标签高度**算（`10K` 约 20pt，比本体高 10pt 一倍）。",
        "",
    ]
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(description="生成符号几何表")
    ap.add_argument("--check", action="store_true", help="只校验现有表是否过期")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--out", default=None, help="输出路径")
    args = ap.parse_args()

    here = os.path.dirname(os.path.abspath(__file__))
    out = args.out or os.path.join(os.path.dirname(here), "references",
                                   "geometry.md")

    tex, origins = build_probe()
    tmp = tempfile.mkdtemp(prefix="sngeo_")
    try:
        net = compile_probe(tmp, tex)
        if not net:
            return 2
        geo, sc = measure(net, origins)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if not geo:
        print("错误: 探针没量到任何器件本体盒", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(geo, ensure_ascii=False, indent=2, default=str))
        return 0

    text = render_md(geo, sc)

    if args.check:
        if not os.path.exists(out):
            print(f"--check: {out} 不存在（请先跑 gen_geometry.py）", file=sys.stderr)
            return 1
        old = open(out, encoding="utf-8").read()
        if old.strip() != text.strip():
            print(f"--check: {out} 已过期", file=sys.stderr)
            return 1
        print(f"--check: {out} 是最新的")
        return 0

    with open(out, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"已写出 {out}")
    print(f"  收录 {len(geo)} 个符号几何")
    return 0


if __name__ == "__main__":
    sys.exit(main())
