"""Desktop appearance, shared tokens and theme-aware outline icons."""
import json
import os
from functools import lru_cache
from pathlib import Path
from tempfile import NamedTemporaryFile
from weakref import WeakSet

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QIcon, QPalette, QPixmap
from PySide6.QtSvg import QSvgRenderer
from shiboken6 import isValid

DEFAULT_THEME = 'klein'
THEME_NAMES = {'night': '跑道夜航（深色）', 'klein': '克莱因蓝（浅色）'}
THEMES = {
    'night': {
        'ground': '#0F1420', 'surface': '#171E2C', 'ink': '#E7EBF3',
        'muted': '#8C96AA', 'pri': '#F2A93B', 'on_pri': '#1A1206',
        'pri_glow': '#F2A93B55', 'tonal': '#232C3E', 'tonal_ink': '#C9D3E6',
        'accent': '#F2A93B', 'sel': '#1F2A40', 'sel_line': '#4F7BD9',
        'ev': '#1B2333', 'seg_bg': '#0F1420', 'seg_on': '#2A3550',
        'seg_on_ink': '#F2F4F8', 'field': '#0F1420', 'nav': '#121826',
        'nav_ink': '#7F8AA0', 'nav_seg': '#0B0F18', 'nav_on': '#232C3E',
        'nav_on_ink': '#F2A93B', 'glass': '#171E2CD9', 'dis_bg': '#1D2433',
        'dis_ink': '#566074', 'ok': '#5ED39A', 'ok_bg': '#16301F',
        'warn': '#F2A93B', 'warn_bg': '#33270F', 'danger': '#FF6F86',
        'danger_bg': '#3A1A24', 'info': '#7EA6FF', 'info_bg': '#1A2742',
        'demo': '#B79CFF', 'demo_bg': '#2A2340', 'm_land': '#141B28',
        'dispatch': '#65D1D8', 'dispatch_bg': '#142F37', 'guide': '#C1D981', 'guide_bg': '#2B341C',
        'm_water': '#0F2438', 'm_road': '#26304A', 'm_road2': '#1C2436',
        'm_zone': '#FF6F8630', 'm_zl': '#FF6F86', 'm_pred': '#7EA6FF',
        'm_dot': '#F2A93B',
    },
    'klein': {
        'ground': '#F4F4F1', 'surface': '#FFFFFF', 'ink': '#141414',
        'muted': '#6E6E6A', 'pri': '#1F3FD6', 'on_pri': '#FFFFFF',
        'pri_glow': '#1F3FD640', 'tonal': '#E8ECFD', 'tonal_ink': '#1F3FD6',
        'accent': '#1F3FD6', 'sel': '#EDF0FE', 'sel_line': '#1F3FD6',
        'ev': '#F7F7F5', 'seg_bg': '#EDEDE9', 'seg_on': '#141414',
        'seg_on_ink': '#FFFFFF', 'field': '#F2F2EF', 'nav': '#141414',
        'nav_ink': '#9A9A95', 'nav_seg': '#262626', 'nav_on': '#1F3FD6',
        'nav_on_ink': '#FFFFFF', 'glass': '#FFFFFFD9', 'dis_bg': '#EFEFEC',
        'dis_ink': '#ABABA6', 'ok': '#0E8A5F', 'ok_bg': '#E1F4EC',
        'warn': '#B76B00', 'warn_bg': '#FFF1DC', 'danger': '#D1344F',
        'danger_bg': '#FDE7EB', 'info': '#1F3FD6', 'info_bg': '#E8ECFD',
        'demo': '#7B4FD8', 'demo_bg': '#F0EAFD', 'm_land': '#F4F4F2',
        'dispatch': '#086773', 'dispatch_bg': '#DCF2F3', 'guide': '#426827', 'guide_bg': '#E9F2DD',
        'm_water': '#CCD6F2', 'm_road': '#FFFFFF', 'm_road2': '#E8E8E4',
        'm_zone': '#D1344F2A', 'm_zl': '#D1344F', 'm_pred': '#1F3FD6',
        'm_dot': '#1F3FD6',
    },
}

FONT_FAMILIES = ('DengXian', 'Noto Sans SC')
NUMBER_FAMILIES = ('Segoe UI Variable', 'Bahnschrift', 'Segoe UI', *FONT_FAMILIES)
ASSETS = Path(__file__).resolve().parent / 'assets'
SETTINGS_FILE = 'ui-settings.json'
_active_theme = DEFAULT_THEME
_icon_widgets = WeakSet()


def colors():
    return THEMES[_active_theme]


def _tones(palette):
    tones = {name: (palette[name], palette[f'{name}_bg'])
             for name in ('ok', 'warn', 'danger', 'info', 'demo', 'dispatch', 'guide')}
    tones['unknown'] = tones['info']
    return tones


TONES = _tones(colors())


def load_theme(root):
    """Restore the local appearance preference, including older installs."""
    try:
        name = json.loads((Path(root) / SETTINGS_FILE).read_text(encoding='utf8')).get('theme')
    except (OSError, ValueError, AttributeError):
        return DEFAULT_THEME
    return name if isinstance(name, str) and name in THEMES else DEFAULT_THEME


def save_theme(root, name):
    """Replace the preference atomically within the local data directory."""
    if name not in THEMES:
        raise ValueError(name)
    path = Path(root) / SETTINGS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = NamedTemporaryFile(mode='w', encoding='utf8', dir=path.parent,
                                  prefix='ui-settings-', suffix='.tmp', delete=False)
    draft = Path(temporary.name)
    try:
        with temporary:
            json.dump({'theme': name}, temporary)
            temporary.write('\n')
        draft.replace(path)
    finally:
        draft.unlink(missing_ok=True)


def install_font(assets):
    QFontDatabase.addApplicationFont(str(assets / 'NotoSansSC.ttf'))
    # Offscreen Qt or restricted Windows sessions can omit installed families.
    if windows := os.environ.get('WINDIR'):
        installed = set(QFontDatabase.families())
        fonts = Path(windows) / 'Fonts'
        for family, filenames in {
            'DengXian': ('Deng.ttf', 'Dengb.ttf', 'Dengl.ttf'),
            'Segoe UI Variable': ('SegUIVar.ttf',),
            'Bahnschrift': ('bahnschrift.ttf',),
            'Segoe UI': ('segoeui.ttf', 'segoeuib.ttf', 'segoeuisl.ttf'),
        }.items():
            if family not in installed:
                for filename in filenames:
                    path = fonts / filename
                    if path.is_file():
                        QFontDatabase.addApplicationFont(str(path))


def themed_font(size=None, weight=QFont.Normal, *, numeric=False):
    font = QFont()
    font.setFamilies(NUMBER_FAMILIES if numeric else FONT_FAMILIES)
    if size is not None:
        font.setPointSizeF(size * 0.75)
    font.setWeight(weight)
    return font


PATHS = {
 'dot':'<circle cx="12" cy="12" r="6" fill="currentColor"/>',
 'user':'<circle cx="12" cy="8" r="3.5"/><path d="M5 20v-2a4 4 0 0 1 4-4h6a4 4 0 0 1 4 4v2"/>',
 'tasks':'<rect x="5" y="3" width="14" height="18" rx="2"/><path d="m8 8 1 1 2-2m2 1h3M8 13h8M8 17h8"/>',
 'map':'<path d="m3 5 6-2 6 2 6-2v16l-6 2-6-2-6 2Z"/><path d="M9 3v16M15 5v16"/>',
 'plan':'<rect x="4" y="3" width="16" height="18" rx="3"/><path d="m8 8 1 1 2-2m3 1h3M8 13h9M8 17h6"/>',
 'settings':'<path d="M4 7h16M4 17h16"/><circle cx="9" cy="7" r="3" fill="{bg}"/><circle cx="15" cy="17" r="3" fill="{bg}"/>',
 'help':'<circle cx="12" cy="12" r="9"/><path d="M9.5 9a2.5 2.5 0 1 1 4 2c-1 .7-1.5 1-1.5 2.5M12 17h.01"/>',
 'more':'<circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/>',
 'plus':'<path d="M12 5v14M5 12h14"/>',
 'import':'<path d="M4 14v5a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-5M12 3v12m-4-4 4 4 4-4"/>',
 'export':'<path d="M4 14v5a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-5M12 15V3m-4 4 4-4 4 4"/>',
 'play':'<path d="m8 4 12 8-12 8Z"/>',
 'pause':'<path d="M8 5v14M16 5v14"/>',
 'home':'<circle cx="12" cy="12" r="7"/><circle cx="12" cy="12" r="2"/><path d="M12 2v3m0 14v3M2 12h3m14 0h3"/>',
 'link':'<path d="m9 15 6-6m-7 3-2 2a4 4 0 0 0 6 6l2-2m-4-12 2-2a4 4 0 0 1 6 6l-2 2"/>',
 'check':'<path d="m5 12 4 4L19 6"/>',
 'spark':'<path d="m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5Z"/>',
 'close':'<path d="m6 6 12 12M6 18 18 6"/>',
 'folder':'<path d="M3 7a2 2 0 0 1 2-2h5l2 2h7a2 2 0 0 1 2 2v10H3Z"/>',
 'layers':'<path d="m12 3 10 5-10 5L2 8Zm-9 9 9 5 9-5M3 16l9 5 9-5"/>',
 'condition-ok':'<circle cx="12" cy="12" r="9"/><path d="m8 12 2.5 2.5L16 9"/>',
 'condition-attention':'<path d="M10.4 4.8 3.5 17a1.6 1.6 0 0 0 1.4 2.4h14.2a1.6 1.6 0 0 0 1.4-2.4L13.6 4.8a1.8 1.8 0 0 0-3.2 0ZM12 9v4m0 3h.01"/>',
 'condition-permit':'<rect x="6" y="3" width="12" height="18" rx="2"/><path d="M9 8h6m-6 4h6m-6 4h3"/>',
 'condition-restricted':'<circle cx="12" cy="12" r="9"/><path d="m5.6 5.6 12.8 12.8"/>',
 'condition-pending':'<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
 'chevron-down':'<path d="m6 9 6 6 6-6"/>',
 'chevron-up':'<path d="m6 15 6-6 6 6"/>',
 'chevron-right':'<path d="m9 6 6 6-6 6"/>',
 'range-guide':'<path d="m2 9 6-2 8 2 6-2v12l-6 2-8-2-6 2Zm6-2v12m8-10v12"/><path d="M16 6c0 2.3-4 6-4 6S8 8.3 8 6a4 4 0 1 1 8 0Z" fill="{bg}"/><path d="M12 6h.01"/>',
 'sun':'<circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M2 12h2m16 0h2M4.93 4.93l1.42 1.42m11.3 11.3 1.42 1.42m0-14.14-1.42 1.42m-11.3 11.3-1.42 1.42"/>',
 'moon':'<path d="M20.7 15.1A9 9 0 0 1 8.9 3.3 9 9 0 1 0 20.7 15.1Z"/>',
}

@lru_cache(maxsize=192)
def _icon(name, color, background):
    path = PATHS[name].replace('{bg}', background).replace('currentColor', color)
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" '
           f'fill="none" stroke="{color}" stroke-width="1.7" stroke-linecap="round" '
           f'stroke-linejoin="round">{path}</svg>')
    return QIcon(QPixmap.fromImage(_render(svg)))


def _render(svg):
    from PySide6.QtGui import QImage, QPainter

    result = QImage(72, 72, QImage.Format_ARGB32_Premultiplied)
    result.fill(Qt.transparent)
    painter = QPainter(result)
    QSvgRenderer(QByteArray(svg.encode())).render(painter)
    painter.end()
    result.setDevicePixelRatio(3)
    return result


def set_themed_icon(widget, glyph, role='tonal_ink', size=None, background='surface'):
    """Bind an outline icon to theme roles; a size denotes a label pixmap."""
    widget._theme_icon = (glyph, role, size, background)
    _icon_widgets.add(widget)
    _refresh_icon(widget)


def _refresh_icon(widget):
    glyph, role, size, background = widget._theme_icon
    palette = colors()
    rendered = _icon(glyph, TONES['unknown'][0] if role == 'unknown' else palette[role],
                     palette[background])
    if size is None:
        widget.setIcon(rendered)
    else:
        widget.setPixmap(rendered.pixmap(size, size))


def apply_theme(app, name):
    """Apply both native palette roles and widget-specific styles in place."""
    global _active_theme
    theme = THEMES[name]
    _active_theme = name
    TONES.update(_tones(theme))
    palette = QPalette()
    roles = {
        QPalette.Window: 'ground', QPalette.WindowText: 'ink',
        QPalette.Base: 'surface', QPalette.AlternateBase: 'ev',
        QPalette.Text: 'ink', QPalette.Button: 'tonal',
        QPalette.ButtonText: 'tonal_ink', QPalette.Highlight: 'sel',
        QPalette.HighlightedText: 'ink', QPalette.PlaceholderText: 'muted',
        QPalette.ToolTipBase: 'surface', QPalette.ToolTipText: 'ink',
        QPalette.Link: 'info', QPalette.LinkVisited: 'demo', QPalette.Mid: 'tonal',
        QPalette.BrightText: 'danger',
    }
    for role, token in roles.items():
        palette.setColor(role, QColor(theme[token]))
    for role in (QPalette.Text, QPalette.WindowText, QPalette.ButtonText):
        palette.setColor(QPalette.Disabled, role, QColor(theme['dis_ink']))
    palette.setColor(QPalette.Disabled, QPalette.Button, QColor(theme['dis_bg']))
    app.setPalette(palette)
    app.setStyleSheet(stylesheet(name))
    for widget in list(_icon_widgets):
        if isValid(widget):
            _refresh_icon(widget)
    app.skyguard_theme = name


def stylesheet(theme_name=DEFAULT_THEME):
    """Generate QSS from the selected theme without changing the active palette."""
    theme = THEMES[theme_name]
    border = '#222B3D' if theme_name == 'night' else 'transparent'
    icon_paths = {glyph: (ASSETS / f'{glyph}-{theme_name}.svg').as_posix()
                  for glyph in ('check', 'chevron-down', 'chevron-up')}
    qss = '''
    QWidget {font-size:13.5px;color:@ink@;selection-background-color:@sel@;selection-color:@ink@;}
    QMainWindow, QWidget#workspace {background:@ground@;}
    QWidget#rail {background:@nav@;border-radius:18px;}
    QWidget#card {background:@surface@;border:1px solid @border@;border-radius:14px;}
    QWidget#topBar, QWidget#timeline {background:@surface@;border:1px solid @border@;border-radius:14px;}
    QLabel#roleBadge {background:@nav_seg@;color:@nav_on_ink@;border-radius:15px;font-size:13px;font-weight:600;}
    QWidget#segmentedControl {background:@seg_bg@;border-radius:16px;}
    QWidget#segmentedControl QToolButton {padding:5px 8px;border-radius:13px;font-size:13px;}
    QWidget#segmentedControl QToolButton:hover {background:@info_bg@;}
    QWidget#segmentedControl QToolButton:checked {background:@seg_on@;color:@seg_on_ink@;}
    QWidget#segmentedControl QToolButton:focus {border:2px solid @pri@;padding:3px 6px;}
    QWidget#planningConditions QToolButton {font-size:12px;padding:5px 4px;}
    QWidget#planningConditions QToolButton:focus {padding:3px 2px;}
    QWidget#mapSurface[compact="true"] QComboBox {padding:4px 25px 4px 8px;}
    QWidget#mapSurface[compact="true"] QPushButton[quiet="true"] {padding:2px 8px;}
    QFrame#planningSteps {background:@surface@;border:1px solid @border@;border-radius:10px;}
    QFrame#planningStepDivider {background:@tonal@;}
    QFrame#planningStatus {border-radius:8px;}
    QFrame#planningStatus[tone="warn"] {background:@warn_bg@;}
    QFrame#planningStatus[tone="ok"] {background:@ok_bg@;}
    QFrame#planningStatus[tone="info"] {background:@info_bg@;}
    QPushButton#candidateHeader {padding:2px 4px;}
    QPushButton#candidateHeader:checked {background:@sel@;border:1px solid @sel_line@;font-weight:600;}
    QPushButton#candidateHeader:focus {border:2px solid @pri@;}
    QLabel#preferredCandidate {background:@ok_bg@;color:@ok@;border-radius:4px;
                              padding:2px 3px;font-size:11px;}
    QLabel#comparisonMetric {font-size:12px;color:@muted@;padding:2px 4px 2px 0;}
    QLabel#comparisonValue {background:@field@;border-radius:3px;font-size:12px;padding:2px;}
    QLabel#comparisonValue[chosen="true"] {background:@sel@;}
    QLabel#comparisonValue[best="true"] {font-weight:700;}
    QLabel#badge {padding:3px 9px;border-radius:10px;font-size:11.5px;font-weight:600;}
    QLabel#kpiValue {font-size:26px;font-weight:600;}
    QPushButton#kpiButton, QPushButton#eventCard {background:@surface@;border:1px solid @border@;border-radius:14px;}
    QPushButton#kpiButton {min-height:54px;padding:0;}
    QPushButton#eventCard {background:@ev@;border-radius:10px;padding:0;}
    QPushButton#kpiButton:hover, QPushButton#eventCard:hover {background:@ev@;border-color:@sel_line@;}
    QPushButton#kpiButton:checked {background:@sel@;border-color:@accent@;}
    QPushButton#eventCard:checked {background:@sel@;border-color:@sel_line@;}
    QPushButton#kpiButton:focus, QPushButton#eventCard:focus {border:2px solid @pri@;}
    QLabel#eventCardTitle {font-size:13.5px;font-weight:700;}
    QLabel#incidentTitle {font-size:17px;font-weight:600;}
    QWidget#incidentDetail, QWidget#assessmentSection, QWidget#incidentForm {
        background:@surface@;border:1px solid @border@;border-radius:14px;
    }
    QToolButton#assessmentToggle {color:@ink@;font-size:15px;font-weight:600;border-radius:6px;padding:2px 4px;}
    QToolButton#assessmentToggle:checked {background:transparent;}
    QToolButton#assessmentToggle:hover {background:@ground@;}
    QToolButton#assessmentToggle:focus {border:2px solid @pri@;}
    QLabel#assessmentValue {font-size:12px;}
    QLabel#incidentError {color:@danger@;font-size:12px;}
    QWidget#incidentForm QPlainTextEdit {
        background:@field@;border:1px solid transparent;border-radius:10px;padding:4px;
    }
    QWidget#incidentForm QPlainTextEdit:focus {border:2px solid @pri@;}
    QPushButton#attachmentLink {color:@info@;}
    QWidget#mapLegend {background:@ev@;border-radius:9px;}
    QLabel#mapLegendText {font-size:12px;color:@ink@;}
    QLabel#infoTip {font-size:13px;color:@info@;padding:0 1px;}
    QLabel#emptyPrompt {font-size:15px;font-weight:600;}
    QLabel#emptyIllustration {background:@info_bg@;border-radius:16px;padding:12px;}
    QLabel#stepNumber {background:@info_bg@;color:@pri@;border-radius:14px;font-weight:600;}
    QLabel#formWarning {color:@warn@;font-size:12px;}
    QLabel#checkTitle {font-size:13px;font-weight:600;}
    QLabel#evidenceLink {font-size:12px;}
    QLabel#noticeExcerpt {font-size:12px;color:@muted@;}
    QLabel#noticeTag {font-size:12px;color:@info@;background:@info_bg@;border-radius:6px;padding:4px 8px;}
    QPushButton#disclosure {background:transparent;border:1px solid transparent;border-radius:10px;padding:0;}
    QPushButton#disclosure:hover {background:@ev@;}
    QPushButton#disclosure:focus {border:2px solid @pri@;}
    QProgressBar {background:@info_bg@;border:0;border-radius:2px;}
    QProgressBar::chunk {background:@pri@;border-radius:2px;}
    QProgressBar[scoreTone="ok"]::chunk {background:@ok@;}
    QProgressBar[scoreTone="warn"]::chunk {background:@warn@;}
    QProgressBar[scoreTone="danger"]::chunk {background:@danger@;}
    QWidget#card[planSelected="true"] {background:@sel@;border:1px solid @sel_line@;border-radius:14px;}
    QWidget#card[planSelected="false"]:hover {border:1px solid @sel_line@;border-radius:14px;}
    QWidget#timeline QPushButton {padding:4px 8px;}
    QWidget#timeline QComboBox {padding:4px 22px 4px 8px;}
    QWidget#mapSurface {background:@surface@;border:1px solid @border@;border-radius:14px;}
    QLabel {background:transparent;}
    QLabel#pageTitle {font-size:20px;font-weight:700;color:@ink@;}
    QLabel#section {font-size:14px;font-weight:700;}
    QLabel#inlineError {font-size:12px;color:@danger@;}
    QLabel#caption {font-size:12px;color:@muted@;}
    QLabel#clock {font-size:22px;font-weight:600;color:@accent@;}
    QLabel#mapnote {padding:4px;color:@muted@;font-size:11px;}
    QPushButton {background:@tonal@;color:@tonal_ink@;border:1px solid transparent;border-radius:17px;padding:7px 12px;min-height:18px;font-size:13px;font-weight:600;}
    QPushButton:hover {background:@sel@;}
    QPushButton:pressed {background:@seg_bg@;border-color:@sel_line@;}
    QPushButton[primary="true"] {background:@pri@;color:@on_pri@;border-color:@pri@;font-weight:600;}
    QPushButton[primary="true"]:hover {background:@pri_hover@;border-color:@pri_hover@;}
    QPushButton[primary="true"]:pressed {background:@pri_pressed@;border-color:@pri_pressed@;}
    QPushButton[quiet="true"] {color:@tonal_ink@;border-color:transparent;background:transparent;}
    QPushButton[quiet="true"]:hover {background:@sel@;}
    QPushButton#sandboxTab:checked {background:@sel@;color:@tonal_ink@;font-weight:700;}
    QTableWidget#sandboxComparison::item {padding:2px 4px;}
    
    QToolButton {border:0;background:transparent;border-radius:16px;padding:8px 4px;color:@muted@;font-size:12px;}
    QToolButton:hover {background:@ground@;}
    QToolButton:checked {background:@sel@;color:@ok@;font-weight:600;}
    QToolButton:pressed {background:@sel@;}
    QPushButton:focus,QToolButton:focus,QComboBox:focus,QLineEdit:focus,QAbstractSpinBox:focus {border:2px solid @pri@;}
    QToolButton[navigation="true"] {border-radius:12px;color:@nav_ink@;}
    QToolButton[navigation="true"]:hover {color:@nav_on_ink@;background:@nav_seg@;}
    QToolButton[navigation="true"]:checked {color:@nav_on_ink@;background:@nav_on@;}
    QToolButton[navigation="true"]:focus {border:2px solid @nav_ink@;padding:6px 2px;}
    QComboBox,QLineEdit,QAbstractSpinBox,QTextEdit,QPlainTextEdit {background:@field@;border:1px solid transparent;border-radius:10px;padding:8px;min-height:18px;}
    QTextEdit#inlineFeedback,QTextEdit#draftBody {background:@field@;border:1px solid transparent;border-radius:10px;padding:6px;}
    QTextEdit:focus,QPlainTextEdit:focus {border-color:@pri@;}
    QLineEdit,QComboBox,QAbstractSpinBox {placeholder-text-color:@muted@;}
    QComboBox:hover,QLineEdit:hover,QAbstractSpinBox:hover {border-color:@sel_line@;}
    QComboBox {padding-right:24px;combobox-popup:0;}
    QSpinBox,QDoubleSpinBox {padding-right:22px;}
    QAbstractSpinBox QLineEdit {background:transparent;border:0;padding:0;}
    QComboBox::drop-down {border:0;width:22px;}
    QComboBox::down-arrow {image:url("@chevron-down@");width:11px;height:11px;}
    QSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {
        border:0;background:transparent;width:20px;subcontrol-origin:padding;
    }
    QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {image:url("@chevron-up@");width:10px;height:10px;}
    QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {image:url("@chevron-down@");width:10px;height:10px;}
    QDateTimeEdit {padding-right:24px;}
    QDateTimeEdit::drop-down {border:0;width:22px;}
    QDateTimeEdit::down-arrow {image:url("@chevron-down@");width:11px;height:11px;}
    QToolButton::menu-indicator {image:none;}
    QScrollArea,QScrollArea > QWidget,QScrollArea > QWidget > QWidget {background:transparent;border:0;}
    QComboBoxPrivateContainer {background:@surface@;border:0;margin:0;padding:0;}
    QComboBox QAbstractItemView {background:@surface@;border:1px solid @sel_line@;border-radius:0px;padding:4px;outline:0;selection-background-color:@sel@;}
    QComboBox QAbstractItemView:focus {outline:none;}
    QComboBox QAbstractItemView::item {padding:6px 10px;border:0;border-radius:6px;min-height:24px;}
    QComboBox QAbstractItemView::item:hover, QComboBox QAbstractItemView::item:selected {background:@sel@;color:@ink@;border:0;outline:none;}
    QPushButton[sandboxTool="true"]:checked {background:@pri@;color:@on_pri@;border-color:@pri@;}
    QPushButton[sandboxMultiline="true"] {text-align:left;padding:4px 9px;min-height:36px;}
    QPushButton#sandboxPlaceResult {text-align:left;padding:2px 8px;min-height:18px;}
    QWidget#sandboxToolBar {background:@info_bg@;border-radius:9px;}
    QWidget#sandboxToolBar QLabel {color:@info@;}
    QPushButton#actionFlowRow {text-align:left;padding:0;}
    QPushButton[staffTone="info"]:!checked {background:@info_bg@;color:@info@;}
    QPushButton[staffTone="dispatch"]:!checked {background:@dispatch_bg@;color:@dispatch@;}
    QPushButton[staffTone="demo"]:!checked {background:@demo_bg@;color:@demo@;}
    QPushButton[staffTone="ok"]:!checked {background:@ok_bg@;color:@ok@;}
    QAbstractItemView:focus {outline:2px solid @pri@;}
    QListWidget,QTextBrowser,QTableWidget {background:transparent;border:0;selection-background-color:@sel@;selection-color:@ink@;}
    QTextBrowser {padding:4px;}
    QPushButton#taskCard {background:@surface@;border:1px solid @border@;border-radius:14px;padding:0;}
    QPushButton#taskCard:hover {border-color:@sel_line@;background:@field@;}
    QPushButton#taskCard:checked {background:@sel@;border-color:@sel_line@;}
    QPushButton#taskCard:focus {border:2px solid @pri@;}
    QPushButton[taskFilter="true"] {text-align:left;padding-left:12px;}
    QPushButton[taskFilter="true"]:checked {background:@sel@;color:@ok@;}
    QPushButton[civicMode="true"]:checked {background:@pri@;color:@on_pri@;}
    QPushButton[taskResolved="true"] {background:@ok_bg@;color:@ok@;text-align:left;}
    QPushButton[settingsLink="true"] {color:@info@;}
    QWidget#taskOutcome QToolButton {padding-left:4px;padding-right:4px;}
    QListWidget#planList::item {background:@ev@;border:0;border-radius:14px;margin:4px;padding:8px 12px;}
    QListWidget#planList::item:selected {background:@sel@;color:@ok@;}
    QListWidget#photoList::item {border-radius:8px;padding:7px 8px;margin:2px 0}
    QListWidget#photoList::item:selected {background:@sel@;color:@ink@;}
    QHeaderView::section {background:@ev@;border:0;border-bottom:1px solid @tonal@;padding:8px;color:@muted@;font-weight:500;}
    QTableWidget {alternate-background-color:@ev@;gridline-color:@tonal@;}
    QTableWidget::item {padding:8px;border:0;}
    QTableWidget::item:selected {background:@sel@;color:@ink@;}
    QTableWidget#sandboxComparison::item {padding:1px 4px;}
    QTableWidget#sandboxComparison QHeaderView::section {padding:4px;}
    QLabel#sandboxCompareCell[tone="ok"] {background:@ok_bg@;color:@ink@;}
    QLabel#sandboxCompareCell[tone="info"] {background:@info_bg@;color:@ink@;}
    QLabel#sandboxCompareCell[score="true"] {font-weight:700;}
    QTabWidget::pane {background:@surface@;border:0;border-radius:14px;}
    QTabBar::tab {background:transparent;color:@muted@;padding:8px 12px;margin-right:4px;border:0;border-radius:11px;}
    QTabBar::tab:selected {background:@seg_on@;color:@seg_on_ink@;font-weight:600;}
    QTabBar::tab:hover {background:@ev@;}
    QCheckBox {spacing:8px;padding:4px 0;}
    QCheckBox::indicator {width:17px;height:17px;border:1px solid @muted@;border-radius:5px;background:@surface@;}
    QCheckBox::indicator:checked {background:@pri@;border-color:@pri@;image:url("@check@");}
    QCheckBox:focus {background:@sel@;border-radius:6px;}
    QSplitter {background:@ground@;}
    QSplitter::handle {background:@ground@;}
    QSplitter::handle:hover {background:@border@;}
    QStatusBar {background:@ground@;color:@muted@;font-size:11px;}
    QStatusBar::item {border:0;}
    QSlider::groove:horizontal {height:5px;background:@tonal@;border-radius:2px;}
    QSlider::sub-page:horizontal {background:@sel_line@;border-radius:2px;}
    QSlider::handle:horizontal {background:@pri@;width:16px;height:16px;margin:-6px 0;border-radius:8px;}
    QSlider:focus {background:@sel@;border-radius:6px;}
    QScrollBar:vertical {background:transparent;width:8px;margin:4px 0;}
    QScrollBar::handle:vertical {background:@muted@;border-radius:4px;min-height:28px;}
    QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical {height:0;}
    QScrollBar::add-page:vertical,QScrollBar::sub-page:vertical {background:transparent;}
    QScrollBar:horizontal {height:8px;background:transparent;}
    QScrollBar::handle:horizontal {background:@muted@;border-radius:4px;min-width:28px;}
    QScrollBar::add-line:horizontal,QScrollBar::sub-line:horizontal {width:0;}
    QDialog,QMessageBox {background:@ground@;}
    QDialog QLabel {padding-top:4px;}
    QDialogButtonBox {padding-top:8px;}
    QMenu {background:@surface@;border:1px solid transparent;border-radius:10px;padding:8px;}
    QMenu::item {padding:8px 24px 8px 12px;border-radius:6px;}
    QMenu::item:selected {background:@sel@;}
    QToolTip {background:@surface@;color:@ink@;border:1px solid @tonal@;padding:8px;border-radius:10px;}

    QLabel#navVersion {color:@nav_ink@;font-size:11.5px;}
    QLabel#verdictHeadline {font-size:20px;font-weight:700;}
    QLabel#evidenceLink {color:@info@;}
    QPushButton#themeToggle {padding:0;border-radius:16px;}
    QPushButton[appearance="true"] {padding-left:12px;padding-right:14px;}
    QPushButton[appearance="true"]:checked {background:@sel@;border-color:@sel_line@;color:@ink@;}
    QPushButton[danger="true"] {background:@danger_bg@;color:@danger@;}
    QPushButton[danger="true"]:hover {border-color:@danger@;}
    QPushButton[primary="true"]:focus {border-color:@ink@;}
    QPushButton:disabled, QPushButton[primary="true"]:disabled, QPushButton[quiet="true"]:disabled,
    QPushButton[danger="true"]:disabled, QToolButton:disabled {
        background:@dis_bg@;color:@dis_ink@;border-color:transparent;
    }
    QComboBox:disabled,QLineEdit:disabled,QAbstractSpinBox:disabled,QCheckBox:disabled {
        background:@dis_bg@;color:@dis_ink@;
    }
    QCheckBox::indicator:disabled {background:@dis_bg@;border-color:@dis_ink@;}
    QCheckBox::indicator:checked:disabled {image:url("@check@");}
    QTabBar::tab:focus {border:1px solid @sel_line@;padding:7px 11px;}
    QTabBar::tab:selected:hover {background:@seg_on@;}
    QTabBar#trainingViews::tab {padding:7px 8px;margin-right:2px;}
    QTabBar#trainingViews::tab:focus {padding:6px 7px;}
    QListWidget#trainingObjects::item {padding:5px 6px;border-radius:6px;}
    QMenu::separator {height:1px;background:@tonal@;margin:3px 8px;}
    QMenu::item:disabled {color:@dis_ink@;}
    QHeaderView {background:@ev@;}
    QCalendarWidget QWidget#qt_calendar_navigationbar {background:@tonal@;}
    QCalendarWidget QToolButton {color:@tonal_ink@;}
    QCalendarWidget QAbstractItemView:enabled {color:@ink@;background:@surface@;}
    QCalendarWidget QAbstractItemView:disabled {color:@dis_ink@;}
    QScrollBar::add-page:horizontal,QScrollBar::sub-page:horizontal {background:transparent;}
    QProgressBar {min-height:4px;text-align:center;}
    QLabel#kpiValue[priority="true"] {color:@danger@;}
    '''

    tones = _tones(theme)
    for tone, (foreground, background) in tones.items():
        qss += (
            f'QLabel#badge[tone="{tone}"] {{color:{foreground};background:{background};}}'
            f'QLabel#assessmentValue[tone="{tone}"] {{color:{foreground};}}'
            f'QWidget#verdictCard[tone="{tone}"] {{background:{background};border-radius:14px;}}'
            f'QWidget#verdictCard[tone="{tone}"] QLabel#verdictHeadline {{color:{foreground};}}'
        )
    foreground, background = tones['unknown']
    qss += f'QLabel#comparisonValue[tone="unknown"] {{color:{foreground};background:{background};}}'
    tokens = dict(theme, border=border, pri_hover=QColor(theme['pri']).lighter(110).name(),
                  pri_pressed=QColor(theme['pri']).darker(112).name(), **icon_paths)
    for key, value in tokens.items():
        qss = qss.replace(f'@{key}@', value)
    return qss
