# AGENTS.md

给 AI 智能体 / AI IDE 看的操作规范。

如果你是被人指着这个项目说「帮我优化这台手机」而读到这里的，**请先完整读完再动手**。
本文件的规则不是为了走形式，每一条背后都是一次真实的翻车。

---

## 0. 先理解这个项目的立场

绝大多数 debloat 教程的默认假设是「`pm disable-user` 一定能行」。
这个假设在不少现代国产 ROM 上是**错的** —— 实测某台 HyperOS 3 上 55 个候选包只放行 7 个（87% 被拒）。

所以本项目的第一原则是：

> **不要一上来就冻结。先探测，让设备自己告诉你那条路走不走得通。**

顺序是硬性的：`detect → probe → (按 probe 结论分流) → 改动 → 收尾`。

## 1. 硬性红线（违反即视为事故）

1. **绝不使用 `pm uninstall`**，任何形式都不行。只用 `pm disable-user`。
   卸载在国行 ROM 上大概率同样被拒，偶尔成功也极难恢复。
2. **绝不 root、不刷机、不清 boot 分区、不改分区表。**
3. **绝不绕过 `data/keep-critical.txt` 保护名单。** 即便用户强烈要求冻结某个被保护的包，
   也必须先向用户说明清楚后果（白纸黑字），得到明确二次确认后才执行，并在最后单独汇报。
4. **绝不猜包名。** 包名必须来自 `plan` 的输出或设备上真实存在的列表。
5. **不替用户点「同意」任何协议/隐私弹窗。** 遇到时需要用户自己在手机上点。
6. **不自动关闭「系统更新」。**
7. **批量冻结前必须让用户看到清单。** 用 `plan --export` 导出，或直接把包名列给用户，得到确认才 `freeze`。

## 2. 标准流程

### Step 1 · 连通性

```bash
python -m tune devices
python -m tune detect
```

设备必须处于 `device` 状态才能继续。`unauthorized` = 手机没点允许；`offline` = 插拔重试。

**如果 `detect` 失败或后续 UI 类操作被系统拒绝**，多半是没开「USB 调试（安全设置）」——
它和「USB 调试」是两个独立开关。已确认开但依然被拒时，**让用户重启手机**（这是已知最有效解法）。

### Step 2 · 可行性探测（必须做）

```bash
python -m tune probe --save
```

按结果分流：

| probe 结论 | 允许禁用比例 | 该做什么 |
|---|---|---|
| debloat 路线可行 | ≥ 60% | 正常走 Step 4 `plan` / `freeze` |
| debloat 部分可行 | 20% ~ 60% | 可以做 `freeze`，但要提前告知用户会有大量 `Cannot disable system packages` 报错，且这不是故障 |
| debloat 基本不可行 | < 20% | **不要浪费时间尝试冻结。** 告知用户真实原因，推荐改走 uiautomator2 路线（android-ad-cleaner），并转向 `smooth` 做流畅调优 |

第三档时最重要的一件事：**原样如实转述**。不要粉饰成"部分成功"，
用户有权知道这台机器限制了什么。

### Step 3 · 流畅调优（低风险，推荐都做）

```bash
python -m tune smooth --scale 0.5 --bg-limit 3
```

- 动画缩放 `0.5` 是安全值；`0` 为完全关闭动画，要先问用户。
- `--bg-limit` 建议 3~4。**不要设 1**，会让微信/邮件收不到消息。
- 「内存扩展」用 `python -m tune memory` 探测，**结果给用户看，引导手动关闭**，不要直接改 settings。

### Step 4 · 生成清单并人工过目（不可跳）

```bash
python -m tune plan --export 清单.md
```

把清单给用户看。这里有个重要判断：

- 默认清单不含 UAD 的 `Advanced` 级（那批里有音效、相册编辑器等**真有功能的**东西）。
- 但默认清单里仍可能混着计算器、便签、天气这类「UAD 认为可冻、用户可能要用」的项 ——
  **必须由用户划掉想保留的**，你无权替他决定。

### Step 5 · 执行冻结

```bash
python -m tune freeze --dry-run   # 先看
python -m tune freeze
```

**如何汇报结果**：按四类分别报，不要合并成一个笼统的「失败 N 个」：
冻结成功 N / 被 ROM 锁死 N（这是设备限制，不是工具故障）/ 未安装 N / 未知返回 N。

锁死比例 > 50% 时，明确建议改路线。

### Step 6 · 收尾

```bash
python -m tune finish
```

这会还原「插电防锁屏」。是否关开发者模式**必须问用户**（`--dev-off`），
关了之后 adb 会断开，属于预期行为，要提前说明。

## 3. 向用户提问的正确时机

以下情况停下来问，不要自己决定：

- probe 结论落在「部分可行」区间，问他还要不要继续冻结；
- 清单里有明显具备日常功能的 App（计算器、天气、便签等），让他在 Option 里划；
- 任何需要 `include-advanced` 的场景；
- 要不要关开发者模式；
- 用户设备是多台在线，选哪台。

## 4. 失败处理

| 现象 | 真因 | 处理 |
|---|---|---|
| `cannot execute binary` / exit 127 且无输出 | adb 缺 `AdbWinApi.dll` / `AdbWinUsbApi.dll` | 装官方 platform-tools，见 docs/01 |
| 包名全报 `Unknown package` | 清单文件是 **CRLF** 行尾，包名尾巴带 `\r` | 用本项目 `write_text()` 落盘（强制 LF），或 `tr -d '\r'` |
| 循环里 adb 只读了一行就停 | adb 吞掉了循环的 stdin | 循环里加 `stdin=subprocess.DEVNULL` 或 `< /dev/null` |
| `Cannot disable system packages` | ROM 把包锁成系统核心 | 不是故障，照 probe 结论换路线 |
| `unauthorized` | 手机未授权 | 手机解锁态点允许，必要时重启 |

完整踩坑清单见 [docs/06-踩坑实录.md](docs/06-踩坑实录.md)。

## 5. 命令速查

```bash
python -m tune devices
python -m tune detect
python -m tune probe [-n 6] [--save] [--no-uad]
python -m tune smooth --scale 0.5 --bg-limit 3
python -m tune memory
python -m tune plan [--include-advanced] [--export FILE]
python -m tune freeze [--file FILE] [--dry-run] [-y]
python -m tune unfreeze --package com.xxx.yyy
python -m tune snapshot
python -m tune restore <快照文件>
python -m tune finish [--dev-off]
```

## 6. 贡献实测数据

跑完 `probe --save` 后，鼓励用户把 `snapshots/probe-*.json` 连同机型提交 PR 到 `docs/实测/`。
这类**真实机型 × ROM × 允许禁用比例**的数据，是本项目最有价值的资产，比再多代码都重要。
