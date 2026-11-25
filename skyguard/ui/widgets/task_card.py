"""Export a portable PNG with the selected flight and its shot list."""
from datetime import timedelta
from math import floor, hypot, log10

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen, QPolygonF

from ...aerial import local_metres
from ...flight_route import FlightRoute, route_points
from ...photo_mission import mission_for, shot_altitude
from ...theme import themed_font


def write_task_card(filename, plan, city, map_image=None):
    route = FlightRoute.parse(plan['query']['route'])
    mission = plan.get('mission') or mission_for(route, 'travel', '', '')
    image = QImage(1000, 775 + len(mission['shots']) * 42, QImage.Format_RGB32)
    image.fill(QColor('#f4f6fb'))
    painter = QPainter(image)
    ink, accent = QColor('#141b33'), QColor('#2447cd')
    painter.setRenderHint(QPainter.Antialiasing)
    painter.fillRect(QRectF(0, 0, 1000, 109), accent)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor('#ffffff'))
    painter.drawRoundedRect(QRectF(43, 114, 914, 145), 14, 14)
    painter.setPen(QColor('#ffffff'))
    painter.setFont(themed_font(24, QFont.Bold))
    painter.drawText(QRectF(62, 45, 875, 55), Qt.TextSingleLine, plan['title'])
    painter.setPen(ink)
    painter.setFont(themed_font(15))
    local = route.start - timedelta(minutes=15)
    painter.drawText(62, 143, f'{city} · {route.start:%Y-%m-%d} · 建议到场 {local:%H:%M} · 北京时间')
    painter.drawText(62, 169, f'飞行 {route.start:%H:%M:%S}–{route.end:%H:%M:%S} · 最高 {max(w.altitude_m for w in route.waypoints):.0f} 米')
    painter.drawText(62, 205, f'起飞点 {route.takeoff[0]:.6f}, {route.takeoff[1]:.6f} · WGS84')
    state = plan.get('result') or '飞行条件待查询'
    pill = QRectF(62, 218, min(870, painter.fontMetrics().horizontalAdvance(state) + 24), 29)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor('#e8ecfd'))
    painter.drawRoundedRect(pill, 14, 14)
    painter.setPen(accent)
    painter.drawText(pill, Qt.AlignCenter, state)
    rect = QRectF(62, 275, 876, 360)
    if map_image is not None and not map_image.isNull():
        scaled = map_image.scaled(876, 360, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        painter.drawImage(QPointF(62+(876-scaled.width())/2, 275), scaled)
    else:
        painter.fillRect(rect, QColor('#e4ebfb'))
        project, _ = local_metres(route.takeoff)
        values = [project(*point) for point in route_points(route)]
        if values[0] != project(*route.takeoff):
            values.insert(0, project(*route.takeoff))
        xs, ys = zip(*values)
        scale = min(735/max(max(xs)-min(xs), 20), 242/max(max(ys)-min(ys), 20))
        middle_x, middle_y = (min(xs)+max(xs))/2, (min(ys)+max(ys))/2
        points = QPolygonF([QPointF(rect.center().x()+(x-middle_x)*scale,rect.center().y()-(y-middle_y)*scale)
                            for x,y in values])
        painter.setPen(QPen(accent, 5))
        painter.drawPolyline(points)
        painter.setPen(Qt.NoPen)
        painter.setBrush(accent)
        for a, b in zip(points, list(points)[1:]):
            dx, dy = b.x() - a.x(), b.y() - a.y()
            length = hypot(dx, dy)
            if length < 35:
                continue
            ux, uy = dx / length, dy / length
            tip = QPointF((a.x() + b.x()) / 2 + 7 * ux, (a.y() + b.y()) / 2 + 7 * uy)
            base = QPointF(tip.x() - 14 * ux, tip.y() - 14 * uy)
            painter.drawPolygon(QPolygonF([tip, base + QPointF(-7 * uy, 7 * ux),
                                           base + QPointF(7 * uy, -7 * ux)]))
        painter.setFont(themed_font(11, QFont.Bold))
        for index, waypoint in enumerate(route.waypoints, 1):
            x, y = project(*waypoint.xy)
            point = QPointF(rect.center().x() + (x-middle_x)*scale, rect.center().y() - (y-middle_y)*scale)
            painter.setPen(QPen(QColor('#ffffff'), 1))
            painter.setBrush(accent)
            painter.drawEllipse(point, 11, 11)
            painter.drawText(QRectF(point.x()-11, point.y()-11, 22, 22), Qt.AlignCenter, str(index))
        takeoff = points[0] + QPointF(-15, 13)
        painter.setBrush(QColor('#107957'))
        painter.drawEllipse(takeoff, 11, 11)
        painter.drawText(QRectF(takeoff.x()-11, takeoff.y()-11, 22, 22), Qt.AlignCenter, '起')
        painter.setFont(themed_font(13))
        painter.setPen(ink)
        painter.drawText(84, 307, '航线示意图 · 上方为北')
        painter.drawText(84, 613, '起  起降点    1、2…  航点    →  航段方向')
        unit = 10 ** floor(log10(125 / scale))
        metres = max(value * unit for value in (1, 2, 5, 10) if value * unit <= 130 / scale)
        width = round(metres * scale)
        painter.drawLine(905-width, 615, 905, 615)
        for end in (905-width, 905):
            painter.drawLine(end, 610, end, 620)
        painter.drawText(QRectF(770, 586, 135, 22), Qt.AlignRight, f'约 {metres:g} 米')
    painter.setPen(ink)
    painter.setPen(QPen(QColor('#cbd5ed'), 1))
    painter.drawLine(62, 659, 938, 659)
    painter.setPen(ink)
    painter.setFont(themed_font(18, QFont.Bold))
    painter.drawText(62, 691, '拍摄清单')
    painter.setFont(themed_font(15))
    for index, shot in enumerate(mission['shots'], 1):
        bearing = f" · 朝向 {shot['heading']:.0f}°" if shot['heading'] is not None else ''
        pitch = f" · 云台 {shot['pitch']:.1f}°" if shot.get('pitch') is not None else ''
        move = f" · {shot['move']}" if shot.get('move') else ''
        y = 708 + index * 42
        painter.drawText(QRectF(62, y, 876, 22), Qt.TextSingleLine,
                         f"{index:02d}   {shot['title']}{move} · {shot_altitude(shot)} 米{bearing}{pitch} · 录 {shot['record_s']} 秒")
        if shot.get('tip'):
            painter.drawText(QRectF(98, y + 22, 840, 20), Qt.TextSingleLine, shot['tip'])
    painter.end()
    if not image.save(str(filename), 'PNG'):
        raise ValueError('任务卡未能保存，请选择可写入的位置')
