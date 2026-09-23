# TSGenerator 原稿编辑器

TSGenerator 用来编辑播放器使用的 UTF-8 `.tscps` 源文件。它不保存逐字
间隔；需要逐字时间时，请使用播放器项目中的编译工具。

安装可选 GUI 依赖并启动：

```powershell
python -m pip install ".[gui]"
python TSGenerator/Main.py
python TSGenerator/Main.py source/Scripts/plot.tscps
```

编辑器支持新建、打开、保存和另存为，以及角色对白、旁白、休息、清空屏幕
和播放音乐事件。打开位于剧情包 `Scripts` 目录的原稿时，会从
`Scripts/__init__.json` 的 `CHARACTERS` 载入角色缩写和样式，并补充当前
原稿中尚未配置的缩写。保存前会检查角色缩写、指令、休息时长、音乐简称以及
单行文本。工具栏提供“连续预览”和“单页预览”；预览使用黑底等宽字体，单页
模式按 `<c>` 逐页推进。原稿没有逐字间隔时，连续预览默认使用可调的短间隔。
没有安装 PySide6 时，`TSGenerator.model` 和源格式解析器仍可在脚本或测试中使用。
