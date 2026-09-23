"""Interactive entry menu."""

from pathlib import Path

from .format import parse_tscp
from .plot import PlotPackageError, discover_plot
from .renderer import TerminalRenderer
from .timing import compile_file


class InteractiveMenu:
    def __init__(self, input_fn=input, output_fn=print) -> None:
        self.input = input_fn
        self.output = output_fn

    def run(self) -> None:
        while True:
            self.output("\n剧情播放器\n1) 播放 .tscp\n2) 将 .tscps 转换为 .tscp\n3) 退出")
            choice = self.input("> ").strip()
            if choice == "1":
                self.play()
            elif choice == "2":
                self.convert()
            elif choice in {"3", "q", "quit"}:
                return

    def play(self) -> None:
        package_path = self.input("剧情文件夹或 .tscpkg: ").strip()
        script_name = self.input("要播放的 .tscp 文件名: ").strip()
        try:
            package = discover_plot(package_path)
            renderer = TerminalRenderer(package.characters, music_files=package.music,
                                        music_resolver=package.music_path)
            renderer.render(package.load_script(script_name))
        except (PlotPackageError, OSError, ValueError, RuntimeError) as exc:
            self.output("播放失败: " + str(exc))

    def convert(self) -> None:
        source = self.input("输入 .tscps 文件: ").strip()
        target = self.input("输出 .tscp 文件: ").strip()
        try:
            compile_file(source, target, self.input, self.output)
            self.output("已生成: " + str(Path(target)))
        except (OSError, ValueError) as exc:
            self.output("转换失败: " + str(exc))
