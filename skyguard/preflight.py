"""Prepare a flight checklist using known authority links."""
from dataclasses import dataclass

from .flight_check import Verdict
from .official_airspace import ORDINANCE

UOM = 'https://uom.caac.gov.cn/'
TITLE = '《无人驾驶航空器飞行管理暂行条例》'


@dataclass(frozen=True)
class Preparation:
    id: str
    title: str
    detail: str
    required: bool = False
    reference: str = ''
    url: str = ''


def checklist(category, model, height, night, special, verdict):
    items = []
    if verdict == Verdict.RESTRICTED:
        items.append(Preparation('adjust', '调整飞行计划', '调整范围或时段后重新查飞。', True, 'UOM', UOM))
    elif verdict == Verdict.PERMIT or height > 120:
        items.append(Preparation('application', '办理飞行活动申请', '在 UOM 核对申请条件和进度。', True, 'UOM', UOM))
    else:
        items.append(Preparation('uom', '到 UOM 核对', '核对实时空域、时段和审批要求。', True, 'UOM', UOM))
    items.append(Preparation('register', '完成实名登记', '核对无人机的实名登记状态。', True, TITLE + '第十条', ORDINANCE))
    if category in ('小型', '中型', '大型'):
        items.extend((
            Preparation('license', '核对操控员执照', '准备适用的操控员执照及证件。', True, TITLE + '第十六条', ORDINANCE),
            Preparation('insurance', '核对责任保险', '核对责任保险和活动的承保范围。', True, TITLE + '第十二条', ORDINANCE),
        ))
    else:
        items.extend((
            Preparation('qualification', '核对飞手资质', '核对机型、飞行场景对应的培训与资质要求。', True, TITLE + '第十六、十七条', ORDINANCE),
            Preparation('insurance', '核对责任保险', '经营性飞行须投保责任保险。其他情况按活动核对。', False, TITLE + '第十二条', ORDINANCE),
        ))
    if special:
        items.append(Preparation('special', '核对特殊情形', '请核对最新规定。', True, 'UOM', UOM))
    if night:
        items.append(Preparation('lights', '检查夜间灯光', '检查识别灯，核对夜间飞行最新规定。', True, 'UOM', UOM))
    if not model:
        items.append(Preparation('model', '确认具体机型', '核对设备手册和抗风条件。'))
    items.extend((
        Preparation('documents', '备好证件', '随身备好适用的证件、登记和申请资料。'),
        Preparation('field', '核对现场与视距', '保持视距，避开人群，主动避让载人航空器。', True),
        Preparation('equipment', '检查电池和设备', '检查螺旋桨、控制链路、电量和返航设置。', True),
        Preparation('weather', '复核天气', '起飞前复核风、降雨和能见度。', True),
    ))
    return tuple(items)


def progress(items, checked):
    required = {item.id for item in items if item.required}
    return len(required.intersection(checked)), len(required)
