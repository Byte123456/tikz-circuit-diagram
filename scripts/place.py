#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
标注偏移解算器 —— 读侧车的 A| 记录, 写出 auto_offsets.tex。

为什么要有它
------------
手工摆标注是整条流程里**唯一**的大宗往返: 排版门报"把该标注上移 ≥21.9pt",
得有人把这个数换算成 cm 再回去改源码。这个换算完全机械, 所以交给机器。

关键在于**不重新发明排版器**。TeX 里出自适应摆放需要在图末拿到全部盒、
再重新排版试探位置 —— 挪一个标注可能牵动整图, 既慢又不保证收敛。这里换
一个思路: **把偏移从源码里的数字变成第二遍编译的输入**。

    第一遍 xelatex    偏移全 0, 标注叠在锚点上; 侧车里的 A| 记录带
                      "锚点坐标 + 文字盒", 由这两者可反推文字尺寸
    place.py          按角次序试位, 挑不压线/不压字/不压器件的位置
                      写出 auto_offsets.tex
    第二遍 xelatex    偏移生效

偏移是**显式数据**, 几何本身不动, 所以每遍都是确定的 —— 这是能收敛的原因。

用法
----
    python place.py <侧车.net> [-o auto_offsets.tex] [--report] [--pin 键=x,y]

退出码: 0 = 全部标注都找到了干净位置; 1 = 有标注没找到(文件仍写出, 取的是
最优解); 2 = 用法错误。

设计取舍
--------
* **只挪, 不改内容, 不改锚点。** 锚点是电路结构的一部分(它指示"这个标注
  说的是哪个器件"), 偏移只是排版。分开之后"改了布局"不会污染"标注归属"。
* **候选按角次序表逐排试。** 一个方向放不下时换下一方向, 比"原地拉远"收敛
  快得多 —— 元件左上方挤, west 不行时 east 往往就有大把空位。
* **`--pin` 钉死某些键。** 有些标注位置有语义(比如必须贴在某元件旁的
  "阴极朝上"), 不希望被自动挪走。钉死后 place.py 把它当固定障碍物。
* **解算顺序固定**(按锚点 x、再 y 排)。同一输入必得同一输出 —— 重跑一次
  不该让图变样, 否则每次编译都产生 diff。
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sidecar import parse_sidecar, is_orthogonal  # noqa: E402

PT2CM = 1.0 / 28.45274          # 1pt = 1/28.45274 cm

# 与 check_tex_layout.py 用同一套阈值, 免得"自动摆好了但门禁仍报"
CLEAR_TEXT = 2.0     # 与别的文字盒之间的最小空隙(pt)
CLEAR_WIRE = 2.5     # 与导线的净距(pt)
CLEAR_BODY = 2.5     # 与器件本体盒的净距(pt)
LINE_HALF = 0.6      # 导线半宽, 与 check_tex_layout.LINE_HALF 一致
# 说明块(`\AnnoPack`)之间的垂直间隙。比普通标注大 —— 说明块本来就是"一整段
# 文字", 挨太近会读成连续的一整块。取 10pt 约 3.5mm(手工排版里段间距的量级)。
PACK_GAP = 10.0

# 角次序 -> 单位方向。TikZ 的 north 是 +y(pt 坐标系里 y 向上)。
DIR = {
    "north": (0, 1), "south": (0, -1), "east": (1, 0), "west": (-1, 0),
    "north east": (0.7071, 0.7071), "north west": (-0.7071, 0.7071),
    "south east": (0.7071, -0.7071), "south west": (-0.7071, -0.7071),
    "above": (0, 1), "below": (0, -1), "left": (-1, 0), "right": (1, 0),
}
# 每个方向上的试探步长(倍数): 偏移 = 方向 × (半宽/半高 × 步长 + 半宽/半高)。
# 步长越大离锚点越远。0.62 那一档已经留出约 0.6 个文字宽的缝隙。
STEPS = [0.62, 0.80, 1.02, 1.30, 1.65, 2.10, 2.70, 3.45]


# ---------------------------------------------------------------- 几何
def _box_overlap(a, b, pad=0.0):
    """两盒在给定 pad 下是否相交。pad>0 表示要求额外的净距。"""
    return not (a[2] + pad <= b[0] or b[2] + pad <= a[0]
                or a[3] + pad <= b[1] or b[3] + pad <= a[1])


def _box_dist(a, b):
    """两盒的**分离距离**(Chebyshev, 即"净距"); 相交时返回 0。"""
    dx = max(b[0] - a[2], a[0] - b[2])
    dy = max(b[1] - a[3], a[1] - b[3])
    if dx <= 0 and dy <= 0:
        return 0.0
    return max(dx, dy)


def _seg_box_dist(seg, box):
    """
    正交线段到盒的净距(已扣掉线半宽); 相交/压线时返回负数。
    竖/横各判一次, 都退化成 1 维区间距离, 比通用线段距离简单也更稳。
    """
    x1, y1, x2, y2 = seg
    bx0, by0, bx1, by1 = box
    if abs(x1 - x2) < 0.6:                              # 竖线
        dx = max(bx0 - x1, x1 - bx1, 0.0)
        dy = max(by0 - max(y1, y2), min(y1, y2) - by1, 0.0)
    elif abs(y1 - y2) < 0.6:                            # 横线
        dx = max(bx0 - max(x1, x2), min(x1, x2) - bx1, 0.0)
        dy = max(by0 - y1, y1 - by1, 0.0)
    else:
        return 0.0                                      # 斜线不管(会被门禁报出来)
    d = max(dx, dy)
    return d - LINE_HALF if d > 0 else -LINE_HALF


def _box_at(a, ox, oy):
    """标注 a 在偏移 (ox,oy) 时的文字盒。偏移 0 时盒中心落在锚点上。"""
    x0, y0, x1, y1 = a["box"]
    hw, hh = (x1 - x0) / 2.0, (y1 - y0) / 2.0
    cx, cy = a["ax"] + ox, a["ay"] + oy
    return (cx - hw, cy - hh, cx + hw, cy + hh)


def _box_at_pack(a, ox, oy):
    """
    `\\AnnoPack` 的块盒: **anchor=north west**, 所以盒的左上角落在
    (锚点x + ox, 锚点y + oy), 而不是 \\AnnoAt 那样的"盒中心落在锚点"。
    这个差异必须判对 —— 判错了整列会偏半个块高(说明块高 100~230pt, 偏得离谱)。
    """
    x0, y0, x1, y1 = a["box"]
    w, h = abs(x1 - x0), abs(y1 - y0)
    lx, ty = a["ax"] + ox, a["ay"] + oy
    return (lx, ty - h, lx + w, ty)


def _offsets_for(a, corner, step):
    """某个方向 + 步长下的偏移。corner 为空 → 不换向, 只向下拉开。"""
    x0, y0, x1, y1 = a["box"]
    hw, hh = (x1 - x0) / 2.0, (y1 - y0) / 2.0
    d = DIR.get(corner)
    if d is None:
        return 0.0, -(hh * step + hh)
    return d[0] * (hw * step + hw), d[1] * (hh * step + hh)


def _penalty(box, obstacles):
    """盒与所有障碍的冲突程度。0 = 完全干净。"""
    pen = 0.0
    for ob, kind in obstacles:
        need = {"text": CLEAR_TEXT, "wire": CLEAR_WIRE, "body": CLEAR_BODY}[kind]
        if kind == "wire":
            d = _seg_box_dist(ob, box)
        else:
            d = _box_dist(box, ob)
        if d < need:
            pen += (need - d) if d > 0 else (need + 50.0)
    return pen


# ---------------------------------------------------------------- 解算
def solve(net_path, out_path, pins=None, report=False):
    sc = parse_sidecar(net_path)
    pins = pins or {}

    wires = [w for w in sc["wires"] if is_orthogonal(w)]
    annos = sc["annoat"]

    if not annos:
        print(f"== {net_path}: 侧车里没有 A| 记录。")
        print(r"   → 说明这张图用的是 \Anno 而不是 \AnnoAt —— 它的偏移是手写的,")
        print(r"     不能自动摆。要自动摆位就把标注改写成 "
              r"\AnnoAt{键}{样式}{锚点}{角次序}{文字}。")
        return 0

    # 钉死的键: 偏移由 --pin 给定, 当固定障碍物, 不参与解算
    fixed, todo = [], []
    for a in annos:
        (fixed if a["key"] in pins else todo).append(a)

    # **手写标注(\Anno)当固定障碍物**: 它们没有 A| 记录, 位置是作者钉死的
    # (页脚说明块这类)。不把它们算进障碍, 自动摆位会把别的标注叠上去。
    # 判据: A| 记录集合里没有的编号。
    auto_ns = {a["n"] for a in annos}
    manual = [(a[:4], "text") for a in sc["annos"]
              if a[6] not in auto_ns
              and abs(a[2] - a[0]) >= 0.5 and abs(a[3] - a[1]) >= 0.5]

    obstacles = ([(b, "body") for b in sc["bodies"].values()]
                 + [(w, "wire") for w in wires]
                 # 电源/地符号的**图形范围**也要当障碍 —— 只躲连接点是不够的,
                 # 符号本体(地符号的横杠、VCC 的箭头)照样会被字压住。
                 # 这正是"目视发现、门禁报 0"的那处缺陷的修法。
                 + [(tuple(g[1:]), "body") for g in sc.get("gsyms", [])]
                 + [(_box_at(a, *pins[a["key"]]), "text") for a in fixed]
                 + manual)

    warn = []
    for a in annos:
        # `PACK` 是 `\AnnoPack` 的标记(角次序字段), 不是方向名 —— 别报它。
        if a["corners"].strip().upper() == "PACK":
            continue
        for c in [c.strip() for c in a["corners"].split(",") if c.strip()]:
            if c not in DIR:
                warn.append(f"#{a['n']} {a['key']}: 角次序里的 {c!r} 不认识, "
                            f"可用的是 {sorted(DIR)}")
    # 钉死的位置也可能本来就冲突 —— 那是人的决定, 只提示不自动改
    if report:
        for a in fixed:
            box = _box_at(a, *pins[a["key"]])
            ob = ([(b, "body") for b in sc["bodies"].values()]
                  + [(w, "wire") for w in wires])
            p = _penalty(box, ob)
            if p > 0:
                warn.append(f"#{a['n']} {a['key']}: 钉死的位置本身就不干净 "
                            f"(冲突 {p:.1f})")

    # ---- 先处理 PACK 组(说明块列内堆叠) ----
    # `\AnnoPack` 的 A| 记录把角次序写成 `PACK`, 走这条独立分支。
    # 与 `\AnnoAt` 的模型不同: `\AnnoAt` 是"相对锚点朝某方向偏移", 而说明块
    # **没有元件锚点**, 它要的是**一列里顺序堆叠**。
    #
    # `\AnnoPack` 用 anchor=north west, 所以盒的**左上角**落在 (列x, 列顶y+偏移)。
    # 于是堆叠就是: 第 i 块的 oy = -(前面各块高度之和 + i×间隙)。
    # ⚠ 一定要用**真实高度**(侧车里的盒高), 不能估 —— 说明块高 100~230pt,
    #   估错就是大片重叠。这正是手猜坐标失败的原因。
    pack_groups = {}
    for a in todo:
        if a["corners"].strip().upper() == "PACK":
            pack_groups.setdefault((round(a["ax"], 3), round(a["ay"], 3)),
                                   []).append(a)

    placed = []
    n_dirty = 0
    packed = []
    for gk, members in sorted(pack_groups.items()):
        members.sort(key=lambda a: int(a["n"]))
        used = 0.0                       # 本列已占用的高度(pt, 正数)
        for i, a in enumerate(members):
            x0, y0, x1, y1 = a["box"]
            h = abs(y1 - y0)
            a["_ox"] = 0.0
            a["_oy"] = -used             # 负 = 向下
            a["_corner"] = "PACK"
            box = _box_at_pack(a, a["_ox"], a["_oy"])
            pen = _penalty(box, obstacles + placed)
            a["_pen"] = pen
            if pen > 0.0:
                n_dirty += 1
            placed.append((box, "text"))
            packed.append(a)
            used += h + PACK_GAP

    packed_ids = {id(a) for a in packed}
    todo = [a for a in todo if id(a) not in packed_ids]

    # 顺序固定 → 结果可复现。左边的先占位, 让右侧标注知道自己左边被占了。
    todo.sort(key=lambda a: (round(a["ax"], 3), round(a["ay"], 3)))

    # ---- 按锚点分组 ----
    # ⚠ 同锚点的多个标注**必须整组一起解**, 不能各自独立解。
    # 踩过的坑: PD1 上有两个标注(pd1name / pd1rev), 独立解时第一个占了
    # south west, 第二个就没位置了, 报"挤 52.0"。而人画的版本是**同一侧
    # 上下叠两行** —— 那才是"一个元件配多条说明"的自然排法。
    # 所以: 选一个方向, 在该方向上离锚点固定距离, 然后沿**垂直于该方向的轴**
    # 依次排开(west/east 竖着叠, north/south 横着排)。
    groups = {}
    for a in todo:
        groups.setdefault((round(a["ax"], 3), round(a["ay"], 3)), []).append(a)
    order = sorted(groups, key=lambda k: (k[0], k[1]))

    # placed / n_dirty 已在 PACK 段初始化(说明块先占位), 这里不要重置。
    for gk in order:
        members = sorted(groups[gk], key=lambda a: int(a["n"]))
        ax, ay = gk
        cand = []
        seen = set()
        for a in members:                     # 各成员角次序的并集, 保序去重
            for c in [c.strip() for c in a["corners"].split(",") if c.strip()] or [""]:
                if c not in seen:
                    seen.add(c)
                    cand.append(c)

        best = None
        for ci, corner in enumerate(cand):
            d = DIR.get(corner)
            for si, step in enumerate(STEPS):
                if d is None:                 # 空角次序 -> 纯向下叠
                    d, perp = (0, -1), (0, 1)
                elif abs(d[0]) > 0 and abs(d[1]) == 0:
                    perp = (0, 1)             # west/east: 竖直叠
                elif abs(d[1]) > 0 and abs(d[0]) == 0:
                    perp = (1, 0)             # north/south: 水平排
                else:
                    perp = None               # 对角: 沿方向径向叠
                boxes, tot_d, bad = [], 0.0, 0.0
                cursor = 0.0
                for a in members:
                    x0, y0, x1, y1 = a["box"]
                    hw, hh = (x1 - x0) / 2.0, (y1 - y0) / 2.0
                    if perp is None:
                        off = ((hw * step + hw) * d[0],
                               (hh * step + hh) * d[1])
                    elif perp == (0, 1):      # 竖直叠: 按各自半高累加
                        off = (d[0] * (hw * step + hw), cursor + hh)
                        cursor += 2 * hh + 3.0
                    else:                     # 水平排: 按各自半宽累加
                        off = (cursor + hw, d[1] * (hh * step + hh))
                        cursor += 2 * hw + 3.0
                    bx = _box_at(a, off[0], off[1])
                    boxes.append((a, off, bx))
                    tot_d += (off[0] ** 2 + off[1] ** 2) ** 0.5
                    bad += _penalty(bx, obstacles + placed)
                # 组内互查(前面 _penalty 只查 placed, 不含本组成员)
                for i in range(len(boxes)):
                    for j in range(i + 1, len(boxes)):
                        dd = _box_dist(boxes[i][2], boxes[j][2])
                        if dd < CLEAR_TEXT:
                            bad += (CLEAR_TEXT - dd) + 50.0
                score = (0 if bad <= 0.0 else 1, bad if bad > 0 else 0.0,
                         round(tot_d, 3), ci, si)
                if best is None or score < best[0]:
                    best = (score, boxes, corner, bad)

        _, boxes, corner, pen = best
        for a, off, bx in boxes:
            a["_ox"], a["_oy"], a["_corner"] = off[0], off[1], corner
            a["_pen"] = pen if len(members) == 1 else (0.0 if pen <= 0 else pen)
            placed.append((bx, "text"))
        if pen > 0.0:
            n_dirty += len(members)

    # 写偏移时 PACK 块要一起写(它们不在 todo 里了, 但同样需要 auto_offsets.tex
    # 里的 snao<键> 才生效)。
    all_solved = sorted(packed + todo, key=lambda a: int(a["n"]))
    _write(out_path, net_path, all_solved, warn)
    _report(all_solved, packed, fixed, net_path, n_dirty, out_path, report)
    return 1 if n_dirty else 0


def _write(out_path, net_path, todo, warn):
    lines = [
        "% 本文件由 place.py 自动生成 —— 不要手改。",
        f"% 来源: {os.path.basename(net_path)}",
        "% 改标注位置: 改 .tex 里 \\AnnoAt 的**角次序**(第 4 个参数);",
        "% 说明块(\\AnnoPack)改**列顶坐标**, 或用 place.py 的阈值调间隙。",
        "% 单位 cm(TeX 里这样最直观; 侧车里的坐标是 pt)。",
        "% 键名不带 @ —— 偏移文件在正文里 \\input, 那时 @ 不是字母。",
        "",
    ]
    for a in todo:
        lines.append(f"\\expandafter\\def\\csname snao{a['key']}\\endcsname"
                     f"{{{a['_ox'] * PT2CM:.3f}cm,{a['_oy'] * PT2CM:.3f}cm}}"
                     f"% {a['_corner'] or '下方'}")
    if warn:
        lines.append("")
        lines.extend("% 警告: " + w for w in warn)
    lines.append("")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def _report(todo, packed, fixed, net_path, n_dirty, out_path, verbose):
    if verbose or n_dirty:
        n_pack = len(packed)
        n_pos = len(todo) - n_pack
        print(f"== {net_path}: {len(todo) + len(fixed)} 个自动标注 "
              f"({n_pos} 试位 / {n_pack} 列内堆叠 / {len(fixed)} 钉死)")
        for a in sorted(todo, key=lambda a: int(a["n"])):
            tag = "干净" if a["_pen"] <= 0.0 else f"**挤** {a['_pen']:.1f}"
            how = "堆叠" if a["_corner"] == "PACK" else (a["_corner"] or "下方")
            print(f"   #{int(a['n']):>2} {a['key']:<16} "
                  f"→ {how:<10} "
                  f"偏移 ({a['_ox'] * PT2CM:+.2f},{a['_oy'] * PT2CM:+.2f})cm  {tag}")
        for a in baseline_sorted(fixed):
            print(f"   #{int(a['n']):>2} {a['key']:<16} → 钉死")
    msg = f"已写出 {out_path}"
    if n_dirty:
        msg += (f"   ⚠ {n_dirty} 个标注没找到完全干净的位置(取的是最优解; "
                f"建议调整角次序/列位置, 或挪开挡路的器件)")
    else:
        msg += "   全部干净"
    print(msg)


def baseline_sorted(xs):
    return sorted(xs, key=lambda a: int(a["n"]))


def main():
    ap = argparse.ArgumentParser(
        description="从侧车解算标注偏移, 写出 auto_offsets.tex")
    ap.add_argument("net", help="侧车 .net 文件")
    ap.add_argument("-o", "--out", default=None,
                    help="输出文件(默认与 .net 同目录的 auto_offsets.tex)")
    ap.add_argument("--report", action="store_true", help="逐个列出解算结果")
    ap.add_argument("--pin", action="append", default=[], metavar="键=x,y",
                    help="钉死某键的偏移(单位 pt), 不参与自动摆位。可多次给")
    args = ap.parse_args()

    if not os.path.exists(args.net):
        print(f"错误: 找不到 {args.net}", file=sys.stderr)
        return 2

    pins = {}
    for p in args.pin:
        if "=" not in p:
            print(f"错误: --pin 要写成 键=x,y, 收到 {p!r}", file=sys.stderr)
            return 2
        k, v = p.split("=", 1)
        try:
            x, y = (float(t) for t in v.split(","))
        except ValueError:
            print(f"错误: --pin 的坐标要能解析成两个数, 收到 {v!r}", file=sys.stderr)
            return 2
        pins[k.strip()] = (x, y)

    out = args.out or os.path.join(
        os.path.dirname(os.path.abspath(args.net)), "auto_offsets.tex")
    return solve(args.net, out, pins, args.report)


def _main_guarded():
    """
    把"工具自己崩了"与"有标注摆不开"分开。

    `solve()` 返回 1 表示**有标注摆不开**(仍写出了可用的偏移表), 这是良性的;
    build.py 见到 1 会继续编译。但裸的未捕获异常会让 Python 以 **1** 退出 ——
    于是 place.py **崩溃**和"摆不开"同码, build.py 无从区分, 崩溃被当成
    良性继续往下跑, 而标注用的是陈旧的/根本没生成的偏移表。

    这里兜住所有异常, 以 2 退出(与"用法错误"同码, 都是"工具没跑成")。
    """
    try:
        return main()
    except Exception as e:                       # noqa: BLE001
        import traceback
        traceback.print_exc()
        print(f"place.py 内部错误: {type(e).__name__}: {e}", file=sys.stderr)
        print("→ 这是工具缺陷, 不是你的图的问题。偏移**没有**算出来, "
              "别当成功。", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(_main_guarded())
