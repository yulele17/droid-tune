"""droid-tune —— 不 root 的国产安卓流畅化调优工具。

命令总览（建议按这个顺序跑）：
    devices     列出已连接设备
    detect      看设备与 ROM 信息（决定后面策略能不能套用）
    probe       ★ 可行性探测：这台 ROM 到底让不让禁用包（动手前必跑）
    smooth      流畅调优：动画缩放 / 后台限制
    memory      探测"内存扩展"开关（只探测，不自动改）
    plan        生成冻结候选清单（纯只读）
    freeze      执行冻结（可还原）
    unfreeze    解冻指定包
    snapshot    列出/查看快照
    restore     按快照还原
    finish      收尾：还原防锁屏，必要时关开发者模式
"""

__version__ = "0.1.0"
