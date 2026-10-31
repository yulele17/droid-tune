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

欢迎提 PR 加进去，**务必在行尾注释里写清具体症状**：

```
com.example.foo          # 冻结后 XX 功能失效（机型 + ROM 版本）
```

不收的原则：

- 不放 `com.xxx厂商.*` 这种宽泛通配（会把厂商名下所有包都保护起来，清单就废了）；
- 不收"听说会出问题"的条目，要**实测症状**。

## 代码改动

几条硬约束：

1. **保持零第三方依赖。** 只用 Python 标准库。
   （理由写在 `requirements.txt` 里：adbutils 这条路我们踩过坑，不想让下游再踩）
2. **所有要交给 adb 的文件必须 LF 落盘**，用 `debloat.write_text()`，别直接 `open(w)` ——
   Windows 的默认行为会写 CRLF，包名尾巴带 `\r` 后会全批报 Unknown package。
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
