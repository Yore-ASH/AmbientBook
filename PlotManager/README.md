# PlotManager 剧情素材管理器

PlotManager 把一次剧情收进**一个文件**，并让「加音乐」「加剧本」变成两次点击。

```powershell
python -m pip install ".[gui]"
python PlotManager/Main.py
python PlotManager/Main.py source/dist/ExamplePlot.tscpkg
```

启动整套工具的图形入口是 `Editor/Main.py`（剧情制作工具箱）。

## 它做什么

| 按钮 | 作用 |
| --- | --- |
| 新建剧情包 | 输入名称与简介，生成一个带完整元数据的空 `.tscpkg` |
| 打开 .tscpkg | 载入已有剧情包，列出音乐与剧本 |
| 目录打包成 .tscpkg | 把旧的 `Musics` + `Scripts` 目录剧情迁移成单文件 |
| 应用信息 | 修改剧情名称与简介 |
| 插入音乐 | 选一个或多个音频，逐个确认 `<p>` 简称；音频嵌入包内，`Musics/__init__.json` 的 `CONFIG` 同步更新 |
| 删除选中 | 解除简称；不再被任何简称引用的音频会一并删除 |
| 导入 .tscp | 校验编译格式后放进 `Scripts/` |
| 原稿编译导入 | 读取 `.tscps`，以 0 秒逐字延迟编译成 `.tscp` 再放进 `Scripts/` |
| 删除选中 | 从包里移除剧本 |

音乐表会标出「已嵌入 / 缺少文件」，所以 `CONFIG` 里指向不存在文件的简称一眼就能
看出来。剧本表显示事件行数、可见字数和用到的角色缩写。

`PlotManager.model` 不依赖 PySide6，可以直接在脚本和测试里用：

```python
from PlotManager import model

model.create_package("demo.tscpkg", name="Demo", description="示例")
model.add_music("demo.tscpkg", [("i want.flac", "iw")])
model.add_script("demo.tscpkg", "plot.tscp")
info = model.inspect("demo.tscpkg")
```

## 打包性能

* 文本成员 deflate，音频成员 **store** —— FLAC/OGG/MP3 已经压缩过，再 deflate
  只是白烧 CPU。
* 一次操作只重写一次容器：新增音频与改写 `Musics/__init__.json` 合并成同一遍，
  其余成员按 1 MB 分块流式拷贝，不会把整段音频读进内存。
* 实测 99 MB FLAC：打包约 13 秒，生成的容器 94.6 MB；载入元数据 6 毫秒。
* 播放时音频按需解压到 `%LOCALAPPDATA%\tscpkg-cache`，以大小和 mtime 判断是否
  复用：首次 0.22 秒，之后 2 毫秒。

容器就是普通 ZIP，也可以用 7-Zip 等工具直接查看和拖放文件。
