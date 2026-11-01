# 贡献指南

本项目最缺的不是代码，是**真实机型的实测数据**。

## 最有价值的贡献：一份 probe 报告

```bash
python -m tune probe --save
```

它会生成 `snapshots/probe-*.json`，把机型、ROM 版本、允许禁用比例都记下来了。
PR 到 `docs/实测/` 就行，Markdown 文件一篇。

为什么这个最值钱：debloat 工具满天飞，但**没人系统性地记录「哪些 ROM 到底让不让禁」**。
一条 `(机型, ROM 版本, 允许比例)` 的数据，能帮下一个同机型的人省掉几十次无效尝试和大量困惑。

模板参考 [docs/实测/红米Note13Pro-HyperOS3.md](docs/实测/红米Note13Pro-HyperOS3.md)。

> ⚠️ 提交前请把**序列号脱敏**（写成 `1a43****` 之类），快照文件里也别带能定位到个人的信息。

## 次有价值：补保护名单

`data/keep-critical.txt` 收录「冻了会掉功能」的包。如果你遇到：

- UAD 标成了 Recommended，但冻完之后某个功能坏了；
- 或者某个包冻完导致闪退、功能异常；
- 或者**你自己会用的功能类 App 被冻了**（哪怕它没报错，只是用不了了）；

欢迎提 PR 加进去，**务必在行尾注释里写清具体症状**：

```
com.example.foo          # 冻结后 XX 功能失效（机型 + ROM 版本）
```

> 注意第二类情况和前两类性质不同：前两类是「冻坏了」，后一类是「**UAD 的 Recommended 不等于该冻**」。
> UAD 的分级是社区经验，它把「用不上的冗余组件」和「有实际功能但小众的 App」混在同一档里。
> 本项目站在「宁可少冻十个」的一边，所以凡是用户实际在用的功能类 App，一律进保护名单。

### 一个真实的漏判案例

本项目第一次实机运行时，`com.mi.health`（小米健康）被归进了「建议冻结」批次 ——
因为 UAD 把它标成了 `Recommended`，而它看起来确实像个可有可无的预装组件。

用户跑完才发现：这台手机上小米健康**在用**（记录步数、运动数据），冻掉之后要手动重新启用才行。
于是它被加进了保护名单：

```
com.mi.health                   # 小米健康（运动/步数数据），实测漏判：UAD 标 Recommended，
                                # 但它是有实际功能的工具 App，误冻后用户要用「保留」反悔
```

**教训**：UAD 的 `Recommended` 是「通常可以删」，不是「删了没人会后悔」。
判断依据应该是**这台设备主人实际用不用**，而不是清单上的等级。
如果你也遇到类似情况，欢迎把案例连同症状一起提上来。

不收的原则：

- 不放 `com.xxx厂商.*` 这种宽泛通配（会把厂商名下所有包都保护起来，清单就废了）；
- 不收"听说会出问题"的条目，要**实测症状**。

## 提交前自查（隐私）

**实测报告必然会带设备信息。** 本项目鼓励大家提交自己机型的实测数据，但序列号、
UDID、邮箱、Windows 用户名这些东西一旦进了公开仓库就撤不回来了。

所以仓库里带了一个自查脚本，提交前跑一下：

```bash
python tools/privacy-scan.py --staged      # 提交前：只扫暂存区
python tools/privacy-scan.py --history     # 深度：连 .git 里没提交的残留一起扫
python tools/privacy-scan.py --remote      # 核对 GitHub 线上真实内容
```

有 ERROR 级问题会返回退出码 1，CI 也会因此阻断合并。

### 它能查出什么

| 级别 | 查的东西 |
|---|---|
| **ERROR** | 手机号、身份证号、银行卡号（带 Luhn 校验）、IMEI、iOS UDID、Windows 用户名路径、非 noreply 邮箱、TOTP/2FA 种子密钥 |
| **WARN** | MAC 地址、用户目录、公网 IPv4、高精度经纬度、疑似 TOTP 密钥 |
| **INFO** | 精确到秒的时间戳（会暴露操作时刻） |

### 三个容易被忽略的坑

1. **`.git` 里可能一直躺着没提交过的敏感内容。**
   你 `git add` 过一次又改掉的文件，会以悬空对象的形式留在 `.git` 里。
   `git push` 只推可达对象，所以**它没被上传**——但它一直在你本机。
   `--history` 会把对象库整个翻一遍，并区分「已发布」和「仅本地」。

2. **图片会带元数据。** PNG 的 `tEXt`/`eXIf`、JPEG 的 EXIF 里可能塞着 GPS 坐标和机型。
   提交截图前用 `exiftool -all= file.png` 清一遍。

3. **commit 作者信息是永久的。** 如果 `git config user.email` 没设好，
   Git 会把你的 Windows 用户名写进每一个 commit 且永久公开。
   请用 GitHub noreply 邮箱：`<用户名>@users.noreply.github.com`。

### 把你的设备号加进本地 denylist

规则靠正则只能抓有固定格式的东西，序列号这种抓不住 —— 所以要你自己登记：

```bash
cp tools/privacy-local.json.example .privacy-local.json
# 编辑 .privacy-local.json，填上你的真实序列号 / UDID
python tools/privacy-scan.py --staged
```

`.privacy-local.json` 已被 `.gitignore` 排除，**不会进仓库**。

### 确实需要保留原文时

在 `.privacyignore` 里按 glob 放行（支持 `#` 注释）：

```
docs/实测/*.md
```

放行只是「不报告」，不代表安全，请自己确认那确实是公开信息。

## 代码改动

几条硬约束：

1. **保持零第三方依赖。** 只用 Python 标准库。
   （理由写在 `requirements.txt` 里：adbutils 这条路我们踩过坑，不想让下游再踩）
2. **所有要交给 adb 的文件必须 LF 落盘**，用 `debloat.write_text()`，别直接 `open(w)` ——
   Windows 的默认行为会写 CRLF，包名尾巴带 `\r` 后会全批报 Unknown package。
   仓库已用 `.gitattributes` 强制 `eol=lf`，别绕过它。
3. **调 adb 的 subprocess 必须 `stdin=subprocess.DEVNULL`**，否则在 shell 循环里会吃掉循环的输入。
4. **任何新增的修改类命令，必须同时写快照。**
5. 中文注释优先 —— 这个项目的受众主要是中文用户。

## 不接受的 PR

- 任何形式的 `pm uninstall` / root / 刷机相关；
- 绕过 `data/keep-critical.txt` 保护名单的开关；
- README 里被明确列入「不做的事」（关 MIUI 优化、强制 GPU 渲染等）的实现。

这些不是技术洁癖，是踩出来的边界。

## License

MIT。
