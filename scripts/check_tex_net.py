#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
网表连通性体检（TikZ 路线）—— 电路**对不对**，与 check_tex_layout.py（排版**好不好看**）互补。

用法:
    python check_tex_net.py <侧车.net> [更多.net...]
    python check_tex_net.py *.net

退出码: 0 = 电气上没查出问题, 1 = 有 / 2 = 用法或环境错误。

电气检查: 短路/旁路/穿体/接错/孤标签/斜线/非正交/记账警告。
但覆盖的**类别**不同 —— 差集本身就是本方案的价值所在, 逐条列在下面:

  能查：
    1. 短路        VCC 与 GND 落在同一张网
    2. 旁路        元件两端接在同一张网（用它等于没用它）
    3. 穿体        导线纵穿元件本体
    4. 接错        \Expect 声明了"该接在哪"但没兑现 —— 唯一能抓「接到错的节点」
    5. 孤标签      网络标签只出现一次（同号才相连）
    6. 斜线        斜线段 —— 连通性里没有定义, 会被忽略

  结构上**不可能发生**（端点就是锚点, 写不错）:
    * 畸形线段   侧车坐标是 pgf 算出的浮点数, 不可能"该是坐标却写成颜色常量"
    * 悬空       端点就是锚点, 画线时必然精确落在端子上
    * 飘端子     TikZ 没有"作者手填端子坐标"这个入口, 端子与图形同源

  **查不出**（必须人眼核对）:
    * 极性/方向   LED 阳极接反、电解电容反、续流管方向反 —— 两根线都接在
                 正确端子上, 连通性完美, 任何连通性检查都不报。必须人眼核对。
"""
import argparse
import glob
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sidecar import (parse_sidecar, build_nets, net_of, on_segment,
                     is_orthogonal, seg_cross, fmt_pt, TOL)   # noqa: E402


def analyze(net_path, quiet=False):
    """跑全部门禁, 返回结果 dict（供 check_tex_layout / 测试复用）。"""
    sc = parse_sidecar(net_path)
    find, members, nets, wires, dots, tag_pts = build_nets(sc)

    # ---- 斜线 ----
    slanted = [w for w in sc["wires"] if not is_orthogonal(w)]
    # ---- 意图违背: \WireH 声明水平、\WireV 声明竖直, 但坐标不是 ----
    # 这是本方案要堵的洞: 若**静默丢弃**斜线, 于是
    # 一条斜着画、两端又没真接上的线完全"不存在", 图看着连了、检查器说
    # 没问题。这里因为画线时写了 WH/WV 意图, 就能明确报出来。
    bad_h = [(s, abs(s[1] - s[3])) for s in sc["hint_h"] if abs(s[1] - s[3]) > 0.5]
    bad_v = [(s, abs(s[0] - s[2])) for s in sc["hint_v"] if abs(s[0] - s[2]) > 0.5]

    # ---- 检查 1: 电源与地同网 ----
    vcc_roots = {}
    for name in members:
        up = name.upper()
        if up in ("VCC", "VDD") or name.startswith("+"):
            vcc_roots[name] = net_of(find, members, name)
    r_gnd = net_of(find, members, "GND")
    shorted = []
    for name, r in vcc_roots.items():
        if r is not None and r == r_gnd:
            shorted.append(name)

    # ---- 检查 2: 元件两端同网 = 被旁路 ----
    # 按元件名聚合端子(端子名形如 R1.a / Q1.c / K1.t)
    by_comp = {}
    for tname in sc["terms"]:
        base, _, term = tname.rpartition(".")
        by_comp.setdefault(base, []).append(term)
    bypassed = []
    for base, terms in sorted(by_comp.items()):
        if len(terms) < 2:
            continue
        roots = {net_of(find, members, f"{base}.{t}")
                 for t in terms}
        roots.discard(None)
        if len(roots) == 1:
            net = sorted(nets.get(next(iter(roots)), []))
            hint = ""
            # 找"哪条线不该存在": 竖/横线纵穿本体
            body = sc["bodies"].get(base)
            if body:
                bx0, by0, bx1, by1 = _norm(body)
                for sg in wires:
                    x1, y1, x2, y2 = sg
                    if abs(x1 - x2) < 0.6 and bx0 - TOL <= x1 <= bx1 + TOL \
                       and min(y1, y2) < by0 - TOL and max(y1, y2) > by1 + TOL:
                        hint = (f" → 竖线 x={x1:.1f} 纵穿本体, "
                                f"改成从 y={by0:.1f} 或 y={by1:.1f} 起笔")
                        break
                    if abs(y1 - y2) < 0.6 and by0 - TOL <= y1 <= by1 + TOL \
                       and min(x1, x2) < bx0 - TOL and max(x1, x2) > bx1 + TOL:
                        hint = (f" → 横线 y={y1:.1f} 纵穿本体, "
                                f"改成从 x={bx0:.1f} 或 x={bx1:.1f} 起笔")
                        break
            if not hint:
                # 没有穿体线: 说明两端是靠**同一条导线的两端**并起来的,
                # 或者分别接到同一张网的两个不同支路。
                peers = [t for t in terms]
                hint = (f" → 端子 {peers} 落在同一张网; 顺着网表成员找那条"
                        f"把它们连起来的线")
            bypassed.append((base, net, hint))

    # ---- 检查 3: 导线纵穿元件本体 ----
    crossed = []
    for base, body in sorted(sc["bodies"].items()):
        bx0, by0, bx1, by1 = _norm(body)
        for sg in wires:
            x1, y1, x2, y2 = sg
            if abs(x1 - x2) < 0.6:                       # 竖线
                if bx0 - TOL <= x1 <= bx1 + TOL:
                    if min(y1, y2) < by0 - TOL and max(y1, y2) > by1 + TOL:
                        crossed.append((base, (x1, (by0 + by1) / 2),
                                        f" → 该竖线应止于 y={by0:.1f} "
                                        f"或从 y={by1:.1f} 起笔"))
            elif abs(y1 - y2) < 0.6:                     # 横线
                if by0 - TOL <= y1 <= by1 + TOL:
                    if min(x1, x2) < bx0 - TOL and max(x1, x2) > bx1 + TOL:
                        crossed.append((base, ((bx0 + bx1) / 2, y1),
                                        f" → 该横线应止于 x={bx0:.1f} "
                                        f"或从 x={bx1:.1f} 起笔"))

    # ---- 检查 4: \Expect 声明的期望网络没兑现 ----
    miswired = []
    for tname, want in sc["expect"]:
        if tname not in members:
            miswired.append((tname, want, None,
                             r"该端子没有登记(检查 \cPart 是否漏了端子)"))
            continue
        r_me = net_of(find, members, tname)
        # 目标: 网络名(+5V/GND) 或 "元件.端子"
        r_want = net_of(find, members, want)
        why = ""
        if r_want is None:
            base = want.split(".")[0]
            same = sorted(nm for nm in members
                          if nm == want or nm.endswith("." + want)
                          or nm.startswith(base))
            if same:
                why = f"没有 {want} 这个端子/网络; 相近的有 {same[:5]}"
            else:
                why = f"没有任何元件/网络叫 {want}"
        if r_want is None:
            miswired.append((tname, want, None, why))
        elif r_me != r_want:
            miswired.append((tname, want, sorted(nets.get(r_me, [])), ""))

    # ---- 检查 5: 孤标签 ----
    lonely_tags = sorted(n for n, pts in tag_pts.items() if len(pts) < 2)

    # ---- 检查 6: 端子没接到任何导线(电源/地符号除外) ----
    # 本方案里这结构上少见, 但 \Term 可以登记一个不画线的点 —— 留着当保险。
    #
    # **"没有导线"不等于"悬空"**: 两个端子直接对接(中间不画线, 如电阻下端
    # 正好落在电源符号的接点上)在电气上就是连上了。这种
    # "端子直接对接"(见其检查 4 的注释)。少了这条, 每种"元件直接坐在电源/
    # 地上"的画法都会误报悬空。
    others = list(sc["terms"].items()) + \
             [(f"电源{nm}", (x, y)) for nm, x, y in sc["powers"]]
    dangling = []
    for name, (x, y) in sorted(sc["terms"].items()):
        touch = sum(1 for s in wires if on_segment(x, y, *s))
        if not touch:
            touch = sum(1 for onm, (ox, oy) in others
                        if onm != name and abs(ox - x) < 1.2 and abs(oy - y) < 1.2)
        if not touch:
            dangling.append((name, (x, y)))

    res = {
        "n_wires": len(wires), "n_dots": len(dots),
        "n_terms": len(sc["terms"]), "n_bodies": len(sc["bodies"]),
        "nets": {k: sorted(v) for k, v in nets.items()},
        "members": {n: find(v) for n, v in members.items()},
        "shorted": shorted, "bypassed": bypassed, "crossed": crossed,
        "miswired": miswired, "lonely_tags": lonely_tags,
        "dangling": dangling, "slanted": slanted,
        "bad_h": bad_h, "bad_v": bad_v,
        "unparsed": sc["unparsed"],
        # 记账警告(M|)。**必须报出来**, 不能默默忽略: 它记的是"画图时发现的
        # 不合规事情"(零长导线、找不到 label 节点)。此前电气门完全不读它,
        # 于是 \Wire 里失效的零长判断静默穿过了两道门 —— 漏检能穿过去, 不是
        # 因为没有检查, 而是因为**检查结果没人看**。
        "warns": sc.get("warns", []),
    }
    return res


def _norm(box):
    x0, y0, x1, y1 = box
    return (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))


def report(path, r, show_nets=False):
    print(f"== {path}: {r['n_wires']} 条导线, {r['n_dots']} 个结点, "
          f"{r['n_terms']} 个端子, {r['n_bodies']} 个本体盒 ==")
    n = 0
    if r["unparsed"]:
        print(f"  [侧车] {len(r['unparsed'])} 行无法解析 —— 上面的数字偏少, "
              f"先查这些:")
        for ln, txt in r["unparsed"][:3]:
            print(f"           第{ln}行: {txt}")
        n += len(r["unparsed"])
    if r["warns"]:
        # 记账警告: 画图时 TeX 侧发现的不合规事情。不参与连通性判定,
        # 但**必须报** —— 见 analyze() 里 "warns" 那段注释。
        for w in r["warns"]:
            print(f"  [记账] {w}")
        if any("零长" in w for w in r["warns"]):
            print("           → 零长导线不影响连通性, 但它说明"
                  "**你以为两个点是分开的, 实际重合**。删掉那行即可; "
                  "若本意是要接线, 说明取错了锚点。")
        n += len(r["warns"])
    if r["bad_h"]:
        print(f"  [非正交] {len(r['bad_h'])} 条 **\\WireH 声明为水平线**, "
              f"但两端 y 不同 —— 它实际是斜线:")
        for s, dy in r["bad_h"][:3]:
            print(f"           {fmt_pt(s[0], s[1])} -> {fmt_pt(s[2], s[3])}"
                  f"  (y 相差 {dy:.2f}pt ≈ {dy*0.3528:.2f}mm)")
        print("           → 斜线会被连通性分析整个忽略(= 漏检)。改成两段: "
              "先 \\WireV 到目标行, 再 \\WireH 进端子; 或用 `A |- B` 取交点。")
        print("             若两端本意就是同一行, 检查是不是把某个锚点的 "
              "y 算错了。")
        n += len(r["bad_h"])
    if r["bad_v"]:
        print(f"  [非正交] {len(r['bad_v'])} 条 **\\WireV 声明为竖直线**, "
              f"但两端 x 不同 —— 它实际是斜线:")
        for s, dx in r["bad_v"][:3]:
            print(f"           {fmt_pt(s[0], s[1])} -> {fmt_pt(s[2], s[3])}"
                  f"  (x 相差 {dx:.2f}pt ≈ {dx*0.3528:.2f}mm)")
        print("           → 同上: 改用两段正交走线, 或用 `A -| B` 取交点。")
        n += len(r["bad_v"])
    if r["slanted"]:
        print(f"  [斜线] {len(r['slanted'])} 条斜线段（未声明 H/V 意图）—— "
              f"连通性里没有定义(交叉算不算连无从判定), 已被忽略:")
        for s in r["slanted"][:3]:
            print(f"           {fmt_pt(s[0], s[1])} -> {fmt_pt(s[2], s[3])}")
        print("           → 接线一律用 \\WireH / \\WireV, 它们在侧车里留下"
              " H/V 意图, 斜了会被报出来。")
        n += len(r["slanted"])
    if r["shorted"]:
        for name in r["shorted"]:
            print(f"  [短路] {name} 与 GND 落在同一张网！电路一上电就烧。")
        n += len(r["shorted"])
    for base, net, hint in r["bypassed"]:
        print(f"  [旁路] {base} 两端接在同一张网 —— 该元件形同不存在{hint}")
        n += 1
    for base, pt, hint in r["crossed"]:
        print(f"  [穿体] 导线纵穿 {base} 本体 @ {fmt_pt(*pt)} "
              f"—— 该元件被旁路{hint}")
        n += 1
    for name, pt in r["dangling"]:
        print(f"  [悬空] 端子 {name} @ {fmt_pt(*pt)} 没接到任何导线")
        n += 1
    for tname, want, actual, why in r["miswired"]:
        if actual is None:
            print(f"  [接错] {tname} 声明应与 {want} 同网, 但 {why}")
        else:
            print(f"  [接错] {tname} 声明应与 {want} 同网, 实际却在 {actual}")
        n += 1
    for tag in r["lonely_tags"]:
        print(f"  [孤标签] 网络标签「{tag}」只出现一次 —— 同号标签才相连, "
              f"只有一个等于什么都没连。")
        print(f"           → 检查编号是否写错（① 对 ②？）, 或另一端忘了画。")
        n += 1
    if show_nets:
        for i, (root, ms) in enumerate(sorted(r["nets"].items(),
                                              key=lambda kv: -len(kv[1])), 1):
            if len(ms) >= 2:
                print(f"    网{i}: {ms}")
    if n == 0:
        print("  电气连通性: 无短路 / 无旁路 / 无穿体 / 无悬空端子 / 无接错 / "
              "无孤标签 / 无斜线")
    return n


def main():
    ap = argparse.ArgumentParser(description="侧车网表连通性体检 (TikZ 路线)")
    ap.add_argument("files", nargs="+", help="侧车 .net 文件 (支持通配符)")
    ap.add_argument("--show-nets", action="store_true",
                    help="打印每张网的成员, 便于核对功能网是否正确")
    args = ap.parse_args()

    paths = []
    for pat in args.files:
        got = glob.glob(pat)
        paths.extend(got if got else [pat])

    total = 0
    for p in sorted(paths):
        if not os.path.exists(p):
            print(f"跳过(不存在): {p}")
            continue
        r = analyze(p, quiet=True)
        total += report(p, r, show_nets=args.show_nets)
        print()

    print(f"===== 合计电气问题数: {total} =====")
    if total:
        print("必须修掉才能交付。")
    else:
        print("电气连通性干净。注意: 这只证明「连对了」, 不证明参数选得对 ——")
        print("静态工作点、增益、电容取值仍需自己核对;")
        print("**极性/方向(LED 阳极、续流管方向)连通性检查永远查不出, 必须人眼看图。**")
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main())
