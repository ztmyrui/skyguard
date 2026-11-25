import argparse
import sys
import time
import json
import cProfile
import pstats
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from skyguard.domain import demo,snapshot,impact_grid,build_plans
from skyguard.aerial import Forecast, CHINA
from skyguard.aerial_plan import recommend
from skyguard.flight_check import Verdict
from skyguard.flight_route import planned_position, tracking
from datetime import datetime, timedelta

parser = argparse.ArgumentParser(description='Measure the local exercise algorithms.')
parser.add_argument('output', type=Path, nargs='?',
                    default=Path(__file__).resolve().parents[1] / 'build' / 'profile')
output = parser.parse_args().output

scene=demo();samples=[]
profile=cProfile.Profile();profile.enable()
for i in range(1000):
    scene.clock=i%600;t=time.perf_counter()
    for o in scene.observations:snapshot(scene,o)
    impact_grid(scene)
    samples.append((time.perf_counter()-t)*1000)
profile.disable();samples.sort()
start=time.perf_counter()
for i in range(100):build_plans(scene,'obs-a')
plan_ms=(time.perf_counter()-start)*10
output.mkdir(parents=True, exist_ok=True)
result={'scope':'3 observations, 3 activity areas, local core only; no model/network/UI rendering',
        'iterations':1000,'frame_core_p50_ms':round(samples[500],3),'frame_core_p95_ms':round(samples[950],3),
        'plan_mean_ms_3_people':round(plan_ms,3),'note':'本机算法小样本计时，不是城市规模性能承诺'}
start_time = datetime(2026, 11, 21, 10, tzinfo=CHINA)
origin = (112.954, 28.185)
route = tracking(origin, 90, 300, 70, start=start_time, category='轻型')
samples = []
for i in range(1000):
    t = time.perf_counter()
    planned_position(route, start_time + timedelta(seconds=i % 60))
    samples.append((time.perf_counter()-t)*1000)
samples.sort()
result['planned_position_p95_ms'] = round(samples[950], 3)
start = time.perf_counter()
for i in range(10):
    recommend('changsha', ('江河','日出日落'), start_time, start_time+timedelta(days=2),
              '轻型', origin, lambda _: Forecast((), start_time), lambda _: Verdict.CHECK, 8)
result['aerial_recommendation_mean_ms'] = round((time.perf_counter()-start)*100, 3)
from copy import deepcopy
from skyguard.training_tracks import FLAGSHIPS, decode_scenario, example, TrainingFrames
training=decode_scenario(example('风漂后修正'))
track=training['tracks'][0]
training['tracks']=[dict(deepcopy(track),id=f'T{i:03d}') for i in range(100)]
frames=TrainingFrames(training)
frames.frame(0)
start=time.perf_counter()
batch=frames.frame(1)
result['training_100_objects_ms']=round((time.perf_counter()-start)*1000,3)
result['training_object_count']=len(batch)
from skyguard.situation_agent import analyse_target, detect_formations
flagship=TrainingFrames(decode_scenario(example(next(iter(FLAGSHIPS)))))
flagship.frame(30)
agent_samples=[]
for second in range(31,91):
    start=time.perf_counter()
    active=flagship.frame(second)
    areas=flagship.zones_at(second)
    detect_formations(active,areas)
    for obj in active:
        analyse_target(obj,areas)
    agent_samples.append((time.perf_counter()-start)*1000)
result['flagship_five_aircraft_p95_ms']=round(sorted(agent_samples)[56],3)
from skyguard.civic_replay import CivicReplay
from skyguard.training_city_cases import CIVIC_CASES

civic_samples = []
for name in CIVIC_CASES:
    replay = CivicReplay(decode_scenario(example(name)), 'auto')
    for second in range(121):
        start = time.perf_counter()
        replay.layers(second)
        civic_samples.append((time.perf_counter() - start) * 1000)
result['civic_replay_frames'] = len(civic_samples)
result['civic_replay_p95_ms'] = round(sorted(civic_samples)[int(len(civic_samples) * .95)], 3)
from skyguard.flight_rules import FlightQuery
from skyguard.outlook import review
from skyguard.training_tracks import BEGIN, HOME
from skyguard.zone_catalog import zone_from_record
future_start=BEGIN+timedelta(hours=1)
plans=[]
for i in range(5):
    point=(HOME[0],HOME[1]+i*.00003)
    path=tracking(point,90,220,70,start=future_start,category='轻型')
    query=FlightQuery((),'route',20,path.start,path.end,70,'轻型',demo=False,route=path)
    plans.append(dict(id=f'F{i}',state='已报备',snapshot=dict(query=query.record(),title=f'航线{i}',city='changsha')))
zones=[zone_from_record(dict(id='sample',name='演练公园',kind='park',point=HOME,radius=140))]
t=time.perf_counter()
hints=review(BEGIN,plans,zones,lambda *_:None)
result['outlook_5_routes_ms']=round((time.perf_counter()-t)*1000,3)
result['outlook_hint_count']=len(hints)
(output/'性能检查.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
with (output/'性能热点.txt').open('w',encoding='utf8') as f:pstats.Stats(profile,stream=f).sort_stats('cumulative').print_stats(12)
print(json.dumps(result))
