# 实测：Redmi Note 13 Pro（HyperOS 3）

> 欢迎提 PR 补充你的机型。`python -m tune probe --save` 生成的报告可以直接贴到本目录。

## 设备信息

| 项 | 值 |
|---|---|
| 市场名 | Redmi Note 13 Pro |
| 内部代号 | 2312DRA50C |
| 序列号 | `1a43****`（脱敏处理） |
| 安卓版本 | Android 16（`BP2A.250605.031.A3`） |
| ROM | HyperOS 3，版本号 `OS3.0.306.0.WNRCNXM` |
| 已安装包数 | 475 |

## ★ 可行性结论

| 指标 | 值 |
|---|---|
| 候选包总数 | 55 |
| **允许禁用** | **7** |
| **系统拒绝** | **48** |
| **允许比例** | **约 13%** |
| 结论 | **debloat 路线基本不可行** |

拒绝信息统一为：

```
Exception occurred while executing 'disable-user':
java.lang.SecurityException: Cannot disable system packages
```

这台国行 HyperOS 3 把小米自家绝大部分预装/遥测/overlay 包标成了系统核心包，
`pm disable-user` 对它们不产生任何作用。

## 冻结成功的 7 个（后经机主确认，4 个已解冻）

| 包名 | 说明 | 现状 |
|---|---|---|
| `com.mi.health` | 小米健康 | **已解冻**（机主实际在用） |
| `com.miui.cleanmaster` | 清理大师 | **已解冻**（机主实际在用） |
| `com.xiaomi.mibrain.speech` | 小爱语音引擎相关 | **已解冻**（机主实际在用） |
| `com.xiaomi.shop` | 小米商城 | **已解冻**（机主实际在用） |
| `com.miui.newhome` | 新版桌面（未启用则无害） | 保持冻结 |
| `com.miui.newmidrive` | 小米云盘新版 | 保持冻结 |
| `com.xiaomi.vipaccount` | 小米会员 / VIP 账号 | 保持冻结 |

> ⚠️ 这是一次真实的**漏判**：UAD 把这四个都标成 `Recommended`，
> 它们看起来像可有可无的预装组件，但这台机的机主实际在用。
> 现已全部补进 `data/keep-critical.txt`，详见 [CONTRIBUTING.md](../../CONTRIBUTING.md) 里的「一个真实的漏判案例」。

## 被拒绝的代表包（部分）

`com.miui.analytics`（遥测核心）、`com.miui.daemon`、`com.miui.uireporter`、
`com.miui.yellowpage`、`com.miui.wmsvc`、`com.miui.msa.global`、
以及**全部** `com.miui.*.overlay` 系列。

也就是说，UAD 清单里最想处理的那批，在这台机器上恰好一个都动不了。

## 安全拦截

执行前主动剔除了三个功能关键包，避免误伤：

| 包名 | 不冻的原因 |
|---|---|
| `com.xiaomi.bluetooth` | 冻结后蓝牙可能直接不可用 |
| `com.xiaomi.trustservice` | 支付/登录场景可能异常 |
| `com.xiaomi.simactivate.service` | SIM 卡可能识别不出 |

另外 `com.miui.cloudbackup` 也被列入了保护名单 —— UAD 把它标成 Recommended，
但冻了会丢掉云备份能力，属于典型的「清单分级与本地实际不符」。

**事后补录**：`com.mi.health`（小米健康）最初也在冻结名单里，跑完机主反馈要保留，
已解冻并补进 `data/keep-critical.txt`。这说明"清单分级"和"这台机主用不用"是两件事 ——
前者只是社区经验，后者才是判据。

## 流畅调优部分

| 项 | 原值 | 改后 | 备注 |
|---|---|---|---|
| 三个动画缩放 | `1.0` | `0.5` | 生效，跟手感提升明显 |
| `background_process_limit` | `null`（未设置） | `3` | 生效 |
| 内存扩展 | 读到 `miui_memory_size=256 GB` | 未改动 | 数值格式异常，**不敢自动改**，给手动路径 |

## 这台机的正确处理姿势

1. **不要浪费时间在 debloat 上**（锁死率 87%），先 `probe` 确认再动手；
2. 流畅调优（`smooth` + 手动关内存扩展）**完全有效**，这是本机主要收益来源；
3. 如果目标是**去系统广告/热搜/营销推送**，走 uiautomator2 路线：
   [android-ad-cleaner](https://github.com/rddmwmddr/android-ad-cleaner)
   —— 它不碰 `pm disable`，因此不受上面这个限制。

## 备注

- 系统 OTA 后冻结状态可能被重置，需重跑。
- 「USB 调试（安全设置）」在这台机上打开后一度不生效，**重启手机**后才正常。
- 完整操作日志与还原方式见项目根目录下各自机的 `snapshots/`。
