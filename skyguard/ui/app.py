"""Application entry point, paths and Qt appearance."""
import os
import sys
from pathlib import Path

from PySide6.QtCore import QLibraryInfo, QRectF, Qt, QTimer, QTranslator
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QSplashScreen

from .. import __version__
from ..theme import apply_theme, colors, install_font, load_theme, themed_font


def asset_path():
    """Return the bundled application asset directory."""
    return Path(__file__).resolve().parents[1] / 'assets'


def data_path():
    """Return the configured or default data directory."""
    override = os.environ.get('SKYGUARD_DATA_DIR')
    if override:
        return Path(override)
    return Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'SkyguardDesktop'


def install_style(app, root=None):
    """Install the shared font, palette, Qt translations and stylesheet."""
    if not hasattr(app, 'skyguard_translator'):
        assets = asset_path()
        install_font(assets)
        app.setStyle('Fusion')
        app.setFont(themed_font(13.5))
        app.setWindowIcon(QIcon(str(assets / 'skyguard.ico')))
        app.skyguard_translator = QTranslator(app)
        if app.skyguard_translator.load(
            'qtbase_zh_CN', QLibraryInfo.path(QLibraryInfo.TranslationsPath)
        ):
            app.installTranslator(app.skyguard_translator)
    name = load_theme(root if root is not None else data_path())
    if getattr(app, 'skyguard_theme', None) != name:
        apply_theme(app, name)


def _splash():
    palette = colors()
    canvas = QPixmap(620, 350)
    canvas.fill(QColor(palette['ground']))
    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(palette['surface']))
    painter.drawRoundedRect(QRectF(26, 26, 568, 298), 24, 24)
    mark = QPixmap(str(asset_path() / 'skyguard.png'))
    painter.drawPixmap(58, 64, mark.scaled(100, 100, Qt.KeepAspectRatio, Qt.SmoothTransformation))
    painter.setPen(QColor(palette['ink']))
    painter.setFont(themed_font(38, QFont.Bold))
    painter.drawText(184, 112, '低空守望')
    painter.setPen(QColor(palette['muted']))
    painter.setFont(themed_font(15))
    painter.drawText(187, 148, '航拍出行与态势演练')
    painter.setPen(QColor(palette['accent']))
    painter.setFont(themed_font(16, QFont.Medium))
    painter.drawText(60, 220, f'v{__version__}')
    painter.setPen(QColor(palette['muted']))
    painter.end()
    return QSplashScreen(canvas)


def main():
    """Start the desktop workspace with the original splash screen."""
    from .shell import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName('SkyguardDesktop')
    app.setApplicationVersion(__version__)
    app.setOrganizationName('Skyguard')
    install_style(app)
    splash = _splash()
    splash.show()
    splash.showMessage('正在准备工作台…', Qt.AlignBottom | Qt.AlignHCenter, QColor(colors()['muted']))
    app.processEvents()
    window = MainWindow()

    def show():
        window.show()
        splash.finish(window)

    QTimer.singleShot(900, show)
    return app.exec()
