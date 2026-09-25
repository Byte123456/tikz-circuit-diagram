#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
回归测试: check_tex_net.py 必须能报出故意植入的错误。

为什么必须有: 门禁最危险的失效模式不是"误报", 而是**漏报** —— 它一声不响
地放过一个短路, 你就以为电路是对的。这个测试文件就是为此存在
(43 用例)。这里做同样的事, 但用**合成侧车**而不是渲染图:

  * 合成侧车 = 手写几行 W/T/B/P, 构造出特定拓扑;
  * 比"改 .tex 再编译"快得多(毫秒 vs 1.5 秒), 而且能精确控制几何;
  * 断言的是**检查器的判定**, 不是渲染结果 —— 这才是要护住的契约。

用法:  python selftest_tex.py
退出码: 0 = 全部通过, 1 = 有用例失败。
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import check_tex_net as N            # noqa: E402
import check_tex_layout as L         # noqa: E402
import _paths                        # noqa: E402   (定位 assets/ 下的 sty)

FAIL = []
PASS = []
# **跳过 ≠ 通过**。此前没有 xelatex 的机器上, 9 个 TeX 用例被 append 进 PASS,
# 最后打印"32/32 全部通过" —— 而实际只跑了 23 个。这正是本文件开头说的那类
# 失效: "查了没问题"和"压根没查"在输出上长得一样。现在分账统计。
SKIP = []


def sidecar(text):
    """把一段侧车文本落成临时文件, 返回路径。"""
    fd, path = tempfile.mkstemp(suffix=".net")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def expect(name, side_text, want_kind, want_substr=None, quiet=True):
    """跑电气门, 断言报出了 want_kind 类问题。"""
    path = sidecar(side_text)
    try:
        r = N.analyze(path, quiet=True)
    except Exception as e:                      # noqa: BLE001
        FAIL.append(f"{name}: 检查器抛异常 {type(e).__name__}: {e}")
        return
    hit = False
    detail = ""
    if want_kind == "short":
        hit = bool(r["shorted"])
        detail = str(r["shorted"])
    elif want_kind == "bypass":
        hit = bool(r["bypassed"])
        detail = str([b[0] for b in r["bypassed"]])
    elif want_kind == "cross":
        hit = bool(r["crossed"])
        detail = str([(c[0], c[1]) for c in r["crossed"]])
    elif want_kind == "dangling":
        hit = bool(r["dangling"])
        detail = str([d[0] for d in r["dangling"]])
    elif want_kind == "miswired":
        hit = bool(r["miswired"])
        detail = str([(m[0], m[1]) for m in r["miswired"]])
    elif want_kind == "lonely":
        hit = bool(r["lonely_tags"])
        detail = str(r["lonely_tags"])
    elif want_kind == "nonortho":
        hit = bool(r["bad_h"]) or bool(r["bad_v"])
        detail = f"H={len(r['bad_h'])} V={len(r['bad_v'])}"
    elif want_kind == "clean":
        hit = not any([r["shorted"], r["bypassed"], r["crossed"], r["dangling"],
                       r["miswired"], r["lonely_tags"], r["bad_h"], r["bad_v"]])
        detail = (f"short={r['shorted']} bypass={len(r['bypassed'])} "
                  f"cross={len(r['crossed'])} dang={len(r['dangling'])} "
                  f"mis={len(r['miswired'])}")
    else:
        FAIL.append(f"{name}: 未知断言类型 {want_kind}")
        return
    if hit and (want_substr is None or want_substr in detail):
        PASS.append(name)
    else:
        FAIL.append(f"{name}: 期望 {want_kind}({want_substr or ''}) 但得到 {detail}")
    os.unlink(path)


def expect_only_nets(name, side_text, want_same=(), want_diff=()):
    """断言若干对成员"同网"/"不同网" —— 直接护住连通规则本身。"""
    path = sidecar(side_text)
    try:
        r = N.analyze(path, quiet=True)
        members = r["members"]
    except Exception as e:                      # noqa: BLE001
        FAIL.append(f"{name}: 检查器抛异常 {type(e).__name__}: {e}")
        return
    bad = []
    for a, b in want_same:
        if a not in members or b not in members:
            bad.append(f"{a}/{b} 不在成员表")
        elif members[a] != members[b]:
            bad.append(f"{a} 应与 {b} 同网, 实际不同")
    for a, b in want_diff:
        if a in members and b in members and members[a] == members[b]:
            bad.append(f"{a} 不应与 {b} 同网, 实际同网")
    os.unlink(path)
    if bad:
        FAIL.append(f"{name}: " + "; ".join(bad))
    else:
        PASS.append(name)


# =====================================================================
#  1. 短路: +5V 与 GND 同网
# =====================================================================
# 布局:  +5V --(R1)--- 节点X ---(R2)--- GND,  且 X 处一条竖线直连 +5V 到 GND
expect("短路: +5V 与 GND 同网", """
P|+5V|0|0
P|GND|200|0
W|0|0|100|0
W|100|0|100|-50
W|100|-50|200|-50
W|200|-50|200|0
T|R9.a|100|0
T|R9.b|200|0
""", "short")

# =====================================================================
#  2. 旁路: 元件两端同网(两端都接在 +5V 上)
# =====================================================================
expect("旁路: 元件两端接在同一张网", """
P|+5V|0|0
W|0|0|100|0
W|100|0|100|-40
W|100|-40|0|-40
W|0|-40|0|0
T|R9.a|100|0
T|R9.b|100|-40
""", "bypass", "R9")

# =====================================================================
#  3. 穿体: 竖线纵穿电阻本体
# =====================================================================
# R9 本体 y 从 -20 到 -50; 竖线 x=100 从 y=0 一路画到 y=-70, 整段穿本体。
expect("穿体: 竖线纵穿元件本体", """
P|+5V|100|0
P|GND|100|-70
W|100|0|100|-70
T|R9.a|100|-20
T|R9.b|100|-50
B|R9|92|-50|108|-20
""", "cross", "R9")

# =====================================================================
#  4. 悬空: 端子没有任何导线, 也不与别的端子重合
# =====================================================================
expect("悬空: 端子没接到任何导线", """
P|+5V|0|0
W|0|0|60|0
T|R9.a|60|0
T|R9.b|60|-40
""", "dangling", "R9.b")

# =====================================================================
#  5. 接错: \Expect 声明的期望网络没兑现
# =====================================================================
# D9.a 声明该接到 +5V, 实际接在 GND 上 —— 这是"接到错的节点", 前四类都不报。
expect("接错: \\Expect 声明落空(接到错的节点)", """
P|+5V|0|0
P|GND|0|-60
W|0|0|80|0
W|0|-60|80|-60
T|D9.a|80|-60
T|D9.b|40|-60
E|D9.a|+5V
""", "miswired", "D9.a")

# =====================================================================
#  6. 孤标签: 网络标签只出现一次
# =====================================================================
expect("孤标签: 网络标签只出现一次", """
P|+5V|0|0
W|0|0|60|0
T|R9.a|60|0
G|1|30|0
""", "lonely", "1")

# =====================================================================
#  7. 非正交: \WireH 声明水平但实际是斜线
# =====================================================================
# 这是本方案要堵的洞: 若直接丢弃斜线(静默漏检),
# 这里因为画线时留下 H/V 意图, 能明确报出来。
expect("非正交: WireH 实际是斜线", """
P|+5V|0|0
WH|0|0|60|-30
T|R9.a|60|-30
""", "nonortho")

# =====================================================================
#  8. 干净电路: 不该报任何问题(防误报)
# =====================================================================
# 正确的最小电路: +5V --导线-- R9.a ; R9 本体 ; R9.b --导线-- GND。
# **注意本体那一段不能画导线** —— 画了就是把电阻短路, 检查器报 [旁路]
# 是对的(第一版用例就写错了, 反而暴露了"用例本身要能区分对错")。
expect("干净: 正确电路不报任何问题", """
P|+5V|0|0
P|GND|300|0
W|0|0|100|0
W|200|0|300|0
T|R9.a|100|0
T|R9.b|200|0
B|R9|92|-8|208|8
""", "clean")

# =====================================================================
#  9. 端点相接才算连通 / 中途交叉不算(连通规则本身)
# =====================================================================
# 一条竖线(上接 +5V, 下接 GND)与一条横线(两端各挂一个端子)**十字交叉**,
# 交点处**没有结点 J**。期望: 竖线自成一张网(+5V-GND 那条), 横线的两端
# 在**另一张网**上 —— 两个端子同网(它们本来就在同一条线上), 但不与 +5V/GND 同网。
# 这条护住"交叉≠连接": 若规则被改坏, 会报出大量"分压网络短路"假阳性。
expect_only_nets("连通规则: 交叉不加结点 = 不连通", """
P|+5V|0|0
P|GND|0|-100
W|0|0|0|-100
W|-60|-50|60|-50
T|R9.a|-60|-50
T|R9.b|60|-50
""", want_same=[("R9.a", "R9.b")],
     want_diff=[("R9.a", "+5V")])

# 同一条交叉, 但**加上结点 J** -> 现在该连通了(判据的另一半)
expect_only_nets("连通规则: 交叉加结点 = 连通", """
P|+5V|0|0
P|GND|0|-100
W|0|0|0|-100
W|-60|-50|60|-50
J|0|-50
T|R9.a|-60|-50
T|R9.b|60|-50
""", want_same=[("R9.a", "+5V")],
     want_diff=[])

# =====================================================================
#  11. \Chain 依赖的规则: 两元件端子重合 = 连通
# =====================================================================
# \Chain 把一串元件画在**一条路径**上, 相邻元件是"端子对端子"直接对接的,
# 侧车里**没有对应的 W 记录**。这条用例护住"端子重合即连通"规则 ——
# 它一旦被改坏, 所有用 \Chain 画的支路都会连锁报一大片 [悬空]。
# 实测就是这么踩过来的: L1R.a 曾被报悬空, 病因是起点不接端子, 而不是规则错。
expect_only_nets("\\Chain 依赖: 两元件端子重合 = 连通", """
T|C1SR.b|50|0
T|C1SLED.a|50|0
T|C1SLED.b|100|0
P|+5V|0|0
W|0|0|0|0
""", want_same=[("C1SR.b", "C1SLED.a")],
     want_diff=[("C1SR.b", "C1SLED.b")])

# 但**不重合**的两个端子不能因为"都悬空"而被当成连通(防过度连通)
expect_only_nets("\\Chain 依赖: 端子不重合 = 不连通", """
T|C1SR.b|50|0
T|C1SLED.a|80|0
""", want_same=[],
     want_diff=[("C1SR.b", "C1SLED.a")])

# =====================================================================
#  12. 排版门
# =====================================================================
def expect_layout(name, side_text, want_kind, want_hit=True):
    path = sidecar(side_text)
    try:
        r = L.analyze(path)
    except Exception as e:                      # noqa: BLE001
        FAIL.append(f"{name}: 排版检查抛异常 {type(e).__name__}: {e}")
        return
    hit = bool(r[want_kind])
    if hit == want_hit:
        PASS.append(name)
    else:
        FAIL.append(f"{name}: 期望 {want_kind}={want_hit}, 实际 {hit} "
                    f"({str(r[want_kind])[:100]})")
    os.unlink(path)


expect_layout("排版: 文字重叠被报出", """
X|1|R|0|0|60|12|甲标注
X|2|R|30|6|90|18|乙标注
""", "text_overlaps")

expect_layout("排版: 文字压线被报出", """
W|30|0|30|-100
X|1|R|10|-50|50|-38|压在竖线上的字
""", "text_on_wire")

expect_layout("排版: 器件重叠被报出", """
B|RA|0|-30|20|-10
B|RB|5|-25|25|-5
""", "comp_overlaps")

expect_layout("排版: 器件越界被报出", """
C|0|0|100|100
B|RA|80|-40|140|-10
""", "out_of_canvas")

# 第 6 类: 文字压在器件本体上。**这类是两套方案共同的盲区** ——
# 早期的五类检查里也没有它; 实测过"字横压在元件框线上"
# 两边都不报。判据是**文字盒中心落在本体盒内** —— 理由见
# check_tex_layout.py: circuitikz 自动摆的值标签本来就贴着元件本体,
# 若用"两盒相交"判, 每个带 l= 的元件都会误报。
expect_layout("排版: 文字压在器件本体上被报出", """
B|BZ1|0|-30|60|30
X|1|R|10|0|50|12|压在方框里的字
""", "text_on_body")

# 中心在本体**外**就不算 —— 哪怕两盒确有相交(值标签贴边正是这种情形)。
expect_layout("排版: 文字中心在本体外不报(防误报)", """
B|BZ1|0|-30|60|30
X|1|R|60.5|0|90|12|紧贴右边缘的字
""", "text_on_body", want_hit=False)

# 第 7 类: 文字压在**电源/地符号**上。这是**目视**发现的一个盲区 ——
# 示例图里 "D2 1N4148 续流" 的标注盒正好压住 C1 的接地符号, 两道门都报 0。
# 根因: \Power 的 P| 只记**一个连接点**, 拿点跟文字盒比永远比不出重叠。
# 所以另记 GS| = 符号图形范围。判据用"两盒相交"就够 —— 与第 6 类的值标签
# 不同, 地/VCC 符号是**画出来的图形**, 文字压上去没有正当理由。
expect_layout("排版: 文字压在电源/地符号上被报出", """
GS|GND|278|-205|291|-188
X|1|R|270|-195|300|-181|压在接地符号上的字
""", "text_on_gsym")

# 没压上就不报(符号在文字盒右侧 20pt 外)
expect_layout("排版: 文字离符号远不报(防误报)", """
GS|GND|278|-205|291|-188
X|1|R|400|-195|440|-181|离得很远的字
""", "text_on_gsym", want_hit=False)

expect_layout("排版: 干净图不报问题", """
C|0|-100|200|0
W|0|0|100|0
B|RA|40|-10|60|10
X|1|R|0|20|40|32|标注
""", "text_overlaps", want_hit=False)


# =====================================================================
#  12b. 记账警告(零长导线)必须被报出来
# =====================================================================
# `\Wire`/`\WireH`/`\WireV` 在两端重合时**不记 W, 改记 M**(零长导线)。
# 这条路径实测出过问题: 早先用嵌套 `\ifdim` 判坐标相等, 行为不稳定;
# 而且**电气门当时压根不读 M|**, 于是零长线静默穿过了两道门, 还让两个门
# 的导线计数对不上(电气门过滤退化线、排版门不滤)。
# 两个用例各护一层: 检查器要认 M|, 电气门要报 M|。
def expect_warn(name, side_text, want_substr):
    path = sidecar(side_text)
    try:
        r = N.analyze(path)
    except Exception as e:                      # noqa: BLE001
        FAIL.append(f"{name}: 电气检查抛异常 {type(e).__name__}: {e}")
        return
    got = r.get("warns", [])
    if any(want_substr in w for w in got):
        PASS.append(name)
    else:
        FAIL.append(f"{name}: 期望警告含 {want_substr!r}, 实际 {got}")
    os.unlink(path)


expect_warn("记账: 电气门报出零长导线", """
M|零长导线(WV) 两端重合在 (0.0pt,0.0pt) —— 多余的一行
T|R1.a|0|0
""", "零长导线")

# 没有 M| 时 warns 必须是空的 —— 免得"永远有警告"变成噪音
def expect_no_warn(name, side_text):
    path = sidecar(side_text)
    r = N.analyze(path)
    if not r.get("warns"):
        PASS.append(name)
    else:
        FAIL.append(f"{name}: 不该有警告, 实际 {r['warns']}")
    os.unlink(path)


expect_no_warn("记账: 无 M| 时 warns 为空", """
T|R1.a|0|0
T|R1.b|0|-40
B|R1|-5|-38|5|-10
""")


# =====================================================================
#  14. 重名登记: 同名端子/本体盒**异坐标**重复必须报(静默覆盖=少一个实体)
# =====================================================================
# 侧车里一条 T| = 一个电气端子。同名异坐标的重复登记此前只留最后一条,
# 前一条凭空消失, 而图照常画 —— 检查器"看少了"却报 0。
def expect_conflict(name, side_text, want_kind, want_substr=None):
    """断言侧车解析报出了 conflicts。"""
    path = sidecar(side_text)
    r = N.analyze(path, quiet=True)
    got = r.get("conflicts", [])
    hit = any(k == want_kind for k, *_ in got)
    if hit and (want_substr is None
                or any(want_substr == nm for _, nm, *_ in got)):
        PASS.append(name)
    else:
        FAIL.append(f"{name}: 期望 conflicts 含 {want_kind}/{want_substr}, "
                    f"实际 {got}")
    os.unlink(path)


expect_conflict("重名: 端子同名异坐标被报出", """
T|R1.a|0|0
T|R1.a|200|200
""", "T", "R1.a")

expect_conflict("重名: 本体盒同名异坐标被报出", """
T|R1.a|0|0
B|R1|0|0|10|10
B|R1|100|100|110|110
""", "B", "R1")

# 同坐标重复是**良性**的: 同一端子被两条路径登记(如 \Term 与 \SNregTwo 都
# 碰了它)。这种不能报, 否则真图会满屏假问题。
path = sidecar("""
T|R1.a|10|10
T|R1.a|10|10
B|R1|0|0|20|20
B|R1|0|0|20|20
""")
r = N.analyze(path, quiet=True)
if not r.get("conflicts"):
    PASS.append("重名: 同坐标重复登记不报(防误报)")
else:
    FAIL.append(f"重名: 同坐标重复不该报, 实际 {r['conflicts']}")
os.unlink(path)


# =====================================================================
#  15. 近接: 相差 >TOL 但 <=NEAR_TOL 的点对必须报("看着贴住其实断了")
# =====================================================================
# 这条同时护住"容差一致": 两个点要么连通、要么不连通, 不能出现"网表说
# 分开、悬空说接上了"的矛盾。
def expect_near(name, side_text, want_hit=True):
    path = sidecar(side_text)
    r = N.analyze(path, quiet=True)
    hit = bool(r.get("near_miss"))
    if hit == want_hit:
        PASS.append(name)
    else:
        FAIL.append(f"{name}: 期望 near_miss={want_hit}, 实际 {r.get('near_miss')}")
    os.unlink(path)


expect_near("近接: 相差 1.2pt 的两端子被报出(且未连通)", """
T|R1.a|100|100
T|R2.a|101.2|100
""")
expect_near("近接: 精确重合的两端子不报(是连通的)", """
T|R1.a|100|100
T|R2.a|100|100
""", want_hit=False)
expect_near("近接: 相距很远的端子不报", """
T|R1.a|100|100
T|R2.a|100|300
""", want_hit=False)


# 容差一致性: 相差 <=TOL 的两端子必须**连通**(网表)且**不报悬空** ——
# 同一件事只能有一个结论。此前网表 TOL=0.8、悬空判定 1.2, 差 0.5pt 的点
# 会落进"网表判分离、悬空不报"的矛盾区。
expect_only_nets("容差一致: 相差 0.5pt 的两端子判连通", """
T|R1.a|100|100
T|R2.a|100|100.5
""", want_same=[("R1.a", "R2.a")])

path = sidecar("""
T|R1.a|100|100
T|R2.a|100|100.5
""")
r = N.analyze(path, quiet=True)
if r["dangling"]:
    FAIL.append(f"容差一致: 已判连通却报悬空 —— {r['dangling']}")
else:
    PASS.append("容差一致: 已判连通的端子不报悬空(无矛盾)")
os.unlink(path)


# =====================================================================
#  16. 空侧车: 必须报"没检查到东西", 而不是"干净"
# =====================================================================
# 空侧车 / 侧车里没有电气实体时, 若报"电气干净", 那么"漏跑编译"与"电路
# 正确"在输出上完全一样 —— 本文件开头点名的那个失效模式。
path = sidecar("")
r = N.analyze(path, quiet=True)
if r.get("empty"):
    PASS.append("空侧车: 电气门标记 empty(不当成干净)")
else:
    FAIL.append("空侧车: 电气门没标记 empty")
os.unlink(path)

path = sidecar("")
r = L.analyze(path)
if r.get("empty"):
    PASS.append("空侧车: 排版门标记 empty(不当成干净)")
else:
    FAIL.append("空侧车: 排版门没标记 empty")
os.unlink(path)

# 有实体的侧车不能被误标 empty
path = sidecar("P|+5V|0|0\nW|0|0|10|0\nT|R1.a|10|0\n")
r = N.analyze(path, quiet=True)
if not r.get("empty"):
    PASS.append("空侧车: 有实体的侧车不误标 empty")
else:
    FAIL.append("空侧车: 有实体的侧车被误标 empty")
os.unlink(path)


# =====================================================================
#  17. 端到端: TeX 侧必须真的把记账写出来
# =====================================================================
# 上面全是"喂合成侧车给检查器"的单元测试 —— 它们护不住 **TeX 侧宏**的
# 记账是否真的发生。而最危险的一类失效恰好在那儿: 宏静默跳过, 侧车里
# 少记录, 检查器于是"没看过"却报 0。
#
# 这不是假想 —— 基准测试实测到 `\LogLabel` 用 `\ifdim` 比 `\pgfgetlastxy`
# 的宏, **连续调用会静默失效**(三次只写出一条), 值标签全部没进侧车,
# 而排版门报 0 问题。本目录的示例图当时正是这个状态。
# 所以必须有这条用例: 编译一个探测文档, 断言记账条数符合预期。
def expect_tex_logging(name, tex_body, want_counts):
    """编译一段 .tex, 断言侧车里各记号的**条数**。需要工具链可用。"""
    import shutil
    import subprocess
    xelatex = _paths.find_xelatex()          # 统一走 _paths, 不写死用户目录
    if not xelatex:
        SKIP.append(f"{name}（本机没有 xelatex）")
        return
    tmp = tempfile.mkdtemp(prefix="sntex_")
    tex = os.path.join(tmp, "probe.tex")
    with open(tex, "w", encoding="utf-8") as f:
        f.write(tex_body)
    env = _paths.texinputs_env()   # sty 在 assets/, 由 _paths 负责找
    try:
        subprocess.run([xelatex, "-interaction=nonstopmode", "-halt-on-error",
                        "probe.tex"], cwd=tmp, env=env, timeout=120,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        net = os.path.join(tmp, "probe.net")
        if not os.path.exists(net):
            FAIL.append(f"{name}: 编译没产出侧车 probe.net")
            return
        sc = N.parse_sidecar(net)
        got = {"W": len(sc["wires"]), "T": len(sc["terms"]),
               "B": len(sc["bodies"]), "L": sum(1 for a in sc["annos"]
                                                if a[5] == "L"),
               "A": len(sc["annoat"]), "GS": len(sc.get("gsyms", [])),
               "M": len(sc.get("warns", []))}
        bad = [f"{k}={got[k]}≠{v}" for k, v in want_counts.items()
               if got.get(k) != v]
        if bad:
            FAIL.append(f"{name}: 记账条数不符 —— " + ", ".join(bad)
                        + f"（另一类: {got}）")
        else:
            PASS.append(name)
    except Exception as e:                      # noqa: BLE001
        FAIL.append(f"{name}: 编译/解析异常 {type(e).__name__}: {e}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# \LogLabel 必须**每一次调用都写出**一条 L 记录。
# 三次同构调用是刻意的: `\ifdim` 版本的失效模式正是"第一次成功、后续静默
# 跳过", 只测一次会漏掉。
expect_tex_logging("TeX 记账: \\LogLabel 每次调用都写出值标签(3/3)", r"""
\documentclass[border=6pt]{standalone}
\usepackage{ctex}
\usepackage{stm32tikz}
\begin{document}
\begin{tikzpicture}
\cRes{R1}{0,0}{1.5,0}{10K}\LogLabel{R1}
\cRes{R2}{0,-2}{1.5,-2}{10K}\LogLabel{R2}
\cRes{R3}{0,-4}{1.5,-4}{10K}\LogLabel{R3}
\end{tikzpicture}
\end{document}
""", {"L": 3, "T": 6, "B": 3})

# \Chain 必须登记每个元件的两端端子 + 本体盒, 并把元件串起来。
expect_tex_logging("TeX 记账: \\Chain 登记 2 元件共 4 端子 + 2 本体盒", r"""
\documentclass[border=6pt]{standalone}
\usepackage{ctex}
\usepackage{stm32tikz}
\begin{document}
\begin{tikzpicture}
\Chain{C1}{0,0}{1.6cm,0}{R/R/1K, LED/leD/{}}
\end{tikzpicture}
\end{document}
""", {"T": 4, "B": 2, "W": 0})

# \AnnoAt 必须同时写出 X|(给排版门) 和 A|(给 place.py 自动摆位)。
# 两条记录缺一不可: 少了 X| 排版门看不见这个标注(漏检); 少了 A| 它就不参与
# 自动摆位, 而且会被 place.py 当成"手写标注"当障碍物, 等于白占一块地。
# 这里刻意给**两个** \AnnoAt, 因为"第一次成功、后续静默跳过"正是本文件里
# 踩过两次的失效模式(\LogLabel 那个坑)。
expect_tex_logging("TeX 记账: \\AnnoAt 每次调用都写出 X| + A| (2/2)", r"""
\documentclass[border=6pt]{standalone}
\usepackage{ctex}
\usepackage{stm32tikz}
\begin{document}
\begin{tikzpicture}
\cRes{R1}{0,0}{1.5,0}{10K}
\AnnoAt{ka}{ann}{R1.west}{west}{甲}
\AnnoAt{kb}{ann}{R1.east}{east}{乙}
\end{tikzpicture}
\end{document}
""", {"A": 2, "T": 2, "B": 1})

# \cGND / \cVCC 必须登记**符号图形范围** GS| —— 只记连接点 P| 的话,
# "文字压在接地符号上"永远查不出(那正是目视发现的盲区)。
expect_tex_logging("TeX 记账: \\cGND/\\cVCC 登记符号图形范围 (2 个 GS)", r"""
\documentclass[border=6pt]{standalone}
\usepackage{ctex}
\usepackage{stm32tikz}
\begin{document}
\begin{tikzpicture}
\cVCC{+5V}{0,0}
\cGND{0,-3}
\end{tikzpicture}
\end{document}
""", {"GS": 2})

# 两端重合的 \WireV 必须记成 M|(记账警告) 而**不是** W|。
# 这是 `\ifdim` 判坐标失效那个坑的回归: 失效时会记出一条零长 W|,
# 电气门过滤掉它、排版门不滤, 于是两个门的"导线条数"对不上, 而没人报错。
expect_tex_logging("TeX 记账: 零长导线记 M| 不记 W|", r"""
\documentclass[border=6pt]{standalone}
\usepackage{ctex}
\usepackage{stm32tikz}
\begin{document}
\begin{tikzpicture}
\cRes{R1}{0,0}{0,-1.5}{10K}
\WireV{R1a}{R1a}
\WireV{R1b}{R1b}
\end{tikzpicture}
\end{document}
""", {"W": 0, "M": 2})

# \MCUauto: 3 个引脚按间距等距、围绕块中心, 且**列表第一个在最上面**。
# ⚠ 后一条是语义断言, 不是几何断言 —— 顺序反了间距一样匀, 图上看不出错,
#   任何排版/电气门都查不出。初版就写反了(自下而上), 所以值得钉住。
def expect_mcu_pins(name, tex_body, n_pins, want_pitch_cm):
    import shutil
    import subprocess
    xelatex = shutil.which("xelatex")
    if not xelatex:
        SKIP.append(f"{name}（本机没有 xelatex）")
        return
    tmp = tempfile.mkdtemp(prefix="snmcu_")
    with open(os.path.join(tmp, "probe.tex"), "w", encoding="utf-8") as f:
        f.write(tex_body)
    env = _paths.texinputs_env()   # sty 在 assets/, 由 _paths 负责找
    try:
        subprocess.run([xelatex, "-interaction=nonstopmode", "-halt-on-error",
                        "probe.tex"], cwd=tmp, env=env, timeout=120,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        sc = N.parse_sidecar(os.path.join(tmp, "probe.net"))
        pins = [(k, v[1]) for k, v in sc["terms"].items()
                if k.startswith("MB.")]
        # 按 y 从上到下排(图像坐标里 y 大 = 上)
        pins.sort(key=lambda kv: -kv[1])
        names = [k.split(".")[1] for k, _ in pins]
        want = [f"P{i}" for i in range(n_pins)]
        if names != want:
            FAIL.append(f"{name}: 引脚自上而下应为 {want}, 实际 {names}")
            return
        ys = [y for _, y in pins]
        pitch = abs(ys[0] - ys[1]) / 28.45274
        if abs(pitch - want_pitch_cm) > 0.01:
            FAIL.append(f"{name}: 间距 {pitch:.3f}cm ≠ {want_pitch_cm}cm")
            return
        # 首末引脚应围绕块中心对称
        mid = (ys[0] + ys[-1]) / 2
        ctr = sc["terms"].get("MBc")
        if ctr and abs(mid - ctr[1]) > 0.6:
            FAIL.append(f"{name}: 引脚不居中(中点 {mid:.1f} vs 中心 {ctr[1]:.1f})")
            return
        PASS.append(name)
    except Exception as e:                      # noqa: BLE001
        FAIL.append(f"{name}: 异常 {type(e).__name__}: {e}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


expect_mcu_pins("TeX: \\MCUauto 3 引脚自上而下、等距 0.9cm、居中", r"""
\documentclass[border=6pt]{standalone}
\usepackage{ctex}
\usepackage{stm32tikz}
\begin{document}
\begin{tikzpicture}
\MCUauto{MB}{0}{0}{3cm}{3.4cm}{说明}{0.9cm}{P0, P1, P2}
\Term{MCU中心}{MBc}
\end{tikzpicture}
\end{document}
""", 3, 0.9)

expect_mcu_pins("TeX: \\MCUauto 2 引脚落在 ∓0.45cm", r"""
\documentclass[border=6pt]{standalone}
\usepackage{ctex}
\usepackage{stm32tikz}
\begin{document}
\begin{tikzpicture}
\MCUauto{MB}{0}{0}{3cm}{3.4cm}{说明}{0.9cm}{P0, P1}
\Term{MCU中心}{MBc}
\end{tikzpicture}
\end{document}
""", 2, 0.9)

# \RowAt / \ColAt: 取参考点的单轴坐标。这类宏的失效模式是"看起来对但差一点"
# (取错轴、或者被上一次 \pgfgetlastxy 覆盖), 而差几 pt 门禁查不出 ——
# 所以断言精确相等。
def expect_axis(name, tex_body, want):
    """编译并断言 MBc 之外的端子坐标精确等于 want(容差 0.01pt)。"""
    import shutil
    import subprocess
    xelatex = shutil.which("xelatex")
    if not xelatex:
        SKIP.append(f"{name}（本机没有 xelatex）")
        return
    tmp = tempfile.mkdtemp(prefix="snaxis_")
    with open(os.path.join(tmp, "probe.tex"), "w", encoding="utf-8") as f:
        f.write(tex_body)
    env = _paths.texinputs_env()   # sty 在 assets/, 由 _paths 负责找
    try:
        subprocess.run([xelatex, "-interaction=nonstopmode", "-halt-on-error",
                        "probe.tex"], cwd=tmp, env=env, timeout=120,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        sc = N.parse_sidecar(os.path.join(tmp, "probe.net"))
        bad = []
        for k, (wx, wy) in want.items():
            gx, gy = sc["terms"].get(k, (None, None))
            if gx is None or abs(gx - wx) > 0.01 or abs(gy - wy) > 0.01:
                bad.append(f"{k}: 期望 ({wx},{wy}) 实际 ({gx},{gy})")
        if bad:
            FAIL.append(f"{name}: " + "; ".join(bad))
        else:
            PASS.append(name)
    except Exception as e:                      # noqa: BLE001
        FAIL.append(f"{name}: 异常 {type(e).__name__}: {e}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# base 在 (0,-3.9); \RowAt 取它的 y、\ColAt 取它的 x。
# 期望(单位 pt, 1cm=28.45274):
#   l=(3.4cm, -3.9cm)  r=(5.5cm, -3.9cm)  c=(0, -7.0cm)
#   s = `base |- r` = (base.x, r.y) = (0, -3.9cm) —— `A |- B` 是"A 的 x、
#   B 的 y", 顺序别记反(这条断言就是钉住它的)。
CM = 28.45274
expect_axis("TeX: \\RowAt/\\ColAt 精确取单轴坐标", r"""
\documentclass[border=6pt]{standalone}
\usepackage{ctex}
\usepackage{stm32tikz}
\begin{document}
\begin{tikzpicture}
\coordinate (base) at (0,-3.9);
\RowAt{l}{base}{3.4}
\RowAt{r}{base}{5.5}
\ColAt{c}{base}{-7.0}
\Span{s}{base}{r}
\Term{l}{l}\Term{r}{r}\Term{c}{c}\Term{s}{s}
\end{tikzpicture}
\end{document}
""", {"l": (3.4 * CM, -3.9 * CM), "r": (5.5 * CM, -3.9 * CM),
      "c": (0.0, -7.0 * CM), "s": (0.0, -3.9 * CM)})


# \AnnoPack: 同列的多行说明块必须被 place.py **按真实高度分开**。
# 这是"问题 2"的回归 —— 它护的是一个**门禁查不出**的东西: 手写坐标撞上时
# 排版门当然会报, 但报之前已经浪费了一次 build; 而如果两块高度恰好估错到
# "差一点点", 还可能报不出来(挨着但不重叠)。所以断言的是"上游就能排开"。
def expect_pack(name, tex_body):
    """编译 + 跑 place.py, 断言同列两块的最终盒不重叠且有序。"""
    import shutil as _sh
    import subprocess as _sp
    xelatex = _sh.which("xelatex")
    if not xelatex:
        SKIP.append(f"{name}（本机没有 xelatex）")
        return
    here = os.path.dirname(os.path.abspath(__file__))
    tmp = tempfile.mkdtemp(prefix="snpack_")
    # ⚠ 偏移文件与探测文档都用**独一无二**的名字, 不要用 `auto_offsets.tex` /
    #   `probe.tex` 这种通用名。实测踩过: 用例在 tmp 里编译, 但 `TEXINPUTS`
    #   末尾的分隔符让**当前工作目录**仍在搜索路径上 —— 于是 cwd 里若恰好有
    #   一份同名的 `auto_offsets.tex`(开发目录里就有), xelatex 会**优先读到
    #   那一份**, 刚生成的这份被忽略。症状是"偏移算了但没生效", 且**不报任何
    #   错**(读到的是一份合法文件)。
    uniq = os.path.basename(tmp)
    off_name = f"sn_off_{uniq}.tex"
    tex_name = f"sn_probe_{uniq}.tex"
    try:
        with open(os.path.join(tmp, tex_name), "w", encoding="utf-8") as f:
            f.write(tex_body.replace("@OFFFILE@", off_name))
        net = os.path.join(tmp, tex_name[:-4] + ".net")
        # 第一遍: 偏移全 0 -> 两块重叠; 然后解算, 再编一遍让偏移生效
        for _ in range(2):
            _sp.run([xelatex, "-interaction=nonstopmode", tex_name],
                    cwd=tmp, env=_paths.texinputs_env(), timeout=180,
                    stdout=_sp.DEVNULL, stderr=_sp.DEVNULL)
            if not os.path.exists(net):
                FAIL.append(f"{name}: 编译没产出侧车")
                return
            # 解算偏移(第一遍之后就要跑, 否则第二遍无偏移可用)
            r = _sp.run([sys.executable, os.path.join(here, "place.py"), net,
                         "-o", os.path.join(tmp, off_name)],
                        cwd=tmp, env=_paths.texinputs_env(), timeout=60,
                        capture_output=True, text=True, encoding="utf-8",
                        errors="replace")
            if r.returncode not in (0, 1):
                FAIL.append(f"{name}: place.py rc={r.returncode} {r.stderr[:120]}")
                return
        sc = N.parse_sidecar(net)
        blocks = [a for a in sc["annos"] if "块" in a[4]]
        if len(blocks) < 2:
            FAIL.append(f"{name}: 只找到 {len(blocks)} 个块（应 2 个）")
            return
        blocks.sort(key=lambda a: -max(a[1], a[3]))       # 上 -> 下
        hi, lo = blocks[0], blocks[1]
        gap = min(hi[1], hi[3]) - max(lo[1], lo[3])
        if gap < 0:
            FAIL.append(f"{name}: 两块仍重叠 {gap:.1f}pt（堆叠没生效）")
            return
        PASS.append(f"{name}（间隙 {gap:.1f}pt）")
    except Exception as e:                              # noqa: BLE001
        FAIL.append(f"{name}: 异常 {type(e).__name__}: {e}")
    finally:
        _sh.rmtree(tmp, ignore_errors=True)


# 两块给**同一个列顶** -> 第一遍偏移都为 0, 完全重叠; place.py 必须按真实高度排开。
expect_pack("TeX: \\AnnoPack 同列两块自动分开", r"""
\documentclass[border=6pt]{standalone}
\usepackage{ctex}
\usepackage{stm32tikz}
\begin{document}
\begin{tikzpicture}
\InputAnnoOffsets{@OFFFILE@}
\coordinate (col) at (0,0);
\AnnoPack{blkA}{ann, align=left, text width=5cm}{col}{%
  \textbf{块一}\\[2pt] 第一行\\第二行\\第三行\\第四行}
\AnnoPack{blkB}{ann, align=left, text width=5cm}{col}{%
  \textbf{块二}\\[2pt] 甲\\乙}
\DumpCanvas
\end{tikzpicture}
\end{document}
""")


def main():
    total = len(PASS) + len(FAIL) + len(SKIP)
    print(f"通过 {len(PASS)} / 失败 {len(FAIL)} / 跳过 {len(SKIP)} "
          f"(共 {total})\n")
    for p in PASS:
        print(f"  ok   {p}")
    for s in SKIP:
        print(f"  skip {s}")
    if FAIL:
        print()
        for f in FAIL:
            print(f"  FAIL {f}")
        print(f"\n===== {len(FAIL)} 个用例失败 =====")
        return 1
    if SKIP:
        # **有跳过就不报"全部通过"** —— 被跳过的用例正是 TeX 侧那些(记账条数、
        # 宏静默失效), 它们"没跑过"和"跑过且通过"完全不是一回事。CI 里应当
        # 装好 TeX 让 SKIP=0; 本机缺工具链时明确告知还差多少用例没验。
        print(f"\n===== {len(PASS)} 个通过, 但 {len(SKIP)} 个**跳过未验** =====")
        print("跳过的多是 TeX 端到端用例(需要 xelatex)。它们护的是"
              "\"宏静默失效\"这类最危险的漏检,")
        print("**不能当成通过**。装好 MiKTeX/TeX Live(含 ctex) 后重跑, "
              "SKIP 应为 0。")
        return 1
    print("\n===== 全部通过 =====")
    print("检查器能抓: 短路 / 旁路 / 穿体 / 悬空 / 接错 / 孤标签 / 非正交,")
    print("排版七类(文字重叠/压线/压器件/压符号/平行过近/越界/器件重叠),")
    print("并且 TeX 侧记账条数正确(端到端); 正确电路与干净排版不误报。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
