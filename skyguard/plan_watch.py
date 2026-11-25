"""Compare public notice scans with a saved plan without mutating either."""
from .flight_rules import instant


def due_cities(plans, now):
    return {p['city'] for p in plans if not p.get('cancelled') and not p['query'].get('demo')
            and instant(p['query']['end']) > now}


def update_notice_watch(plan, scan, now):
    saved = plan.get('watch', {})
    if not scan.checked or scan.inaccessible or scan.stale or plan['city'] not in due_cities((plan,), now):
        return saved, ()
    start, end = (instant(plan['query'][key]) for key in ('start', 'end'))
    prior = saved.get('fingerprint', {})
    alerts = list(saved.get('alerts', ()))
    seen = {item['url'] for item in alerts}
    changes = []
    fingerprint = dict(prior)
    for notice in scan.notices:
        periods = [[left.isoformat(), right.isoformat()] for left, right in notice.periods]
        overlap = any(left < end and start < right for left, right in notice.periods)
        match = '时段重叠' if overlap else '时段待核对' if not periods else '时段未重叠'
        current = {'title': notice.title, 'periods': periods, 'match': match}
        before = prior.get(notice.url)
        if notice.url not in seen and (overlap if before is None else before['periods'] != periods):
            changes.append({'title': notice.title, 'url': notice.url, 'match': match, 'read': False,
                            'at': (scan.at or now).isoformat()})
            seen.add(notice.url)
        fingerprint[notice.url] = current
    return {'fingerprint': fingerprint, 'alerts': alerts + changes,
            'checked_at': (scan.at or now).isoformat(),
            'sources': list(scan.checked)}, tuple(changes)
