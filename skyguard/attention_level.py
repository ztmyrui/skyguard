"""Versioned, explainable attention levels for low-altitude civil observation."""
from dataclasses import dataclass

from .zone_catalog import read_table


@dataclass(frozen=True)
class Attention:
    level: int
    score: float
    trend: str
    detail: tuple
    since: int | None
    version: str

    @property
    def label(self):
        return ('留意','关注','优先核验','立即协调')[self.level-1]


def assess(facts, second, previous=None, *, rules=None, override=None):
    rules = rules or read_table('attention-rules.json')
    base = 3 if facts.get('rating') == 'S1' or facts.get('near_s1') else 2 if facts.get('rating') == 'S2' else 1
    detail = [('A00', f'区域起评 {base} 级')]
    additions = [(key, text, delta) for key,text,flag,delta in rules['add'] if facts.get(flag)]
    reductions = [(key, text, delta) for key,text,flag,delta in rules['subtract'] if facts.get(flag)]
    for group in (additions,reductions):
        if group:
            key,text,value = max(group,key=lambda row:row[2])
            detail.append((key,text))
    target = min(4,max(1,base+max((r[2] for r in additions),default=0)-max((r[2] for r in reductions),default=0)))
    direct, since = False, None
    if facts.get('restricted') or facts.get('airport_core') and facts.get('unfiled'):
        target, direct = 4, True
        detail.append(('A40','进入生效限制范围'))
    if facts.get('verified_on_site'):
        target, direct = 1, True
        detail.append(('A41','到场核实结束'))
    if facts.get('verified_exit') and previous and previous.level>=3 and not direct:
        target, direct = max(target,2), True
        detail.append(('A43','观察岗与当前航迹确认已离开敏感区'))
    if facts.get('exercise_resolved') and not direct:
        target, direct = 1, True
        detail.append(('A44','演练航迹确认已恢复合规或完成降落'))
    if facts.get('insufficient') and previous and not direct:
        target=max(target,previous.level)
        detail.append(('A42','资料待核对'))
    if previous and target < previous.level and not direct:
        since = previous.since if previous.since is not None else second
        distance = max(0, second-since)
        if distance < rules['hold_seconds']:
            target=previous.level
        else:
            target=previous.level-1
            since=None
        detail.append(('A30','等级按 30 秒保持规则更新'))
    if override:
        value,reason=override
        if value not in range(1,5) or not reason.strip():
            raise ValueError('请选择等级并填写调整原因')
        target,since=value,None
        detail.append(('A99','人工调整 · '+reason[:120]))
    weights=rules['weights']
    strength=sum(w*min(1,max(0,float(facts.get(k,0)))) for k,w in weights.items())
    score=(target-1)*25+25*max(0,min(.9999,strength))
    trend='—' if previous is None else '↑' if score-previous.score >= 2 else '↓' if previous.score-score >= 2 else '—'
    return Attention(target,round(score,2),trend,tuple(detail),since,rules['version'])
