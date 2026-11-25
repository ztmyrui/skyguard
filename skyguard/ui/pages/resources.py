"""Local roster, rules, announcement drafts and audit views."""
import csv
import io
import json
import re
from collections import Counter
from ...system_clock import now as system_now
from pathlib import Path

from PySide6.QtCore import QSignalBlocker, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox, QFileDialog, QFormLayout, QHBoxLayout, QHeaderView, QLineEdit,
    QListWidget, QStackedWidget, QTabWidget, QTextEdit, QVBoxLayout, QWidget,
)

from ...civic import ROLES, STAFF_FIELDS, validate_city
from ...theme import TONES, colors
from ...zone_catalog import read_table
from ..presenters import audit_rows
from ..widgets.common import ActionButton, Badge, Card, EmptyState, SectionHeader, add_row, label, scroll_contents, table
from .base import DutyPage

RULE_TYPES = {'restriction': '限制', 'coordination': '场地协调', 'coverage': '数据覆盖'}
DIFF_TONES = {'新增': 'ok', '删除': 'danger', '修改': 'warn'}
DIFF_FIELDS = {
    'name': '名称', 'kind': '类型', 'start': '开始时刻', 'end': '结束时刻', 'lower': '最低高度',
    'upper': '最高高度', 'demo': '资料类别', 'verified': '复核状态', 'source': '来源', 'issuer': '发布单位',
    'note': '说明', 'rings': '空间边界', 'categories': '适用机型',
    'lat': '纬度', 'lon': '经度', 'open_s': '开放时刻', 'close_s': '关闭时刻',
    'range_m': '视野半径', 'enabled': '启用状态',
}


def _fill(grid, rows):
    grid.setUpdatesEnabled(False)
    try:
        grid.setRowCount(0)
        for row in rows:
            add_row(grid, row)
    finally:
        grid.setUpdatesEnabled(True)


def _tab():
    page = QWidget()
    layout = QHBoxLayout(page)
    layout.setContentsMargins(8, 8, 8, 8)
    layout.setSpacing(8)
    return page, layout


def _csv_text(headers, rows):
    stream = io.StringIO()
    writer = csv.writer(stream)
    writer.writerow(headers)
    writer.writerows(rows)
    return stream.getvalue()


def _clock_seconds(text, origin):
    if not re.fullmatch(r'(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]', text):
        raise ValueError('班次请按 HH:mm:ss 输入')
    hour, minute, second = map(int, text.split(':'))
    return (hour * 3600 + minute * 60 + second - origin) % 86400


def _changed_fields(previous, current, clock):
    changes = []
    for key in sorted(previous.keys() | current.keys()):
        before, after = previous.get(key), current.get(key)
        if before == after:
            continue
        field = DIFF_FIELDS.get(key, key)
        if key in ('open_s', 'close_s'):
            changes.append(f'{field} {clock(before)} → {clock(after)}')
        elif key == 'range_m':
            changes.append(f'{field} {before} → {after} 米')
        else:
            changes.append(field)
    return '、'.join(changes)


def _changes(old, new, clock):
    for key, title in (('rules', '规则'), ('posts', '观察岗')):
        before = {row['id']: row for row in old.get(key, ())}
        after = {row['id']: row for row in new[key]}
        for item_id in sorted(before.keys() | after.keys()):
            previous, current = before.get(item_id), after.get(item_id)
            if previous != current:
                change = '新增' if previous is None else '删除' if current is None else '修改'
                name = (current or previous)['name']
                if previous and current and previous['name'] != current['name']:
                    name = f'{previous["name"]} → {current["name"]}'
                detail = _changed_fields(previous, current, clock) if previous and current else '—'
                yield title, change, item_id, name, detail


class ResourcesPage(DutyPage):
    """Maintain resources without leaving the duty workspace."""
    title = '资料资源'
    staff_changed = Signal()
    cities_changed = Signal()

    def __init__(self, context):
        super().__init__(context)
        self.tabs = QTabWidget()
        self.body.addWidget(self.tabs)
        self._staff_tab()
        self._city_tab()
        self._announcement_tab()
        self._audit_tab()
        self._zone_types_tab()
        self._route_tolerances_tab()
        self._training_roster_tab()
        self.load_staff(context.civic.data['staff'])
        self.refresh_cities()
        self.tabs.currentChanged.connect(lambda _: self.refresh())

    def actions(self):
        return ()

    def _route_tolerances_tab(self):
        from PySide6.QtWidgets import QSpinBox
        page, layout = _tab()
        self.tabs.addTab(page, '航线容差')
        card = Card()
        card.body.addWidget(SectionHeader('按机型设置航线单侧宽度'))
        form = QFormLayout()
        self.tolerances = {}
        for category, value in read_table('route-tolerances.json')['by_category'].items():
            control = QSpinBox()
            control.setRange(1, 10000)
            control.setSuffix(' 米')
            control.setValue(value)
            self.tolerances[category] = control
            form.addRow(category, control)
        card.body.addLayout(form)
        row = QHBoxLayout()
        row.addWidget(ActionButton('保存调整', self._save_tolerances, variant='primary'))
        row.addWidget(ActionButton('恢复默认', lambda: self._render_tolerances(default=True)))
        card.body.addLayout(row)
        card.body.addStretch()
        layout.addWidget(card)
        self._render_tolerances()
        self.context.dataset_changed.connect(self._render_tolerances)

    def _render_tolerances(self, default=False):
        saved = {} if default else self.context.civic.data.get('route_tolerances', {})
        for category, value in read_table('route-tolerances.json')['by_category'].items():
            self.tolerances[category].setValue(saved.get(category, value))

    def _save_tolerances(self):
        from copy import deepcopy
        state = deepcopy(self.context.civic.data)
        values = {category: control.value() for category, control in self.tolerances.items()}
        state['route_tolerances'] = values
        self.context.civic.commit(state, '更新航线容差', values)
        self.context.civic_changed.emit()
        self.context.status.emit('已保存航线容差', 4000)

    def _zone_types_tab(self):
        page,layout=_tab()
        self.tabs.addTab(page,'区域类型')
        panel=Card()
        panel.body.addWidget(SectionHeader('区域敏感度与估算范围'))
        self.zone_types=table(['类型','默认等级','缓冲（米）','高峰时段'])
        panel.body.addWidget(self.zone_types,1)
        row=QHBoxLayout()
        row.addWidget(ActionButton('保存调整',self._save_zone_types,variant='primary'))
        row.addWidget(ActionButton('恢复默认',self._reset_zone_types))
        row.addStretch()
        panel.body.addLayout(row)
        layout.addWidget(panel,1)
        self._render_zone_types()

    def _render_zone_types(self):
        defaults=read_table('zone-types.json')
        custom=self.context.training.data['types']
        self.zone_values=custom or defaults['types']
        grid=self.zone_types
        grid.setRowCount(0)
        for entry in self.zone_values.values():
            peaks='、'.join(f'{a}–{b}' for _,a,b in entry.get('peak',())) or '按区域档案'
            add_row(grid,(entry['name'],entry['default'],entry['buffer'],peaks))
            for column in (1,2):
                item=grid.item(grid.rowCount()-1,column)
                item.setFlags(item.flags()|Qt.ItemIsEditable)
        from PySide6.QtWidgets import QAbstractItemView
        grid.setEditTriggers(QAbstractItemView.DoubleClicked|QAbstractItemView.EditKeyPressed)

    def _save_zone_types(self):
        def save():
            values={}
            for index,(key,current) in enumerate(self.zone_values.items()):
                rating=self.zone_types.item(index,1).text().strip()
                radius=int(self.zone_types.item(index,2).text())
                if rating not in ('S1','S2','S3','低影响') or not 10<=radius<=5000:
                    raise ValueError('等级填写 S1、S2、S3 或低影响，半径填写 10 至 5000 米')
                values[key]=dict(current,default=rating,buffer=radius)
            self.context.training.update_types(values)
            self._render_zone_types()
            self.context.status.emit('已保存，重新加载情景后生效',5000)
        self.context.safe(save)

    def _reset_zone_types(self):
        self.context.training.update_types(None)
        self._render_zone_types()

    def _training_roster_tab(self):
        from datetime import datetime, timedelta
        from PySide6.QtCore import QDateTime, QTimeZone
        from PySide6.QtWidgets import QDateTimeEdit, QSpinBox
        from ...aerial import CHINA
        page, layout = _tab()
        self.tabs.addTab(page, '预演排班')
        panel=Card()
        layout.addWidget(panel,1)
        panel.body.addWidget(SectionHeader('未来三天 · 排班'))
        listing=table(['人员','开始（北京时间）','结束（北京时间）'])
        panel.body.addWidget(listing,1)
        session=self.context.training

        def render():
            names={p['id']:p['name'] for p in self.context.civic.data['staff']}
            _fill(listing,[(names.get(r['person'],r['person']),r['start'][:16].replace('T',' '),
                            r['end'][:16].replace('T',' ')) for r in session.data['roster']])
        render()
        person=QComboBox()
        for p in self.context.civic.data['staff']:
            person.addItem(p['name'],p['id'])
        start=QDateTimeEdit()
        start.setTimeZone(QTimeZone(8*3600))
        start.setDateTime(QDateTime.fromString(system_now(CHINA).isoformat(timespec='seconds'),Qt.ISODate))
        start.setDisplayFormat('yyyy-MM-dd HH:mm')
        start.setCalendarPopup(True)
        span=QSpinBox()
        span.setRange(15,720)
        span.setValue(60)
        span.setSuffix(' 分钟')
        form=QFormLayout()
        for name,field in (('人员',person),('开始',start),('时长',span)):
            form.addRow(name,field)
        panel.body.addLayout(form)
        def save():
            if not person.currentData():
                raise ValueError('请先添加值班人员')
            first=start.dateTime().toPython().replace(tzinfo=CHINA)
            session.save_shift(person.currentData(),first,first+timedelta(minutes=span.value()))
            render()
        panel.body.addWidget(ActionButton('登记排班',lambda:self.context.safe(save),variant='primary'))

    def _staff_tab(self):
        page, layout = _tab()
        panel = Card()
        layout.addWidget(panel)
        self.tabs.addTab(page, '人员与值班')
        panel.body.addWidget(SectionHeader('人员与值班'))
        self.shift_hint = label('')
        panel.body.addWidget(self.shift_hint)
        self.staff = table(['工号', '姓名', '岗位能力', '班次开始', '班次结束', '起始岗位', '可用'])
        self.staff.setEditTriggers(self.staff.EditTrigger.DoubleClicked | self.staff.EditTrigger.EditKeyPressed)
        self.staff.itemChanged.connect(lambda _: self._validate_staff())
        panel.body.addWidget(self.staff, 1)
        self.staff_errors = label('', 'inlineError')
        panel.body.addWidget(self.staff_errors)
        row = QHBoxLayout()
        for text, callback in (('新增一行', self.add_staff), ('移除选中行', self.remove_staff),
                               ('导入 CSV', self.import_staff), ('导出 CSV', self.export_staff)):
            row.addWidget(ActionButton(text, callback))
        row.addStretch()
        self.staff_save = ActionButton('保存人员与班次', self.save_staff, variant='primary')
        row.addWidget(self.staff_save)
        panel.body.addLayout(row)

    def load_staff(self, records):
        scene = self.context.service.scene
        self.shift_hint.setText(
            f'长沙演练 {scene.clock_text(0)}–{scene.clock_text(scene.duration_s)} · 双击单元格编辑，班次使用 HH:mm:ss。'
        )
        with QSignalBlocker(self.staff):
            _fill(self.staff, [[self._staff_cell(record, key) for key in STAFF_FIELDS] for record in records])
        self._validate_staff()

    def _staff_cell(self, record, key):
        value = record.get(key, '')
        if key == 'available' and isinstance(value, bool):
            return '在岗' if value else '不可用'
        if key in ('start_s', 'end_s'):
            try:
                scene = self.context.service.scene
                seconds = int(value)
                return scene.clock_text(seconds) if 0 <= seconds <= scene.duration_s else value
            except (ValueError, TypeError):
                return value
        return value if value is not None else ''

    def _staff_rows(self):
        return [{key: self.staff.item(index, col).text().strip() for col, key in enumerate(STAFF_FIELDS)}
                for index in range(self.staff.rowCount())]

    def refresh_appearance(self):
        # Existing validation colors are item roles and survive a palette change.
        self._validate_staff()

    def _validate_staff(self):
        rows = self._staff_rows()
        ids = Counter(row['id'] for row in rows)
        posts = {post['id'] for post in self.context.civic.cities()['changsha']['posts']}
        messages = []
        with QSignalBlocker(self.staff):
            for index, row in enumerate(rows):
                errors = self._validate_staff_row(row, ids, posts)
                for column, key in enumerate(STAFF_FIELDS):
                    cell = self.staff.item(index, column)
                    message = errors.get(key)
                    cell.setBackground(QColor(TONES['danger'][1]) if message else QColor())
                    cell.setForeground(QColor(TONES['danger'][0] if message else colors()['ink']))
                    cell.setToolTip(message or cell.text())
                if errors:
                    messages.append(f'第 {index + 1} 行：' + '、'.join(dict.fromkeys(errors.values())))
        self.staff_errors.setText('；'.join(messages[:4]) + (f'；另有 {len(messages) - 4} 行有误' if len(messages) > 4 else ''))
        self.staff_errors.setVisible(bool(messages))
        self.staff_save.setEnabled(not messages)
        return rows if not messages else None

    def _validate_staff_row(self, row, ids, posts):
        errors = {}
        if not row['id'] or ids[row['id']] > 1:
            errors['id'] = '请填写唯一工号'
        if not row['name']:
            errors['name'] = '请填写姓名'
        if row['role'] not in ROLES:
            errors['role'] = '岗位能力填写现场核验或联络核实'
        if row['post_id'] not in posts:
            errors['post_id'] = '起始岗位须存在于长沙规则包'
        flag = row['available'].lower()
        if flag not in {'true', 'false', '1', '0', '在岗', '不可用'}:
            errors['available'] = '可用状态填写在岗或不可用'
        else:
            row['available'] = flag in {'true', '1', '在岗'}
        scene = self.context.service.scene
        for key in ('start_s', 'end_s'):
            try:
                row[key] = _clock_seconds(row[key], scene.start_of_day_s)
                if row[key] > scene.duration_s:
                    errors[key] = '时刻超出演练范围'
            except ValueError as exc:
                errors[key] = str(exc)
        if not {'start_s', 'end_s'} & errors.keys() and row['end_s'] <= row['start_s']:
            errors['end_s'] = '班次结束须晚于开始'
        return errors

    def staff_rows(self):
        """Return validated roster rows in the CSV and persistence format."""
        rows = self._validate_staff()
        if rows is None:
            raise ValueError('请先修正表格中的人员与班次错误。')
        return rows

    def add_staff(self):
        scene = self.context.service.scene
        posts = self.context.civic.cities()['changsha']['posts']
        row = self.staff.rowCount()
        with QSignalBlocker(self.staff):
            add_row(self.staff, ('', '', '现场核验', scene.clock_text(0), scene.clock_text(scene.duration_s),
                                 posts[0]['id'] if posts else '', '在岗'))
        self._validate_staff()
        self.staff.setCurrentCell(row, 0)
        self.staff.editItem(self.staff.currentItem())

    def remove_staff(self):
        if self.staff.currentRow() >= 0:
            self.staff.removeRow(self.staff.currentRow())
            self._validate_staff()

    def import_staff(self):
        path, _ = QFileDialog.getOpenFileName(self, '导入人员 CSV', '', 'CSV (*.csv)')
        if path:
            def load():
                contents = Path(path).read_text(encoding='utf-8-sig')
                self.load_staff(list(csv.DictReader(io.StringIO(contents))))
            self.context.safe(load)

    def export_staff(self):
        def content():
            return _csv_text(STAFF_FIELDS, ([row[key] for key in STAFF_FIELDS] for row in self.staff_rows()))
        self._save_file('导出人员 CSV', '人员班次.csv', 'CSV (*.csv)', content, 'utf-8-sig')

    def save_staff(self):
        try:
            self.context.civic.save_staff(self.staff_rows())
        except (OSError, ValueError, TypeError) as exc:
            self.staff_errors.setText(str(exc))
            self.staff_errors.show()
            return
        self.context.service.sync_staff()
        self.staff_changed.emit()
        self.context.status.emit('人员与值班已保存。', 6000)

    def _city_tab(self):
        page, layout = _tab()
        self.tabs.addTab(page, '城市规则包')
        catalog = Card()
        catalog.setFixedWidth(204)
        catalog.body.addWidget(SectionHeader('城市'))
        self.cities = QListWidget()
        self.cities.setObjectName('planList')
        self.cities.setAccessibleName('城市规则包')
        self.cities.currentRowChanged.connect(self.show_city)
        catalog.body.addWidget(self.cities, 1)
        layout.addWidget(catalog)
        self.city_stack = QStackedWidget()
        layout.addWidget(self.city_stack, 1)
        self.city_stack.addWidget(self._city_detail())
        self.city_stack.addWidget(self._city_preview())
        self._pending_city = None

    def _city_detail(self):
        panel = Card()
        self.city_title = SectionHeader('')
        panel.body.addWidget(self.city_title)
        for title, attribute, headers, weight in (
            ('规则', 'rules', ['名称', '类型', '演练／正式', '时段', '来源'], 3),
            ('观察岗', 'posts', ['编号', '名称', '开放时段', '视野半径', '状态'], 3),
            ('数据来源', 'sources', ['来源地址'], 2),
        ):
            panel.body.addWidget(SectionHeader(title))
            grid = table(headers)
            grid.setMinimumHeight(76)
            grid.setWordWrap(False)
            grid.verticalHeader().setDefaultSectionSize(32)
            setattr(self, attribute, grid)
            panel.body.addWidget(grid, weight)
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(ActionButton('导出 JSON', self.export_city, 'export'))
        row.addWidget(ActionButton('导入并预览', self.import_city, 'import', 'primary'))
        panel.body.addLayout(row)
        return panel

    def _city_preview(self):
        panel = Card()
        self.preview_title = SectionHeader('')
        panel.body.addWidget(self.preview_title)
        self.preview_warning = label('正式资料须核实发布单位、空间边界及有效期后使用。', 'inlineError')
        panel.body.addWidget(self.preview_warning)
        self.preview_counts = QVBoxLayout()
        panel.body.addLayout(self.preview_counts)
        self.diff = table(['对象', '变更', '编号', '名称', '差异摘要'])
        header = self.diff.horizontalHeader()
        for column, width in enumerate((88, 80, 166, 224)):
            header.setSectionResizeMode(column, QHeaderView.Interactive)
            self.diff.setColumnWidth(column, width)
        self.diff.verticalHeader().setDefaultSectionSize(42)
        panel.body.addWidget(self.diff, 1)
        self.preview_error = label('', 'inlineError')
        self.preview_error.hide()
        panel.body.addWidget(self.preview_error)
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(ActionButton('取消', self.cancel_city, variant='quiet'))
        self.city_confirm = ActionButton('确认导入', self.confirm_city, variant='primary')
        row.addWidget(self.city_confirm)
        panel.body.addLayout(row)
        return panel

    def refresh_cities(self, selected=None):
        if selected is None:
            selected = self._city_id()
        selected_draft = self.announce_city.currentData()
        self._city_ids = list(self.context.civic.cities())
        with QSignalBlocker(self.cities), QSignalBlocker(self.announce_city):
            self.cities.clear()
            self.announce_city.clear()
            for identifier, city in self.context.civic.cities().items():
                self.cities.addItem(city['name'])
                self.announce_city.addItem(city['name'], identifier)
            self.cities.setCurrentRow(self._city_ids.index(selected) if selected in self._city_ids else 0)
            self.announce_city.setCurrentIndex(max(0, self.announce_city.findData(selected_draft)))
        self.show_city()
        self.show_drafts()

    def _city_id(self):
        row = self.cities.currentRow()
        return self._city_ids[row] if row >= 0 else None

    def show_city(self, *_):
        city = self.context.civic.cities().get(self._city_id())
        if city is None:
            return
        if self._pending_city is not None:
            self.cancel_city()
        self.city_title.setText(f'{city["name"]} · {len(city["rules"])} 条规则 · {len(city["posts"])} 个观察岗')
        _fill(self.rules, (
            (rule['name'], RULE_TYPES[rule['kind']], '演练' if rule.get('demo') else '正式',
             f'{rule["start"][:16].replace("T", " ")} – {rule["end"][:16].replace("T", " ")}',
             ' · '.join(filter(None, (rule.get('issuer'), rule.get('source')))) or '—')
            for rule in city['rules']
        ))
        clock = self.context.service.scene.clock_text
        _fill(self.posts, (
            (post['id'], post['name'], f'{clock(post["open_s"])}–{clock(post["close_s"])}',
             f'{post["range_m"]} 米', '启用' if post['enabled'] else '停用')
            for post in city['posts']
        ))
        _fill(self.sources, ((source,) for source in city['sources']))

    def export_city(self):
        identifier = self._city_id()
        if identifier:
            self._save_file('导出城市规则包', identifier + '.json', 'JSON (*.json)',
                            lambda: json.dumps(self.context.civic.cities()[identifier], ensure_ascii=False, indent=2))

    def import_city(self):
        path, _ = QFileDialog.getOpenFileName(self, '导入城市规则包', '', 'JSON (*.json)')
        if path:
            self.context.safe(lambda: self.preview_city(Path(path).read_text(encoding='utf-8-sig')))

    def preview_city(self, text):
        """Validate the file and display changes before publication."""
        city = validate_city(json.loads(text))
        previous = self.context.civic.cities().get(city['id'])
        rows = list(_changes(validate_city(previous) if previous else {}, city,
                             self.context.service.scene.clock_text))
        self._pending_city = (city['id'], text)
        self.preview_title.setText(f'{city["name"]} · 导入预览')
        self.preview_warning.setVisible(any(not rule['demo'] for rule in city['rules']))
        self.preview_error.hide()
        counts = Counter((category, change) for category, change, *_ in rows)
        while self.preview_counts.count():
            self.preview_counts.takeAt(0).widget().deleteLater()
        for category in ('规则', '观察岗'):
            summary = QWidget()
            row = QHBoxLayout(summary)
            row.setContentsMargins(0, 0, 0, 0)
            row.addWidget(label(category, 'bodyText'))
            for change, tone in DIFF_TONES.items():
                row.addWidget(Badge(f'{change} {counts[category, change]}', tone))
            row.addStretch()
            self.preview_counts.addWidget(summary)
        _fill(self.diff, ((category, '', item_id, name, detail)
                          for category, _, item_id, name, detail in rows))
        for index, (_, change, *_) in enumerate(rows):
            item = self.diff.item(index, 1)
            item.setData(Qt.AccessibleTextRole, change)
            item.setToolTip(change)
            holder = QWidget()
            badge_layout = QHBoxLayout(holder)
            badge_layout.setContentsMargins(8, 0, 8, 0)
            badge_layout.addWidget(Badge(change, DIFF_TONES[change]), 0, Qt.AlignLeft | Qt.AlignVCenter)
            self.diff.setCellWidget(index, 1, holder)
        self.cities.setEnabled(False)
        self.city_stack.setCurrentIndex(1)

    def cancel_city(self):
        self._pending_city = None
        self.cities.setEnabled(True)
        self.city_stack.setCurrentIndex(0)

    def confirm_city(self):
        if not self._pending_city:
            return
        identifier, contents = self._pending_city
        try:
            self.context.civic.import_city(contents)
        except (OSError, TypeError, ValueError) as exc:
            self.preview_error.setText(str(exc))
            self.preview_error.show()
            return
        self.cancel_city()
        self._validate_staff()
        self.refresh_cities(identifier)
        self.cities_changed.emit()
        self.context.status.emit('城市规则包已导入。', 6000)

    def _announcement_tab(self):
        page, layout = _tab()
        self.tabs.addTab(page, '公告草稿')
        panel, catalog = Card(), Card()
        layout.addWidget(panel, 1)
        layout.addWidget(catalog, 1)
        panel.body.addWidget(SectionHeader('登记公告草稿'))
        panel.body.addWidget(label('登记公告原文供后续核对。'))
        form = QFormLayout()
        self.announce_city = QComboBox()
        self.draft_title = QLineEdit()
        self.draft_source = QLineEdit()
        self.draft_source.setPlaceholderText('https:// 官方公告原文')
        self.draft_text = QTextEdit()
        self.draft_text.setObjectName('draftBody')
        form.addRow('城市', self.announce_city)
        form.addRow('标题', self.draft_title)
        form.addRow('来源', self.draft_source)
        panel.body.addLayout(form)
        panel.body.addWidget(label('正文'))
        self.draft_text.setAccessibleName('公告正文')
        panel.body.addWidget(self.draft_text, 1)
        self.draft_error = label('', 'inlineError')
        self.draft_error.hide()
        panel.body.addWidget(self.draft_error)
        panel.body.addWidget(ActionButton('保存公告草稿', self.save_draft, variant='primary'), 0, Qt.AlignRight)
        catalog.body.addWidget(SectionHeader('已登记草稿'))
        self.draft_scroll, self.drafts = scroll_contents()
        catalog.body.addWidget(self.draft_scroll, 1)

    def show_drafts(self):
        while self.drafts.count():
            widget = self.drafts.takeAt(0).widget()
            if widget:
                widget.hide()
                widget.deleteLater()
        civic = self.context.civic
        drafts = civic.data['announcements']
        if not drafts:
            self.drafts.addWidget(EmptyState('暂无公告草稿', glyph='folder'), 1)
        for item in reversed(drafts):
            card = Card()
            card.body.addWidget(label(item['title'], 'section'))
            name = civic.cities().get(item['city'], {}).get('name', item['city'])
            card.body.addWidget(label(f'{name} · {item["source"]}'))
            card.body.addWidget(label(item['text'], 'bodyText'))
            self.drafts.addWidget(card)
        self.drafts.addStretch()

    def save_draft(self):
        try:
            self.context.civic.save_announcement(self.announce_city.currentData(), self.draft_title.text(),
                                                 self.draft_source.text(), self.draft_text.toPlainText())
        except (OSError, ValueError) as exc:
            self.draft_error.setText(str(exc))
            self.draft_error.show()
            return
        for field in (self.draft_title, self.draft_source, self.draft_text):
            field.clear()
        self.draft_error.hide()
        self.show_drafts()
        self.context.status.emit('公告草稿已保存。', 6000)

    def _audit_tab(self):
        page, layout = _tab()
        self.tabs.addTab(page, '审计记录')
        panel = Card()
        layout.addWidget(panel)
        panel.body.addWidget(SectionHeader('全局审计'))
        controls = QHBoxLayout()
        self.audit_search = QLineEdit()
        self.audit_search.setAccessibleName('审计关键字')
        self.audit_search.setPlaceholderText('搜索时间、动作或说明')
        self.audit_search.textChanged.connect(self._filter_audit)
        controls.addWidget(self.audit_search, 1)
        self.audit_action = QComboBox()
        self.audit_action.setAccessibleName('审计动作类型')
        self.audit_action.currentIndexChanged.connect(self._filter_audit)
        controls.addWidget(self.audit_action)
        controls.addWidget(ActionButton('导出 CSV', self.export_audit, 'export'))
        panel.body.addLayout(controls)
        self.audit_headers = ('时间（北京时间）', '动作', '说明')
        self.audit_table = table(self.audit_headers)
        header = self.audit_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        panel.body.addWidget(self.audit_table, 1)
        self.audit_summary = label('')
        panel.body.addWidget(self.audit_summary)
        self._audit_records = []
        self._audit_visible = []

    def _load_audit(self):
        context = self.context
        records = context.service.repo.history(limit=None) + context.civic.repo.history(limit=None)
        records += context.training.repo.history(limit=None)
        records.sort(key=lambda record: record['at'], reverse=True)
        self._audit_records = list(zip((record['action'] for record in records), audit_rows(records)))
        actions = sorted({action for action, _ in self._audit_records})
        selected = self.audit_action.currentData()
        with QSignalBlocker(self.audit_action):
            self.audit_action.clear()
            self.audit_action.addItem('全部动作', None)
            for action in actions:
                self.audit_action.addItem(action, action)
            self.audit_action.setCurrentIndex(max(0, self.audit_action.findData(selected)))
        self._filter_audit()

    def _filter_audit(self, *_):
        query = self.audit_search.text().strip().casefold()
        selected = self.audit_action.currentData()
        self._audit_visible = [row for action, row in self._audit_records
                               if (selected is None or action == selected)
                               and query in ' '.join((action, *row)).casefold()]
        _fill(self.audit_table, self._audit_visible)
        self.audit_summary.setText(f'显示 {len(self._audit_visible)} 条记录' if self._audit_visible else '没有匹配的审计记录')

    def export_audit(self):
        self._save_file('导出审计 CSV', '低空守望_审计记录.csv', 'CSV (*.csv)',
                        lambda: _csv_text(self.audit_headers, self._audit_visible), 'utf-8-sig')

    def _save_file(self, title, filename, file_filter, content, encoding='utf-8'):
        path, _ = QFileDialog.getSaveFileName(self, title, filename, file_filter)
        if path:
            self.context.safe(lambda: Path(path).write_text(content(), encoding=encoding, newline=''))

    def refresh(self):
        if self.tabs.currentIndex() == 3:
            self._load_audit()
