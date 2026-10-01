---
name: tikz-circuit-diagram
description: 用 TikZ/circuitikz 画 STM32 电路图（接线图、原理图、电路图、示意图），并在排版时顺手吐出网表侧车供 Python 检查。产出矢量 PDF/SVG，中文正常。当用户要画电路图、接线图、原理图，或要求"图要能自检/不会短路/检查电气正确性"，或提到 circuitikz、TikZ 画电路、把电路图升级成可验证的图时使用。也用于排查已有 TikZ 电路图的排版与电气问题。
---

# TikZ 电路图 —— 画图时顺手吐网表，Python 负责检查

## 这个技能的核心思路

画电路图最难的地方不在**画**，在**验证**：图看着连了，不代表电气上连了；
闸道检查报"没问题"，不代表真的检查过。

本技能让**图自己吐出可检查的网表**：

> **TikZ 在排版时就已经有了精确坐标。让"检查器从图里反推几何"变成
> "检查器读画图时顺手写下的日志"。**

具体做法是 `\Wire{a}{b}` 画线时**先把两端解析成 pt 坐标，再用同一组坐标**
既画线又写侧车文件（`<jobname>.net`）。图与日志共用一次解析，所以
**不可能脱节**——这是结构性保证，不是靠纪律。

一个直接后果值得先知道：**"畸形线段"、"悬空端子"、"飘端子"这三类缺陷在
本方案里写不出来**。端点就是锚点，画线时必然精确落在端子上。

## 文件在哪

| 路径 | 作用 |
|---|---|
| `references/quickref.md` | 宏签名、端子、坐标名、踩坑 —— **写图前读这一页** |
| `references/geometry.md` | **符号几何表**（端子偏移/本体宽高/最小跨度/值标签高度），自动生成 |
| `references/sidecar-format.md` | 侧车格式、连通规则、TeX 侧陷阱 —— **改检查器或 style 时才读** |
| `assets/stm32tikz.sty` | 核心宏包（由 `build.py` 自动加进 `TEXINPUTS`，不用拷贝） |
| `assets/example_photodiode_relay.tex` | 完整示例，可照抄 |
| `scripts/` | `build.py` 一键闭环（`--probe` 可测符号）；`gen_pins.py` 读工程引脚；`gen_geometry.py` 生成几何表；`place.py` 摆标注与说明块；两道门；`selftest_tex.py` 回归 |

---

## 标准流程

### 1. 读 `references/quickref.md`

宏签名、端子命名、自动布局的产出坐标名都在那一页。**不必读
`stm32tikz.sty` 源码**（900+ 行，注释比代码长，那是给改工具的人看的）。

### 2. 引脚从工程读，不要手抄

```bash
python <skill>/scripts/gen_pins.py /path/to/fw --list      # 先看有哪些标签
python <skill>/scripts/gen_pins.py /path/to/fw \
    --tag LED_R=ADC --tag LED_G=Drive                      # 生成 pins.tex
```

**手抄引脚会烧片子**（写错端口/写错电平），而且会悄悄过期。

### 3. 写 `.tex`：坐标一律从锚点来

**最关键的一条规矩**：端子坐标不允许手填。内置器件用 circuitikz 锚点，
自绘器件用 TikZ 命名坐标——都在"几何定义处"算出来，调用点拿不到手填机会。

```latex
\documentclass[border=12pt]{standalone}
\usepackage{ctex}                 % 中文必需(缺了 build 会报「CJK 字形缺失」, 见常见坑)
\usepackage{stm32tikz}
\begin{document}
\begin{tikzpicture}[font=\small]

\InputAnnoOffsets{auto_offsets.tex}   % 标注偏移表(place.py 生成, 不存在则全 0)
\input{pins.tex}

\coordinate (senseTop) at (0,0);
\cVCC{+3.3V}{senseTop}
\cRes{R1}{senseTop}{0,-1.7}{10K}
\LogLabel{R1}                         % 把 10K 的真实排版盒收进侧车

% 接线一律用 \WireH / \WireV（它们声明 H/V 意图, 斜了会被报出来）
\WireV{R1b}{pdK}
\WireH{tap}{adcRow}

% 标注: 位置交给 place.py 自动摆
\AnnoAt{r1name}{ann}{R1.west}{west,south west}{R1}

% 声明期望网络 —— 唯一能抓「接到错的节点」的手段
\Expect{R1.b}{+3.3V}

\DumpCanvas                          % 必须在 \end{tikzpicture} 之前
\end{tikzpicture}
\end{document}
```

选工具的规矩：**串联支路用 `\Chain`，一个节点接 3~4 个元件用 `\Bus`，
反并联用两次 `\Chain` 竖放 + 导线接成并联**（`\Bus` 把元件横排，看不出
反并联那层关系）。同一行/列用 `\RowAt`/`\ColAt`，MCU 引脚用 `\MCUauto`，
普通标注用 `\AnnoAt`，**多行说明块用 `\AnnoPack`**。
签名和产出坐标名见 quickref 的「自动布局」与「标注」两节。

#### ⚠ 两条会白费一整次 build 的错

**① 摆器件的间距要查表，不要按记忆估。**
`references/geometry.md` 里有每个符号的端子偏移、本体宽高、最小跨度。
估出来的数**门禁查不出来**——排版门只查"重叠/穿体/压线"，两个器件挨得太近
但没碰上，它一条都不报，于是"估的 0.55cm"能过门禁，只是刚好没撞上而已。
（实测：`\cPart` 的本体尺寸是常数、不随跨度缩放；但跨度小于本体宽时**本体盒会
越出端点**，所以间距要按**本体宽**算，不是按跨度。）

**② 写 `\Expect` 之前先看网表（build 每次都自动打印）。**
连通性分析里**元件本体是网络边界**：隔着电阻本体两点就是两张网。
凭电路直觉写断言会被报 `[接错]`，而门禁报得对。对照网表写，一次就过。

### 4. 一键闭环（必须跑，不可跳过）

```bash
python <skill>/scripts/build.py circuit --svg
```

它依次做五件事：`xelatex` → `place.py` 自动摆标注 → 再 `xelatex` →
两道门 + 网表摘要 → `pdftocairo` 出 SVG。工作目录就是你运行它的目录；
**产物在构建结束时统一归档进 `circuit/` 子目录**（`.aux/.log/.net/.pdf/.svg`
和中间产物 `auto_offsets.tex`），工程目录只留 `.tex` 图源。换目录用
`--outdir <目录>`；`--outdir .` 恢复"产物留在原地"的旧行为。编译本身仍在
工程目录进行，`\input{pins.tex}` 这类相对引用不受影响。

**两道门都返回 0 才算交付。**

> **`rc=2` 不是"电路有问题"，是"门根本没检查成"**（侧车不存在/为空、
> 参数写错）。两者要分清 —— 见到 2 先去查为什么没跑起来，别看网表。

单独跑某一道（调试时用）：

```bash
python <skill>/scripts/place.py circuit.net --report            # 标注摆位
python <skill>/scripts/check_tex_layout.py circuit.net          # ① 排版门
python <skill>/scripts/check_tex_net.py --show-nets circuit.net # ② 电气门
python <skill>/scripts/selftest_tex.py                          # 回归
```

### 5. 两道门各查什么

**① 排版门**（`check_tex_layout.py`，七类）：
文字重叠 / 文字压线 / 文字压器件本体 / 文字压电源地符号 / 平行线过近 /
器件越界 / 器件重叠。

**② 电气门**（`check_tex_net.py`）：
短路（VCC 与 GND 同网）/ 旁路（元件两端同网）/ 穿体（导线纵穿本体）/
接错（`\Expect` 没兑现）/ 孤标签 / 斜线 / 非正交（H/V 意图没兑现）/
近接（两端点相差 <2pt 却 >0.8pt，看着贴住其实断了）/ 重名（同名端子登记在
两个不同点，会静默覆盖掉一个端子）/ 容器框（MCU 方块）。
**电源/地的识别比旧版宽**：`5V`/`3V3`/`VBUS`/`VIN` 与 `VSS`/`AGND` 也算，
因为短路是最该抓的缺陷，命名约定不该把它挡在门外。
另外会报**记账警告**（零长导线、值标签丢失）与**空侧车**（没检查到东西，
不等于干净）。

报错都给**可照做的改法**（"把该标注上移 ≥21.9pt"这种），因为标注盒是
**真实测量值**而非估算。

### 6. 人眼核对极性 —— 没有工具能替代

**极性/方向任何连通性检查都查不出。** 两根线都接在正确的端子上时，连通性
完美，两道门全报 0。build 每次都会打印网表摘要；网多时手动跑全量，
逐条读方向性器件两端落在哪个网：

```bash
python <skill>/scripts/check_tex_net.py --show-nets circuit.net
```

每个器件的正确方向（LED / 续流管 / 光敏管 / 电解电容 / 三极管 / 开漏）见
**quickref 的「极性」一节**。`\Expect` 能抓"接到错的**节点**"，但抓不住
"同一元件**两端接反**"。

---

## 电气陷阱：画法错误（会让你短路而不自知）

**短路和"元件被旁路"在图上就是一根普通的直线。** 线条笔直、元件规整、
不压字不重叠，排版门给满分，人眼也给满分。**看图发现不了电气错误，
别把"我再瞄一眼"当检查手段。**

1. **竖直元件的接线只从最近的端子起笔。** 电阻本体在两端点连线的中段，
   任何"从本体上方一路画到下方"的竖线都是**穿体**（= 把该元件旁路）。

---

## 常见坑

### 中文必须 `\usepackage{ctex}`

骨架里已带。缺了它有两种表现，**build.py 都会自动识别并报错**，无需预读：
旧 TeX 报 `TeX capacity exceeded`（完全不指向真因）；新 MiKTeX 更阴险——
**编译成功、门禁全绿、但 PDF/SVG 里中文全部没渲染**（中文标注的排版盒成了
空盒，量了等于没量）。看到 build 报「CJK 字形缺失」就补 ctex，别去查图。
（`器件名必须 ASCII`、`零长导线` 两个相关坑见 quickref 的「器件」与「画线」两节。）

### 值标签必须跟 `\LogLabel`

器件写了 `l=`/`v=` 之后必须 `\LogLabel{R1}`，否则值标签的排版盒不进侧车，
**排版门就看不到它**（可能压线也不报）。器件名拼错时侧车会出现
`M|找不到 label 节点` 警告——别忽略它。

---

## ⚠️ 改工具之前必读

**"查了没问题"和"压根没看"在输出上长得一模一样。**

改动检查器或 style 之后，**一定要跑 `selftest_tex.py`**（58 用例，含端到端
断言"TeX 侧记账条数正确"）。它抓的是"检查器本身被改坏"，这是最危险的一类
回归。新增检查时，**先写一个能复现的注错用例并确认它 FAIL**，再去修。

`selftest_tex.py` 把**通过/失败/跳过分开统计**：跳过（本机没 xelatex 时那
几个 TeX 端到端用例）**不算通过**，会以退出码 1 明确告知还差多少没验。
见到 `skip` 就别声称"全绿"。

开发史上四个"门禁报 0 却其实没查"的盲区（以及各自的根因）见
`references/sidecar-format.md`。

---

## 交付前检查清单

- [ ] 引脚来自 `gen_pins.py`（它读工程的 `main.h`/`.ioc`），**没有手抄**
- [ ] 端子坐标全部来自锚点/命名坐标，**没有手填偏移**
- [ ] 接线用 `\WireH`/`\WireV`（不是裸 `\Wire`）
- [ ] 值标签都跟了 `\LogLabel`
- [ ] `build.py` 跑完 **0 Error**（编译）
- [ ] `check_tex_layout.py` 返回 **0**（排版七类全过）
- [ ] `check_tex_net.py` 返回 **0**（电气九类全过）—— 返回 **2** 是"没检查成"，
      别当通过
- [ ] `--show-nets` 人工核对过**方向性器件**两端落在哪个网
- [ ] `selftest_tex.py` **58/58，且跳过 0 个**（如果改过检查器或 style）
- [ ] 渲染出 PNG 用眼睛看一遍**布局**（中文显示成方块是渲染器缺 CJK 字体，
      SVG/PDF 本身正常，不要去"修"字体）
