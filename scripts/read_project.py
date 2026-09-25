#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
从 STM32 工程目录读引脚与时钟配置 —— 本技能自带, 不依赖任何外部库。

为什么单独一个模块: `gen_pins.py` 要"从源码读引脚, 不要手抄"(手抄会写错端口,
而且会悄悄过期)。这段读文件的逻辑只有 60 行, 自成一个模块比依赖外部库好 ——
技能要能在任何仓库里单独跑起来。

读什么、按什么顺序
------------------
1. `Core/Inc/main.h`(或 `Inc/main.h`)—— 找 `#define <标签>_Pin GPIO_PIN_n`
   与 `#define <标签>_GPIO_Port GPIOx` **成对**出现, 这是 CubeMX 给设了
   User Label 的引脚。标签就是 `<标签>`。
2. 若上面一个都没读到, 退回 `.ioc`: 找 `P<口><号>.Signal=...`, 标签取
   `P<口><号>.GPIO_Label`(没有就用引脚名)。**必须回退** —— 有些工程在 CubeMX
   里没设 User Label, `main.h` 里就一个 `_Pin` 宏都没有, 只读 `main.h` 会
   **静默返回空字典**, 调用方还以为这工程没引脚。
3. 时钟: 优先 `Core/Inc/stm32f1xx_hal_conf.h` 的 `#define HSE_VALUE`(最权威),
   否则从 `.ioc` 的 `PLLMUL` + `PLLSourceVirtual` 反推。

用法
----
    from read_project import read_config
    cfg = read_config(os.path.expanduser("~/my-stm32-project"))
    cfg["pins"]        # {"LED_R": ("PA0", "GPIOA"), ...}  引脚名统一是 "PA0" 形式
    cfg["pin_source"]  # "main.h" | "ioc" | "none"
    cfg["warnings"]    # [str]
"""
import os
import re


def read_config(project_dir, verbose=False):
    """
    读 STM32 工程目录。返回 dict:
      "pins"       {标签: (引脚, 端口)} —— 引脚形如 "PA0"
      "pin_source" "main.h" | "ioc" | "none"
      "hse"        int | None
      "ioc"        {键: 值}
      "toolchain"  str | None
      "warnings"   [str]
    """
    res = {"pins": {}, "pin_source": "none", "hse": None,
           "ioc": {}, "toolchain": None, "warnings": []}

    # ---- 1) main.h 的引脚宏 ----
    main_h = None
    for cand in ("Core/Inc/main.h", "Inc/main.h"):
        p = os.path.join(project_dir, cand)
        if os.path.exists(p):
            main_h = p
            break
    if main_h:
        txt = open(main_h, encoding="utf-8", errors="replace").read()
        pins = dict(re.findall(r"#define\s+(\w+)_Pin\s+GPIO_PIN_(\d+)", txt))
        ports = dict(re.findall(r"#define\s+(\w+)_GPIO_Port\s+(GPIO[A-E])", txt))
        for label, num in pins.items():
            port = ports.get(label)
            if port:
                # 统一成 "PA0" 形式 —— `\MCU`/`\MCUauto` 的引脚名也是这个写法。
                # 两处不一致的话调用方得写两套名字才能对上, 很容易 KeyError。
                res["pins"][label] = (port.replace("GPIO", "P") + num, port)
        if res["pins"]:
            res["pin_source"] = "main.h"

    # ---- 2) .ioc: 引脚兜底 + 时钟 + 工具链 ----
    iocs = []
    if os.path.isdir(project_dir):
        iocs = sorted(f for f in os.listdir(project_dir) if f.endswith(".ioc"))
    if iocs:
        ioc_txt = open(os.path.join(project_dir, iocs[0]),
                       encoding="utf-8", errors="replace").read()
        for k, v in re.findall(r"^(RCC\.\w+|ProjectManager\.\w+)=(.+)$",
                               ioc_txt, re.M):
            res["ioc"][k] = v.strip()
        # HSE 反推: 72MHz / PLLMUL
        m = re.search(r"PLLMUL=RCC_PLL_MUL(\d+)", ioc_txt)
        if m and res["ioc"].get("RCC.PLLSourceVirtual", "").endswith("HSE"):
            res["hse"] = 72_000_000 // int(m.group(1))
        res["toolchain"] = res["ioc"].get("ProjectManager.TargetToolchain")

        if not res["pins"]:
            # 引脚名可能带多个后缀: PA0-WKUP / PC13-TAMPER-RTC / PD0-OSC_IN
            # 所以用 (-[\w-]+)* 而不是只允许一个 -WORD。
            labels = {}
            for m in re.finditer(
                    r"^(P[A-E]\d+(?:-[\w-]+)*)\.GPIO_Label=(.+)$", ioc_txt, re.M):
                labels[m.group(1)] = m.group(2).strip()
            for m in re.finditer(r"^(P[A-E]\d+(?:-[\w-]+)*)\.Signal=(\w+)$",
                                 ioc_txt, re.M):
                raw_pin, _sig = m.group(1), m.group(2)
                pin = raw_pin.split("-")[0]           # PA0-WKUP -> PA0
                port = "GPIO" + pin[1]
                tag = labels.get(raw_pin, pin)
                res["pins"][tag] = (pin, port)
            if res["pins"]:
                res["pin_source"] = "ioc"
                res["warnings"].append(
                    "main.h 里没有引脚宏(该工程未设 User Label), "
                    "引脚改从 .ioc 读取, 标签为引脚名或 .ioc 中的 GPIO_Label; "
                    "注意 .ioc 也会列出晶振/调试等固定功能脚")

    if not res["pins"]:
        res["warnings"].append(
            "没能从 main.h 或 .ioc 读到任何引脚 —— 请确认工程目录结构")

    # ---- 3) hal_conf.h 的 HSE_VALUE(最权威) ----
    for cand in ("Core/Inc/stm32f1xx_hal_conf.h", "Inc/stm32f1xx_hal_conf.h",
                 "Core/Inc/stm32f4xx_hal_conf.h", "Inc/stm32f4xx_hal_conf.h"):
        p = os.path.join(project_dir, cand)
        if os.path.exists(p):
            txt = open(p, encoding="utf-8", errors="replace").read()
            m = re.search(r"#define\s+HSE_VALUE\s+(\d+)", txt)
            if m:
                res["hse"] = int(m.group(1))
            break

    if verbose:
        print(f"引脚来源: {res['pin_source']}  ({len(res['pins'])} 个)")
        for k, v in sorted(res["pins"].items()):
            print(f"  {k:14s} {v[0]}")
        print(f"HSE: {res['hse']}  |  工具链: {res['toolchain']}")
        for w in res["warnings"]:
            print("  ⚠ " + w)

    return res
