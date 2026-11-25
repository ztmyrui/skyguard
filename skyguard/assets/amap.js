let bridge=null,map=null,payload=null,selected='',showRisk=true,running=false,pending=null,overlays=[],ready=false,homeCenter=null,fitted=false;
let gridOverlays=[], gridVersion=0, viewTimer;
const cache=new Map(),statusNode=document.getElementById('status');let readyTimer;
let routeViewport=null,staticKey='';
let routeFocus='', focusRouteRevision=0;
let sandboxViewport=null, sandboxPin=null, sandboxPinTimer, pointFocusRevision=0;
let draftOverlays=[],draftKey='',homeZoom=15;
const mapStyles={night:'amap://styles/darkblue',klein:'amap://styles/normal'};
const root=document.documentElement;
let theme=root.dataset.theme==='night'?'night':'klein';
function mapColor(role){return getComputedStyle(root).getPropertyValue('--'+role).trim()}
function setTheme(name){
 const next=name==='night'?'night':'klein';
 const changed=next!==theme;
 theme=next;root.dataset.theme=theme;
 if(map)map.setMapStyle(mapStyles[theme]);
 if(changed&&ready&&payload){
  drawDraft(payload);
  updateScene(payload);
 }
}
function safeCode(value){const s=String(value||'').toUpperCase();const known=s.match(/(?:USERKEY_PLAT_NOMATCH|INVALID_USER_(?:KEY|SCODE|DOMAIN|IP)|SERVICE_NOT_AVAILABLE|DAILY_QUERY_OVER_LIMIT|ACCESS_TOO_FREQUENT|OVER_QUOTA|CONVERSION_TIMEOUT|CONVERSION_FAILED|TYPEERROR|REFERENCEERROR|NETWORK_ERROR)/);if(known)return known[0];return /^[A-Z][A-Z0-9_]{1,79}$/.test(s)&&!/^[A-F0-9]{32,}$/.test(s)?s:'SERVICE_RESPONSE_ERROR'}
function conversionCode(state,result){const known=safeCode(typeof result==='string'?result:result?.info||result?.error||'');if(known!=='SERVICE_RESPONSE_ERROR')return known;return 'SDK_'+safeCode(state)+'_'+(result===null?'NULL':Array.isArray(result)?'ARRAY':typeof result).toUpperCase()}
let statusTimer;
function status(text, settled=false){
 clearTimeout(statusTimer);
 statusNode.textContent=settled?'':text;
 if(bridge)bridge.mapStatus(text);
 if(ready&&text&&!settled)statusTimer=setTimeout(()=>{statusNode.textContent=''},6500);
}
function fail(text){document.getElementById('setup').style.display='flex';document.getElementById('heading').textContent='高德地图尚未连接';document.getElementById('message').textContent=text;bridge?.mapUnavailable?.();status(text)}
function esc(s){return String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
new QWebChannel(qt.webChannelTransport,c=>{bridge=c.objects.bridge;document.getElementById('configure').onclick=()=>bridge.configureMap();boot()});
async function boot(){
 try{const config=await (await fetch('config.json',{cache:'no-store'})).json();
  if(!config.configured){status('未配置高德地图 · 点击“填写地图配置”');return}
  status('正在连接高德地图…');document.getElementById('setup').style.display='none';
  window._AMapSecurityConfig={serviceHost:config.serviceHost};
  const script=document.createElement('script');script.src='https://webapi.amap.com/maps?v=2.0&key='+encodeURIComponent(config.key)+'&callback=initialize';
  script.onerror=()=>fail('高德地图加载失败，请检查网络后重新连接。');document.head.appendChild(script);
  readyTimer=setTimeout(()=>{if(!ready)fail('连接超时，请核对Web端Key、安全密钥、域名限制及网络，然后重新连接。')},25000);
 }catch(e){fail('地图配置未能读取，请打开地图设置后重新连接。')}
}
window.initialize=async function(){
 try{const center=[112.965,28.18];homeCenter=center;map=new AMap.Map('map',{viewMode:'2D',zoom:15,center,resizeEnable:true,mapStyle:mapStyles[theme]});
  setTheme(theme);
  map.on('complete',()=>{if(ready)return;ready=true;setTheme(theme);clearTimeout(readyTimer);document.getElementById('setup').style.display='none';status('');bridge.mapReady(1);if(payload)updateScene(payload);reportViewport()});
  map.on('moveend',reportViewport);
  for(const event of ['mapmove','moveend','zoomchange','zoomend','resize'])map.on(event,()=>{TrainingMap.paint();paintPhotoFrame()});
  map.on('click',async e=>{
   if(payload?.flight_control||payload?.training_scene==='sandbox-run')return;
   if(payload?.sandbox_edit){
    if(!payload.sandbox_edit.tool)return;
    const scene=sceneKey(payload), tool=sandboxToolKey(payload);
    const current=()=>sceneKey(payload)===scene&&sandboxToolKey(payload)===tool;
    try{
     const [point]=await SkyMapCore.inverseBatch([[e.lnglat.lng,e.lnglat.lat]],points=>convert(points,current));
     if(current())bridge.mapClicked(point[1],point[0]);
    }catch(_){if(current())status('点选坐标暂不可用，请重试。')}
    return;
   }
   if(payload?.route_editing){routeEdit('add',-1,e.lnglat);return}
   if(payload?.public){bridge.publicPoint(payload.city,e.lnglat.lat,e.lnglat.lng);return}
   const location=sceneKey(payload);
   status('正在转换点选坐标…');
   try{
    const p=await SkyMapCore.inverse([e.lnglat.lng,e.lnglat.lat],convert);
    if(sceneKey(payload)!==location)return;
    bridge.mapClicked(p[1],p[0]);status('已选取观察位置 · 请按现场情况填写位置误差',true);
   }catch(e){if(sceneKey(payload)===location)status('点选坐标转换失败，未创建位置。请重试或手动输入WGS84坐标。')}
  });
  map.on('rightclick',()=>{if(payload?.sandbox_edit?.tool)bridge.sandboxEditAction('cancel','',0,0,0)});
  document.addEventListener('keydown',e=>{
   if(e.key==='Escape'&&payload?.sandbox_edit?.tool)bridge.sandboxEditAction('cancel','',0,0,0);
  });
 }catch(e){clearTimeout(readyTimer);const code=safeCode(e.name)+' / '+safeCode(e.message);fail('底图初始化失败（'+code+'）。请保留此提示。')}
};
let useFetchConversion=false,conversionQueue=Promise.resolve(),lastConversionAt=0;
async function convertViaProxy(points){
 const query=new URLSearchParams({s:'rsv3',locations:points.map(p=>p.map(v=>Number(v).toFixed(6)).join(',')).join(';'),coordsys:'gps',output:'json'});
 const r=await fetch('/_AMapService/v3/assistant/coordinate/convert?'+query,{cache:'no-store',signal:AbortSignal.timeout(12000)});
 if(!r.ok)throw Error('HTTP_'+r.status);const result=await r.json();
 if(String(result.status)!=='1')throw Error(conversionCode('error',result));
 const locations=String(result.locations||'').split(';').map(p=>p.split(',').map(Number));
 if(locations.length!==points.length||locations.some(p=>p.length!==2||p.some(v=>!Number.isFinite(v))))throw Error('CONVERSION_FAILED');return locations;
}
async function convertOnce(points){
 if(useFetchConversion)return convertViaProxy(points);
 try{return await new Promise((resolve,reject)=>{let done=false;const t=setTimeout(()=>{done=true;reject(Error('CONVERSION_TIMEOUT'))},12000);
  AMap.convertFrom(points.map(p=>new AMap.LngLat(p[0],p[1])),'gps',(state,result)=>{if(done)return;clearTimeout(t);done=true;
   if(state==='complete'&&result.locations?.length===points.length)resolve(result.locations.map(p=>[p.lng,p.lat]));else reject(Error(conversionCode(state,result)));
  });})}catch(e){const result=await convertViaProxy(points);useFetchConversion=true;return result}
}
function convert(points, valid=()=>true){
 const task=conversionQueue.then(async()=>{for(let attempt=0;attempt<3;attempt++){
  if(!valid())throw Error('SCENE_CHANGED');
  const delay=Math.max(0,1200-(Date.now()-lastConversionAt));if(delay)await new Promise(r=>setTimeout(r,delay));
  if(!valid())throw Error('SCENE_CHANGED');
  lastConversionAt=Date.now();
  try{return await convertOnce(points)}catch(e){if(!/(?:QPS|TOO_FREQUENT|SERVER_IS_BUSY|GATEWAY_TIMEOUT)/.test(e.message)||attempt===2)throw e}
 }});conversionQueue=task.catch(()=>{});return task;
}
function reportViewport(){
 clearTimeout(viewTimer);
 viewTimer=setTimeout(async ()=>{
  if(!ready||!bridge||!(payload?.sandbox_edit||payload?.city==='changsha'&&payload.grid_visible))return;
  const key=sceneKey(payload);
  try{
   const b=map.getBounds(),sw=b.getSouthWest(),ne=b.getNorthEast();
   const center=payload?.sandbox_edit?map.getCenter():null;
   const native=[[sw.lng,sw.lat],[ne.lng,ne.lat]];
   if(center)native.push([center.lng,center.lat]);
   const [[w,s],[e,n],point]=await SkyMapCore.inverseBatch(native,convertOnce);
   if(sceneKey(payload)===key&&(payload?.grid_visible||payload?.sandbox_edit)&&
      (!center||compareNativeCenter(center,map.getCenter()))){
    bridge.mapViewport(w,s,e,n,map.getZoom());
    if(point&&payload?.sandbox_edit)bridge.sandboxEditAction('center','',0,...point);
   }
  }catch(_){}
 },300);
}
function compareNativeCenter(a,b){return a.lng===b.lng&&a.lat===b.lat}
function clearSandboxPin(){
 clearTimeout(sandboxPinTimer);
 if(sandboxPin){map.remove(sandboxPin);sandboxPin=null}
}
async function focusPoint(point,zoom,pin){
 const revision=++pointFocusRevision,scene=sceneKey(payload);
 try{
  const [native]=await SkyMapCore.convertBatch([point],convert,cache);
  if(revision!==pointFocusRevision||scene!==sceneKey(payload))return;
  if(zoom)map.setZoomAndCenter(zoom,native,false);else map.panTo(native);
  if(pin&&payload?.sandbox_edit){
   clearSandboxPin();
   sandboxPin=new AMap.Marker({position:native,anchor:'bottom-center',bubble:true,zIndex:130,
    content:'<div class="sandbox-waypoint" title="'+esc(pin)+'" style="background:#1f55cc">📍 '+esc(pin)+'</div>'});
   map.add(sandboxPin);
   sandboxPinTimer=setTimeout(clearSandboxPin,18000);
  }
 }catch(_){}
}
async function focusRoute(points){
 const id=++focusRouteRevision, city=payload?.city;
 try{
  const path=await SkyMapCore.convertBatch(points,convert,cache);
  if(id!==focusRouteRevision||city!==payload?.city||!path.length)return;
  if(path.every(p=>p[0]===path[0][0]&&p[1]===path[0][1]))map.setZoomAndCenter(18,path[0],true);
  else map.setFitView([new AMap.Polyline({path})],false,[72,55,72,55],19);
 }catch(_){if(id===focusRouteRevision)status('航线定位暂不可用，请重试')}
}
async function drawGrid(cells,bounds){
 const rev=++gridVersion;
 if(!cells.length||payload?.city!=='changsha'||!payload?.grid_visible){
  if(gridOverlays.length){map.remove(gridOverlays);gridOverlays=[];}
  return;
 }
 try{
  const [w,s,e,n]=bounds;
  const anchors=await SkyMapCore.convertBatch([[w,s],[e,s],[w,n],[e,n]],convertOnce,cache);
  if(rev!==gridVersion||payload?.city!=='changsha'||!payload.grid_visible)return;
  // Interpolate the provider offset across the viewport for display only.
  const position=(x,y)=>{
   const u=(x-w)/(e-w),v=(y-s)/(n-s);
   return [0,1].map(i=>anchors[0][i]*(1-u)*(1-v)+anchors[1][i]*u*(1-v)+anchors[2][i]*(1-u)*v+anchors[3][i]*u*v);
  };
  const next=cells.map(([left,bottom,right,top])=>new AMap.Polygon({
   path:[position(left,bottom),position(right,bottom),position(right,top),position(left,top)],
   fillColor:'#16835c',fillOpacity:.25,strokeWeight:0,zIndex:4,bubble:true}));
  map.add(next);
  if(gridOverlays.length)map.remove(gridOverlays);
  gridOverlays=next;
 }catch(_){}
}
async function resolveWeatherPoints(revision,points){
 try{
  const converted=await SkyMapCore.inverseBatch(points,convert);
  bridge.weatherResolved(revision,JSON.stringify(converted));
 }catch(_){bridge.weatherResolved(revision,'')}
}
async function resolvePublicPoints(revision,points){
 try{
  const positions=await SkyMapCore.inverseBatch(points,convert);
  bridge.publicPointsResolved(revision,JSON.stringify(positions));
 }catch(e){bridge.publicPointsResolved(revision,'')}
}
async function lookupRouteScenery(revision,corners){
 try{
  const bounds=await SkyMapCore.convertBatch(corners,convert,cache);
  const [[west,south],[east,north]]=bounds;
  const polygon=[[west,north],[east,south]].map(p=>p.map(v=>v.toFixed(6)).join(',')).join('|');
  const query=new URLSearchParams({s:'rsv3',polygon,
   types:'110000',offset:'10',extensions:'base',output:'JSON'});
  const response=await fetch('/_AMapService/v3/place/polygon?'+query,{cache:'no-store',signal:AbortSignal.timeout(6000)});
  const result=await response.json();
  if(!response.ok||String(result.status)!=='1'||!Array.isArray(result.pois))throw Error('POI_SEARCH_FAILED');
  const picked=result.pois
   .filter(p=>typeof p.name==='string'&&typeof p.location==='string')
   .map(p=>({name:p.name,point:p.location.split(',').map(Number)}))
   .filter(p=>p.point.length===2&&p.point.every(Number.isFinite)).slice(0,10);
  const locations=await SkyMapCore.inverseBatch(picked.map(p=>p.point),convert);
  bridge.publicScenicResolved(revision,JSON.stringify(picked.map((p,i)=>[p.name,locations[i]])));
 }catch(e){bridge.publicScenicResolved(revision,'')}
}
async function lookupSandboxRoad(token, role, start, end){
 const scene=sceneGeneration;
 const current=()=>scene===sceneGeneration&&Boolean(payload?.sandbox_edit);
 const plugin=role==='vehicle'?'Riding':'Walking';
 let timer;
 try{
  const lookup=async()=>{
   const [origin,destination]=await SkyMapCore.convertBatch([start,end],points=>convert(points,current),cache);
   if(!current())return [];
   let sdkTimer;
   const sdkLimit=new Promise(resolve=>{sdkTimer=setTimeout(()=>resolve([]),1800)});
   const sdk=new Promise(resolve=>AMap.plugin(['AMap.'+plugin],()=>{
    if(!current()||typeof AMap[plugin]!=='function'){resolve([]);return}
    try{
     new AMap[plugin]({}).search(origin,destination,(state,result)=>{
      const route=result?.routes?.[0];
      const steps=plugin==='Riding'?route?.rides:route?.steps;
      resolve(state==='complete'&&steps?steps.flatMap(step=>(step.path||[]).map(p=>[p.lng,p.lat])):[]);
     });
    }catch(_){resolve([])}
   })).catch(()=>[]);
   let path=await Promise.race([sdk,sdkLimit]);
   clearTimeout(sdkTimer);
   if(path.length<2&&current()){
    const riding=role==='vehicle';
    const endpoint=riding?'v4/direction/bicycling':'v3/direction/walking';
    const coord=point=>point.map(value=>value.toFixed(6)).join(',');
    const query=new URLSearchParams({s:'rsv3',origin:coord(origin),destination:coord(destination),output:'JSON'});
    const response=await fetch('/_AMapService/'+endpoint+'?'+query,
      {cache:'no-store',signal:AbortSignal.timeout(3000)});
    const result=await response.json();
    const steps=(riding?result.data:result.route)?.paths?.[0]?.steps;
    if(response.ok&&Array.isArray(steps)){
     path=steps.flatMap(step=>typeof step.polyline==='string'?step.polyline.split(';'):[])
      .filter(Boolean).map(point=>point.split(',').map(Number));
    }
   }
   path=path.filter(point=>point.length===2&&point.every(Number.isFinite));
   // A bounded sample keeps conversion and simulation work predictable.
   const stride=Math.max(1,Math.ceil(path.length/39));
   const sampled=path.filter((point,i)=>i%stride===0||i===path.length-1)
    .filter((point,i,rows)=>!i||point[0]!==rows[i-1][0]||point[1]!==rows[i-1][1]);
   return current()&&sampled.length>=2?await SkyMapCore.inverseBatch(sampled,points=>convert(points,current)):[];
  };
  const limit=new Promise(resolve=>{timer=setTimeout(()=>resolve([]),10500)});
  const points=await Promise.race([lookup(),limit]);
  bridge.sandboxRoadResolved(token,JSON.stringify(points));
 }catch(_){bridge.sandboxRoadResolved(token,'[]')}
 finally{clearTimeout(timer)}
}
async function lookupCityPlace(revision,cityName,keyword,options={}){
 let timer;
 const controller=new AbortController();
 const timeout=new Promise((_,reject)=>{
  timer=setTimeout(()=>{controller.abort();reject(Error('POI_TIMEOUT'));},6000);
 });
 async function lookup(){
  const {center, template, landing}=options;
  const query=new URLSearchParams({s:'rsv3',keywords:keyword,city:cityName,citylimit:'true',
   offset:center?'1':revision===null?'20':'5',extensions:'base',output:'JSON'});
  if(center){
   query.set('location',[center.lng,center.lat].map(value=>value.toFixed(6)).join(','));
   query.set('radius','800');
   query.set('sortrule','distance');
  }
  const response=await fetch('/_AMapService/v3/place/'+(center?'around':'text')+'?'+query,
   {cache:'no-store',signal:controller.signal});
  const result=await response.json();
  if(!response.ok||String(result.status)!=='1'||!Array.isArray(result.pois))return [];
  const picked=result.pois.filter(p=>typeof p?.name==='string'&&p.name.trim()&&
   typeof p.location==='string'&&/^\s*-?\d+(?:\.\d+)?\s*,\s*-?\d+(?:\.\d+)?\s*$/.test(p.location))
   .map(p=>({name:p.name.trim().slice(0,80),point:p.location.split(',').map(Number),
    type:typeof p.type==='string'?p.type.slice(0,120):'',address:typeof p.address==='string'?p.address.slice(0,120):''}))
   .filter(p=>Math.abs(p.point[0])<=180&&Math.abs(p.point[1])<=89).slice(0,center?1:20);
  if(!picked.length)return [];
  const locations=await SkyMapCore.inverseBatch(picked.map(p=>p.point),convertOnce);
  return picked.map(({point,...poi},i)=>({...poi,location:locations[i],template,landing}));
 }
 let items=[];
 try{items=await Promise.race([lookup(),timeout]);}catch(_){}finally{clearTimeout(timer);}
 if(revision!==null)bridge.publicPlaceResolved(revision,JSON.stringify(items.slice(0,5)));
 return items;
}
async function lookupSandboxPlace(revision,city,keyword,nearby){
 let timer;
 const scene=sceneKey(payload),center=nearby?map.getCenter():null;
 const categories=[['学校','school'],['商场','shopping'],['公园','park','park_lawn'],
  ['广场',null,'plaza'],['停车场',null,'parking']];
 const lookup=new Promise(resolve=>{
  timer=setTimeout(()=>resolve([]),6500);
  AMap.plugin(['AMap.PlaceSearch'],async ()=>{
   try{
    const search=(term,template,landing)=>new Promise(done=>{
     const service=new AMap.PlaceSearch({city,citylimit:true,pageSize:nearby?1:20,extensions:'base'});
     const callback=(status,result)=>done(status==='complete'?(result?.poiList?.pois||[])
      .map(p=>({name:p.name,type:p.type,point:typeof p.location==='string'?p.location.split(',').map(Number):
       [p.location?.lng,p.location?.lat],template,landing})):[]);
     if(nearby)service.searchNearBy(term,center,800,callback);else service.search(term,callback);
    });
    const results=(await Promise.all(nearby?categories.map(args=>search(...args)):[search(keyword)])).flat();
    const picked=results.filter(p=>typeof p.name==='string'&&p.point.every(Number.isFinite));
    const location=await SkyMapCore.inverseBatch(picked.map(p=>p.point),convertOnce);
    resolve(picked.map(({point,...poi},i)=>({...poi,location:location[i]})));
   }catch(_){resolve([])}
  });
 });
 let items;
 try{items=await lookup}finally{clearTimeout(timer)}
 if(scene!==sceneKey(payload))return;
 if(!items.length){
  items=nearby?(await Promise.all(categories.map(([term,template,landing])=>
   lookupCityPlace(null,city,term,{center,template,landing})))).flat():await lookupCityPlace(null,city,keyword);
 }
 if(scene===sceneKey(payload))bridge.publicPlaceResolved(revision,JSON.stringify(items));
}
let renderedScene=null;
const editor = {scope:'', queue:[], token:0, busy:false, timer:null, markers:new Map(), line:null, loading:'', failed:false};
const scopeOf = p => p?.public && p.route_epoch!==undefined ? p.city+':'+p.route_epoch : '';
const nativeAt = point => cache.get(SkyMapCore.positionKey(point));
const same = (a,b) => a && a[0]===b[0] && a[1]===b[1];

function draftModel(){
 const p=payload, route=p?.routes?.find(row=>row.style==='edit');
 let takeoff=route?nativeAt(route.takeoff):null, query=null;
 let waypoints=(route?.waypoints||[]).map(([lon,lat,height])=>({point:nativeAt([lon,lat]),height}));
 if(p?.point_mode&&p.pins?.length)query=nativeAt([p.pins[0].lon,p.pins[0].lat]);
 for(const edit of editor.queue){
  const {action,index,point}=edit;
  if(action==='takeoff')takeoff=point;
  if(action==='point')query=point;
  if(action==='waypoint')waypoints.push({point,height:p.route_height??70});
  if(action==='move'&&waypoints[index])waypoints[index]={...waypoints[index],point};
  if(action==='insert'){
   const before=waypoints[index-1]?.height??70,after=waypoints[index]?.height??70;
   waypoints.splice(index,0,{point,height:(before+after)/2});
  }
 }
 return {takeoff,query,waypoints};
}
function drawEditorLine(path){
 const interactive=payload?.route_editing&&payload.route_step===1&&!payload.point_mode&&!payload.subject_pick;
 const checked=payload?.routes?.some(route=>route.style==='edit'&&(route.segments?.length||route.itinerary?.length));
 const options={strokeColor:mapColor('pred'),strokeWeight:interactive?12:3,
  strokeOpacity:checked?(interactive?.04:0):interactive?.28:1,zIndex:interactive?74:64};
 if(path.length<2){
  if(editor.line){map.remove(editor.line);editor.line=null}
 }else if(editor.line){
  if(JSON.stringify(editor.path)!==JSON.stringify(path))editor.line.setPath(path);
  if(JSON.stringify(editor.lineOptions)!==JSON.stringify(options))editor.line.setOptions?.(options);
 }else{
  editor.line=new AMap.Polyline({path,...options,bubble:false});
  map.add(editor.line);
  editor.line.on('click', event=>{
   if(payload?.route_step!==1||!payload.route_editing||payload.point_mode||payload.subject_pick||editor.queue.length)return;
   const rows=draftModel().waypoints;
   const p=map.lngLatToContainer(event.lnglat);
   let closest=Infinity, idx=-1;
   for(let i=1;i<rows.length;i++){
    if(!rows[i-1].point||!rows[i].point)continue;
    const a=map.lngLatToContainer(rows[i-1].point), b=map.lngLatToContainer(rows[i].point);
    const x=b.x-a.x,y=b.y-a.y;
    const f=Math.max(0,Math.min(1,((p.x-a.x)*x+(p.y-a.y)*y)/(x*x+y*y||1)));
    const gap=Math.hypot(p.x-a.x-x*f,p.y-a.y-y*f);
    if(gap<closest){closest=gap;idx=i}
   }
   if(idx>0&&closest<14)routeEdit('insert',idx,event.lnglat);
   else routeEdit('add',-1,event.lnglat);
  });
 }
 editor.path=path;editor.lineOptions=options;
}
function paintEditor(){
 const p=payload, model=draftModel(), keys=new Set();
 const route=p?.routes?.find(row=>row.style==='edit');
 const put=(key,point,label,kind,index)=>{
  if(!point)return;
  keys.add(key);
  let row=editor.markers.get(key);
  const content='<div class="edit-anchor '+kind+'"><span class="edit-caption">'+esc(label)+'</span></div>';
  const draggable=index!==undefined&&p.route_step===1&&p.route_editing&&!p.subject_pick;
  if(!row){
   const marker=new AMap.Marker({position:point,anchor:'center',offset:new AMap.Pixel(0,0),
      content,draggable,bubble:false,zIndex:82});
   row={marker,point,content,draggable,dragging:false};editor.markers.set(key,row);
   map.add(marker);
   if(index!==undefined){
    marker.on('click',()=>routeEdit('select',index));
    marker.on('dragstart',()=>{row.dragging=true;paintEditor()});
    marker.on('dragging',event=>{
     if(!event.lnglat)return;
     const current=draftModel(), path=current.waypoints.map(wp=>wp.point);
     path[index]=[event.lnglat.lng,event.lnglat.lat];
     drawEditorLine([current.takeoff,...path].filter(Boolean));
    });
    marker.on('dragend',event=>{
     row.dragging=false;
     routeEdit('move',index,event.lnglat||marker.getPosition());
    });
    marker.on('rightclick',()=>routeEdit('remove',index));
   }
  }else{
   if(row.draggable!==draggable){row.marker.setDraggable?.(draggable);row.draggable=draggable}
   if(!row.dragging&&!same(row.point,point)){row.marker.setPosition(point);row.point=point}
   if(row.content!==content){row.marker.setContent(content);row.content=content}
  }
 };
 if(scopeOf(p)){
  if(p.point_mode)put('query',model.query,'查询点','query');
  else{
   put('takeoff',model.takeoff,route?.provisional_takeoff?'候选起飞点':'起飞点','takeoff');
   model.waypoints.forEach(({point,height},index)=>put('w'+index,point,(index+1)+' · '+height+' 米',
    (height>(route?.heightLimit||120)?'high':'waypoint')+(p.selected_waypoint===index?' active':''),index));
   if(p.route_editing&&p.route_step===1&&!p.subject_pick&&model.waypoints.length<60&&
      ![...editor.markers.values()].some(row=>row.dragging)){
    for(let i=1;i<model.waypoints.length;i++){
     const a=model.waypoints[i-1].point,b=model.waypoints[i].point;
     if(!a||!b||same(a,b))continue;
     const point=[(a[0]+b[0])/2,(a[1]+b[1])/2], key='insert'+i;
     keys.add(key);
     let row=editor.markers.get(key);
     if(!row){
      const button=document.createElement('button');
      button.type='button';button.className='edit-insert';button.textContent='＋';button.title='插入航点';
      button.setAttribute('aria-label','插入航点');
      for(const name of ['pointerdown','pointerup','mousedown','mouseup']){
       button.addEventListener(name,event=>event.stopPropagation());
      }
      button.addEventListener('click',event=>{
       event.stopPropagation();
       routeEdit('insert',i,{lng:row.point[0],lat:row.point[1]});
      });
      const marker=new AMap.Marker({position:point,anchor:'center',bubble:false,zIndex:78,content:button});
      row={marker,point};editor.markers.set(key,row);map.add(marker);
     }else if(!same(row.point,point)){row.point=point;row.marker.setPosition(point)}
    }
   }
  }
 }
 for(const [key,row] of editor.markers)if(!keys.has(key)){map.remove(row.marker);editor.markers.delete(key)}
 drawEditorLine(scopeOf(p)&&!p.point_mode?[model.takeoff,...model.waypoints.map(w=>w.point)].filter(Boolean):[]);
}
function syncEditor(p){
 const scope=scopeOf(p);
 if(scope!==editor.scope){
  clearTimeout(editor.timer);
  Object.assign(editor,{scope,queue:[],busy:false,loading:'',failed:false});
 }
 if(scope){
  editor.queue=editor.queue.filter(row=>!row.token||row.token>p.route_ack);
  const route=p.routes?.find(row=>row.style==='edit');
  const points=route?[route.takeoff,...route.waypoints.map(w=>w.slice(0,2))]:[];
  if(p.point_mode&&p.pins?.length)points.push([p.pins[0].lon,p.pins[0].lat]);
  const key=scope+JSON.stringify(points);
  if(points.some(point=>!nativeAt(point))&&editor.loading!==key){
   editor.loading=key;
   SkyMapCore.convertBatch(points,convert,cache).then(()=>{
    if(editor.loading===key)paintEditor();
   }).catch(()=>status('航点坐标暂未显示，请重试')).finally(()=>{
    if(editor.loading===key)editor.loading='';
   });
  }
 }
 paintEditor();
}
function routeEdit(action,index,point){
 const p=payload;
 if(!scopeOf(p))return;
 if(action==='add'){
  if(!p.route_editing)return;
  action=p.subject_pick?'subject':p.point_mode?'point':p.route_step===0&&!editor.queue.some(row=>row.action==='takeoff')?'takeoff':'waypoint';
 }else if(!p.route_editing||p.route_step!==1||p.point_mode||p.subject_pick)return;
 if(!point){
  if(editor.queue.length)return;
  bridge.routeAction(p.city,action,index,0,0);return;
 }
 const native=[point.lng,point.lat];
 if(!native.every(Number.isFinite)||editor.queue.length>=64)return;
 const move=action==='move'&&editor.queue.findLast(row=>!row.token&&row.action===action&&row.index===index);
 if(move)move.point=native;
 else editor.queue.push({action,index,point:native});
 editor.failed=false;
 bridge.routePending(p.city,p.route_epoch,true);
 paintEditor();
 clearTimeout(editor.timer);
 editor.timer=setTimeout(flushEditor,110);
}
async function flushEditor(){
 if(editor.busy||!editor.queue.length)return;
 const p=payload, scope=editor.scope;
 if(!scope||scope!==scopeOf(p))return;
 const rows=editor.queue.filter(item=>!item.token);
 if(!rows.length)return;
 editor.busy=true;
 const token=++editor.token;
 rows.forEach(row=>{row.token=token});
 try{
  const result=await SkyMapCore.inverseBatch(rows.map(row=>row.point),convert);
  if(editor.scope!==scope)return;
  const edits=rows.map((row,i)=>{
   cache.set(SkyMapCore.positionKey(result[i]),row.point);
   return [row.action,row.index,...result[i]];
  });
  bridge.routeBatch(p.city,p.route_epoch,token,JSON.stringify(edits));
 }catch(_){
  if(editor.scope===scope){
   rows.forEach(row=>{row.token=0});
   editor.failed=true;
   status('航点已标出 · 坐标确认失败，再点地图可重试');
  }
 }finally{
  if(editor.scope===scope){
   editor.busy=false;
   if(!editor.failed){
    if(editor.queue.some(row=>!row.token))flushEditor();
    else bridge.routePending(p.city,p.route_epoch,false);
   }
  }
 }
}
function sandboxToolKey(p){const e=p?.sandbox_edit;return JSON.stringify([e?.step,e?.selected,e?.tool])}
function sceneKey(p){return JSON.stringify([p?.city,p?.public,p?.center,
 p?.training_scene==='sandbox-run'?null:p?.training_bounds,p?.training_scene])}
function redrawSandbox(){if(payload?.sandbox_edit){staticKey='';updateScene(payload)}}
function sandboxMarker(options,kind,id,index,origin){
 const marker=new AMap.Marker({...options,draggable:true,bubble:false});
 const scene=sceneKey(payload);
 marker.on('dragend',async ()=>{
  const native=marker.getPosition(),valid=()=>scene===sceneKey(payload)&&!!payload?.sandbox_edit;
  try{
   const [[lon,lat]]=await SkyMapCore.inverseBatch([[native.lng,native.lat]],points=>convert(points,valid));
   if(valid())bridge.sandboxEditAction(kind,id,index,lon,lat);
  }catch(_){if(valid())marker.setPosition(origin)}
 });
 return marker;
}
let focusedScenic='', sceneWarp=null, sceneGeneration=0, calibrating=false;
function positionMap(p){
 const key=sceneKey(p);
 if(!p.center||key===renderedScene)return;
 renderedScene=key;
 const generation=++sceneGeneration;
 const valid=()=>generation===sceneGeneration;
 aircraftRevision++;
 showTrainingAircraft([]);
 sceneWarp=null;
 calibrating=Boolean(p.training_bounds);
 // City overview only. Features continue to use their matching coordinate system.
 homeCenter=p.center;homeZoom=p.sandbox_edit?10.8:p.training_bounds?15:p.public?14:15;
 fitted=true;
 if(p.sandbox_edit&&sandboxViewport?.city===p.city){
  map.setZoomAndCenter(sandboxViewport.zoom,sandboxViewport.center,true);
 }else if(p.training_scene!=='sandbox-run')map.setZoomAndCenter(homeZoom,homeCenter,true);
 if(p.training_bounds){
  const [w,s,e,n]=p.training_bounds;
  const center=[(w+e)/2,(s+n)/2];
  const cells=p.civic_replay?4:1;
  const point=(x,y)=>[w+(e-w)*x/cells,s+(n-s)*y/cells];
  const grid=Array.from({length:(cells+1)**2},(_,i)=>point(i%(cells+1),Math.floor(i/(cells+1))));
  const checks=[center,point(cells*.375,cells*.25),point(cells*.625,cells*.75)];
  const cross=[...grid,...checks];
  SkyMapCore.convertBatch(cross,points=>convert(points,valid),cache).then(anchors=>{
   if(!valid())return;
   const index=(x,y)=>y*(cells+1)+x;
   const warp=([lon,lat])=>{
    if(lon<w||lon>e||lat<s||lat>n)return null;
    const x=Math.min(cells-1,Math.floor((lon-w)/(e-w)*cells));
    const y=Math.min(cells-1,Math.floor((lat-s)/(n-s)*cells));
    const cell=SkyMapCore.viewportWarp([...point(x,y),...point(x+1,y+1)],
     [anchors[index(x,y)],anchors[index(x+1,y)],anchors[index(x,y+1)],anchors[index(x+1,y+1)]]);
    return cell([lon,lat]);
   };
   // The calibration applies to display; track processing uses WGS84.
   const enough=checks.every((point,i)=>{
    const expected=anchors[i+grid.length], approx=warp(point);
    return approx&&Math.hypot(expected[0]-approx[0],expected[1]-approx[1])<=.000024;
   });
   homeCenter=anchors[grid.length];
   const {width,height}=document.getElementById('map').getBoundingClientRect();
   const far=anchors[index(cells,cells)];
   const dx=Math.abs(far[0]-anchors[0][0]);
   const dy=Math.abs(far[1]-anchors[0][1])/Math.cos(center[1]*Math.PI/180);
   if(width>120&&height>120)homeZoom=Math.min(p.civic_replay?18:15.2,Math.log2(360/256*Math.min((width-100)/dx,(height-80)/dy)));
   map.setZoomAndCenter(homeZoom,homeCenter,p.training_scene!=='sandbox-run');
   if(enough)sceneWarp=warp;
  }).catch(()=>{}).finally(()=>{
   if(!valid())return;
   calibrating=false;
   setAircraft(payload.aircraft||[]);
   drawNext();
  });
 }
}
function drawDraft(p){
 const draft=p.public&&p.draft?.native?p.draft:null;
 const key=theme+JSON.stringify(draft);
 if(key===draftKey)return;
 draftKey=key;
 if(draftOverlays.length)map.remove(draftOverlays);
 draftOverlays=[];
 if(!draft)return;
 const layer={strokeColor:mapColor('pred'),strokeWeight:2,fillColor:mapColor('pred'),fillOpacity:.13,zIndex:61,bubble:true};
 if(draft.form==='circle'&&draft.points.length){
  draftOverlays.push(new AMap.Circle({...layer,center:draft.points[0],radius:draft.radius}));
 }else{
  for(const path of draft.rings)draftOverlays.push(new AMap.Polygon({...layer,path:path.length===1?path[0]:path}));
  if(draft.points.length>1)draftOverlays.push(new AMap.Polyline({...layer,path:draft.points,strokeStyle:'dashed',zIndex:62}));
 }
 draft.points.forEach((point,i)=>draftOverlays.push(new AMap.Marker({
  position:point,bubble:true,content:'<div class="draw-point">'+(i+1)+'</div>',anchor:'center',zIndex:63
 })));
 if(draftOverlays.length)map.add(draftOverlays);
}
function updateScene(p){
 const isPreview=scene=>!!scene?.routes?.some(route=>route.style==='preview');
 if(ready&&payload){
  if(!isPreview(payload)&&isPreview(p))routeViewport=[map.getZoom(),map.getCenter()];
  if(isPreview(payload)&&!isPreview(p)){
   if(routeViewport&&payload.city===p.city&&p.public)map.setZoomAndCenter(routeViewport[0],routeViewport[1],true);
   routeViewport=null;
  }
  const previewKey=scene=>scene?.routes?.find(route=>route.style==='preview')?.key;
  if(previewKey(payload)!==previewKey(p)){
   map.remove(overlays);overlays=[];
  }
 }
 if(ready&&payload&&sceneKey(payload)!==sceneKey(p)){
  if(payload.sandbox_edit&&p.training_scene==='sandbox-run'){
   sandboxViewport={city:p.city,zoom:map.getZoom(),center:map.getCenter()};
  }else if(!p.sandbox_edit||sandboxViewport?.city!==p.city)sandboxViewport=null;
  if(sandboxPin)clearSandboxPin();
  map.remove(overlays);overlays=[];drawGrid([],[]);reportViewport();
 }
 const wasGrid=payload?.grid_visible;
 payload=p;
 const {route_epoch,route_ack,route_step,route_height,route_editing,selected_waypoint,aircraft,flight_control,
        formations,agent_links,observation_posts,active_highlights,staff_markers,civic_links,attention_cards,feed,...display}=p;
 display.tactical=Boolean(observation_posts?.length);
 const base={...display,routes:(p.routes||[]).flatMap(route=>route.style!=='edit'?[route]:
   route.segments?.length||route.itinerary?.length||p.photo_preview?[{...route,style:'checked-edit',waypoints:[],
    shots:p.photo_preview?route.shots:[]}]:[]),
   pins:p.point_mode?[]:p.pins};
 const key=theme+selected+showRisk+JSON.stringify(base);
 if(key!==staticKey){staticKey=key;pending=base}
 if(ready){
  positionMap(p);drawDraft(p);syncEditor(p);
  if(wasGrid&&!p.grid_visible)drawGrid([],[]);
  if(!wasGrid&&p.grid_visible)reportViewport();
  setAircraft(p.aircraft||[]);
  TrainingMap.fly(document.getElementById('map'),!!p.flight_control,async point=>{
   const pixel=new AMap.Pixel(point.x,point.y), value=map.containerToLngLat(pixel);
   return SkyMapCore.inverse([value.lng,value.lat],convert);
  },bridge,(x,y)=>map.panBy(x,y));
  if(!running)drawNext();
 }
}
async function drawNext(){
 if(!pending||!ready||running||calibrating)return;running=true;const p=pending;pending=null;
 const generation=sceneGeneration;
 const points=[];const add=(lat,lon)=>{points.push([lon,lat]);return points.length-1};
 const grid=p.grid.map(c=>({data:c,i:add(c[0],c[1])}));const areas=p.areas.map(a=>({data:a,i:add(a.lat,a.lon)}));
 const events=p.observations.filter(o=>o.lat!==null&&!o.linked_track).map(o=>({
  data:o,i:add(o.lat,o.lon),future:o.prediction.map(t=>({data:t,i:add(t.lat,t.lon)})),
  history:(o.history||[]).map(t=>add(t.lat,t.lon))
 }));
 const zones=(p.civic_replay?[]:p.zones||[]).filter(zone=>!p.tactical||!zone.kind.startsWith('zone-'))
   .map(zone=>({data:zone,rings:zone.rings.map(r=>r.map(([lng,lat])=>add(lat,lng)))}));
 const pins=(p.pins||[]).map(pin=>({data:pin,i:add(pin.lat,pin.lon)}));
 const routes=(p.routes||[]).map(route=>({
  ...route, p:route.path.map(([lon,lat])=>add(lat,lon)),
  takeoffIndex:add(route.takeoff[1],route.takeoff[0]),
  subjectIndex:route.subject?add(route.subject[1],route.subject[0]):null,
  wp:(route.waypoints||[]).map(([lon,lat,h])=>({i:add(lat,lon),h})),
  legs:(route.segments||[]).map(s=>({...s,p:s.path.map(([lon,lat])=>add(lat,lon))})),
  phases:(route.itinerary||[]).map(item=>({...item,p:item.path.map(([x,y])=>add(y,x))})),
  shots:((route.style==='preview'||p.photo_preview)?route.shots||[]:[]).map((item,index)=>({...item,index:index+1,i:add(item.point[1],item.point[0]),
      p:(item.path||[]).map(([x,y])=>add(y,x))})),
  current:route.current?add(route.current[1],route.current[0]):null
 }));
 const editing=p.sandbox_edit;
 const sandbox=editing?{
  center:add(editing.center[1],editing.center[0]),
  sites:editing.sites.map(s=>({...s,i:add(s.point[1],s.point[0])})),
  drones:editing.drones.map(d=>({...d,path:d.points.map(([x,y])=>add(y,x)),
   triggers:(d.triggers||[]).map(t=>({...t,i:add(t.point[1],t.point[0])}))})),
  staff:editing.staff.map(s=>({...s,i:add(s.point[1],s.point[0])}))
 }:null;
 const aerial=p.public&&p.aerial;
 const pointIndex=([lon,lat])=>add(lat,lon);
 const aerialPoints=aerial?{
  center:pointIndex(aerial.point),
  scenes:aerial.scenes.map(scene=>({data:scene,i:pointIndex(scene.point)})),
  line:aerial.line.map(pointIndex),segment:aerial.segment.map(pointIndex)
 }:null;
 try{
  const mapped=sceneWarp&&points.map(sceneWarp);
  const loc=mapped&&mapped.every(Boolean)?mapped:await SkyMapCore.convertBatch(points,
    batch=>convert(batch,()=>generation===sceneGeneration),cache);
  if(sceneKey(p)!==sceneKey(payload))return;
  if(p.routes?.find(route=>route.style==='preview')?.key!==payload?.routes?.find(route=>route.style==='preview')?.key)return;
  if((p.aerial||payload?.aerial)&&p.aerial!==payload?.aerial)return;
  if(!!p.photo_preview!==!!payload?.photo_preview)return;
  if(p.sandbox_edit&&JSON.stringify(p.sandbox_edit)!==JSON.stringify(payload?.sandbox_edit))return;
  const next=[],eventMarkers=[];
  if(sandbox){
   next.push(new AMap.Circle({center:loc[sandbox.center],radius:15000,strokeColor:'#7c8794',
    strokeOpacity:.75,strokeWeight:1.5,strokeStyle:'dashed',fillOpacity:0,bubble:true,zIndex:8}));
   for(const site of sandbox.sites){
    const tint=site.kind==='lawn'?'#278369':'#b25f41';
    next.push(new AMap.Circle({center:loc[site.i],radius:site.radius,strokeColor:tint,strokeWeight:1,
     strokeStyle:site.kind==='lawn'?'dashed':'solid',fillColor:tint,fillOpacity:.10,bubble:true,zIndex:15}));
    next.push(sandboxMarker({position:loc[site.i],content:'<div class="marker">'+esc(site.name)+'</div>',
     anchor:site.kind==='lawn'?'top-center':'bottom-center',zIndex:114},'site',site.id,0,loc[site.i]));
   }
   for(const drone of editing.step>=1?sandbox.drones:[]){
    const tint=drone.selected?'#1f55cc':'#748aa6';
    if(drone.path.length>1)next.push(new AMap.Polyline({path:drone.path.map(i=>loc[i]),strokeColor:tint,
     strokeWeight:drone.selected?3:2,strokeOpacity:drone.selected?.9:.62,bubble:true,zIndex:32}));
    drone.path.forEach((i,index)=>next.push(sandboxMarker({position:loc[i],anchor:'center',
     zIndex:drone.selected?101:80,content:'<div class="sandbox-waypoint" style="background:'+tint+'">'+
     esc(drone.id)+' · '+(index+1)+'</div>'},'waypoint',drone.id,index,loc[i])));
    for(const trigger of drone.triggers)next.push(new AMap.Marker({position:loc[trigger.i],anchor:'bottom-center',
     bubble:true,zIndex:125,content:'<div class="sandbox-waypoint" style="background:#934c00" title="'+
      esc(trigger.text)+'">⚡</div>'}));
   }
   for(const person of editing.step===2?sandbox.staff:[]){
    const style=TrainingMap.civicRole(person.role);
    if(person.coverage_m>0){
     const tint=mapColor('pred');
     next.push(new AMap.Circle({center:loc[person.i],radius:person.coverage_m,strokeColor:tint,
      strokeOpacity:.60,strokeWeight:1.5,strokeStyle:'dashed',fillColor:tint,fillOpacity:.09,bubble:true,zIndex:18}));
    }
    next.push(sandboxMarker({position:loc[person.i],anchor:'center',zIndex:110,
     content:'<div class="sandbox-waypoint" title="'+esc(person.name)+'" style="background:'+style.color+'">'+
      esc(style.glyph)+'</div>'},
     'staff',person.id,0,loc[person.i]));
   }
  }
  const prediction=mapColor('pred'),danger=mapColor('danger'),warning=mapColor('warn'),ok=mapColor('ok');
  for(const {data,rings} of zones){
   const roles={'zone-s1':danger,'zone-school':warning,'zone-recovery':ok,'zone-road':'#748ca4'};
   const color=roles[data.kind]||(data.kind==='restriction'?danger:data.kind==='plan'||data.kind==='filing'?
    (data.filing_style==='past'?'#788393':prediction):ok);
   const path=rings.map(ring=>ring.map(i=>loc[i]));
   const shape=new AMap.Polygon({path:path.length===1?path[0]:path,strokeColor:color,
      strokeWeight:data.kind==='zone-road'?3:data.highlight?2:1,fillColor:color,
      fillOpacity:data.kind==='zone'?0:data.highlight?.15:.08,
      strokeStyle:data.filing_style==='future'||['zone-school','zone-recovery'].includes(data.kind)?'dashed':'solid',zIndex:8,bubble:data.kind!=='filing'});
   if(roles[data.kind]&&path[0]?.length>1){
    const edge=path[0].slice(0,-1), middle=edge.reduce((total,xy)=>[total[0]+xy[0]/edge.length,total[1]+xy[1]/edge.length],[0,0]);
    next.push(new AMap.Marker({position:middle,anchor:'center',zIndex:16,bubble:true,
     content:'<div class="marker" style="color:'+color+'">'+esc(data.name)+'</div>'}));
   }
   if(data.kind==='filing')shape.on('click',()=>bridge.selectEvent(data.id));
   next.push(shape);
  }
  if(!routes.some(r=>r.style==='preview'||r.style==='flight'))routeFocus='';
  for(const route of routes){
   const path=route.p.map(i=>loc[i]), duty=route.style==='filing';
   if(duty||(!route.legs.length&&!route.phases.length)){
    if(path.length>1){
     const line=new AMap.Polyline({path,strokeColor:route.style==='actual'?'#347a78':prediction,strokeWeight:duty?3:4,
      strokeStyle:duty?'dashed':'solid',showDir:!duty,zIndex:68,bubble:!duty});
     if(duty)line.on('click',()=>bridge.selectEvent(route.id));
     next.push(line);
    }
   }else for(const leg of route.legs){
    const color=leg.state==='restricted'?danger:leg.state==='clear'?prediction:warning;
    const coords=leg.p.map(i=>loc[i]);
    const same=coords[0][0]===coords[1][0]&&coords[0][1]===coords[1][1];
    next.push(same?new AMap.Circle({center:coords[0],radius:12,strokeColor:color,strokeWeight:4,fillOpacity:0,zIndex:68,bubble:true}):
     new AMap.Polyline({path:coords,strokeColor:color,strokeWeight:4,showDir:true,zIndex:68,bubble:true}));
   }
   for(const phase of route.phases){
    if(phase.phase==='起飞爬升'||phase.phase==='降落')continue;
    const coords=phase.p.map(i=>loc[i]), returning=phase.phase==='返航', filming=phase.phase==='拍摄';
    if(!returning&&!filming&&route.legs.length)continue;
    const same=coords[0][0]===coords[1][0]&&coords[0][1]===coords[1][1];
    const tint=filming?'#6941cf':returning?'#45566e':prediction;
    if(!same)next.push(new AMap.Polyline({path:coords,strokeColor:tint,strokeWeight:filming?5:3,
      strokeStyle:returning?'dashed':'solid',showDir:true,zIndex:route.legs.length?64:returning?73:70,bubble:true}));
   }
   for(const shot of route.shots){
    if(shot.id===route.selected_shot&&shot.p.length>1)next.push(new AMap.Polyline({
     path:shot.p.map(i=>loc[i]),strokeColor:'#803fff',strokeWeight:8,strokeOpacity:route.legs.length?.38:1,zIndex:route.legs.length?64:82,bubble:true
    }));
    const marker=new AMap.Marker({position:loc[shot.i],anchor:'bottom-center',zIndex:94,bubble:false,
      offset:new AMap.Pixel(p.photo_preview?(shot.id===route.selected_shot?30:18):0,p.photo_preview?-26:0),
      content:'<div class="marker selected photo-shot">'+shot.index+' '+esc(shot.title)+'</div>'});
    marker.on('click',()=>bridge.selectEvent('photo-shot:'+shot.id));
    next.push(marker);
   }
   if(duty||route.style==='actual'||route.style==='checked-edit')continue;
   if((route.style==='preview'||route.style==='flight')&&routeFocus!==route.key){
    routeFocus=route.key;
    if(path.length>1){
     const helper=new AMap.Polyline({path});
     map.setFitView([helper],false,[78,62,78,62],19);
    }else map.panTo(loc[route.takeoffIndex]);
   }
   if(route.subjectIndex!==null)next.push(new AMap.Marker({position:loc[route.subjectIndex],anchor:'bottom-center',
    content:'<div class="marker selected">'+esc(route.subject_name)+'</div>',bubble:true,zIndex:78}));
   const takeoff=route.phases.length?(route.provisional_takeoff?'候选起降点':'起降点'):
     (route.provisional_takeoff?'候选起飞点':'起飞点');
   next.push(new AMap.Marker({position:loc[route.takeoffIndex],content:'<div class="marker">'+takeoff+'</div>',
    bubble:true,offset:new AMap.Pixel(-34,16),zIndex:76}));
   if(!route.shots.length)route.wp.forEach((w,i)=>{
    next.push(new AMap.Marker({position:loc[w.i],bubble:true,zIndex:80,
     content:'<div class="marker"'+(w.h>(route.heightLimit||120)?' style="background:#ffda83;color:#522b03"':'')+'>'+(i+1)+' · '+esc(w.h)+' m</div>'}));
   });
   if(route.current!==null){
    next.push(new AMap.CircleMarker({center:loc[route.current],radius:7,strokeColor:'#fff',strokeWeight:2,
      fillColor:prediction,fillOpacity:1,zIndex:100,bubble:true}));
   }
  }
  for(const pin of pins)next.push(new AMap.Marker({position:loc[pin.i],bubble:true,
    content:'<div class="marker">'+esc(pin.data.name)+'</div>',anchor:'bottom-center',zIndex:60}));
  if(aerialPoints){
   if(aerialPoints.line.length>1)next.push(new AMap.Polyline({path:aerialPoints.line.map(i=>loc[i]),
    strokeColor:prediction,strokeWeight:2,strokeOpacity:.8,zIndex:65,bubble:true}));
   if(aerialPoints.segment.length===2)next.push(new AMap.Polyline({path:aerialPoints.segment.map(i=>loc[i]),
    strokeColor:prediction,strokeWeight:5,strokeOpacity:.9,zIndex:70,bubble:true}));
   const when=aerial.time?' · '+esc(aerial.time):'';
   next.push(new AMap.Marker({position:loc[aerialPoints.center],bubble:true,anchor:'bottom-center',zIndex:86,
    content:'<div class="marker">◆ 天气查询点'+when+'</div>'}));
   for(const entry of aerialPoints.scenes){
    const scene=entry.data,isSelected=scene.id===aerial.selected;
    const mark=new AMap.Marker({position:loc[entry.i],anchor:'bottom-center',zIndex:isSelected?98:90,
     content:'<div class="marker'+(isSelected?' selected':'')+'">'+scene.number+' '+esc(scene.name)+'</div>'});
    mark.on('click',()=>bridge.selectEvent(scene.id));next.push(mark);
    if(isSelected&&focusedScenic!==aerial.focus){
     map.panTo(loc[entry.i]);
    }
   }
   if(aerialPoints.scenes.length)focusedScenic=aerial.focus;
  }else focusedScenic='';
  if(showRisk)for(const g of grid){const value=g.data[2];next.push(new AMap.Circle({center:loc[g.i],radius:65,strokeWeight:0,fillColor:value>=50?danger:value>=30?warning:ok,fillOpacity:.2,zIndex:10}));}
  for(const a of areas)next.push(new AMap.Circle({center:loc[a.i],radius:a.data.radius_m,strokeColor:ok,strokeStyle:'dashed',strokeWeight:1,fillOpacity:.035,zIndex:20}));
  for(const e of events){const o=e.data,color=o.priority==='优先核验'?danger:prediction;
   if(e.history.length>1)next.push(new AMap.Polyline({path:e.history.map(i=>loc[i]),strokeColor:color,
      strokeWeight:2,strokeOpacity:.75,zIndex:22}));
   if(o.track_state==='仅目击待核实')next.push(new AMap.Circle({center:loc[e.i],radius:o.accuracy_m,
      fillColor:warning,strokeColor:warning,strokeWeight:1,fillOpacity:.12,zIndex:23}));
   if(e.future.length){next.push(new AMap.Polyline({path:e.future.map(t=>loc[t.i]),strokeColor:prediction,strokeWeight:o.id===selected?3:2,strokeStyle:'dashed',zIndex:30}));const last=e.future.at(-1);next.push(new AMap.Circle({center:loc[last.i],radius:last.data.radius_m,strokeColor:prediction,strokeStyle:'dashed',strokeWeight:1,fillOpacity:.05,zIndex:25}));}
   const marker=new AMap.Marker({position:loc[e.i],content:'<div class="marker'+(selected===o.id?' selected':'')+'">'+esc(o.name)+'</div>',anchor:'bottom-center',zIndex:selected===o.id?120:100});
   marker.on('click',()=>bridge.selectEvent(o.id));next.push(marker);eventMarkers.push(marker);
  }
  map.add(next);if(overlays.length)map.remove(overlays);overlays=next;
  if(!fitted&&eventMarkers.length){map.setFitView(eventMarkers,false,[70,70,70,70],16);homeCenter=map.getCenter();fitted=true}
  if(statusNode.textContent.startsWith('底图已连接；事件坐标暂不可用'))status('');
 }catch(e){status('底图已连接；事件坐标暂不可用（'+safeCode(e.name)+' / '+safeCode(e.message)+'），保留上一帧。')}
 finally{running=false;if(pending)setTimeout(drawNext,100)}
}
function selectEvent(id){if(selected===id)return;selected=id;if(payload)updateScene(payload)}
function setRisk(value){if(showRisk===value)return;showRisk=value;if(payload)updateScene(payload)}
function home(){if(map&&homeCenter)map.setZoomAndCenter(homeZoom,homeCenter)}


function showTrainingAircraft(list, layers) {
 TrainingMap.update(list,document.getElementById('map'),p=>map.lngLatToContainer(p),
                    id=>bridge.selectEvent(id),'aircraft.svg',payload?.flight_follow?p=>map.panTo(p):null,layers);
}
let photoRevision=0,photoFrame=null,photoPosition=null,photoDot=null,photoRay=null;
let pendingPhotoKey='',photoError='';
function paintPhotoFrame(){
 if(!photoDot||!photoPosition)return;
 const point=map.lngLatToContainer(photoPosition);
 if(!Number.isFinite(point?.x)||!Number.isFinite(point?.y))return;
 photoDot.style.left=point.x+'px';
 photoDot.style.top=point.y+'px';
}
function drawPhotoFrame(a,b){
 if(!photoFrame)return;
 const {fraction,body_heading,heading}=photoFrame;
 photoPosition=a.map((value,index)=>value+(b[index]-value)*fraction);
 if(!photoDot){
  photoDot=document.createElement('div');
  photoDot.className='photo-aircraft';
  photoDot.setAttribute('aria-hidden','true');
  const airframe=document.getElementById('photo-airframe').content.cloneNode(true);
  photoRay=document.createElement('span');
  photoRay.className='photo-camera-ray';
  photoDot.append(photoRay,airframe);
  document.getElementById('map').append(photoDot);
 }
 photoDot.lastElementChild.style.transform='rotate('+(body_heading||0)+'deg)';
 photoRay.hidden=heading===null;
 photoRay.style.transform='rotate('+heading+'deg)';
 paintPhotoFrame();
}
function setPhotoFrame(frame){
 if(!ready)return;
 photoFrame=frame;
 if(!frame){
  ++photoRevision;
  pendingPhotoKey='';
  photoDot?.remove();
  photoDot=null;photoRay=null;photoPosition=null;photoError='';
  return;
 }
 const points=[frame.a,frame.b],found=points.map(nativeAt);
 if(found.every(Boolean)){drawPhotoFrame(...found);return}
 const key=points.map(SkyMapCore.positionKey).join('|');
 if(key===pendingPhotoKey)return;
 pendingPhotoKey=key;
 const revision=++photoRevision;
 SkyMapCore.convertBatch(points,convert,cache).then(()=>{
  if(revision!==photoRevision)return;
  pendingPhotoKey='';
  if(photoFrame)setPhotoFrame(photoFrame);
 }).catch(err=>{
  if(revision!==photoRevision)return;
  pendingPhotoKey='';
  photoError=safeCode(err.message);
  status('航线预演坐标待重试');
 });
}
let aircraftRevision=0, aircraftConversion=null;
async function setAircraft(list) {
 const current=++aircraftRevision;
 const generation=sceneGeneration;
 if(calibrating)return;
 const scene=payload;
 const layers={formations:scene?.formations,links:scene?.agent_links,posts:scene?.observation_posts,
  zones:payload?.observation_posts?.length?payload.zones?.filter(z=>z.kind.startsWith('zone-')):[]};
 const mapLayerPoints=convert=>({
  civic:Boolean(scene?.civic_replay),
  highlights:(scene?.active_highlights||[]).map(z=>({...z,point:convert(z.point)})),
  staff:(scene?.staff_markers||[]).map(s=>({...s,point:convert([s.lon,s.lat]),route:s.route.map(convert),
   target_point:s.target_point?convert(s.target_point):null})),
  civicLinks:(scene?.civic_links||[]).map(l=>({...l,start:convert(l.start),end:convert(l.end)})),
  formations:(layers.formations||[]).map(g=>({...g,hull:g.hull.map(convert),points:g.points.map(convert)})),
  links:(layers.links||[]).map(l=>({...l,start:convert(l.start),end:convert(l.end)})),
  posts:(layers.posts||[]).map(p=>({...p,point:convert([p.lon,p.lat])})),
  zones:(layers.zones||[]).map(z=>({...z,rings:z.rings.map(r=>r.map(convert))}))
 });
 const mapRows=convert=>list.map(row=>({...row,xy:convert([row.lon,row.lat]),
  tail:(row.tail||[]).map(convert),prediction:(row.prediction||[]).map(convert),
  connector:row.connector?convert(row.connector):null,cpa:row.cpa?{...row.cpa,point:convert(row.cpa.point)}:null}));
 if(sceneWarp){
  const display=mapRows(sceneWarp);
  let missing=false;
  const mapped=mapLayerPoints(point=>{const value=sceneWarp(point);if(!value)missing=true;return value});
  if(!missing&&display.every(r=>r.xy)){
   showTrainingAircraft(display.map(row=>({...row,lon:row.xy[0],lat:row.xy[1]})),mapped);
   return;
  }
 }
 const points=[], add=p=>points.push(p)-1;
 mapRows(add);mapLayerPoints(add);
 if(!points.length){showTrainingAircraft([]);return}
 if(aircraftConversion?.generation===generation)return;
 const request={generation};
 aircraftConversion=request;
 try{
  const converted=await SkyMapCore.convertBatch(points,batch=>convert(batch,
   ()=>generation===sceneGeneration),cache);
  if(generation!==sceneGeneration||!payload?.aircraft?.length&&list.length)return;
  let index=0;
  const next=()=>converted[index++];
  showTrainingAircraft(mapRows(next).map(row=>({...row,lon:row.xy[0],lat:row.xy[1]})),mapLayerPoints(next));
 }catch(_){if(current===aircraftRevision)status('模拟航迹坐标暂未转换')}
 finally{
  if(aircraftConversion===request){
   aircraftConversion=null;
   if(generation===sceneGeneration&&current!==aircraftRevision)setAircraft(payload.aircraft||[]);
  }
 }
}
