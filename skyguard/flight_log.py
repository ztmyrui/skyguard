"""Normalize flight records and calculate summaries in Beijing time."""
from collections import Counter
from math import isfinite

from .aerial import CHINA
from .flight_rules import instant

ISSUES = ('无', '天气', '信号', '人员干扰', '其他')


def validate_record(fields):
    start, end = (instant(fields[key]) for key in ('start', 'end'))
    altitude = float(fields['altitude'])
    if end <= start:
        raise ValueError('结束时间须晚于开始时间')
    if not isfinite(altitude) or not 0 < altitude <= 10000:
        raise ValueError('请填写 0 至 10000 米之间的实际最高高度')
    place, model = (str(fields.get(key, '')).strip() for key in ('place', 'model'))
    if not place or not model:
        raise ValueError('请填写地点和机型')
    issue = fields.get('issue', '无')
    if issue not in ISSUES:
        raise ValueError('请选择遇到的问题')
    return dict(start=start.isoformat(), end=end.isoformat(), altitude=altitude,
                place=place[:120], model=model[:100], issue=issue, plan_id=fields.get('plan_id') or None,
                note=str(fields.get('note', ''))[:2000], photo_note=str(fields.get('photo_note', ''))[:1000])


def statistics(records, now):
    month = now.astimezone(CHINA).strftime('%Y-%m')
    places = Counter(r['place'] for r in records)
    return {
        'month_count': sum(instant(r['start']).astimezone(CHINA).strftime('%Y-%m') == month for r in records),
        'seconds': sum((instant(r['end']) - instant(r['start'])).total_seconds() for r in records),
        'places': sorted(places.items(), key=lambda pair: (-pair[1], pair[0]))[:3],
        'issue_share': sum(r['issue'] != '无' for r in records) / len(records) if records else 0,
    }
