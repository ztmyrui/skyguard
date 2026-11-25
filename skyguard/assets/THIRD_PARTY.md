# 地图、字体和组件来源

## 高德地图

应用底图使用高德地图 JS API 和高德官方坐标转换服务。
官方在线核对入口直接打开天地图·湖南的网页。
参考机位的部分地物几何来自 OpenStreetMap，© OpenStreetMap contributors，ODbL 1.0。
来源：https://www.openstreetmap.org/copyright

## 字体与组件

- Noto Sans SC，来自google/fonts官方仓库ofl/notosanssc，SIL Open Font License 1.1；字体及NotoSansSC-LICENSE.txt随包提供。
- PySide6/Qt及其他Python依赖保留各自许可，独立程序目录中附第三方许可资料。
- 应用SVG图标与控制图标由本项目原创绘制，不复制参考截图素材。

虚拟无人机、目击、人员和活动由本项目编写并标记为演练；没有使用旧赛题10批无人机、地形、武器或派生结果。

## 公众航拍数据

- 和风天气逐小时预报，依本机账号的 JWT 和套餐调用，https://www.qweather.com/ 。
- Open-Meteo 高度层风速，https://open-meteo.com/ ，免费托管端点适用于非商业使用。
- 文旅景点名称及公开来源见 `scenic-catalog.json`，坐标表示用于取景规划的参考点。在线补充候选通过高德地点搜索并对照文旅出处。
- 航拍太阳位置计算参考 NOAA Global Monitoring Laboratory 的太阳位置公式，https://gml.noaa.gov/grad/solcalc/solareqns.PDF 。


## v0.16.0 操作优化

底图沿用高德 JSAPI 与官方 normal、darkblue 样式。空条件推荐沿用本机文旅机位目录。新增地图视锥、航点按钮和任务卡示意图由程序绘制。
