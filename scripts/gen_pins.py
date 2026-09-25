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

退出码: 0 = 已写出; 1 = 标签没找到; 2 = 用法/环境错误。
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from read_project import read_config                # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="从 STM32 工程读引脚 → pins.tex")
    ap.add_argument("proj", help="STM32 工程目录(含 .ioc 或 Core/Inc/main.h)")
    ap.add_argument("--tag", action="append", default=[], metavar="标签=宏后缀",
                    help="标签映射, 可多次给; 不给则全部标签 → \\Pin<标签名>")
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

    # 组装映射: 标签 -> 宏后缀
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
            mapping[tag] = suffix
    else:
        mapping = {t: t for t in pins}

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
