#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
侧车解析 + 连通性内核 —— 检查器共用的那部分。

**设计要点: 检查器不解析图, 只读画图时写下的侧车。**
取数方式是从**画图时自己写下的**侧车读, 不是从渲染产物里正则
反推几何(`_extract_netlist`): 剔除元件分组、过滤 data-deco、只认正交线段、
还要专门查"坐标不是数字的畸形 <line>"。本方案里 TikZ 在排版时就拿到了
精确坐标, 画线的那一刻顺手写进侧车, 所以:

  * 不需要"反推几何"这一步, 也就不需要 `_seg_cross` 之外的几何启发式;
  * **畸形线段这一整类缺陷结构上消失** —— 侧车里的坐标是 pgf 算出来的
    浮点数, 不可能"本该是坐标却写成颜色常量";
  * **悬空 / 飘端子也结构上消失** —— 端点就是锚点本身, 画 `\Wire{a}{b}`
    时 a/b 必然精确落在端子上, 差几像素物理上不可能。

保留的规则(决定成败, 别改):
  * **端点相接才算连通; 中途交叉不算** —— 除非交点处登记了结点 J。
    少了这条, 两条只是视觉上交叉的导线会被判成连通, 报出一堆
    "分压网络短路"的假阳性, 比不查还糟。
  * 容差 0.8pt。侧车坐标是精确值(不是渲染反推的), 所以容差可以很小。

侧车格式见 stm32tikz.sty 头部注释。
"""
import os

TOL = 0.8                      # 端点落在另一线段上的容差(pt)
JUNCTION_TOL = 1.5             # 结点与交点匹配的容差(pt)


def _num(v):
    """把侧车里的数值字段转成 float。

    侧车坐标是 `\\the\\dimen` 出来的, 自带单位后缀(如 `12.3pt`)。保留单位是
    为了文件人工可读; 这里统一剥掉。**不做静默容错** —— 除了这个 pt 后缀,
    其他非数字一律当解析失败上报(侧车格式错必须响, 不能装作没看见)。
    """
    v = v.strip()
    if v.endswith("pt"):
        v = v[:-2]
    return float(v)


# ================================================================
#  侧车解析
# ================================================================
def parse_sidecar(path):
    """
    读 .net 侧车, 返回:
      {"wires": [(x1,y1,x2,y2), ...],
       "dots":  [(x,y), ...],
       "terms": {名字: (x,y)},
       "bodies":{名字: (x0,y0,x1,y1)},
       "powers":[(网络名, x, y), ...],
       "tags":  [(号, x, y), ...],
       "expect":[(端子名, 目标), ...],
       "annos": [(x0,y0,x1,y1, 文字), ...],
       "canvas":(x0,y0,x1,y1) | None}
    """
    out = {"wires": [], "dots": [], "terms": {}, "bodies": {},
           "powers": [], "tags": [], "expect": [], "annos": [], "canvas": None,
           "hint_h": [], "hint_v": [], "warns": [], "annoat": [], "names": [],
           "gsyms": []}
    bad = []
    with open(path, encoding="utf-8", errors="replace") as f:
        for lineno, raw in enumerate(f, 1):
            line = raw.rstrip("\n").rstrip("\r")
            if not line or line.startswith("#"):
                continue
            kind, _, rest = line.partition("|")
            try:
                if kind in ("W", "WH", "WV"):
                    x1, y1, x2, y2 = (_num(v) for v in rest.split("|"))
                    out["wires"].append((x1, y1, x2, y2))
                    # WH/WV 是"意图声明": 调用方声称这条线是水平/竖直的。
                    # 检查器据此核对实际坐标 —— 这是把斜线从"静默忽略"
                    # 变成"明确报错"的关键。
                    if kind == "WH":
                        out["hint_h"].append((x1, y1, x2, y2))
                    elif kind == "WV":
                        out["hint_v"].append((x1, y1, x2, y2))
                elif kind == "J":
                    x, y = (_num(v) for v in rest.split("|"))
                    out["dots"].append((x, y))
                elif kind == "T":
                    name, x, y = rest.split("|")
                    out["terms"][name] = (_num(x), _num(y))
                elif kind == "B":
                    name, x0, y0, x1, y1 = rest.split("|")
                    out["bodies"][name] = (_num(x0), _num(y0),
                                           _num(x1), _num(y1))
                elif kind == "P":
                    name, x, y = rest.split("|")
                    out["powers"].append((name, _num(x), _num(y)))
                elif kind == "G":
                    num, x, y = rest.split("|")
                    out["tags"].append((num, _num(x), _num(y)))
                elif kind == "E":
                    name, want = rest.split("|", 1)
                    out["expect"].append((name, want))
                elif kind == "X":
                    # X|序号|类型|x0|y0|x1|y1|文字 —— 文字可含 |, 限量切分
                    parts = rest.split("|", 6)
                    x0, y0, x1, y1 = (_num(v) for v in parts[2:6])
                    # 第 7 个元素是**序号**(字符串)。place.py 靠它区分
                    # "有 A| 记录的自动标注" 与 "只有 X| 的手写标注(如页脚
                    # 说明块)" —— 后者要当**固定障碍物**, 否则自动摆位会把
                    # 别的标注叠在它上面。
                    out["annos"].append((x0, y0, x1, y1, parts[6],
                                         parts[1], parts[0]))
                elif kind == "A":
                    # A|键|编号|锚点x|锚点y|角次序|样式|参考点原文|x0|y0|x1|y1|文字
                    # 供 place.py 自动摆位。角次序/样式/参考点都只作展示,
                    # 真正要算的是: 锚点 + 文字尺寸 → 该放哪。
                    p = rest.split("|", 12)
                    out["annoat"].append({
                        "key": p[0], "n": p[1],
                        "ax": _num(p[2]), "ay": _num(p[3]),
                        "corners": p[4], "style": p[5], "ref": p[6],
                        "box": tuple(_num(v) for v in p[7:11]),
                        "text": p[11] if len(p) > 11 else "",
                    })
                elif kind == "GS":
                    # GS|网络名|x0|y0|x1|y1 —— 电源/地**符号的图形范围**。
                    # 它不是电气实体(连通性只看 P| 那个连接点), 是**排版
                    # 障碍物** —— 用来查"文字压在接地符号/VCC 符号上"。
                    # 见 stm32tikz.sty 里 \SNgbbox 的注释(这是个目视才发现、
                    # 两道门都报 0 的盲区)。
                    name, x0, y0, x1, y1 = rest.split("|")
                    out["gsyms"].append((name, _num(x0), _num(y0),
                                         _num(x1), _num(y1)))
                elif kind == "N":
                    # N|生成坐标名|... —— \ShowNames 的产物, 纯参考, 检查器忽略
                    out["names"].append(rest)
                elif kind == "M":
                    # 记账警告(如 \LogLabel 找不到 label 节点) —— 不静默吞掉
                    out.setdefault("warns", []).append(rest)
                elif kind == "C":
                    x0, y0, x1, y1 = (_num(v) for v in rest.split("|"))
                    out["canvas"] = (x0, y0, x1, y1)
                else:
                    bad.append((lineno, line))
            except (ValueError, IndexError):
                bad.append((lineno, line))
    out["unparsed"] = bad
    return out


# ================================================================
#  几何小工具
# ================================================================
def on_segment(px, py, x1, y1, x2, y2, tol=TOL):
    """点是否落在线段上(含端点)。只认正交线段。"""
    if abs(x1 - x2) < 0.6:                              # 竖线
        return abs(px - x1) <= tol and min(y1, y2) - tol <= py <= max(y1, y2) + tol
    if abs(y1 - y2) < 0.6:                              # 横线
        return abs(py - y1) <= tol and min(x1, x2) - tol <= px <= max(x1, x2) + tol
    return False


def seg_cross(a, b):
    """两条正交线段是否交叉(不含共线)。返回交点或 None。"""
    (ax1, ay1, ax2, ay2), (bx1, by1, bx2, by2) = a, b
    a_vert = abs(ax1 - ax2) < 0.6
    b_vert = abs(bx1 - bx2) < 0.6
    if a_vert == b_vert:
        return None
    v, h = (a, b) if a_vert else (b, a)
    vx, vy0, vy1 = v[0], min(v[1], v[3]), max(v[1], v[3])
    hy, hx0, hx1 = h[1], min(h[0], h[2]), max(h[0], h[2])
    if hx0 - TOL <= vx <= hx1 + TOL and vy0 - TOL <= hy <= vy1 + TOL:
        return (vx, hy)
    return None


def is_orthogonal(seg):
    """是不是正交线段(横或竖)。斜线在连通性里没有定义。"""
    x1, y1, x2, y2 = seg
    return abs(x1 - x2) < 0.6 or abs(y1 - y2) < 0.6


def is_degenerate(seg):
    """零长线段 —— 无信息, 忽略。"""
    x1, y1, x2, y2 = seg
    return abs(x1 - x2) < 0.01 and abs(y1 - y2) < 0.01


# ================================================================
#  连通性(并查集)
# ================================================================
def build_nets(sc):
    """
    用并查集把侧车还原成网表。返回 (find, members, nets, aliases):
      find    并查集查找函数
      members {成员名: 并查集节点}   成员名形如 R1.a / Q1.c / +5V / GND / 标签1
      nets    {根: [成员名, ...]}
    """
    wires = [w for w in sc["wires"]
             if is_orthogonal(w) and not is_degenerate(w)]
    dots = sc["dots"]

    parent = {}

    def find(a):
        parent.setdefault(a, a)
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    def key(p):
        return ("pt", round(p[0], 1), round(p[1], 1))

    # 每条线段一个节点, 两端点并进去
    for i, (x1, y1, x2, y2) in enumerate(wires):
        nid = ("seg", i)
        union(nid, key((x1, y1)))
        union(nid, key((x2, y2)))

    # 端点相接: 某线段端点落在另一线段上 -> 连通
    # (T 型接头靠这条自动连通, 不需要画结点)
    for i, a in enumerate(wires):
        for j, b in enumerate(wires):
            if i == j:
                continue
            for p in ((b[0], b[1]), (b[2], b[3])):
                if on_segment(p[0], p[1], *a):
                    union(("seg", i), key(p))

    # 交叉: 只有交点处有结点才算连通
    for i in range(len(wires)):
        for j in range(i + 1, len(wires)):
            pt = seg_cross(wires[i], wires[j])
            if not pt:
                continue
            if any(abs(pt[0] - dx) <= JUNCTION_TOL and abs(pt[1] - dy) <= JUNCTION_TOL
                   for dx, dy in dots):
                union(("seg", i), ("seg", j))

    members = {}

    # 网络标签: 同号 = 同一张网
    tag_pts = {}
    for num, x, y in sc["tags"]:
        k = key((x, y))
        union(("nettag", num), k)
        tag_pts.setdefault(num, []).append(k)
        for i, s in enumerate(wires):
            if on_segment(x, y, *s):
                union(k, ("seg", i))

    # 电源/地符号: 它自己就是网络名。多个同名自动同网。
    for name, x, y in sc["powers"]:
        k = key((x, y))
        union(("net", name), k)
        members[name] = ("net", name)
        # **不要 continue** —— 电源符号也要挂到它所在的导线上。
        # (这里踩过坑: 接在导线**中点**的电源被静默孤立,
        #  而检查器报 0 问题 —— 最危险的一类漏检。)
        for i, s in enumerate(wires):
            if on_segment(x, y, *s):
                union(k, ("seg", i))

    # 元件端子
    for name, (x, y) in sc["terms"].items():
        k = key((x, y))
        members[name] = k
        for i, s in enumerate(wires):
            if on_segment(x, y, *s):
                union(k, ("seg", i))

    # 分组
    nets = {}
    for name, node in members.items():
        nets.setdefault(find(node), []).append(name)
    for num, nodes in tag_pts.items():
        nm = f"标签{num}"
        members[nm] = nodes[0]
        nets.setdefault(find(nodes[0]), []).append(nm)
    for i in range(len(wires)):
        nets.setdefault(find(("seg", i)), [])

    return find, members, nets, wires, dots, tag_pts


def net_of(find, members, name):
    return find(members[name]) if name in members else None


def fmt_pt(x, y):
    return f"({x:.0f},{y:.0f})"


def ratio_to_pt(v):
    """pt -> 一个便于阅读的数值(直接保留一位小数)。"""
    return v
