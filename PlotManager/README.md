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
| 新建剧情包 | **先选文件位置**，再输入名称（默认取文件名）与简介 |
| 打开 .tscpkg | 载入已有剧情包，列出音乐与剧本 |
| **导出副本 .tscpkg** | 把当前包（含已插入的音乐和剧本）导出成另一个 `.tscpkg`；导出后仍继续编辑原来那个包 |
| **文件夹导出为 .tscpkg** | 把带 `Musics` + `Scripts` 的整个文件夹打包成一个 `.tscpkg`，音乐与 `Scripts/*.tscp`、`*.tscps` 都会装进去 |
| 应用信息 | 修改剧情名称与简介 |
| 插入音乐 | 选一个或多个音频，逐个确认 `<p>` 简称；**每个音频都会先问你这是纯音乐还是带歌词**；音频嵌入包内，`Musics/__init__.json` 同步更新 |
| 设置歌词 | 修改选中曲目的类型、歌词来源与显示颜色 |
| 删除选中 | 解除简称；不再被任何简称引用的音频和歌词会一并删除 |
| 导入 .tscp | 校验编译格式后放进 `Scripts/` |
| 原稿编译导入 | 读取 `.tscps`，以 0 秒逐字延迟编译成 `.tscp` 再放进 `Scripts/` |
| 编辑内容 / 导出选中 | 直接在包内改剧本，或导出到磁盘改完再导入回来 |
| 删除选中 | 从包里移除剧本 |

音乐表有六列：`简称 / 类型 / 文件 / 大小 / 歌词 / 颜色`，一眼能看出哪些是纯音乐、
哪些带歌词、歌词文件在不在，颜色格子也按实际颜色显示。

## 歌词

插入音乐时回答「不是纯音乐」后，接着问歌词从哪来：

* **现成的 `.lrc` 文件** —— 直接嵌进包里（会统一成 LF 换行，和构建平台无关）。
* **`lyrics.txt` + 现场录制** —— 选一个纯文本歌词文件，弹出录制窗口：点「开始录制」
  后音乐开始播放，**每按一次 Enter / Space 记录当前行的出现时间**；进度、已录行数
  和当前时间实时显示，全部录完自动停止。时间列**可以直接双击手改**（支持 `12.34`
  或 `0:12.34`），改完点确定即以 LRC 存进包里。

两条路最后都写成标准 LRC，所以歌词文件随时可以拿到外面用文本编辑器改。

歌词的显示颜色在插入时用取色器选择（默认白色），之后可以用「设置歌词」再改。

## 为什么没有「保存」按钮

**编辑剧情包时没有单独的保存步骤**：`插入音乐`、`导入 .tscp`、`编辑内容`、
`应用信息` 都是**立即写进当前 `.tscpkg` 文件**的（音频嵌入、歌词嵌入与
`Musics/__init__.json` 改动在同一次重写里完成）。所以「把已添加的音乐和剧本导出成
`.tscpkg`」有两种情况：

* 你本来就是用「新建剧情包」开的 `.tscpkg` —— 那些内容**已经在文件里**了，
  想另外产出一份就用「导出副本 .tscpkg」。
* 你把音乐和剧本放在**文件夹**里（`Musics/` + `Scripts/`）—— 用
  「文件夹导出为 .tscpkg」，它会把整个文件夹装成一个单文件。

剧本表显示事件行数、可见字数和用到的角色缩写。

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
