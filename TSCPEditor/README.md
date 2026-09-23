# TSCPEditor 编译剧情编辑器

TSCPEditor 是一个小型 `.tscp` 编辑器，只处理已经编译的剧情：打开、保存，
并最小化编辑对白、指令和逐字延迟。它与播放器使用相同的解析器和保存器，
因此会保留所有事件及逐字延迟。

```powershell
python -m pip install ".[gui]"
python TSCPEditor/Main.py
# 或直接打开文件：
python TSCPEditor/Main.py source/Scripts/plot.tscp
```

角色缩写可以直接在对白编辑框中输入。逐字延迟可以留空以使用默认值，也可以输入 JSON 数组，例如
`[0.05, 0.1]`，或输入逗号分隔的数值。延迟数量必须与文本字符数量一致。

未安装 PySide6 时，`TSCPEditor.model` 及其他模块仍可用于脚本和测试。
旧的 `PlotWriter` 包仍作为兼容导入别名保留。
