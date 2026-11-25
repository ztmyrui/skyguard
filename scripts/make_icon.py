from pathlib import Path
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QImage,QPainter
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtCore import Qt

app=QApplication([])
root=Path(__file__).resolve().parents[1]/'skyguard'/'assets'
image=QImage(256,256,QImage.Format_ARGB32_Premultiplied);image.fill(Qt.transparent)
painter=QPainter(image);painter.setRenderHint(QPainter.Antialiasing)
QSvgRenderer(str(root/'skyguard.svg')).render(painter);painter.end()
assert image.save(str(root/'skyguard.png'))
assert image.save(str(root/'skyguard.ico'))
print('Saved Soft UI application icon')
