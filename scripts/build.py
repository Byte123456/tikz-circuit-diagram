#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
一键闭环 —— 编译 → 自动摆标注 → 重编 → 双门 → 出 SVG。

为什么要有它: 五道工序散成五条命令时, 漏跑一道的代价是"以为检过了其实没检"
(开发本工具时的真事: 排版门一度看不到值标签, 报 0 问题; 不是"查了没问题",
是"压根没看")。串成一条命令, 漏跑就成了执行不了。

**工作目录 = 你运行它的目录**(不是脚本所在目录)。图与产物都留在你当前所在的
工程目录里; `stm32tikz.sty` 由脚本自动加进 TEXINPUTS, 不用拷贝、不用改
preamble 里的 \\usepackage 路径。

用法:
    python <skill>/scripts/build.py circuit            # 不给扩展名
    python <skill>/scripts/build.py circuit.tex        # 给也行
    python <skill>/scripts/build.py circuit --svg      # 额外出 SVG
    python <skill>/scripts/build.py circuit --no-place # 关掉自动摆标注
    python <skill>/scripts/build.py circuit --report   # 逐个列出摆位结果

退出码: 0 = 全绿; 1 = 有一道门报问题; 2 = 编译失败或用法错误。
"""
import argparse
import os
import re
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _paths                                        # noqa: E402


def run(cmd, env=None):
    """在**当前工作目录**跑命令(不是脚本目录)。"""
    return subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace",
                          env=env or os.environ.copy())


def find_xelatex():
    """找 xelatex —— 统一走 _paths, 不在这里写死用户目录。"""
    return _paths.find_xelatex()


def tex_compile(stem, env):
    """跑一遍 xelatex。返回 (ok, 输出)。"""
    x = find_xelatex()
    if not x:
        return False, "找不到 xelatex(需要 MiKTeX/TeX Live 且装了 ctex)"
    r = run([x, "-interaction=nonstopmode", f"{stem}.tex"], env=env)
    out = (r.stdout or "") + (r.stderr or "")
    # 按**日志标记**判定, 不按退出码。理由(实测): nonstopmode 下 xelatex 遇到
    # `\undefinedmacro` 这类**可恢复错误**会以 rc=1 退出, **但照样产出 PDF** ——
    # 只看退出码会把"图能出、但带着错误"判成失败; 反过来若用 rc==0 判成功,
    # 则"编译出图了但控制台有 ! 错误"会被放过。真正该拦的是 `! ` / Emergency
    # stop / Fatal error 这些**致命标记**。
    # (注: xelatex **成功**时 rc=0, 失败 rc=1 —— 曾经把"没设 TEXINPUTS 导致
    #  sty not found"的失败误当成"成功却返回 1", 现已在正确环境下复核。)
    ok = ("! " not in out and "Emergency stop" not in out
          and "Fatal error" not in out)
    return ok, out


def first_errors(out, n=8):
    keys = ("!", "Undefined control sequence", "No shape named",
            "Giving up on this path", "Illegal unit of measure",
            "Unknown function", "Missing character")
    return [ln for ln in out.splitlines() if any(k in ln for k in keys)][:n]


def tex_hints(out):
    """从编译输出里识别"报错误导性很强"的已知坑, 给出指向真因的提示。

    这些提示存在的意义: 让 agent **不用预读文档**就能排错。此前"缺 ctex
    报爆栈"必须写进 SKILL.md 常见坑让人提前记住 —— 现在报错自己会说话。
    """
    hints = []
    if "capacity exceeded" in out and "input stack" in out:
        # 缺 ctex 的**旧**形态: 中文没有字形, TeX 递归到爆栈, 报出来的
        # 栈溢出完全不指向真因。(新版 MiKTeX 已改走"静默丢字形"路线,
        # 见 cjk_missing —— 但旧 TeX Live 上仍是这个表现, 提示保留。)
        hints.append("→ 「TeX capacity exceeded / input stack」多半不是图的"
                     "问题: 缺 \\usepackage{ctex}。中文没有字形时 TeX 会"
                     "递归爆栈, 检查 preamble 是否已加载 ctex。")
    return hints


_CJK_MISS = re.compile(
    r"Missing character: There is no ([\u3400-\u9fff\uf900-\ufaff]) in font")


def cjk_missing(log_path):
    """检查 .log 里有没有「CJK 字形缺失」(缺 \\usepackage{ctex} 的标志)。

    这个形态比爆栈**更阴险**(实测, MiKTeX 25.x): 编译成功、双门全绿,
    但 PDF/SVG 里中文**全部没渲染** —— 中文标注的排版盒量出来是空的,
    门禁拿这些空盒照样判"无重叠"。这是渲染层的"压根没看": 图看着是好的,
    交出去才发现满纸空洞。好在 .log 每丢一个字形就记一行
    `Missing character: There is no ⟨CJK⟩ in font`, 一查便知。

    只匹配 CJK 码位: 本工具的中文只经 ctex 管理, ctex 正常时不会出现
    这行; `; in font nullfont` 之类的英文噪音(未定义宏的副产物)不误伤。
    """
    if not os.path.exists(log_path):
        return False
    with open(log_path, encoding="utf-8", errors="replace") as f:
        return bool(_CJK_MISS.search(f.read()))


def cjk_fail_if_missing(stem):
    """编译"成功"后核 .log; 发现中文没渲染就按**失败**处理。

    返回 True 表示已报错, 调用方应立刻 return 2。整个交付物在渲染层是坏的
    (中文全空), 放它过门禁等于交一张白卷。
    """
    if not cjk_missing(stem + ".log"):
        return False
    print("  ⚠⚠ 编译「成功」, 但 .log 里有「CJK 字形缺失」—— 中文**没有渲染**!",
          file=sys.stderr)
    print("     → 缺 \\usepackage{ctex}。此时双门全绿也是假的: 中文标注的"
          "排版盒是空的, 量了等于没量。", file=sys.stderr)
    print("       修法: preamble 加 \\usepackage{ctex}(放在 stm32tikz 之前)。",
          file=sys.stderr)
    return True


def print_net_summary(net):
    """把网表**自动**打出来(紧凑版)。

    为什么: "写 \\Expect 前先跑 --show-nets" 曾是一条必须预读、靠自觉遵守的
    行为规则 —— 而"查了没问题"和"压根没看"在输出上一模一样, 跳过它毫无
    声息。把网表直接摆在每次 build 的输出里, 这条规则就不再需要被记住:
    信息每次都在眼前。网太多时只报数量, 保持输出紧凑。
    """
    try:
        from sidecar import parse_sidecar, build_nets   # noqa: E402
        sc = parse_sidecar(net)
        _, _, nets, *_ = build_nets(sc)
    except Exception:                    # noqa: BLE001
        return                           # 门禁会报告侧车问题, 这里不添乱
    named = sorted((sorted(set(ms)) for ms in nets.values() if len(ms) >= 2),
                   key=lambda ms: (-len(ms), ms))
    if not named:
        return
    if len(named) <= 10:
        print("  网表(写 \\Expect 前对照; 元件本体是网络边界):")
        for i, ms in enumerate(named, 1):
            print(f"    网{i}: {', '.join(ms)}")
    else:
        print(f"  网表: {len(named)} 张多成员网(不逐条列出; "
              f"要看全跑 check_tex_net.py --show-nets {net})")


def gate(script, net, extra=()):
    here = os.path.dirname(os.path.abspath(__file__))
    r = run([sys.executable, os.path.join(here, script), *extra, net])
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def main():
    ap = argparse.ArgumentParser(description="一键闭环: 编译 → 摆位 → 双门 → SVG")
    ap.add_argument("tex", help=".tex 文件名或主干(可省扩展名)")
    ap.add_argument("--svg", action="store_true", help="额外用 pdftocairo 出 SVG")
    ap.add_argument("--no-place", action="store_true", help="关掉自动摆标注")
    ap.add_argument("--report", action="store_true", help="摆位时逐个列出结果")
    ap.add_argument("--probe", metavar="TEX",
                    help="编译一个一次性探针文档并打印侧车摘要(不跑门禁)。"
                         "TEX 给 `-` 表示从 stdin 读。省掉手写探针和 TEXINPUTS 的坑")
    args = ap.parse_args()

    if args.probe is not None:
        return probe(args.probe)

    stem = args.tex[:-4] if args.tex.lower().endswith(".tex") else args.tex
    # 工作目录 = 用户当前目录
    if not os.path.exists(stem + ".tex"):
        print(f"错误: 当前目录下找不到 {stem}.tex\n"
              f"  (工作目录: {os.getcwd()})", file=sys.stderr)
        return 2

    sty_dir = _paths.find_sty()
    if not sty_dir:
        print(_paths.sty_hint(), file=sys.stderr)
        return 2
    env = _paths.texinputs_env()
    print(f"样式: {os.path.join(sty_dir, 'stm32tikz.sty')}")

    net = stem + ".net"

    # ---- 第 1 遍 ----
    print(f"[1/5] xelatex {stem}.tex (第 1 遍)")
    if os.path.exists(net):
        os.remove(net)                       # 避免读到上一轮残留
    ok, out = tex_compile(stem, env)
    if not ok or not os.path.exists(net):
        print("  编译失败。前几条:")
        for ln in first_errors(out, 10):
            print("    " + ln)
        for h in tex_hints(out):
            print("  " + h)
        log = stem + ".log"
        if os.path.exists(log):
            logtxt = ""
            with open(log, encoding="utf-8", errors="replace") as f:
                logtxt = f.read()
            for ln in first_errors(logtxt, 14):
                print("    " + ln)
            for h in tex_hints(logtxt):
                print("  " + h)
        return 2
    print("  ok")
    if cjk_fail_if_missing(stem):
        return 2

    # ---- 自动摆位 + 第 2 遍 ----
    if not args.no_place:
        print("[2/5] place.py 解算标注偏移")
        here = os.path.dirname(os.path.abspath(__file__))
        cmd = [sys.executable, os.path.join(here, "place.py"), net]
        if args.report:
            cmd.append("--report")
        r = run(cmd)
        print("  " + (r.stdout or "").strip().replace("\n", "\n  "))
        # place.py 的退出码: 0 = 全摆开, 1 = 有摆不开的(仍写偏移), >=2 = 工具错误。
        # **>=2 必须让整条 build 失败** —— 此前一律继续, 于是 place.py 崩了
        # (参数错、读不到实体)时 build 仍报"全绿", 而标注其实用的是上一次的
        # 偏移表, 甚至根本没有偏移表。
        if r.returncode >= 2:
            print(f"  place.py 失败 (rc={r.returncode}) —— 标注偏移没算出, 停。",
                  file=sys.stderr)
            if r.stderr:
                print("  " + r.stderr.strip().replace("\n", "\n  "),
                      file=sys.stderr)
            return 2
        if r.returncode == 1:
            print("  ⚠ 有标注**摆不开**(仍尽量给了偏移) —— 见上面的 [挤] 报告。",
                  file=sys.stderr)
        print("[3/5] xelatex (第 2 遍, 偏移生效)")
        ok2, out2 = tex_compile(stem, env)
        if not ok2:
            print("  第二遍编译失败:")
            for ln in first_errors(out2):
                print("    " + ln)
            for h in tex_hints(out2):
                print("  " + h)
            return 2
        print("  ok")
        if cjk_fail_if_missing(stem):
            return 2
    else:
        print("[2/5] 自动摆位: 关掉了 (--no-place)")
        print("[3/5] 跳过")

    # ---- 双门 ----
    print("[4/5] 两道门")
    rc_l, o_l = gate("check_tex_layout.py", net)
    rc_n, o_n = gate("check_tex_net.py", net)
    for o in (o_l, o_n):
        for ln in o.rstrip().splitlines():
            if (ln.startswith("=====") or ln.startswith("==") or "  [" in ln
                    or ln.startswith("错误:")):
                print("  " + ln)
    # rc=2 表示"输入有问题/侧车根本没检查" —— 与"检查了且有问题"不同, 但也
    # 绝不能算过。此前只把非 0 与 0 二分, 空/缺失侧车被当成 rc=0 放过去了。
    if rc_l == 0 and rc_n == 0:
        print("  排版 0 / 电气 0 —— 全绿")
    elif 2 in (rc_l, rc_n):
        # rc=2 是"输入没检查成"(缺失/空侧车/用法错), 与 rc=1"查了有问题"
        # 是两回事。混在一起会让人以为"电路有问题", 实际是**门根本没跑起来**。
        print(f"  排版 rc={rc_l} / 电气 rc={rc_n} —— **有门没检查成**(见错误行)。")
        for o, rc in ((o_l, rc_l), (o_n, rc_n)):
            if rc != 0:
                print(o)
    else:
        print(f"  排版 rc={rc_l} / 电气 rc={rc_n} —— 有问题:")
        for o, rc in ((o_l, rc_l), (o_n, rc_n)):
            if rc != 0:
                print(o)
    # 网表摘要(自动, 紧凑): 让"写 \Expect 前先看网"不再是一条需要预读的规则
    print_net_summary(net)

    # ---- SVG ----
    svg_fail = False
    if args.svg:
        print("[5/5] pdftocairo -svg")
        pdf = stem + ".pdf"
        svg = stem + ".svg"
        if not os.path.exists(pdf):
            print("  失败: 没有 .pdf —— 上一遍编译没产出 PDF", file=sys.stderr)
            svg_fail = True
        elif not shutil.which("pdftocairo"):
            print("  失败: 你**明确要了** --svg 但没装 pdftocairo。",
                  file=sys.stderr)
            print("        → 装 poppler(pdftocairo) 后重跑; "
                  "或去掉 --svg 只要 .pdf。", file=sys.stderr)
            svg_fail = True
        else:
            if os.path.exists(svg):
                os.remove(svg)          # 先删, 好判断这次是否真的产出
            r = run(["pdftocairo", "-svg", pdf, svg])
            # 以**产物是否存在**判定, 不只看退出码。pdftocairo 正常失败时确实
            # 会返回非 0(实测: 输入不存在 rc=1), 所以退出码可用; 但"以文件为准"
            # 更稳 —— 它同时覆盖了"rc=0 却没写出文件"这种(理论上可能的)情形,
            # 而只看退出码抓不到它。开销为零, 所以两个都查。
            if r.returncode != 0 or not os.path.exists(svg):
                print(f"  失败: pdftocairo rc={r.returncode}, "
                      f"SVG {'没产出' if not os.path.exists(svg) else '已产出'}"
                      f"{(r.stderr or '').strip()[:200]}", file=sys.stderr)
                svg_fail = True
            else:
                print("  ok")
    else:
        print("[5/5] SVG: 未请求 (加 --svg)")

    if svg_fail:
        return 2                       # 明确请求的产物没出来 -> 整条失败
    if 2 in (rc_l, rc_n):
        return 2                       # 有门没检查成(输入问题), 不是"电路有问题"
    return 0 if (rc_l == 0 and rc_n == 0) else 1


def probe(spec):
    """
    探测模式: 编译一段临时 .tex, 把侧车里的几何**摘要**打印出来。

    为什么需要它: 想知道"某符号的端子在哪个偏移、本体多大", 以前要手写探针
    .tex 再编译读侧车 —— 而裸 `xelatex` **不会**自动加 TEXINPUTS, 于是第一脚
    就踩 `stm32tikz.sty not found`; `\\typeout` 的输出在 stdout 里 grep 不到,
    得去 tail 日志; 测出来的数还要手抄。这一模式把这三步都省了, 而且用的是
    与正式 build **完全相同**的编译环境(同一个 TEXINPUTS、同一个 xelatex),
    所以探针里能编译通过的, 正式图里一定能通过。

    用法:
        python <skill>/scripts/build.py x --probe probe.tex
        python <skill>/scripts/build.py x --probe -      # 从 stdin 读

    探针文档的常规写法(见 assets/geometry.md 的生成脚本也是这么干的):
        \\documentclass[border=4pt]{standalone}
        \\usepackage{ctex}
        \\usepackage{stm32tikz}
        \\begin{document}
        \\begin{tikzpicture}
        \\cNPN{Q}{0,0}
        \\cR{R1}{3,0}{4.5,0}{10K}\\LogLabel{R1}
        \\DumpCanvas
        \\end{tikzpicture}
        \\end{document}
    """
    import tempfile
    if spec == "-":
        src = sys.stdin.read()
    else:
        if not os.path.exists(spec):
            print(f"错误: 找不到探针文件 {spec}", file=sys.stderr)
            return 2
        with open(spec, encoding="utf-8") as f:
            src = f.read()

    sty_dir = _paths.find_sty()
    if not sty_dir:
        print(_paths.sty_hint(), file=sys.stderr)
        return 2
    env = _paths.texinputs_env()

    tmp = tempfile.mkdtemp(prefix="snprobe_")
    try:
        with open(os.path.join(tmp, "probe.tex"), "w", encoding="utf-8") as f:
            f.write(src)
        # 在临时目录里编译 —— 探针不该污染当前工作目录
        r = subprocess.run([find_xelatex() or "xelatex",
                            "-interaction=nonstopmode", "probe.tex"],
                           cwd=tmp, env=env, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=180)
        net = os.path.join(tmp, "probe.net")
        out = (r.stdout or "") + (r.stderr or "")
        errs = first_errors(out)
        if errs:
            print("编译有错误:")
            for ln in errs:
                print("   " + ln)
        if not os.path.exists(net):
            print("错误: 没产出侧车 probe.net", file=sys.stderr)
            return 2

        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from sidecar import parse_sidecar
        sc = parse_sidecar(net)

        print(f"== 探针侧车: {len(sc['terms'])} 端子 / {len(sc['bodies'])} 本体盒 "
              f"/ {len(sc['annos'])} 文字盒 / {len(sc['wires'])} 导线 ==")
        if sc["bodies"]:
            print("\n-- 器件本体盒 (x0,y0 .. x1,y1 pt; 宽 × 高) --")
            for n, b in sorted(sc["bodies"]):
                x0, y0 = min(b[0], b[2]), min(b[1], b[3])
                x1, y1 = max(b[0], b[2]), max(b[1], b[3])
                print(f"   {n:<14} {x0:8.2f},{y0:9.2f} .. {x1:8.2f},{y1:9.2f}"
                      f"   {x1 - x0:6.2f} × {y1 - y0:6.2f}")
        if sc["terms"]:
            print("\n-- 端子 (名字 @ x,y pt) --")
            for n, (x, y) in sorted(sc["terms"].items()):
                print(f"   {n:<20} ({x:8.2f}, {y:9.2f})")
        if sc.get("gsyms"):
            print("\n-- 电源/地符号图形范围 --")
            for g in sc["gsyms"]:
                print(f"   {g[0]:<8} x {g[1]:8.2f}..{g[3]:8.2f}"
                      f"   y {g[2]:9.2f}..{g[4]:9.2f}")
        if sc["annos"]:
            print("\n-- 文字盒 (含值标签) --")
            for a in sc["annos"]:
                kind = {"L": "值标签", "R": "标注"}.get(a[5], a[5])
                print(f"   [{kind}] {a[4][:44]:<46}"
                      f" 宽 {abs(a[2] - a[0]):6.2f} 高 {abs(a[3] - a[1]):6.2f}")
        if sc.get("annoat"):
            print("\n-- 自动摆位记录 (A|) --")
            for a in sc["annoat"]:
                print(f"   {a['key']:<16} 锚点 ({a['ax']:8.2f},{a['ay']:9.2f})"
                      f"  角次序 {a['corners']!r}")
        if sc.get("warns"):
            print("\n-- 记账警告 --")
            for w in sc["warns"]:
                print("   " + w)
        print(f"\n完整侧车留在: {net}")
        return 0
    finally:
        pass      # 探针目录**故意不删** —— 报错时用户要能去看 probe.log


if __name__ == "__main__":
    sys.exit(main())
