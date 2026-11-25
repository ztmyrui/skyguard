"""Build the shared AI/template sections from a minimized flight summary."""
from dataclasses import dataclass
import re

from .flight_rules import instant
from .flight_route import FlightRoute

SECTIONS = ('申报要点', '风险与应对', '请求事项')
DECLARATION = '飞行许可和审批请到 UOM 核对。'
URL = re.compile(r'https?://[^\s，。；、！）]+', re.I)
DOCUMENT_ID = re.compile(r'(?:批复|许可|批准|审批|文号)(?:编号|文号|号)?[：:\s]*[A-Z0-9〔\[（(第][A-Z0-9〔\[（(〕\]）)\-号]{2,}|[\u4e00-\u9fff]{1,12}[〔\[（(]20\d{2}[〕\]）)]\s*\d+\s*号', re.I)


@dataclass(frozen=True)
class Material:
    sections: tuple[str, str, str]
    ai: bool = False

    @property
    def text(self):
        return '\n\n'.join([*(f'{title}\n{text}' for title, text in zip(SECTIONS, self.sections)), DECLARATION])


def facts_for(city, query, result, purpose='', states=(), notices=()):
    q = query.record() if hasattr(query, 'record') else query
    form = q['form']
    area = (f'{city}，圆形范围，半径 {q["radius_m"]} 米' if form == 'circle' else
            f'{city}，航线共 {len(q["points"])} 点，单侧 {q["radius_m"]} 米' if form == 'route' else
            f'{city}，多边形共 {len(q["points"])} 个顶点')
    route = FlightRoute.parse(q['route']) if q.get('route') else None
    window = route.filing_window if route else (instant(q['start']), instant(q['end']))
    return {'城市': city, '时段': [value.isoformat() for value in window],
            '范围概述': area, '高度': q['altitude_agl'], '机型': q['category'],
            '目的': str(purpose).strip()[:200], '结论': str(result),
            '核对事项': list(states), '相关公告': list(notices)[:5]}


def template(facts):
    begin, end = (instant(x).strftime('%Y-%m-%d %H:%M') for x in facts['时段'])
    essential = (f'{begin} 至 {end}\n{facts["范围概述"]}\n最高离地 {facts["高度"]} 米，'
                 f'{facts["机型"]}，用途 {facts["目的"] or "待填写"}\n联系方式 待填写')
    attention = facts['结论'] or '空域待核对'
    notices = '、'.join(facts['相关公告'])
    risks = f'{attention}。出发前复核天气、现场情况和 UOM。'
    if notices:
        risks += f'请核对公告 {notices}。'
    return Material((essential, risks, '请场地方确认活动范围、时间及联络方式。'))


def verified_material(reply, facts):
    baseline = template(facts)
    if not isinstance(reply, dict) or not any(isinstance(reply.get(key), str) for key in SECTIONS):
        raise ValueError('报备说明格式有误')
    supplied = str(facts)
    accepted = []
    changed = False
    for label, fallback in zip(SECTIONS, baseline.sections):
        text = reply.get(label)
        if (isinstance(text, str) and 0 < len(text.strip()) <= 750
                and all(value in supplied for value in URL.findall(text) + DOCUMENT_ID.findall(text))):
            accepted.append(text.strip().replace(DECLARATION, '').strip() or fallback)
            changed = changed or accepted[-1] != fallback
        else:
            accepted.append(fallback)
    return Material(tuple(accepted), changed)
