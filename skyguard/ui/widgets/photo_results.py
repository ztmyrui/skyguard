"""Local photo and video review tied to a saved flight plan."""
from copy import deepcopy
from datetime import datetime
from pathlib import Path
import shutil
from uuid import uuid4

from PySide6.QtCore import QSignalBlocker, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QImageReader, QPixmap
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import QFileDialog, QComboBox, QHBoxLayout, QLabel, QListWidget, QMessageBox, QSizePolicy, QStackedWidget, QWidget

from ...flight_route import FlightRoute
from ...photo_media import media_metadata
from ...photo_mission import match_shot, mission_for
from .common import ActionButton, Card, SectionHeader, label

IMAGE_SUFFIXES = {'.jpg', '.jpeg', '.png', '.webp', '.tif', '.tiff', '.bmp'}
VIDEO_SUFFIXES = {'.mp4', '.mov', '.m4v', '.avi', '.mkv'}


class MediaDropList(QListWidget):
    files_dropped = Signal(object)

    def __init__(self):
        super().__init__()
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event):
        if event.mimeData().hasUrls():
            files = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
            if files:
                self.files_dropped.emit(files)
            event.acceptProposedAction()
        else:
            super().dropEvent(event)


class PhotoLabel(QLabel):
    def __init__(self, text=''):
        super().__init__(text)
        self.original = QPixmap()
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        self.setAlignment(Qt.AlignCenter)

    def clear(self):
        self.original = QPixmap()
        super().clear()

    def show_image(self, image):
        self.original = QPixmap.fromImage(image)
        self._scale()

    def _scale(self):
        if not self.original.isNull():
            self.setPixmap(self.original.scaled(self.contentsRect().size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._scale()


class PhotoResults(QWidget):
    back = Signal()
    map_requested = Signal(bool)
    supplement = Signal(object, object)
    repeat_requested = Signal(object)
    locate = Signal(object)

    def __init__(self, context):
        super().__init__()
        self.context = context
        self.plan = self.mission = self.route = None
        self.left, self.center, self.right = Card(), Card(), Card()
        self.left.setMinimumWidth(250)
        self.right.setMinimumWidth(270)
        self.right.setMaximumWidth(320)
        self.left.body.addWidget(SectionHeader('拍摄清单'))
        self.progress = label('')
        self.left.body.addWidget(self.progress)
        self.shots = QListWidget()
        self.shots.setObjectName('photoList')
        self.shots.setAccessibleName('拍摄镜头和归类进度')
        self.shots.currentRowChanged.connect(self._select_shot)
        self.left.body.addWidget(self.shots, 1)
        self.done = ActionButton('标记已拍', self._mark_done)
        self.left.body.addWidget(self.done)
        self.left.body.addWidget(ActionButton('返回我的计划', self.back.emit, variant='quiet'))

        self.header = SectionHeader('实拍成果')
        self.center.body.addWidget(self.header)
        self.picture = PhotoLabel('导入照片或视频，开始整理。')
        self.picture.setAlignment(Qt.AlignCenter)
        self.picture.setMinimumSize(180,160)
        self.reference = PhotoLabel()
        self.reference.setAlignment(Qt.AlignCenter)
        self.reference.hide()
        self.video = QVideoWidget()
        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.player.setAudioOutput(self.audio)
        self.player.setVideoOutput(self.video)
        self.player.errorOccurred.connect(lambda *_: self.media_details.setText('视频无法预览，可用系统播放器打开'))
        self.visual = QStackedWidget()
        images = QWidget()
        duo = QHBoxLayout(images)
        duo.setContentsMargins(0,0,0,0)
        self.previous_column, self.current_column = Card(), Card()
        self.previous_column.body.addWidget(label('上次拍摄', 'checkTitle'))
        self.previous_column.body.addWidget(self.reference, 1)
        self.current_caption = label('本次拍摄', 'checkTitle')
        self.current_caption.hide()
        self.current_column.body.addWidget(self.current_caption)
        self.current_column.body.addWidget(self.picture, 1)
        self.previous_column.hide()
        duo.addWidget(self.previous_column, 1)
        duo.addWidget(self.current_column, 1)
        self.visual.addWidget(images)
        self.visual.addWidget(self.video)
        self.center.body.addWidget(self.visual, 1)
        tools = QHBoxLayout()
        self.play = ActionButton('播放／暂停', self._play_video)
        self.play.hide()
        tools.addWidget(self.play)
        tools.addWidget(ActionButton('在地图上看', lambda: self.map_requested.emit(True)))
        tools.addStretch(1)
        self.center.body.addLayout(tools)

        self.right.body.addWidget(SectionHeader('素材'))
        self.right.body.addWidget(ActionButton('导入照片或视频', self.choose_files, variant='primary'))
        self.right.body.addWidget(ActionButton('导出素材清单', self.export_manifest, variant='quiet'))
        self.list = MediaDropList()
        self.list.files_dropped.connect(self._import)
        self.list.setObjectName('photoList')
        self.list.setAccessibleName('当前计划的实拍素材')
        self.list.currentRowChanged.connect(self._select_media)
        self.right.body.addWidget(self.list, 1)
        self.media_details = label('', 'bodyText')
        self.right.body.addWidget(self.media_details)
        self.link = QComboBox()
        self.link.setAccessibleName('关联镜头')
        self.link.activated.connect(self._associate)
        self.right.body.addWidget(self.link)
        self.open_file = ActionButton('打开原始素材', self._open_file, variant='quiet')
        self.right.body.addWidget(self.open_file)
        self.remove_file = ActionButton('移出当前素材', self._remove_media, variant='quiet')
        self.right.body.addWidget(self.remove_file)
        self.retake = ActionButton('从这个机位补拍', self._supplement)
        self.right.body.addWidget(self.retake)
        self.retake_full = ActionButton('生成同机位复拍计划', lambda: self.repeat_requested.emit(self.plan))
        self.right.body.addWidget(self.retake_full)

    @property
    def folder(self):
        return self.context.civic.repo.path.parent / 'photo-media'

    def load(self, plan):
        self.player.stop()
        self.plan = plan
        self.route = FlightRoute.parse(plan['query']['route'])
        self.mission = deepcopy(plan.get('mission')) or mission_for(self.route, 'free', '', '')
        self.header.setText(plan['title'])
        self._populate()

    def _persist(self):
        self.context.civic.update_plan_details(self.plan['id'], mission=deepcopy(self.mission))
        self.plan = next(row for row in self.context.civic.data['plans'] if row['id']==self.plan['id'])
        self._populate(self.list.currentRow(), self.shots.currentRow())

    def _populate(self, media_index=0, shot_index=-1):
        from collections import Counter

        counts = Counter(item.get('shot') for item in self.mission['materials'])
        completed = set(self.mission.get('completed', ()))
        total = self.mission['shots']
        count = sum(bool(counts[shot['id']]) or shot['id'] in completed for shot in total)
        leftover = counts['']
        self.progress.setText(f'{count} / {len(total)} 已拍' + (f' · {leftover} 待归类' if leftover else ''))
        with QSignalBlocker(self.shots), QSignalBlocker(self.link), QSignalBlocker(self.list):
            self.shots.clear()
            self.shots.addItem('全部镜头')
            self.shots.addItem('待归类')
            self.link.clear()
            self.link.addItem('待归类', '')
            for shot in total:
                status = '已拍' if counts[shot['id']] or shot['id'] in completed else '待拍'
                self.shots.addItem(f"{shot['title']}  ·  {status}")
                self.link.addItem(shot['title'], shot['id'])
            self.shots.setCurrentRow(max(0, min(shot_index, self.shots.count()-1)))
        self.retake_full.setVisible(self.mission['kind'] == 'repeat')
        self._select_shot(self.shots.currentRow(), media_index)

    def _current_shot(self):
        row = self.shots.currentRow() - 2
        return self.mission['shots'][row] if 0 <= row < len(self.mission['shots']) else None

    def _select_shot(self, row, media_index=0):
        shot = self._current_shot()
        always = sorted(self.mission['materials'], key=lambda item: (item['time'] or '9999', item['name']))
        self.visible_media = ([item for item in always if item.get('shot') == shot['id']] if shot else
                              [item for item in always if not item.get('shot')] if row==1 else always)
        with QSignalBlocker(self.list):
            self.list.clear()
            self.list.addItems([item['name'] for item in self.visible_media])
            self.list.setCurrentRow(min(max(0, media_index), self.list.count()-1))
        self.done.setEnabled(shot is not None)
        self.done.setText('撤销已拍标记' if shot and shot['id'] in self.mission['completed'] else '标记已拍')
        self.retake.setEnabled(shot is not None)
        self._select_media(self.list.currentRow())
        if shot:
            self.locate.emit(shot['point'])

    def _current_media(self):
        row = self.list.currentRow()
        return self.visible_media[row] if 0 <= row < len(self.visible_media) else None

    def _select_media(self, index):
        self.player.stop()
        current = self._current_media()
        self.link.setEnabled(bool(current))
        self.open_file.setEnabled(bool(current))
        self.remove_file.setEnabled(bool(current))
        path = self.folder/current['file'] if current else None
        exists = path is not None and path.is_file()
        video = exists and path.suffix.lower() in VIDEO_SUFFIXES
        self.play.setVisible(video)
        self.previous_column.hide()
        self.current_caption.hide()
        self.visual.setCurrentIndex(1 if video else 0)
        self.picture.clear()
        self.reference.clear()
        self.media_details.clear()
        if not current:
            self.picture.setText('当前镜头还没有素材' if self._current_shot() else '导入照片或视频，开始整理。')
            return
        if not exists:
            self.picture.setText('本机素材文件已移走')
        elif video:
            self.player.setSource(QUrl.fromLocalFile(str(path)))
            self.player.play()
        else:
            self._show_image(path, self.picture)
            if prior := self._find_reference(current):
                self._show_image(prior, self.reference)
                self.reference.show()
                self.previous_column.show()
                self.current_caption.show()
        stamp = datetime.fromisoformat(current['time']).strftime('%Y-%m-%d %H:%M:%S') if current['time'] else '拍摄时间待补充'
        point = current.get('point')
        place = f'{point[0]:.5f}, {point[1]:.5f}' if point else '拍摄位置待补充'
        self.media_details.setText(f'{stamp}\n{place}')
        with QSignalBlocker(self.link):
            self.link.setCurrentIndex(max(0, self.link.findData(current.get('shot', ''))))
        if point:
            self.locate.emit(point)

    def _find_reference(self, item):
        if self.mission['kind'] != 'repeat' or not item.get('shot'):
            return None
        previous = next((row for row in self.context.civic.data['plans']
                         if row['id'] == self.mission.get('previous_plan')), None)
        matches = previous.get('mission', {}).get('materials', ()) if previous else ()
        return next((self.folder / entry['file'] for entry in matches if entry.get('shot') == item['shot']
                     and Path(entry['file']).suffix.lower() in IMAGE_SUFFIXES), None)

    @staticmethod
    def _show_image(path, widget):
        reader = QImageReader(str(path))
        reader.setAutoTransform(True)
        image = reader.read()
        if image.isNull():
            widget.setText('照片无法预览')
        else:
            widget.show_image(image)

    def export_manifest(self):
        import csv

        filename, _ = QFileDialog.getSaveFileName(self, '导出素材清单', '素材清单.csv', 'CSV 文件 (*.csv)')
        if not filename:
            return
        names = {shot['id']: shot['title'] for shot in self.mission['shots']}
        def save():
            with Path(filename).open('w', encoding='utf-8-sig', newline='') as stream:
                writer = csv.writer(stream)
                writer.writerow(('文件', '镜头', '拍摄时间', '经度 WGS84', '纬度 WGS84'))
                for item in self.mission['materials']:
                    point = item.get('point') or ('','')
                    name = item['name']
                    if name.startswith(('=', '+', '-', '@')):
                        name = "'" + name
                    writer.writerow((name, names.get(item.get('shot'), '待归类'), item.get('time',''), *point))
            return True
        if self.context.safe(save):
            self.context.status.emit('素材清单已导出', 4000)

    def choose_files(self):
        files, _ = QFileDialog.getOpenFileNames(self, '导入实拍素材', '', '照片与视频 (*.jpg *.jpeg *.png *.webp *.tif *.tiff *.bmp *.mp4 *.mov *.m4v *.avi *.mkv)')
        self._import(files)

    def _import(self, files):
        if not files:
            return
        if self.context.safe(lambda: self.import_files(files)) == 0:
            self.context.status.emit('未发现可导入的照片或视频', 4000)

    def import_files(self, filenames):
        self.folder.mkdir(parents=True, exist_ok=True)
        added = []
        errors = []
        for filename in filenames:
            source = Path(filename)
            if source.suffix.lower() not in IMAGE_SUFFIXES | VIDEO_SUFFIXES:
                continue
            identifier = uuid4().hex
            target = self.folder / (identifier + source.suffix.lower())
            try:
                metadata = media_metadata(source)
                shutil.copyfile(source, target)
            except OSError:
                target.unlink(missing_ok=True)
                errors.append(source.name)
                continue
            row = dict(metadata, id=identifier, file=target.name, name=source.name)
            row['shot'] = match_shot(row, self.route, self.mission['shots'])
            added.append(row)
        if added:
            self.mission['materials'].extend(added)
            try:
                self._persist()
            except Exception:
                for row in added:
                    (self.folder / row['file']).unlink(missing_ok=True)
                    self.mission['materials'].remove(row)
                raise
        if errors:
            QMessageBox.warning(self, '部分素材未导入', '请检查这些文件：' + '、'.join(errors))
        return len(added)

    def _remove_media(self):
        item = self._current_media()
        if not item or QMessageBox.question(
            self, '移出当前素材', f'移出「{item["name"]}」并删除导入时保存的本机副本？'
        ) != QMessageBox.Yes:
            return

        def remove():
            self.player.stop()
            self.player.setSource(QUrl())
            records = self.mission['materials']
            index = records.index(item)
            filename = item['file']
            if Path(filename).name != filename:
                raise ValueError('素材文件名无效')
            path = self.folder / filename
            shared = any(entry['file'] == filename for plan in self.context.civic.data['plans']
                         for entry in (plan.get('mission') or {}).get('materials', ())
                         if plan['id'] != self.plan['id'] or entry['id'] != item['id'])
            staged = None
            if not shared and path.is_file():
                staged = path.with_name('.remove-' + uuid4().hex + path.suffix)
                path.rename(staged)
            records.pop(index)
            try:
                self.context.civic.update_plan_details(self.plan['id'], mission=deepcopy(self.mission))
            except Exception:
                records.insert(index, item)
                if staged:
                    staged.rename(path)
                raise
            self.plan = next(row for row in self.context.civic.data['plans'] if row['id'] == self.plan['id'])
            self._populate(self.list.currentRow(), self.shots.currentRow())
            if staged:
                staged.unlink(missing_ok=True)

        self.context.safe(remove)

    def _associate(self, index):
        if item := self._current_media():
            item['shot'] = self.link.itemData(index)
            self.context.safe(self._persist)

    def _mark_done(self):
        if shot := self._current_shot():
            done = self.mission['completed']
            if shot['id'] in done:
                done.remove(shot['id'])
            else:
                done.append(shot['id'])
            self.context.safe(self._persist)

    def _open_file(self):
        if item := self._current_media():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.folder/item['file'])))

    def _play_video(self):
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def _supplement(self):
        if shot := self._current_shot():
            self.supplement.emit(self.plan, shot)
