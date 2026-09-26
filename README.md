# tikz-circuit-diagram

用 TikZ/circuitikz 画电路图，**让图在排版时自己吐出网表**，再由 Python 检查。
产出矢量 PDF/SVG，中文正常。

> 画电路图最难的地方不在**画**，在**验证**：图看着连了，不代表电气上连了；
> 检查报"没问题"，不代表真的检查过。

---

## 核心思路

`\Wire{a}{b}` 画线时**先把两端解析成 pt 坐标，然后用同一组坐标既画线、又写
一个侧车文件**（`<jobname>.net`）：

```latex
\WireV{R1b}{pdK}
%  → 画一条竖线
%  → 同时往 .net 里写: WV|0.0pt|-48.4pt|0.0pt|-79.7pt
```

图与日志共用**一次**坐标解析，所以**不可能脱节**——这是结构性保证，不是靠纪律。
检查器只读这个 `.net`，不去渲染产物里反推几何。

一个直接后果：**"畸形线段"、"悬空端子"、"飘端子"这三类缺陷写不出来**。
端点就是锚点，画线时必然精确落在端子上。

## 两道门

```bash
python scripts/check_tex_layout.py circuit.net   # ① 排版门：七类
python scripts/check_tex_net.py    circuit.net   # ② 电气门：七类 + 记账警告
```

**① 排版门**：文字重叠 / 文字压线 / 文字压器件本体 / 文字压电源地符号 /
平行线过近 / 器件越界 / 器件重叠。

**② 电气门**：短路（VCC 与 GND 同网）/ 旁路（元件两端同网）/ 穿体（导线纵穿
本体）/ 接错（`\Expect` 没兑现）/ 孤标签 / 斜线 / 非正交。

报错都给**可照做的改法**（"把该标注上移 ≥21.9pt"这种），因为标注盒是
**真实测量值**而非估算。

**两道门都返回 0 才算交付。** 但——

### ⚠ 两道门查不出方向

**极性/方向任何连通性检查都查不出。** LED 阳极接反、续流管方向反、电解电容
正负反：两根线都接在正确的端子上，连通性完美，两道门全报 0。

必须用 `--show-nets` 打印网表，逐个核对方向性器件两端落在哪个网。

---

## 安装

本目录就是一个 skill（`SKILL.md` + `references/` + `scripts/` + `assets/`）。

```bash
# 放到你的 agent 技能目录（按你用的工具选一个）
cp -r tikz-circuit-diagram ~/.zcode/skills/          # 或
cp -r tikz-circuit-diagram <项目>/.agents/skills/
```

**依赖**：`xelatex`（带 `ctex` 的 MiKTeX / TeX Live / MacTeX）、`pdftocairo`
（poppler，只在要 SVG 时需要）、Python 3 标准库。
脚本不依赖任何第三方 Python 包。

## 用法

```bash
# 引脚从工程读，不要手抄
python scripts/gen_pins.py /path/to/fw --tag LED_R=ADC --tag LED_G=Drive

# 一键闭环：编译 → 自动摆标注 → 再编译 → 两道门 → SVG
python scripts/build.py circuit --svg

# 单独跑某一道
python scripts/place.py circuit.net --report              # 标注自动摆位
python scripts/check_tex_net.py --show-nets circuit.net   # 电气门 + 网表
python scripts/selftest_tex.py                            # 回归 58 用例
```

完整的工作流、宏速查、电气陷阱见 **[SKILL.md](SKILL.md)**（写图前读
**[references/quickref.md](references/quickref.md)**）。

## 三件自动布局工具

不用手算坐标，也不用靠"估"：

```latex
% 串联支路：起点 + 步进向量 + 元件表；间距不用算
\Chain{LEDS}{ledStart}{1.6cm,0}{R3/R/1K, LED1/leD/{}}

% 标注：偏移由 place.py 解算，写进 auto_offsets.tex 第二遍生效
\AnnoAt{r1name}{ann}{R1.west}{west,south west}{R1}

% 多行说明块：给一个列顶，按真实高度顺序堆叠
\AnnoPack{note1}{ann, align=left, text width=8cm}{noteCol}{...}
```

`place.py` 的摆位是**两遍编译**：第一遍偏移全 0，侧车记下每个标注的锚点与
文字盒；`place.py` 解算出不压线/不压字/不压器件/不压符号的位置；第二遍偏移
生效。偏移是**显式数据**，所以每遍确定、可复现（连续三次构建结果相同）。

## 目录

```
SKILL.md                     工作流 + 踩坑 + 交付检查清单
references/
  quickref.md                接口速查（宏签名/端子/坐标名）—— 写图前读这一页
  geometry.md                符号几何表（端子偏移/本体宽高/最小跨度），自动生成
  sidecar-format.md          侧车格式、连通规则、TeX 侧陷阱
scripts/
  build.py                   一键闭环（--probe 可测符号几何）
  place.py                   标注偏移解算器
  gen_pins.py                从 STM32 工程读引脚 → pins.tex
  gen_geometry.py            生成 references/geometry.md
  read_project.py            读 main.h / .ioc / hal_conf.h
  sidecar.py                 侧车解析 + 连通性并查集
  check_tex_layout.py        排版门
  check_tex_net.py           电气门
  selftest_tex.py            回归测试（58 用例）
assets/
  stm32tikz.sty              核心宏包
  example_photodiode_relay.tex  完整示例，可照抄
```

## 一个值得记下来的失效模式

开发过程中新增的每一项检查能力**没有一次是靠调阈值发现的**，都是"换个角度
看图"发现的。共同点是：**根因不是阈值不对，而是"被检查的对象压根不在检查的
范围内"**。

所以——**"查了没问题"和"压根没看"在输出上长得一模一样。**
改动检查器之后一定要跑 `selftest_tex.py`；新增检查时先写一个能复现的注错
用例并确认它 **FAIL**，再去修。

## 许可

MIT，见 [LICENSE](LICENSE)。
