"""Saved routes and trip conditions for the public workspace."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QMenu, QMessageBox, QStackedWidget, QVBoxLayout, QWidget
from ..widgets.photo_results import PhotoResults
from ..widgets.task_card import write_task_card

from ...flight_route import FlightRoute, display_route
from ..presenters import flight_window
from ..widgets.common import ActionButton, Badge, Card, EmptyState, SectionHeader, label, scroll_contents
from ..widgets.flight_journey import clear
from ..widgets.flight_result import Disclosure, VERDICT_TONES, _link
from .base import Page


class PlanCard(Card):
    def __init__(self, plan, city, page):
        super().__init__()
        self.plan = plan
        self.page = page
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAccessibleName(plan['title'])
        self.body.addWidget(SectionHeader(plan['title']))
        query = plan['query']
        if not query.get('route'):
            self.body.addWidget(Badge('旧版范围报备', 'unknown'))
            self.body.addWidget(ActionButton('移出计划列表', lambda: page.cancel_plan(plan['id']), variant='quiet'))
            return
        route = FlightRoute.parse(query['route'])
        self.body.addWidget(label(f'{city} · {flight_window(route.start, route.end, show_year=True)}', 'bodyText'))
        badge = Badge(plan.get('result') or '待查询', VERDICT_TONES.get(plan.get('verdict'), 'unknown'))
        self.body.addWidget(badge)
        shots = (plan.get('mission') or {}).get('shots', ())
        score = (plan.get('aerial') or {}).get('score')
        metadata = ([f'{len(shots)} 个镜头'] if shots else []) + ([f'上次评分 {score:g} 分'] if score is not None else [])
        if metadata:
            self.body.addWidget(label(' · '.join(metadata)))
        if plan.get('draft'):
            self.body.addWidget(label('航线草稿'))
        self._conditions()
        self._history()
        actions = QHBoxLayout()
        self.primary = ActionButton('重新查飞' if plan.get('draft') else '出门前刷新',
                                    lambda: page.plan_requested.emit(plan, True), variant='primary')
        actions.addWidget(self.primary)
        if not plan.get('started_at'):
            actions.addWidget(ActionButton('改时间', lambda: page.reschedule_requested.emit(plan)))
        extra = ActionButton('更多', None, variant='quiet')
        menu = QMenu(extra)
        menu.addAction('导出现场任务卡', lambda: page.export_card(plan))
        menu.addAction('在地图上看', lambda: page.select_plan(plan))
        menu.addAction('登记飞行日志', lambda: page.log_requested.emit(plan))
        menu.addAction('复制航线', lambda: page.copy_requested.emit(plan))
        menu.addAction('移出计划列表', lambda:page.cancel_plan(plan['id']))
        extra.setMenu(menu)
        actions.addWidget(extra)
        self.body.addLayout(actions)
        self.body.addWidget(ActionButton('整理实拍', lambda: page.open_results(plan)))

    def set_selected(self, selected):
        if self.property('planSelected') != selected:
            self.setProperty('planSelected', selected)
            self.style().unpolish(self)
            self.style().polish(self)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self.rect().contains(event.position().toPoint()):
            self.page.select_plan(self.plan)
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            self.page.select_plan(self.plan)
            event.accept()
        else:
            super().keyPressEvent(event)

    def _conditions(self):
        pending = [item for item in self.plan.get('watch',{}).get('alerts',()) if not item.get('read')]
        if not pending:
            return
        detail = Disclosure('公告已更新')
        detail.row.addWidget(label('公告已更新','checkTitle'),1)
        detail.add_arrow()
        self.body.addWidget(detail)
        links=Card()
        links.hide()
        for row in pending:
            links.body.addWidget(_link(row['title'],row['url']))
        detail.toggled.connect(links.setVisible)
        self.body.addWidget(links)

    def _history(self):
        history = [row for row in self.page.context.civic.data['filings']
                   if row['plan_id']==self.plan['id']]
        if not history:
            return
        fold = Disclosure('历史报备与回执')
        fold.row.addWidget(label('历史报备与回执'),1)
        fold.add_arrow()
        self.body.addWidget(fold)
        contents = Card()
        contents.hide()
        for row in history:
            review=row.get('review')
            if review:
                summary=review.get('comparison')
                contents.body.addWidget(label(row['id'][:12]+' · '+review['outcome'], 'bodyText'))
                if summary:
                    contents.body.addWidget(label(f"最大横向 {summary['horizontal_m']:g} 米 · 累计偏离 {summary['duration_s']} 秒"))
            else:
                contents.body.addWidget(label(row['id'][:12]+' · '+row['state']))
        fold.toggled.connect(contents.setVisible)
        self.body.addWidget(contents)


class MyPlansPage(Page):
    title = '我的计划'
    uses_map = True
    plan_requested=Signal(object,bool)
    reschedule_requested=Signal(object)
    copy_requested=Signal(object)
    plan_cancelled=Signal(str)
    supplement_requested=Signal(object,object)
    repeat_requested=Signal(object)
    log_requested=Signal(object)

    def __init__(self, context):
        super().__init__(context)
        panel = Card()
        self.scroll,self.list_body = scroll_contents()
        panel.body.addWidget(self.scroll,1)
        self.list_panel = panel
        row=QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(panel,1)
        self.results = PhotoResults(context)
        self.results.back.connect(self.close_results)
        self.results.map_requested.connect(self.result_map)
        self.results.locate.connect(self._locate_result)
        self.results.supplement.connect(self.supplement_requested)
        self.results.repeat_requested.connect(self.repeat_requested)
        row.addWidget(self.results.left)
        self.map_holder = QWidget()
        self.map_holder.setMinimumWidth(420)
        self.map_slot = QVBoxLayout(self.map_holder)
        self.map_slot.setContentsMargins(0,0,0,0)
        self.center = QStackedWidget()
        map_and_return = QWidget()
        map_controls = QVBoxLayout(map_and_return)
        map_controls.setContentsMargins(0,0,0,0)
        self.back_to_media = ActionButton('返回素材预览', lambda: self.result_map(False))
        self.back_to_media.hide()
        map_controls.addWidget(self.back_to_media,0)
        map_controls.addWidget(self.map_holder,1)
        self.center.addWidget(map_and_return)
        self.center.addWidget(self.results.center)
        row.addWidget(self.center,2)
        row.addWidget(self.results.right)
        self.results.left.hide()
        self.results.right.hide()
        self.body.addLayout(row,1)
        self.in_results = False
        self.result_point = None
        self.cards=[]
        self.active=None

    def open_results(self, plan):
        self.in_results = True
        self.active = plan
        self.result_point = None
        self.results.load(plan)
        self.list_panel.hide()
        self.results.left.show()
        self.results.right.show()
        self.back_to_media.show()
        self.result_map(self.results.mission['kind'] == 'travel')

    def close_results(self):
        self.results.player.stop()
        self.in_results = False
        self.result_point = None
        self.list_panel.show()
        self.results.left.hide()
        self.results.right.hide()
        self.back_to_media.hide()
        self.result_map(True)
        self.refresh()
        self.map_changed.emit()

    def result_map(self, shown):
        self.center.setCurrentIndex(0 if shown else 1)
        self.map_holder.setVisible(shown)
        self.map_changed.emit()
        if shown and self.result_point is not None and self.map_view and self.map_view.is_ready:
            self.map_view.focus_point(self.result_point)

    def _locate_result(self, point):
        self.result_point = point
        if not self.map_holder.isHidden() and self.map_view and self.map_view.is_ready:
            self.map_view.focus_point(point)

    def export_card(self, plan):
        filename, _ = QFileDialog.getSaveFileName(self, '导出现场任务卡', '低空守望任务卡.png', 'PNG 图片 (*.png)')
        if filename:
            if not filename.lower().endswith('.png'):
                filename += '.png'
            city = self.context.civic.cities()[plan['city']]['name']
            map_image = None
            if (self.active and self.active['id'] == plan['id'] and not self.map_holder.isHidden()
                    and self.map_view and self.map_view.is_ready):
                map_image = self.map_view.web.grab().toImage()
            def action():
                write_task_card(filename, plan, city, map_image)
                return True
            if self.context.safe(action):
                self.context.status.emit('现场任务卡已导出', 5000)

    def on_leave(self):
        self.results.player.stop()

    def select_plan(self, plan):
        self.active=plan
        for card in self.cards:
            card.set_selected(card.plan['id'] == plan['id'])
        self.map_changed.emit()
        if self.map_view and self.map_view.is_ready and plan['query'].get('route'):
            from ...flight_route import route_points
            self.map_view.focus_route(route_points(FlightRoute.parse(plan['query']['route'])))

    def focus_plan(self, identifier):
        card=next((card for card in self.cards if card.plan['id']==identifier),None)
        if card:
            self.select_plan(card.plan)
            self.scroll.ensureWidgetVisible(card,0,12)

    def map_payload(self):
        civic=self.context.civic
        city=self.active['city'] if self.active else self.context.public_city
        routes=[]
        if self.active and self.active['query'].get('route'):
            route=FlightRoute.parse(self.active['query']['route'])
            routes=[dict(display_route(route),style='flight',key=self.active['id']+route.start.isoformat())]
        pins = []
        if self.in_results and self.results.plan:
            actual = self.results.mission['materials']
            pins = [dict(lon=item['point'][0],lat=item['point'][1],name=item['name'])
                    for item in actual if item.get('point')][:100]
            if not pins:
                pins = [dict(lon=shot['point'][0],lat=shot['point'][1],name=shot['title'])
                        for shot in self.results.mission['shots']]
        return dict(city=city,center=civic.cities()[city]['center'],public=True,
                    observations=[],areas=[],grid=[],zones=[],routes=routes,pins=pins)

    def refresh(self):
        if self.in_results:
            return
        clear(self.list_body)
        civic=self.context.civic
        plans=sorted((plan for plan in civic.data['plans'] if not plan['cancelled']),
                     key=lambda plan:plan.get('saved_at',''),reverse=True)
        previous=self.active['id'] if self.active else None
        self.active=next((plan for plan in plans if plan['id']==previous),plans[0] if plans else None)
        self.map_holder.setVisible(bool(plans))
        self.cards=[]
        if not plans:
            self.list_body.addWidget(EmptyState('暂无飞行计划',description='画一条航线或从拍摄方案开始。',glyph='plan'),1)
            return
        self.list_body.addWidget(SectionHeader('本机飞行计划'))
        for plan in plans:
            card=PlanCard(plan,civic.cities()[plan['city']]['name'],self)
            card.set_selected(plan['id'] == self.active['id'])
            self.cards.append(card)
            self.list_body.addWidget(card)
        self.list_body.addStretch()

    def cancel_plan(self, identifier):
        if QMessageBox.question(self,'移出飞行计划','将这份计划移出列表？')!=QMessageBox.Yes:
            return

        def cancel():
            self.context.civic.hide_plan(identifier)
            return True
        if self.context.safe(cancel):
            self.plan_cancelled.emit(identifier)
            self.refresh()
            self.map_changed.emit()
