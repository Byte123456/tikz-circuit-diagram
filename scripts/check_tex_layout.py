#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
排版体检（TikZ 路线）—— 图**好不好看**，与 check_tex_net.py（电路**对不对**）互补。

用法:
    python check_tex_layout.py <侧车.net> [更多.net...]
    python check_tex_layout.py *.net

退出码: 0 = 排版上没查出问题, 1 = 有 / 2 = 用法或环境错误。

七类检查:
  1. 文字重叠    两个标注的排版盒相交
  2. 文字压线    标注盒与导线相交（压在线上, 看着像"这根线被截断了"）
  3. 平行线过近  两条平行导线的间距小于阈值
  4. 器件越界    元件本体盒超出画布
  5. 器件重叠    两个元件本体盒相交

每条检查都用**真实排版盒**, 不是估算 —— TikZ 排版完直接给
north east / south west 的真实盒, 中英混排、数学公式($I_b$)、上下标
全都算得准。所以"文字重叠"这类误报/漏报会明显少。

⚠ **不能改成按字符数估宽度**: 中文按字拍脑袋、英文按固定系数, 长中文串
偏差很大, 而字宽直接决定"判不判重叠"。

**报错都给可照做的改法**（"把 R1 上移到 y≤247"这种），
不是只说"有问题"。
"""
import argparse
import glob
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sidecar import parse_sidecar, fmt_pt, is_orthogonal, on_segment  # noqa: E402

PT2MM = 0.3527778

# 阈值（单位 pt）。注意 pt 与 px 数值不可直接比 —— 12.5px 的字高
# 在这里约 9.4pt, 所以下面按"视觉等效"取, 并都在报告里注明。
MIN_TEXT_GAP = 1.0        # 两个文字盒之间至少要空的距离
MIN_PARALLEL = 7.0        # 两条平行导线的最小中心距(≈2.5mm)
LINE_HALF = 0.6           # 导线半宽：判"文字压线"时给线留的厚度


def _box(b):
    x0, y0, x1, y1 = b
    return (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))


def _inter(a, b, pad=0.0):
    """两个 AABB 是否有交集(可留 pad 间隙)。"""
    ax0, ay0, ax1, ay1 = _box(a)
    bx0, by0, bx1, by1 = _box(b)
    return not (ax1 + pad <= bx0 or bx1 + pad <= ax0
                or ay1 + pad <= by0 or by1 + pad <= ay0)


def _overlap_area(a, b):
    ax0, ay0, ax1, ay1 = _box(a)
    bx0, by0, bx1, by1 = _box(b)
    dx = min(ax1, bx1) - max(ax0, bx0)
    dy = min(ay1, by1) - max(ay0, by0)
    return (max(dx, 0.0), max(dy, 0.0))


def _seg_in_box(seg, box, half=LINE_HALF):
    """线段穿过盒(把线宽算成 half 的厚度)。只认正交线段。"""
    x1, y1, x2, y2 = seg
    bx0, by0, bx1, by1 = _box(box)
    if abs(x1 - x2) < 0.6:                        # 竖线
        return (bx0 - half <= x1 <= bx1 + half
                and min(y1, y2) < by1 - half and max(y1, y2) > by0 + half)
    if abs(y1 - y2) < 0.6:                        # 横线
        return (by0 - half <= y1 <= by1 + half
                and min(x1, x2) < bx1 - half and max(x1, x2) > bx0 + half)
    return False


def _parallel_gap(a, b):
    """两条平行正交线的中心距；不平行返回 None。"""
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    a_v, b_v = abs(ax1 - ax2) < 0.6, abs(bx1 - bx2) < 0.6
    a_h, b_h = abs(ay1 - ay2) < 0.6, abs(by1 - by2) < 0.6
    if a_v and b_v:
        # 竖直平行: 比 x；且 y 区间要**真的重叠**才算"平行段"
        ox = min(max(ay1, ay2), max(by1, by2)) - max(min(ay1, ay2), min(by1, by2))
        if ox <= 0:
            return None
        return (abs(ax1 - bx1), "x", ox)
    if a_h and b_h:
        ox = min(max(ax1, ax2), max(bx1, bx2)) - max(min(ax1, ax2), min(bx1, bx2))
        if ox <= 0:
            return None
        return (abs(ay1 - by1), "y", ox)
    return None


def analyze(path, min_text_gap=MIN_TEXT_GAP, min_parallel=MIN_PARALLEL,
            margin=0.0, strict_bounds=False):
    sc = parse_sidecar(path)
    wires = [w for w in sc["wires"] if is_orthogonal(w)]
    # **滤掉零面积文字盒**: \Chain/\Bus 为了"值为空时不标"统一写 `l={\v}`,
    # 空值会让 circuitikz 生成一个没有内容的 label 节点。它面积是 0, 参与
    # 碰撞检测只会制造噪音(还可能报出"与自己重叠"之类的假问题)。
    # 判据用面积: 宽 <0.5pt 或高 <0.5pt 的都当"没有字"。
    annos = [a for a in sc["annos"]
             if abs(a[2] - a[0]) >= 0.5 and abs(a[3] - a[1]) >= 0.5]
    # 器件本体盒 + 容器框一起参与"越界/重叠"(都占版面);
    # 但"文字压器件"**只看本体盒** —— 容器(MCU 方块)里本来就写字(引脚名),
    # 按本体盒判会每个引脚误报一次。见 \ContainerBox 的注释。
    real_bodies = list(sc["bodies"])
    bodies = real_bodies + list(sc.get("containers", []))

    # ---- 1. 文字重叠 ----
    text_overlaps = []
    for i in range(len(annos)):
        for j in range(i + 1, len(annos)):
            a, b = annos[i], annos[j]
            if _inter(a[:4], b[:4], pad=min_text_gap):
                dx, dy = _overlap_area(a[:4], b[:4])
                text_overlaps.append((a[4], b[4], a[:4], b[:4], dx, dy))

    # ---- 2. 文字压线 ----
    text_on_wire = []
    for a in annos:
        box = a[:4]
        for w in wires:
            if _seg_in_box(w, box):
                text_on_wire.append((a[4], w, box))
                break

    # ---- 3. 平行线过近 ----
    # 查的是"平行线太近看着像短路"。**但同网的两条平行线不算问题**
    # (比如同一根地的两个分支), 所以这里只报不同网的 —— 需要网表配合,
    # 为保持 check 独立(只看排版), 这里报全部过近对, 由人判断。
    too_close = []
    for i in range(len(wires)):
        for j in range(i + 1, len(wires)):
            g = _parallel_gap(wires[i], wires[j])
            if g is None:
                continue
            dist, axis, span = g
            if 0.4 < dist < min_parallel and span > 2.0:
                too_close.append((wires[i], wires[j], dist, axis, span))

    # ---- 4. 器件越界 ----
    # 没有 C| 记录时**不能沉默**: \DumpCanvas 是手工调用的宏, 漏写就没有画布。
    # 此时"器件越界"整类检查无从进行, 报告里却照样打"无器件越界" —— 与"真的
    # 没越界"完全不可分。改成明确报告"这项没查成"(no_canvas), 计入问题数。
    out_of_canvas = []
    canvas = sc["canvas"]
    no_canvas = canvas is None and bool(bodies)
    if canvas:
        cx0, cy0, cx1, cy1 = _box(canvas)
        for name, b in bodies:
            bx0, by0, bx1, by1 = _box(b)
            if bx0 < cx0 - margin or bx1 > cx1 + margin \
               or by0 < cy0 - margin or by1 > cy1 + margin:
                out_of_canvas.append((name, b, canvas))

    # ---- 5. 器件重叠 ----
    comp_overlaps = []
    for i in range(len(bodies)):
        for j in range(i + 1, len(bodies)):
            (na, ba), (nb, bb) = bodies[i], bodies[j]
            if _inter(ba, bb, pad=0.5):
                dx, dy = _overlap_area(ba, bb)
                if dx > 0.5 and dy > 0.5:
                    comp_overlaps.append((na, nb, ba, bb, dx, dy))

    # ---- 6. 文字压在器件本体上 ----
    # 这一类是**最容易漏的**: 只比文字盒之间、文字盒与导线是不够的:
    # 「文字重叠」只比文字盒之间, 「文字压线」只比文字盒与导线 ——
    # 于是"字压在元件方框线上"两边都漏。
    #
    # 这是基准测试里 TikZ 侧 agent 实测踩到的: 蜂鸣器 BZ1 的 +/− 记号放在
    # 方框的 north/south 锚点上, x 仍在方框宽度内 -> 与本体盒重叠, 而两道门
    # 都不报。渲染图上就是"字横压在框线上", 读起来像元件的一部分。
    #
    # 判据: **文字盒的中心落在本体盒内**。
    #
    # 为什么不用"两盒相交就报": circuitikz 自动摆放的值标签(如电阻上方的
    # `10K`)**本来就贴着元件本体**, 实测重叠约 2pt —— 那是正常排版, 相交
    # 判据会对每个带值标签的元件误报。而真正的"字压在框线上"(基准测试里
    # 蜂鸣器 B+/− 那个案例)是文字**中心**进到本体里, 重叠 3pt 以上。
    # 中心判据把这两类干净地分开, 比调阈值更可靠 —— 它有物理含义:
    # "这个字写在元件身上了"。
    #
    # 仍留一点余量: 中心要进本体**内侧 0.5pt** 才算, 免得正好压边界的
    # 标注来回抖动。
    text_on_body = []
    for a in annos:
        cx = (a[0] + a[2]) / 2.0
        cy = (a[1] + a[3]) / 2.0
        for name, bb in real_bodies:
            bx0, by0, bx1, by1 = _box(bb)
            if (bx0 + 0.5 <= cx <= bx1 - 0.5
                    and by0 + 0.5 <= cy <= by1 - 0.5):
                # 报重叠量时仍按盒相交算, 便于判断严重程度
                dx, dy = _overlap_area(a[:4], bb)
                text_on_body.append((a[4], name, bb, dx, dy))

    # ---- 7. 文字压在电源/地符号上 ----
    # 与第 6 类同源, 但障碍是**符号图形**而不是器件本体: 接地符号的三道横杠
    # 与立杆、VCC 符号的箭头。\Power 的 P| 只记一个**连接点**, 拿点跟文字盒
    # 比永远比不出重叠 —— 所以这里用 GS| 记的图形范围。
    #
    # 这个盲区是**目视**发现的: 示例图里 "D2 1N4148 续流" 的标注盒正好压住
    # C1 的接地符号(横杠落在两个数字中间), 而两道门都报 0。判据用"两盒相交"
    # 就够 —— 与值标签不同, 地/VCC 符号是**画出来的图形**, 不是排版标签,
    # 文字压上去没有任何"本来就这样"的正当理由。
    text_on_gsym = []
    for a in annos:
        for name, gx0, gy0, gx1, gy1 in sc.get("gsyms", []):
            gb = _box((gx0, gy0, gx1, gy1))
            if _inter(a[:4], gb, pad=0.0):
                dx, dy = _overlap_area(a[:4], gb)
                text_on_gsym.append((a[4], name, gb, dx, dy))

    return {"text_overlaps": text_overlaps, "text_on_wire": text_on_wire,
            "too_close": too_close, "out_of_canvas": out_of_canvas,
            "comp_overlaps": comp_overlaps, "text_on_body": text_on_body,
            "text_on_gsym": text_on_gsym,
            "n_wires": len(wires),
            "n_annos": len(annos), "n_bodies": len(bodies),
            "canvas": canvas, "warns": sc.get("warns", []),
            "unparsed": sc["unparsed"],
            # 侧车里没有 C| 却又有器件盒: "器件越界"这一整类**没法查**。
            # 不能和"真的没越界"混为一谈。
            "no_canvas": no_canvas,
            # 侧车里没有任何可查对象 —— 不是"排版干净", 是**没东西可查**。
            "empty": not (wires or annos or bodies or sc.get("gsyms")),
            "n_containers": len(sc.get("containers", []))}


def _short(s, n=26):
    s = s.replace("\n", " ")
    return s if len(s) <= n else s[:n] + "…"


def report(path, r, verbose=False):
    print(f"== {path}: {r['n_wires']} 条导线, {r['n_annos']} 个文字盒, "
          f"{r['n_bodies']} 个器件盒 ==")
    n = 0
    if r["unparsed"]:
        print(f"  [侧车] {len(r['unparsed'])} 行无法解析, 排版结论不可信:")
        for ln, t in r["unparsed"][:3]:
            print(f"           第{ln}行: {t}")
        n += len(r["unparsed"])
    if r.get("empty"):
        print("  [空侧车] 侧车里没有导线/文字盒/器件盒 —— 这不是「排版干净」, "
              "是**根本没检查到东西**。")
        print("           → 确认 .tex 真的画了东西且 \\DumpCanvas 在 "
              "\\end{tikzpicture} 之前; 或删掉这份残片重跑 build.py。")
        n += 1
    if r.get("no_canvas"):
        # 有器件盒但没有 C|(画布): "器件越界"整类查不了。别让它静默通过。
        print("  [无画布] 侧车里有器件盒但没有 C| 记录 —— **「器件越界」"
              "这一类没查成**。")
        print("           → 多半是 \\DumpCanvas 漏写或写在了 "
              "\\end{tikzpicture} 之后。它必须在之前。")
        n += 1
    for w in r["warns"]:
        print(f"  [记账] {w}")
        n += 1
    if r["canvas"]:
        cx0, cy0, cx1, cy1 = _box(r["canvas"])
        print(f"    画布 {cx1-cx0:.0f} × {cy1-cy0:.0f} pt "
              f"({(cx1-cx0)*PT2MM:.0f} × {(cy1-cy0)*PT2MM:.0f} mm)")
    for na, nb, ba, bb, dx, dy in r["comp_overlaps"]:
        print(f"  [器件重叠] {na} 与 {nb} 的本体盒相交 "
              f"{dx:.1f}×{dy:.1f}pt（{dx*PT2MM:.1f}×{dy*PT2MM:.1f}mm）")
        # 给出可照做的改法: 往哪个方向挪多少
        ax0, ay0, ax1, ay1 = _box(ba)
        bx0, by0, bx1, by1 = _box(bb)
        need_x = dx + 2.0
        need_y = dy + 2.0
        if dy <= dx:
            if ax1 <= bx1:
                print(f"           → 把 {nb} 的 x 右移 ≥{need_x:.1f}pt "
                      f"（或把 {na} 左移）")
            else:
                print(f"           → 把 {nb} 的 x 左移 ≥{need_x:.1f}pt "
                      f"（或把 {na} 右移）")
        else:
            if ay1 <= by1:
                print(f"           → 把 {nb} 的 y 上移 ≥{need_y:.1f}pt "
                      f"（或把 {na} 下移）")
            else:
                print(f"           → 把 {nb} 的 y 下移 ≥{need_y:.1f}pt "
                      f"（或把 {na} 上移）")
        n += 1
    for a, b, ba, bb, dx, dy in r["text_overlaps"]:
        print(f"  [文字重叠] 「{_short(a)}」与「{_short(b)}」的排版盒相交 "
              f"{dx:.1f}×{dy:.1f}pt")
        ax0, ay0, ax1, ay1 = _box(ba)
        bx0, by0, bx1, by1 = _box(bb)
        if dy <= dx:
            shift = dx + 2.0
            if ay1 <= by1:
                print(f"           → 把后者**上移** ≥{shift:.1f}pt "
                      f"({shift*PT2MM:.1f}mm), 或给两者拉开横向间距")
            else:
                print(f"           → 把后者**下移** ≥{shift:.1f}pt "
                      f"({shift*PT2MM:.1f}mm), 或给两者拉开横向间距")
        else:
            shift = dy + 2.0
            if ax1 <= bx1:
                print(f"           → 把后者**右移** ≥{shift:.1f}pt "
                      f"({shift*PT2MM:.1f}mm)")
            else:
                print(f"           → 把后者**左移** ≥{shift:.1f}pt "
                      f"({shift*PT2MM:.1f}mm)")
        n += 1
    for a, w, box in r["text_on_wire"]:
        print(f"  [文字压线] 「{_short(a)}」压在导线 "
              f"{fmt_pt(w[0], w[1])}→{fmt_pt(w[2], w[3])} 上")
        bx0, by0, bx1, by1 = _box(box)
        if abs(w[0] - w[2]) < 0.6:
            print(f"           → 这段是竖线 x={w[0]:.1f}。把该标注挪到 "
                  f"x≥{w[0]+ (bx1-bx0)/2 + 3:.1f} 或 x≤"
                  f"{w[0] - (bx1-bx0)/2 - 3:.1f}")
        else:
            print(f"           → 这段是横线 y={w[1]:.1f}。把该标注挪到 "
                  f"y≥{w[1] + (by1-by0)/2 + 3:.1f} 或 y≤"
                  f"{w[1] - (by1-by0)/2 - 3:.1f}")
        n += 1
    for text, name, bb, dx, dy in r["text_on_body"]:
        print(f"  [文字压器件] 「{_short(text)}」压在器件 {name} 的本体上 "
              f"（重叠 {dx:.1f}×{dy:.1f}pt）")
        bx0, by0, bx1, by1 = _box(bb)
        # 给可照做的改法: 往元件外侧挪, 并给足间隙
        print(f"           → 该元件本体 x {bx0:.1f}..{bx1:.1f}, "
              f"y {by0:.1f}..{by1:.1f}")
        print(f"            把标注挪到 x≤{bx0 - 3:.1f} 或 x≥{bx1 + 3:.1f}"
              f"（元件左右两侧外）, 或挪到元件上/下方的空白区。")
        n += 1
    for text, name, gb, dx, dy in r["text_on_gsym"]:
        print(f"  [文字压符号] 「{_short(text)}」压在 {name} 符号上 "
              f"（重叠 {dx:.1f}×{dy:.1f}pt）")
        gx0, gy0, gx1, gy1 = gb
        print(f"           → {name} 符号图形 x {gx0:.1f}..{gx1:.1f}, "
              f"y {gy0:.1f}..{gy1:.1f}（\u005cPower 只记连接点, 记不到这块, "
              f"所以压在符号上以前查不出）")
        print(f"            把标注挪到 x≤{gx0 - 3:.1f} 或 x≥{gx1 + 3:.1f}, "
              f"或 y≤{gy0 - 3:.1f} / y≥{gy1 + 3:.1f}")
        n += 1
    for a, b, dist, axis, span in r["too_close"]:
        print(f"  [平行线过近] {fmt_pt(a[0],a[1])}→{fmt_pt(a[2],a[3])} 与 "
              f"{fmt_pt(b[0],b[1])}→{fmt_pt(b[2],b[3])} 相距仅 {dist:.1f}pt "
              f"({dist*PT2MM:.1f}mm)")
        print(f"           → 两条线都沿 {axis} 方向, 中心距 <7pt。把其中一条"
              f"沿垂直方向移开 ≥{MIN_PARALLEL - dist + 1:.1f}pt;")
        print(f"             若它们**本来就该连在一起**, 改成一条线加一个"
              f" \\Junction, 不要画成两条。")
        n += 1
    for name, b, cv in r["out_of_canvas"]:
        bx0, by0, bx1, by1 = _box(b)
        cx0, cy0, cx1, cy1 = _box(cv)
        print(f"  [器件越界] {name} 本体盒 "
              f"({bx0:.0f}..{bx1:.0f}, {by0:.0f}..{by1:.0f}) 超出画布 "
              f"({cx0:.0f}..{cx1:.0f}, {cy0:.0f}..{cy1:.0f})")
        print(f"           → 会被渲染器裁掉。把该器件内移, 或调大 "
              f"standalone 的 border / 画布尺寸")
        n += 1
    if n == 0:
        print("  排版: 无文字重叠 / 无文字压线 / 无文字压器件 / 无文字压符号 / "
              "无平行线过近 / 无器件越界 / 无器件重叠")
    return n


def main():
    ap = argparse.ArgumentParser(description="侧车排版体检 (TikZ 路线)")
    ap.add_argument("files", nargs="+", help="侧车 .net 文件 (支持通配符)")
    ap.add_argument("--min-text-gap", type=float, default=MIN_TEXT_GAP)
    ap.add_argument("--min-parallel", type=float, default=MIN_PARALLEL)
    ap.add_argument("--strict-bounds", action="store_true",
                    help="器件盒紧贴画布边缘也算越界")
    args = ap.parse_args()

    paths = []
    for pat in args.files:
        got = glob.glob(pat)
        paths.extend(got if got else [pat])

    total = 0
    missing = 0
    for p in sorted(paths):
        if not os.path.exists(p):
            # 同电气门: 路径错/没产出/被删都走这里, 静默跳过 = "输入错也算过"。
            print(f"错误: 侧车不存在: {p}", file=sys.stderr)
            missing += 1
            continue
        if os.path.getsize(p) == 0:
            print(f"错误: 侧车是空文件: {p} "
                  f"(没检查到任何东西, 不等于排版干净)", file=sys.stderr)
            missing += 1
            continue
        r = analyze(p, min_text_gap=args.min_text_gap,
                    min_parallel=args.min_parallel,
                    margin=0.0 if args.strict_bounds else 1.0)
        total += report(p, r)
        print()

    if missing:
        print(f"===== {missing} 个侧车**根本没检查**(不存在或为空) =====", file=sys.stderr)
        return 2
    print(f"===== 合计排版问题数: {total} =====")
    if total:
        print("建议修掉再交付(文字重叠/压线会让图读错)。")
    else:
        print("排版干净。注意: 这只证明「摆得整齐」, 不证明电路对 —— "
              "电气项要跑 check_tex_net.py。")
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main())
