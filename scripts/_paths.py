#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
定位本技能的资源 —— 所有脚本共用这一处逻辑, 不要各自抄一份。

只定位 `stm32tikz.sty`(本技能自带, 在 assets/ 下; 编译时设进 TEXINPUTS)。

为什么要有它: 这段逻辑一旦被几个脚本各抄一份, 就很容易有人写死一个绝对路径
兜底 —— 别人 clone 下去那个路径不存在, 报"找不到 sty", 而真因只是"路径写死了"。
所以集中一处, 且**不写死绝对路径**。

查找顺序:
    环境变量 STM32TIKZ_STY → 本文件上溯各层的 assets/stm32tikz.sty
    → 脚本旁边 → 开发目录 → 当前工作目录
"""
import os
import shutil

_here = os.path.dirname(os.path.abspath(__file__))
_cache = {}


def _upward(start, name, levels=8):
    """从 start 逐级向上找名为 name 的文件/目录。"""
    d = start
    for _ in range(levels):
        p = os.path.join(d, name)
        if os.path.exists(p):
            return p
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    return None


def find_sty():
    """
    返回 stm32tikz.sty 的**目录**(要放进 TEXINPUTS)。找不到返回 None。
    目录而不是文件 —— xelatex 认的是搜索路径。
    """
    if "sty" in _cache:
        return _cache["sty"]
    cands = []
    env = os.environ.get("STM32TIKZ_STY")
    if env:
        cands.append(env if os.path.isdir(env) else os.path.dirname(env))
    # assets/ 在 skills/<name>/ 下; scripts/ 与它同级
    p = _upward(_here, os.path.join("assets", "stm32tikz.sty"))
    if p:
        cands.append(os.path.dirname(p))
    # 也接受"就在脚本旁边"的布局(开发时把 sty 和脚本放一起)
    if os.path.exists(os.path.join(_here, "stm32tikz.sty")):
        cands.append(_here)
    # 开发目录这种"工具与图混放"的布局
    p = _upward(_here, "stm32tikz.sty")
    if p:
        cands.append(os.path.dirname(p))
    cands.append(os.getcwd())

    for c in cands:
        if c and os.path.exists(os.path.join(c, "stm32tikz.sty")):
            _cache["sty"] = c
            return c
    _cache["sty"] = None
    return None


def sty_path():
    """返回 stm32tikz.sty 的完整路径(报错信息里用)。"""
    d = find_sty()
    return os.path.join(d, "stm32tikz.sty") if d else None


def texinputs_env():
    """返回一个把 sty 目录加进 TEXINPUTS 的环境副本。"""
    env = dict(os.environ)
    d = find_sty()
    if d:
        # TEXINPUTS 的分隔符: 本机(MiKTeX/win32)用 ';'; 其它平台用 ':'。
        sep = ";" if os.name == "nt" else ":"
        old = env.get("TEXINPUTS", "")
        # ⚠⚠ 目录必须放在**末尾** —— 这是"技能目录里的同名文件遮蔽用户工程
        #   文件"的坑, 实测踩到:
        #
        #   先前写成 `d + sep + old + sep`(技能目录**排在最前**), 于是 xelatex
        #   优先在技能目录里找**任何** `\input` 的文件。技能目录里恰好有一份
        #   `auto_offsets.tex`(开发时的残留), 结果在别的目录编译时,
        #   `\InputAnnoOffsets{auto_offsets.tex}` 读到的是**技能目录那一份**,
        #   刚生成的那份被忽略 —— 症状是"偏移算了但没生效", 两块说明文字仍然
        #   重叠, 而且**编译不报任何错**(读到了一个合法文件)。
        #
        #   写成 `old + sep + d + sep` 后: 用户工程(含 cwd)优先, 技能目录只在
        #   找不到时兜底。`.sty` 本来就只有技能目录里有, 所以照旧能找到。
        #
        # ⚠ 末尾那个 sep 也**必须**有 —— 它的含义是"再加上默认搜索路径"
        #   (含当前工作目录)。少了它, cwd 里的文件会找不到。
        env["TEXINPUTS"] = old + sep + d + sep
    return env


def sty_hint():
    return (
        f"错误: 找不到 stm32tikz.sty。\n"
        f"  · 它应在本技能的 assets/ 下(本脚本位置: {_here})。\n"
        f"  · 或设 STM32TIKZ_STY=/path/to/dir-with-sty。"
    )


def find_xelatex():
    """
    找 xelatex 可执行文件。先看 PATH, 再试几个常见的安装位置。

    **不写死用户目录** —— 早先这里硬编码了 `C:\\Users\\<某人>\\AppData\\...`,
    换个用户/换台机器就失效。改用 `%LOCALAPPDATA%` 环境变量拼路径, 任何人
    的本机路径都能正确展开。
    """
    p = shutil.which("xelatex")
    if p:
        return p
    cands = []
    local = os.environ.get("LOCALAPPDATA")
    if local:
        cands.append(os.path.join(local, "Programs", "MiKTeX", "miktex",
                                  "bin", "x64", "xelatex.exe"))
    cands += [
        r"C:\Program Files\MiKTeX\miktex\bin\x64\xelatex.exe",
        r"C:\Program Files (x86)\MiKTeX\miktex\bin\x64\xelatex.exe",
        "/Library/TeX/texbin/xelatex",           # macOS (MacTeX)
        "/usr/bin/xelatex",                      # Linux
        "/usr/local/bin/xelatex",
    ]
    for c in cands:
        if os.path.exists(c):
            return c
    return None


def xelatex_hint():
    return (
        "错误: 找不到 xelatex。\n"
        "  · 需要带 ctex 的 TeX 发行版: MiKTeX / TeX Live / MacTeX。\n"
        "  · 装好后确认 `xelatex --version` 能跑, 或把它所在目录加进 PATH。\n"
        "  · 本技能只用 xelatex + pdftocairo + Python 标准库, 没有别的依赖。"
    )


if __name__ == "__main__":
    print(f"脚本目录  : {_here}")
    print(f"sty 目录  : {find_sty()}")
    print(f"xelatex   : {find_xelatex()}")
