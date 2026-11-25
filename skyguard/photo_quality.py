"""Read the same evidence for suggested and hand-drawn photo flights."""
from dataclasses import dataclass, replace
from datetime import timedelta
from functools import lru_cache
from math import cos, radians

from .aerial import assess_weather, piecewise, sun_position
from .flight_route import WGS84, timeline
from .photo_mission import camera_at


@dataclass(frozen=True)
class PhotoQuality:
    forecast: object
    weather: float | None
    light: float
    framing: float | None
    reasons: tuple[str, str, str]

    @property
    def components(self):
        return (('天气', self.weather), ('光线', self.light), ('取景', self.framing))

    @property
    def score(self):
        if self.weather is None or self.framing is None or self.forecast.hazard:
            return None
        return .45 * self.weather + .25 * self.light + .30 * self.framing


@lru_cache(maxsize=144)
def _camera_samples(route):
    pieces = timeline(route)
    shots = [piece for piece in pieces if piece.shot]
    if not shots:
        shots = [piece for piece in pieces if piece.phase in ('进场','转场','悬停')] or list(pieces)
    return [((piece.begin_s + (piece.end_s-piece.begin_s)*fraction), piece.at(fraction),
             camera_at(piece,fraction))
            for piece in shots[:60] for fraction in (.2,.5,.8)]


@lru_cache(maxsize=512)
def _target_line(xy, subject):
    result = WGS84.Inverse(xy[1],xy[0],subject[1],subject[0])
    return result['azi1'] % 360, result['s12']


def score_photo(query, center, forecast, start, ceiling, style, subject=None, *, sample_route=None):
    base = assess_weather(forecast, center, query, start, ceiling, style)
    rows, causes = [], []
    for time, hour, _, heights in base.rows:
        value, cause = None, ''
        if hour:
            gust = hour.surface_gust_10m if hour.surface_gust_10m is not None else hour.gust
            if all(v is not None for v in (gust, hour.rain_probability, hour.visibility_km)):
                wind = piecewise(gust, ((3,100),(6,75),(9,35),(12,0)))
                rain = 100*(1-hour.rain_probability)
                visibility = piecewise(hour.visibility_km, ((1,0),(4,50),(10,100)))
                value = .50*wind + .30*rain + .20*visibility
                cause = min(((wind, f'阵风 {gust:g} 米/秒'),(rain, f'降水 {hour.rain_probability:.0%}'),
                             (visibility, f'能见度 {hour.visibility_km:g} 公里')),key=lambda pair:pair[0])[1]
        rows.append((time,hour,value,heights))
        causes.append(cause)
    scored = [index for index, row in enumerate(rows) if row[2] is not None]
    weather = None
    technical = ('选择具体机型以计算天气分','计划高度风况或关键天气资料不足')
    weather_note = base.status if base.status and base.status not in technical else '部分小时缺少阵风、降水或能见度'
    if scored and len(scored) == len(rows):
        worst = min(scored,key=lambda index:rows[index][2])
        stamp, _, weather, _ = rows[worst]
        weather_note = f'{stamp:%m-%d %H:%M} · {causes[worst]}（地面天气）'
    status = '' if weather is not None else weather_note

    samples = _camera_samples(sample_route or query.route) if query.route else ()
    if not samples:
        samples = ((0, None, None),)
    illumination, framing = [], []
    light_labels = []
    for offset, point, camera in samples:
        xy = point.xy if point else center
        angle, sun = sun_position(xy, start + timedelta(seconds=offset))
        if subject and point:
            target, distance = _target_line(xy,tuple(subject))
            heading = target if camera is None else camera
            error = abs((target-heading+180)%360-180)
            placement = piecewise(distance, ((0,45),(40,85),(160,100),(1000,95),(2400,45),(4000,10)))
            framing.append(.70 * piecewise(error,((0,100),(15,90),(35,50),(65,0))) + .30*placement)
        else:
            heading = camera
        difference = abs((sun-heading+180)%360-180) if heading is not None else None
        label = ('逆光' if difference < 55 else '顺光' if difference > 125 else '侧光') if difference is not None else '太阳高度'
        light_labels.append(label)
        if style in ('日出','日落'):
            period = (sun < 180) == (style == '日出')
            elevation = piecewise(angle,((-8,0),(-2,45),(0,90),(5,100),(10,75),(18,5))) if period else 0
            intent = 100 if difference is None else piecewise(difference, ((0, 72), (45, 100), (90, 68), (180, 10)))
        elif style == '夜景':
            elevation = piecewise(angle,((-18,100),(-6,100),(0,55),(8,0)))
            intent = 100
        else:
            elevation = piecewise(angle,((-2,0),(8,55),(23,100)))
            intent = 85 if difference is None else 55-45*cos(radians(difference))
        illumination.append(.85*elevation+.15*intent)
    light = sum(illumination)/len(illumination)
    scenic = sum(framing)/len(framing) if framing else None
    framing_note = ('按主体方向和距离估算，遮挡待核对' if scenic is not None else
                    '在地图上标出拍摄主体，即可补评取景')
    light_note = ('按日夜与太阳高度评估' if style == '夜景' else
                  f'主要镜头{max(light_labels,key=light_labels.count)} · 太阳位置估算')
    corrected = replace(base, rows=tuple(rows), weather=weather, light=light, status=status)
    return PhotoQuality(corrected, weather, light, scenic, (weather_note,light_note,framing_note))
