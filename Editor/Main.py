"""统一启动剧情制作工具链。"""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent

try:  # pragma: no cover - depends on the optional GUI dependency
    from PySide6.QtCore import Qt, QProcess
    from PySide6.QtWidgets import (
        QApplication,
        QLabel,
        QMainWindow,
        QMessageBox,
        QPushButton,
        QVBoxLayout,
        QWidget,
    )

    QT_AVAILABLE = True
except ImportError:  # pragma: no cover
    QT_AVAILABLE = False


TOOLS = (
    ("角色生成器", "CharacterCreator/Main.py", "创建角色缩写、名称和 ANSI 样式"),
    ("TSCPS 剧情编写器", "TSGenerator/Main.py", "编写无逐字时间的 .tscps 文件"),
    ("TSCP 转换器", "Ts2Tp/Main.py", "为对白逐字设计播放时间并生成 .tscp"),
    ("TSCP 文件编辑器", "TSCPEditor/Main.py", "修改已经生成的 .tscp 文件"),
    ("剧情素材管理器", "PlotManager/Main.py", "把音乐与 .tscp 装进单文件剧情包"),
    ("剧情播放器", "Main.py", "选择并播放 source 中的剧情"),
)


if QT_AVAILABLE:

    class EditorWindow(QMainWindow):
        def __init__(self) -> None:
            super().__init__()
            self.setWindowTitle("剧情制作工具箱")
            self.resize(620, 560)
            self._processes: list[QProcess] = []
            self._build_ui()

        def _build_ui(self) -> None:
            central = QWidget(self)
            layout = QVBoxLayout(central)
            layout.setContentsMargins(48, 36, 48, 36)
            layout.setSpacing(12)

            title = QLabel("剧情制作工具箱")
            title.setAlignment(Qt.AlignmentFlag.AlignCenter)
            title.setStyleSheet("font-size: 28px; font-weight: bold;")
            subtitle = QLabel("从角色配置到剧情播放的统一入口")
            subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
            subtitle.setStyleSheet("color: #777; margin-bottom: 18px;")
            layout.addWidget(title)
            layout.addWidget(subtitle)

            for label, relative_path, description in TOOLS:
                button = QPushButton(label)
                button.setMinimumHeight(42)
                button.setToolTip(description)
                button.clicked.connect(
                    lambda checked=False, path=relative_path, name=label: self._launch(path, name)
                )
                layout.addWidget(button)

            layout.addStretch(1)
            self.status = QLabel("请选择要使用的工具")
            self.status.setStyleSheet("color: #777;")
            layout.addWidget(self.status)
            self.setCentralWidget(central)

        def _launch(self, relative_path: str, name: str) -> None:
            script = ROOT / relative_path
            if not script.is_file():
                QMessageBox.critical(self, "无法启动工具", "找不到文件：%s" % script)
                return
            process = QProcess(self)
            process.setProgram(sys.executable)
            process.setArguments([str(script)])
            process.setWorkingDirectory(str(ROOT))
            process.errorOccurred.connect(
                lambda error, tool=name: self._process_error(tool, error)
            )
            process.finished.connect(
                lambda code, status, tool=name: self.status.setText(
                    "%s 已关闭（退出码 %d）" % (tool, code)
                )
            )
            self._processes.append(process)
            process.start()
            if not process.waitForStarted(3000):
                self._process_error(name, process.error())
                return
            self.status.setText("已启动：%s" % name)

        def _process_error(self, name: str, error) -> None:
            self.status.setText("%s 启动失败" % name)
            QMessageBox.warning(
                self,
                "工具启动失败",
                "%s 无法启动，请确认 .venv 中已安装 PySide6。" % name,
            )

        def closeEvent(self, event) -> None:
            for process in self._processes:
                if process.state() != QProcess.ProcessState.NotRunning:
                    process.terminate()
            event.accept()


def main() -> int:
    if not QT_AVAILABLE:
        print("Editor 需要 PySide6，请在 .venv 中执行 pip install PySide6")
        return 1
    app = QApplication(sys.argv)
    window = EditorWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
