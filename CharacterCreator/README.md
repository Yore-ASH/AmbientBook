# CharacterCreator 角色配置生成器

这是一个可选的 PySide6 中文小工具，用来生成可以直接粘贴到
`Scripts/__init__.json` 的 `CHARACTERS` 对象中的单个条目。它支持：

* 角色缩写和全名；
* 常用 ANSI 前景色、粗体和下划线组合；
* 自定义 SGR ANSI 输入（支持 `\033`、`\x1b` 或实际 ESC，不支持斜体）；
* 样式预览、JSON 生成和复制到剪贴板。
* 逐个填写角色并暂存到列表，最后一次生成完整的 `CHARACTERS` 对象。

从仓库根目录运行：

```powershell
python -m pip install ".[gui]"
python CharacterCreator/Main.py
```

生成的文本不含外层 `CHARACTERS` 大括号和末尾逗号。粘贴到已有最后一项
后，请按需要添加逗号；工具生成的内容本身是有效的 JSON 对象成员片段。
`CharacterCreator.model` 不依赖 PySide6，可单独用于脚本和测试。

点击“批量生成”后，逐个填写角色并点击“加入列表”；可以删除选中的临时角色。
全部填写完成后点击“统一生成”，生成结果包含完整的 `{}`，可直接替换或复制到
`CHARACTERS` 字段中。
