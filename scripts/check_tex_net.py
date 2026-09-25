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
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sidecar import (parse_sidecar, build_nets, net_of,
                     is_orthogonal, seg_cross, fmt_pt, TOL)   # noqa: E402


# ---- 电源/地的识别 ----------------------------------------------------
# 为什么需要一张**较宽**的表: 短路是"一上电就烧"的最高危缺陷, 而早期实现只把
# `VCC`/`VDD`/`+` 开头当作电源、只把精确的 `GND` 当作地。真实工程里
# `5V`/`3V3`/`VBUS`/`VIN`(以及 `VSS`/`GNDA`/`AGND` 这类地)极常见, 它们与地
# 短路时**一律漏检**(实测: `5V`-`GND` 死短路报"干净", 换成 `+5V` 才报)。
#
# 宁可宽一点: 把某个网络名误判成"电源"的代价是**多报一条待核**; 漏判的代价
# 是**放过一个死短路**。前者可接受, 后者不可接受。
_RAIL_RE = re.compile(
    r"^(?:\+.*"                                   # +5V / +3.3V / +12V
    r"|P?\d+(?:[._]?\d+)?V\d*$"                   # 5V / 3V3 / 5V0 / 3.3V / P3V3
    r"|V(?:CC|DD|BUS|IN|BAT|SYS|DDA|REF|DDIO|CCIO)$"
    r"|VBAT$|VUSB$)$",
    re.IGNORECASE)
_GROUND_RE = re.compile(
    r"^(?:GND[A-Z0-9_]*|AGND|DGND|VSS[A-Z0-9]*|0V|EARTH|GNDD|.*_GND)$",
    re.IGNORECASE)


def is_rail(name):
    """这个网络名看着像**电源轨**吗(VCC/VDD/VBUS/5V/3V3/+xV...)?"""
    return bool(_RAIL_RE.match(name.strip()))


def is_ground(name):
    """这个网络名看着像**地**吗(GND/VSS/GNDA/AGND...)? """
    return bool(_GROUND_RE.match(name.strip()))


def analyze(net_path, quiet=False):
    """跑全部门禁, 返回结果 dict（供 check_tex_layout / 测试复用）。"""
    sc = parse_sidecar(net_path)
    find, members, nets, wires, dots, tag_pts, near_miss = build_nets(sc)

    # ---- 斜线 ----
    slanted = [w for w in sc["wires"] if not is_orthogonal(w)]
    # ---- 意图违背: \WireH 声明水平、\WireV 声明竖直, 但坐标不是 ----
    # 这是本方案要堵的洞: 若**静默丢弃**斜线, 于是
    # 一条斜着画、两端又没真接上的线完全"不存在", 图看着连了、检查器说
    # 没问题。这里因为画线时写了 WH/WV 意图, 就能明确报出来。
    bad_h = [(s, abs(s[1] - s[3])) for s in sc["hint_h"] if abs(s[1] - s[3]) > 0.5]
    bad_v = [(s, abs(s[0] - s[2])) for s in sc["hint_v"] if abs(s[0] - s[2]) > 0.5]

    # ---- 检查 1: 电源/地短路 ----
    # 三类都报: 电源-地(VCC/GND)、电源-电源(5V/3V3)、地-地(GND/AGND)。
    # 用 NetTag 连起来的**同名**网络已合并成一张网, 所以"同网"就是真短路。
    rails, grounds = {}, {}
    for name in members:
        if is_rail(name):
            rails[name] = net_of(find, members, name)
        elif is_ground(name):
            grounds[name] = net_of(find, members, name)
    shorted = []
    # 电源-地: 最危险
    for rn, r in rails.items():
        for gn, g in grounds.items():
            if r is not None and r == g:
                shorted.append((rn, gn, "电源与地"))
    # 电源-电源 / 地-地: 不同名的两条轨被接在一起
    for group, kind in ((rails, "两条电源"), (grounds, "两种地")):
        names = sorted(group)
        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                if group[names[i]] is not None and \
                   group[names[i]] == group[names[j]]:
                    shorted.append((names[i], names[j], kind))

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
            # 一个名字可能有多条本体盒(线圈+触点), 逐个试。
            for body in sc["bodies_by_name"].get(base, []):
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
    for base, body in sc["bodies"]:
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
    # 正好落在电源符号的接点上)在电气上就是连上了。这种"端子直接对接"由
    # build_nets 里的点-点对接规则负责(见其注释)。
    #
    # ⚠ 判据**必须从网表来**, 不能另写一套几何距离: 此前这里另用了 1.2pt、
    #   网表用 0.8pt, 于是相差 0.5pt 的两个点会出现"网表判分离、悬空不报" ——
    #   两个门禁对同一件事给出矛盾结论, 而用户不知道该信哪个。现在统一:
    #   端子的网里若既没有导线、又没有别的成员, 才算悬空。
    wire_roots = {find(("seg", i)) for i in range(len(wires))}
    dangling = []
    for name in sorted(sc["terms"]):
        r = net_of(find, members, name)
        if r in wire_roots:
            continue                       # 挂上了某条导线 -> 不悬空
        if len(nets.get(r, [])) >= 2:
            continue                       # 与别的端子/电源直接对接 -> 不悬空
        dangling.append((name, sc["terms"][name]))

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
        # 同名端子/本体盒的**异坐标**重复登记 —— 静默覆盖会凭空少一个电气
        # 实体, 而图上看不出来(图照常画)。必须报。
        "conflicts": sc.get("conflicts", []),
        # 相差 >TOL 但 <=NEAR_TOL 的点对: **未连通**, 但几乎贴上 —— 图上看着
        # 连了、电气上是断的, 正是要报的那类。
        "near_miss": near_miss,
        # 侧车是不是"什么都没记"。空侧车不代表电路干净, 代表**没检查过** ——
        # 这是本项目最危险的失效模式("查了没问题"与"压根没看"长得一样)。
        "empty": not (sc["wires"] or sc["terms"] or sc["bodies"]),
        "n_annos": len(sc["annos"]),
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
    if r.get("empty"):
        # 空侧车 / 侧车里没有任何电气实体: **不是"电路干净", 是"没检查过"**。
        # 必须报出来 —— 否则"漏跑编译"与"电路正确"在输出上完全一样。
        print("  [空侧车] 侧车里没有任何导线/端子/本体 —— 这不是「电路干净」, "
              "是**根本没检查到东西**。")
        print("           → 确认 .tex 里用了 \\Wire/\\cPart 等会记账的宏, "
              "且 \\DumpCanvas 在 \\end{tikzpicture} 之前;")
        print("             或该 .net 是上一次失败编译留下的残片 —— "
              "删掉重跑 build.py。")
        n += 1
    for kind, name, old, new, lineno in r.get("conflicts", []):
        # 只可能是端子(T): 本体盒允许同名多条(继电器线圈+触点), 已在解析里
        # 改成保留全部、不覆盖, 所以不再产生 B 冲突。
        print(f"  [重名] 端子「{name}」被登记在两个不同位置 "
              f"(第{lineno}行 {new} vs 先前 {old}) —— 一个端子名只能对应"
              f"一个电气点, 后者会**静默覆盖**前者, 等于凭空少一个端子。")
        print(f"           → 两个不同器件的端子用了同一个名字。改名"
              f"(器件名必须 ASCII 且唯一), 或确认它们本就该重合。")
        n += 1
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
    for a, b, kind in r["shorted"]:
        print(f"  [短路] {kind}「{a}」与「{b}」落在同一张网！"
              f"电路一上电就烧(或至少部分失效)。")
        print(f"           → 顺着网表找那条把它们连起来的线: "
              f"check_tex_net.py --show-nets")
        n += 1
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
    for na, nb, d in r.get("near_miss", []):
        print(f"  [近接] {na} 与 {nb} 相距仅 {d:.2f}pt —— "
              f"**未连通**(容差 {TOL}pt), 但图上看着贴住了。")
        print(f"           → 电气上是断的。用 terminals()/命名坐标取准确位置"
              f"让两端重合, 或补一条导线真正接上。")
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
    missing = 0
    for p in sorted(paths):
        if not os.path.exists(p):
            # **不能当成"跳过"** —— 路径写错、build 没产出、文件被删, 全都
            # 走到这里。静默跳过等于"输入错也算通过", 与漏检同性质。
            print(f"错误: 侧车不存在: {p}", file=sys.stderr)
            missing += 1
            continue
        if os.path.getsize(p) == 0:
            print(f"错误: 侧车是空文件: {p} "
                  f"(没检查到任何东西, 不等于电路干净)", file=sys.stderr)
            missing += 1
            continue
        r = analyze(p, quiet=True)
        total += report(p, r, show_nets=args.show_nets)
        print()

    if missing:
        print(f"===== {missing} 个侧车**根本没检查**(不存在或为空) =====", file=sys.stderr)
        return 2
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
