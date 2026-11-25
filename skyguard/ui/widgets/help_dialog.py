"""Workspace guide and scientific conventions."""
from PySide6.QtWidgets import QDialog, QHBoxLayout, QTabWidget, QVBoxLayout, QWidget

from .common import ActionButton, SectionHeader, label


GUIDE = (
    ('航拍助手', (
        ('切换工作模式', '点击顶栏「切换权限模式」并验证权限密码，即可进入「低空治理」。点击「切换普通模式」返回「航拍助手」。'),
        ('规划航拍航线', '选择城市与拍摄偏好，按模板生成航线，或在地图上自主布置起降点和航点。预演可检查机位、航段与镜头朝向。'),
        ('核对飞行条件', '查看日照与逐小时气象建议，按机型、相对真高和计划时段核对适飞空域及临时管制。起飞前通过官方入口复查。'),
        ('保存计划与日志', '在「我的计划」改期、复查条件或整理实拍素材。飞行结束后在「飞行日志」记录飞行情况。'),
    )),
    ('态势演练', (
        ('选择精选情景', '长沙演练滨江游客区，广州演练演出与校园周边，上海演练滨江展馆市集。地图和行动流按演练时间同步更新。'),
        ('选择协同模式', '自动处置会执行模拟调度。关键确认会请值班员确认派员或改派。点进度条上的事件刻度可定位突发时刻。'),
        ('查看值班助手', '在城市低空智能值班助手中查看当前重点、跟踪行动流，并询问异常、人员位置或处置原因。'),
    )),
    ('自由沙盘', (
        ('选址与编排', '在市中心 15 km 范围内搜索街区、探测周边实景，或载入本城示范考题。依次设置时空事件、无人机、人员与策略，最后启动推演。'),
        ('布设人员', '用本地规则获取布防建议，再选中人员预览有效核验半径。可在地图上拖动人员调整位置。'),
        ('压力测试与复盘', '推演时可注入应急突发。保存至少两套策略后，打开「复盘与策略对比」，按同一考题和即时确认口径查看 A/B 结果。'),
    )),
    ('科学口径与图例', (
        ('相对真高与适飞网格', '相对真高（AGL）是飞行器距正下方地面的垂直高度。真高 120 m 为适飞上限，仍须核对适用机型、空域边界和临时管制。'),
        ('坐标与地图符号', '经纬度按 WGS-84 保存。高德底图使用 GCJ-02，地图适配器负责坐标转换。点击地图顶栏「图例」查看当前场景的符号。'),
        ('覆盖与评分', '覆盖圈表示当前模拟天气下的有效核验半径。综合治理评分依据处置闭环、人群暴露和运力缺口等指标计算，用于比较同一考题。'),
        ('本机数据', '航拍计划、飞行日志与自定义沙盘预案均仅保存在本机。数据目录可在「设置」中查看。'),
    )),
)


class HelpDialog(QDialog):
    def __init__(self, parent=None, initial_tab=0):
        super().__init__(parent)
        self.setWindowTitle('低空守望 · 使用指南')
        self.resize(640, 480)
        body = QVBoxLayout(self)
        body.setContentsMargins(20, 16, 20, 16)
        body.setSpacing(12)
        self.tabs = QTabWidget()
        self.tabs.setObjectName('helpGuideTabs')
        body.addWidget(self.tabs, 1)
        for title, sections in GUIDE:
            page = QWidget()
            content = QVBoxLayout(page)
            content.setContentsMargins(20, 18, 20, 18)
            content.setSpacing(12)
            for heading, description in sections:
                section = QVBoxLayout()
                section.setSpacing(5)
                section.addWidget(SectionHeader(heading))
                section.addWidget(label(description, 'bodyText'))
                content.addLayout(section)
            content.addStretch()
            self.tabs.addTab(page, title)
        self.tabs.setCurrentIndex(initial_tab)
        actions = QHBoxLayout()
        actions.addStretch()
        actions.addWidget(ActionButton('关闭指南', self.accept, variant='secondary'))
        body.addLayout(actions)
