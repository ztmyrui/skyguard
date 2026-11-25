"""Civilian training actions, local reminders, and review summaries."""
from collections import Counter
from copy import deepcopy
from dataclasses import dataclass
from math import ceil

from .flight_route import WGS84,destination
from .zone_catalog import read_table


OUTCOMES=('合规飞行','轻微偏离已纠正','违规已劝离','失联已找回','误报','已移交')
REPLIES=('正在处理','已返航','设备异常')


def recommendation(level, filed, restricted=False):
    if not 1<=level<=4:
        raise ValueError('等级须为 1 至 4')
    if restricted or level==4:
        group='紧急协调'
    elif level==3 or level==2 and not filed:
        group='现场核验'
    elif level==2:
        group='提醒'
    else:
        group='观察'
    table=read_table('disposal-plans.json')
    return group,tuple((key,title,policy) for key,title,policy in table['templates'][group])


def feedback(actions):
    kinds={row['kind'] for row in actions if row['state']=='已完成'}
    results={row.get('result') for row in actions if row['state']=='已完成'}
    return dict(unreachable='联系不上' in results,responded='正在处理' in results,
                return_ack='已返航' in results,verified_on_site='合规飞行' in results and 'close' in kinds)


def apply(actions, target, kind, at, *, source='值班员确认', result='', **detail):
    if not target or at<0:
        raise ValueError('请选择演练对象和时刻')
    output=deepcopy(actions)
    repeat=next((row for row in output if row['target']==target and row['kind']==kind
                 and row['state'] in ('进行中','待办')),None)
    if repeat and kind not in ('reminder_reply','call','close'):
        return output,repeat
    state= '进行中' if kind in ('watch','dispatch','request','notify','search','trigger') else '已完成'
    row=dict(id=max((item['id'] for item in output), default=0)+1,target=target,kind=kind,at=at,state=state,source=source,
             result=result,**detail)
    output.append(row)
    return output,row


def observe_point(posts, shared_point=None):
    """Choose a preset public observation post. Use a shared point only for ranking."""
    available=[post for post in posts if post.get('enabled',True)]
    if not available:
        raise ValueError('请先设置可用观察岗')
    def rank(post):
        if not shared_point:
            return 0,post['id']
        d=WGS84.Inverse(shared_point[1],shared_point[0],post['lat'],post['lon'])['s12']
        return d,post['id']
    post=min(available,key=rank)
    return (post['lon'],post['lat']),post['name']


def walking(start, finish, speed=1.25):
    line=WGS84.Inverse(start[1],start[0],finish[1],finish[0])
    return dict(start=list(start),finish=list(finish),metres=round(line['s12'],1),
                seconds=max(1,ceil(line['s12']/speed)),heading=line['azi1'],basis='直线步行估算')


def movement(record, when):
    route=record.get('walk')
    if not route or when<record['at']:
        return None
    stop=record.get('finished_at') if record.get('state')=='已取消' else None
    if stop is not None and when>=stop:
        return None
    factor=min(1,max(0,(when-record['at'])/route['seconds']))
    return destination(route['start'],route['heading'],route['metres']*factor)


def simulated_landing(sample, wind=None, *, descent=1.5):
    if sample is None:
        raise ValueError('请先记录模拟位置')
    direction,speed=wind or (0,0)
    duration=max(0,sample.get('battery',0))+max(0,sample['height'])/descent
    center=destination((sample['lon'],sample['lat']),direction,duration*max(0,speed))
    radius=max(50,round(.30*duration*max(0,speed),1))
    return dict(point=list(center),radius_m=radius,seconds=round(duration),basis='演练风况估算' if wind else '演练无风估算')


def fork_at(scenario, actions, second):
    branch=deepcopy(scenario)
    branch['name']=f'{scenario["name"]} · {second}s 分支'
    # The recorded exercise is the shared stimulus. Only the action history branches.
    forked=[]
    for item in actions:
        if item['at']>second:
            continue
        copy=deepcopy(item)
        if copy.get('finished_at',-1)>second:
            copy.update(state='进行中',result='')
            copy.pop('finished_at',None)
        forked.append(copy)
    return branch,forked


def summarize(actions):
    done=[a for a in actions if a['kind']=='close']
    counts=Counter(r['result'] for r in done)
    times=[]
    for row in done:
        before=[item['at'] for item in actions if item['target']==row['target'] and item['at']<=row['at']]
        times.append(row['at']-min(before,default=row['at']))
    return dict(events=len({row['target'] for row in actions}),counts=dict(counts),
                average=round(sum(times)/len(times),1) if times else None,
                pending=sorted({row['target'] for row in actions}-{row['target'] for row in done}))
