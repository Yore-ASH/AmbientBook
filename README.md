# TSCP 剧情播放器

这是一套用来编写、计时和播放文字剧情的工具链：把 `.tscps` 原稿编译成带逐字
时间的 `.tscp`，装进剧情包，再用黑底窗口逐字播放并配乐。

## 剧情包

一次剧情由三份元数据和若干剧本、音乐组成，可以有两种存放方式。

**目录形式**（方便工具直接编辑）：

```text
source/ExamplePlot/
  __init__.json          NAME / DESCRIPTION / VERSION
  Musics/
    __init__.json        VERSION 与 CONFIG（简称 -> 文件名）
    i want.flac
  Scripts/
    __init__.json        CHARACTERS 与 DEPENDECE
    Among_of_them.tscp   编译后的剧本
    Among_of_them.tscps  原稿（可选）
```

**单文件形式** `.tscpkg`：把上面整个目录打成一个 ZIP 容器。音乐以**不压缩**
方式存入（FLAC/OGG/MP3 本身已压缩），所以任何解压工具都能打开它，直接把新音乐
拖进去也有效。播放器、`tscp-player` 和素材管理器两种形式都支持。

推荐的工作流：

```text
source/
  ExamplePlot/            工作目录：用 TSGenerator / Ts2Tp 直接编辑
  dist/
    ExamplePlot.tscpkg    打包产物：交付只需要这一个文件
```

`Scripts/__init__.json` 使用 `CHARACTERS` 和 `DEPENDECE` 字段。

## 安装和运行

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m tscp_player
```

日常使用可以直接运行根目录的 `Main.py`：

```powershell
python Main.py
```

它默认读取 `Main.py` 同目录下的 `source` 文件夹，**递归**列出其中找到的所有
`.tscpkg` 容器和剧情目录（所以 `source/dist/` 里的打包产物也会被找到），每个
剧情再列出自己的 `.tscp`。只有一个剧情时直接显示剧本列表，有多个时先选剧情，
同名剧情会附带相对路径以便区分；鼠标悬停能看到完整路径。

某个包损坏或读不出来时**只会被跳过**，界面下方提示「已跳过 N 个」，不会阻止
其它剧情播放。也可以直接指定要播放的目标：

```powershell
python Main.py source\dist\ExamplePlot.tscpkg
python Main.py source\ExamplePlot
```

播放前会显示 `__init__.json` 中的 `NAME`、`DESCRIPTION` 和完整路径。

菜单中的“播放”会读取角色样式、校验版本并播放 `Scripts` 下的 `.tscp`。
音乐控制使用音乐配置中的简称；PyGame 无法加载文件时会报告错误。

## `.tscps` 原稿格式

```text
<c>
<p>utb
[f]你好，欢迎来到这里。
<s>1.5
这是旁白。
```

`<s>` 后接秒数，`<c>` 清屏，`<p>` 后接音乐简称。没有 `[缩写]` 前缀的行是旁白。

### 音乐语义

播放器和计时工具共用同一条规则：**只有切换到不同曲目时才重新加载音乐**。
重新声明当前正在播放的曲目不会重启它，而是从当前位置续播，所以每一句都从
上一句结束的地方继续。换曲从头开始，`<p>stop` 停止播放。

每一行的音乐状态（简称 + 该曲目内的偏移秒）由 `tscp_player.music` 的
`MusicTimeline` 计算，偏移量按前面的句子时长与 `<s>` 等待累加。

> PyGame 只支持 OGG/MP3 随机跳转，FLAC 无法 seek，因此续播是顺序的。

## `.tscp` 编译格式

编译器生成 UTF-8 文本，首行为 `TSCP 1`。每个事件占一行：

* `D|缩写|base64文本|秒延迟列表`：角色对白；
* `N|base64文本|延迟列表`：旁白；
* `S|秒数`、`C`、`P|音乐简称`：控制事件。

文本使用 Base64 是为了让制表符、竖线和 Unicode 不会破坏解析。菜单的转换器会逐字显示文本，用户每按一次 Enter 记录下一个字的时间间隔。

## 工具

* `Ts2Tp` 将无间隔的 `.tscps` 原稿转换为带逐字时间的 `.tscp`，支持逐句和连续设计。
  第一次按计时键才是计时起点；「只录选中句」可以只重录一句话而保留其余句的
  时间；`<p>`/`<s>` 在计时期间会真的执行，表格里的「音乐」列显示每一句开始播
  时音乐所处的位置。
* `PlotManager` 是剧情素材管理器：新建 `.tscpkg`、把旧目录剧情一键打包、插入
  音乐（自动写入 `CONFIG` 简称）、导入 `.tscp` 或把 `.tscps` 编译后导入、删除
  条目、修改剧情名称与简介。
* `TSCPEditor` 只编辑已生成的 `.tscp`（打开、保存、对白和指令）；旧的
  `PlotWriter` 导入路径保留为兼容别名。

启动整套工具的图形入口是 `Editor/Main.py`（剧情制作工具箱）。

## `.tscpkg` 容器

```powershell
python PlotManager/Main.py
python PlotManager/Main.py source/dist/ExamplePlot.tscpkg
```

内部结构就是剧情包的目录结构，另外在 `__init__.json` 里写入
`"FORMAT": "tscpkg 1"`。实现要点：

* 文本成员用 deflate，音频成员用 store，因此打包 99 MB 的 FLAC 不会浪费 CPU；
* 任何改动（新增音乐 + 改写 `Musics/__init__.json`）都在**一次重写**里完成，
  其余成员流式拷贝，不会把整段音频读进内存；
* 播放时音频按需解压到缓存目录（`%LOCALAPPDATA%\tscpkg-cache`），用文件大小和
  修改时间判断是否复用，因此第二次播放不再解压。

`source/dist/` 属于构建产物，已在 `.gitignore` 中排除，随时可以在素材管理器里
用「目录打包成 .tscpkg」重新生成。

## 直接修改 `.tscpkg` 内容

不必先把文件解压出来：

* 素材管理器里选中剧本 →「编辑内容」，直接在包内改编译后的 `.tscp` 文本；
  确定时会校验格式，非法内容不会写回。
* 「导出选中」把剧本复制到磁盘，用 Ts2Tp / TSCPEditor 改完再「导入 .tscp」
  覆盖回去。
* Ts2Tp 现在既能打开 `.tscps` 原稿，也能打开 `.tscp` 编译稿（靠后缀或
  `TSCP 1` 头判断）。打开编译稿会保留已有逐字时间，所以可以只重录一句话。

## 许可证

[Apache License 2.0](LICENSE)

Copyright 2026 Yore-ASH

## 反馈

问题与建议请发到 **ash_server@163.com**，或在仓库里开 Issue。


