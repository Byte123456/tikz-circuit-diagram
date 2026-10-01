#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
从 STM32 工程读出引脚, 生成 pins.tex 供 .tex 引用。

为什么必须有这一步: "引脚从源码读, 不要手抄" —— 手抄会写错(烧片子), 而且会
悄悄过期。TikZ 的 .tex 是纯文本, 直接写 PA0 就是手抄。所以读工程的
`main.h` / `.ioc`, 落成几行 \def, 让 .tex 里的引脚名也变成生成物。

**输出到当前工作目录**, 不是脚本目录。

用法:
    python <skill>/scripts/gen_pins.py <工程目录>
    python <skill>/scripts/gen_pins.py <工程目录> --tag LED_R=ADC --tag LED_G=Drive
    python <skill>/scripts/gen_pins.py <工程目录> --list

  <工程目录>  含 .ioc / Core/Inc/main.h 的 STM32 工程(CubeMX 生成的目录)
  --tag A=B   把工程里的标签 A 映射成 .tex 里的 \\PinB;
              不给 --tag 时默认全部标签 → \\Pin<标签名>
  --list      只列出工程里有哪些标签, 不写文件

⚠ **宏后缀只能含字母**。TeX 的控制序列遇非字母字符即结束, 所以
  `\\def\\PinLED1{PA8}` 定义的其实是 `\\PinLED`(后面那个 `1` 是游离字符),
  调用 `\\PinLED1` 会报 "Use of \\PinLED doesn't match its definition"。
  本脚本会把后缀里的非字母字符去掉; 若净化后两个标签撞成同一个宏名, 会**报错
  退出**并给出改法(而不是静默生成互相覆盖的 \\def)。

退出码: 0 = 已写出; 1 = 标签没找到或宏名冲突; 2 = 用法/环境错误。
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from read_project import read_config                # noqa: E402


def _tex_suffix(name):
    """标签 → 合法的 TeX 控制序列后缀(只保留 ASCII 字母)。

    TeX 的控制序列**只吃字母**, 遇到数字/下划线等非字母字符就结束。去掉它们
    才能保证 `\\Pin<suffix>` 是单个合法控制序列。净化可能让两个标签撞名,
    调用方负责查重。
    """
    return "".join(ch for ch in name if ch.isascii() and ch.isalpha())


def _suggest_fix(clashes):
    """给冲突的宏名生成一行可照抄的 --tag 命令。"""
    parts = []
    for s, tags in sorted(clashes.items()):
        for j, t in enumerate(sorted(tags)):
            parts.append(f"--tag {t}={s}{chr(65 + j)}")
    return " ".join(parts)



def main():
    ap = argparse.ArgumentParser(description="从 STM32 工程读引脚 → pins.tex")
    ap.add_argument("proj", help="STM32 工程目录(含 .ioc 或 Core/Inc/main.h)")
    ap.add_argument("--tag", action="append", default=[], metavar="标签=宏后缀",
                    help="标签映射, 可多次给; 不给则全部标签 → \\Pin<标签名>。"
                         "宏后缀只能含字母(非字母字符会被去掉)")
    ap.add_argument("--list", action="store_true", help="只列出标签, 不写文件")
    ap.add_argument("--out", default="pins.tex", help="输出文件名(默认 pins.tex)")
    args = ap.parse_args()

    if not os.path.isdir(args.proj):
        print(f"错误: 工程目录不存在: {args.proj}", file=sys.stderr)
        return 2

    try:
        cfg = read_config(args.proj)
    except Exception as e:                           # noqa: BLE001
        print(f"错误: 读工程 {args.proj!r} 失败: {type(e).__name__}: {e}",
              file=sys.stderr)
        return 2

    for w in cfg.get("warnings", []):
        print(f"警告: {w}", file=sys.stderr)

    pins = cfg.get("pins", {})
    if not pins:
        print(f"错误: 工程 {args.proj} 里没读到任何引脚标签。", file=sys.stderr)
        print("  确认 .ioc 里给引脚设了 GPIO_Label, 或 main.h 里有 "
              "<标签>_Pin / <标签>_GPIO_Port 宏。", file=sys.stderr)
        print("  用 --list 看能读到什么。", file=sys.stderr)
        return 1

    if args.list:
        print(f"工程 {args.proj} 的引脚标签 (来源: {cfg.get('pin_source')}):")
        for tag, val in sorted(pins.items()):
            port = val[0] if isinstance(val, (list, tuple)) else val
            print(f"  {tag:<20} {port}")
        return 0

    # 组装映射: 标签 -> 宏后缀。后缀一律净化成合法 TeX 控制序列(只含字母),
    # 否则 \def\PinLED1 定义的是 \PinLED, 调用处会报
    # "Use of \PinLED doesn't match its definition" 而白费一次 build。
    if args.tag:
        mapping = {}
        for spec in args.tag:
            if "=" not in spec:
                print(f"错误: --tag 要写成 标签=宏后缀, 收到 {spec!r}",
                      file=sys.stderr)
                return 2
            tag, suffix = (t.strip() for t in spec.split("=", 1))
            if tag not in pins:
                print(f"错误: 工程里没有标签 {tag!r}。可用: {sorted(pins)}",
                      file=sys.stderr)
                return 1
            clean = _tex_suffix(suffix)
            if not clean:
                print(f"错误: --tag {spec!r} 的宏后缀净化后是空串(后缀必须"
                      f"含字母)。", file=sys.stderr)
                return 2
            if clean != suffix:
                print(f"提示: 宏后缀 {suffix!r} 含非字母字符, 已净化成 "
                      f"{clean!r}(TeX 控制序列只吃字母)。", file=sys.stderr)
            mapping[tag] = clean
    else:
        mapping = {}
        for t in pins:
            clean = _tex_suffix(t)
            if not clean:
                print(f"错误: 标签 {t!r} 净化后是空串, 无法生成宏名; "
                      f"请用 --tag {t}=<纯字母宏后缀> 指定。", file=sys.stderr)
                return 2
            mapping[t] = clean

    # 查重: 净化后撞名的话, 两个 \def 会互相覆盖, 而 \PinX 用的是后写的那个
    # —— 引脚静默接错, 编译照过。必须报错, 不能静默。
    by_suffix = {}
    for tag, suffix in mapping.items():
        by_suffix.setdefault(suffix, []).append(tag)
    clashes = {s: ts for s, ts in by_suffix.items() if len(ts) > 1}
    if clashes:
        print("错误: 净化后有标签撞成同一个宏名, 会互相覆盖:", file=sys.stderr)
        for s, ts in sorted(clashes.items()):
            print(f"  \\Pin{s}  ← {sorted(ts)}", file=sys.stderr)
        print("  用 --tag 给它们各自指定不同的纯字母后缀, 例如:", file=sys.stderr)
        print(f"    python {sys.argv[0]} {args.proj} "
              f"{_suggest_fix(clashes)}", file=sys.stderr)
        return 1

    lines = [
        "% 本文件由 gen_pins.py 自动生成 —— 不要手改。",
        "% 改引脚请改 STM32 工程(CubeMX 里改标签), 然后重跑 gen_pins.py。",
        f"% 来源: {args.proj} ({cfg.get('pin_source')})",
        "",
    ]
    for tag, suffix in mapping.items():
        val = pins[tag]
        port = val[0] if isinstance(val, (list, tuple)) else val
        lines.append(f"\\def\\Pin{suffix}{{{port}}}   % {tag}")
    lines.append("")

    with open(args.out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"已写出 {os.path.abspath(args.out)}")
    for tag, suffix in mapping.items():
        val = pins[tag]
        port = val[0] if isinstance(val, (list, tuple)) else val
        print(f"  \\Pin{suffix:<12} = {port:<6} (来自 {tag})")

    if cfg.get("hse"):
        print(f"  HSE = {cfg['hse']} Hz   工具链 {cfg.get('toolchain')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
