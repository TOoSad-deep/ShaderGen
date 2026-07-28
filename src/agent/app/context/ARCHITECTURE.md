# Context 架构

> 状态：休眠兼容模块。当前产品链路不调用；只有恢复 Memory/context 的明确任务才读取本文。

`context/` 负责把已校验 Memory 数据确定性整理为固定 schema 的 `ContextPack`。它不访问 Store、不调用模型、不加载 Prompt；历史数据必须标记为数据而非指令，超预算内容确定性丢弃。
