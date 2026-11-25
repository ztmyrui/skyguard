const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const nodes=new Map();const node=id=>{if(!nodes.has(id))nodes.set(id,{textContent:'',style:{}});return nodes.get(id)};
const context={document:{getElementById:node,documentElement:{dataset:{}}},getComputedStyle:root=>({getPropertyValue:()=>root.dataset.theme==='klein'?'#1F3FD6':'#7EA6FF'}),qt:{webChannelTransport:{}},QWebChannel:function(){},setTimeout,clearTimeout,Map,console,URLSearchParams,AbortSignal,AbortController,window:null};context.window=context;
let complete,click,selectedCity,selectedPoint,style,stroke,shapeDrawn=0;let calls=0;
context.AMap={Map:function(_element,options){style=options.mapStyle;return {
 setMapStyle:value=>{style=value},
 on:(event,cb)=>{if(event==='complete')complete=cb;if(event==='click')click=cb},
 setZoomAndCenter:(zoom,center)=>{selectedCity=[zoom,center]},remove(){},add(items){shapeDrawn+=Array.isArray(items)?items.length:1}
}},LngLat:function(lng,lat){this.lng=lng;this.lat=lat},Circle:function(options){stroke=options.strokeColor},Marker:function(){},
 convertFrom:(points,type,cb)=>{calls++;cb('error',{info:'INVALID_USER_SCODE'})}};
context.TrainingMap={fly(){},update(){}};
vm.createContext(context);vm.runInContext(fs.readFileSync(require('node:path').join(__dirname,'../skyguard/assets/amap.js'),'utf8'),context);
(async()=>{
 await context.initialize();assert.equal(calls,0,'base map must not wait for event conversion');assert.equal(typeof complete,'function');
 assert.equal(style,'amap://styles/normal');
 context.setTheme('night');assert.equal(style,'amap://styles/darkblue');
 style='amap://styles/normal';
 context.setTheme('night');assert.equal(style,'amap://styles/darkblue','same theme repairs provider style');
 assert.equal(context.document.documentElement.dataset.theme,'night');
 const providerNotes=[];
 context.publishStatus=text=>providerNotes.push(text);
 vm.runInContext('bridge={mapReady(){},mapStatus:publishStatus}',context);
 style='amap://styles/normal';complete();
 assert.equal(style,'amap://styles/darkblue','map completion reapplies the current theme');
 assert.equal(node('status').textContent,'','normal map display has no persistent status');
 assert.deepEqual(providerNotes,[''],'normal map display clears the Qt status as well');
 context.takePoint=(...args)=>{selectedPoint=args};
 vm.runInContext("bridge.publicPoint=takePoint; running=true; updateScene({public:true,city:'shenzhen',center:[114.0579,22.5431],draft:{native:true,form:'circle',points:[[114.058,22.54]],radius:75}})",context);
 assert.equal(selectedCity[0],14);assert.equal(selectedCity[1][0],114.0579);
 await click({lnglat:{lat:22.54,lng:114.058}});
 assert.deepEqual(selectedPoint,['shenzhen',22.54,114.058]);
 assert.equal(shapeDrawn,2);assert.equal(calls,0,'opening the city, selecting and drawing the preview have no conversion dependency');
 assert.equal(stroke,'#7EA6FF');
 context.setTheme('klein');
 assert.equal(style,'amap://styles/normal');
 assert.equal(stroke,'#1F3FD6','existing draft uses the new theme without conversion');
 assert.equal(shapeDrawn,4);assert.equal(calls,0);
 vm.runInContext('payload=null; pending=null; running=false',context);
 let requests=0;context.fetch=async(url)=>{requests++;assert.match(url,/s=rsv3/);assert.doesNotMatch(url,/key=|jscode=/);return {ok:true,json:async()=>({status:'1',locations:'112.975,28.187'})}};
 const converted=await vm.runInContext('convert([[112.97,28.19]])',context);assert.equal(converted[0][0],112.975);
 await vm.runInContext('convert([[112.97,28.19]])',context);assert.equal(calls,1,'use compatible local request after SDK failure');assert.equal(requests,2);
 context.fetch=async()=>({ok:true,json:async()=>({status:'0',info:'INVALID_USER_SCODE'})});
 await assert.rejects(()=>vm.runInContext('convert([[112.97,28.19]])',context),/INVALID_USER_SCODE/);
 context.fetch=async()=>({ok:true,json:async()=>({status:'1',locations:'not,a,coordinate'})});
 await assert.rejects(()=>vm.runInContext('convert([[112.97,28.19]])',context),/CONVERSION_FAILED/);
 assert.equal(vm.runInContext("safeCode('https://example.test/?key=01234567890123456789012345678901')",context),'SERVICE_RESPONSE_ERROR');
 assert.equal(vm.runInContext("conversionCode('error',{info:'invalid user: INVALID_USER_DOMAIN'})",context),'INVALID_USER_DOMAIN');
 const places=[];
 context.placeResolved=(revision,json)=>places.push([revision,JSON.parse(json)]);
 vm.runInContext('bridge.publicPlaceResolved=placeResolved',context);
 context.SkyMapCore={inverseBatch:async coordinates=>{
  assert.equal(JSON.stringify(coordinates),'[[112.975,28.187]]');
  return [[112.97,28.19]];
 }};
 context.fetch=async url=>{
  const [path,query]=url.split('?');
  assert.equal(path,'/_AMapService/v3/place/text');
  assert.deepEqual(Object.fromEntries(new URLSearchParams(query)),{s:'rsv3',keywords:'湿地公园',city:'长沙',
   citylimit:'true',offset:'5',extensions:'base',output:'JSON'});
  return {ok:true,json:async()=>({status:'1',pois:[{name:'公园',location:'112.975,28.187',type:'景点',address:'长沙'},
   {name:'空坐标',location:' , '},{name:'异常',location:'190,10'},null]})};
 };
 await context.lookupCityPlace(41,'长沙','湿地公园');
 assert.deepEqual(places.pop(),[41,[{name:'公园',type:'景点',address:'长沙',location:[112.97,28.19]}]]);
 context.fetch=async()=>{throw Error('upstream error');};
 await context.lookupCityPlace(42,'长沙','湿地公园');
 assert.deepEqual(places.pop(),[42,[]]);
 context.setTimeout=(fn,ms)=>setTimeout(fn,ms===6000?1:ms);
 context.fetch=async(_,{signal})=>new Promise((_,reject)=>signal.addEventListener('abort',reject));
 await context.lookupCityPlace(43,'长沙','湿地公园');
 assert.deepEqual(places.pop(),[43,[]]);
 const roads=[];
 context.roadResolved=(token,json)=>roads.push([token,JSON.parse(json)]);
 vm.runInContext('bridge.sandboxRoadResolved=roadResolved; payload={sandbox_edit:{}}',context);
 context.AMap.plugin=(_plugins,callback)=>callback();
 context.AMap.Walking=function(){this.search=(_a,_b,callback)=>callback('error')};
 context.SkyMapCore={convertBatch:async points=>points,inverseBatch:async points=>points};
 context.fetch=async url=>{
  assert.match(url,/\/_AMapService\/v3\/direction\/walking\?/);
  return {ok:true,json:async()=>({route:{paths:[{steps:[{polyline:'112.97,28.19;112.971,28.191'}]}]}})};
 };
 await context.lookupSandboxRoad('walking','foot',[112.97,28.19],[112.971,28.191]);
 assert.deepEqual(roads.pop(),['walking',[[112.97,28.19],[112.971,28.191]]]);
 context.fetch=async url=>{
  assert.match(url,/\/_AMapService\/v4\/direction\/bicycling\?/);
  return {ok:true,json:async()=>({data:{paths:[{steps:[{polyline:'112.97,28.19;112.972,28.192'}]}]}})};
 };
 await context.lookupSandboxRoad('riding','vehicle',[112.97,28.19],[112.972,28.192]);
 assert.deepEqual(roads.pop(),['riding',[[112.97,28.19],[112.972,28.192]]]);
 context.fetch=async()=>{throw Error('provider unavailable')};
 await context.lookupSandboxRoad('offline','foot',[112.97,28.19],[112.971,28.191]);
 assert.deepEqual(roads.pop(),['offline',[]]);
 console.log('AMap lifecycle: independent basemap, coordinate error, and redaction checks passed');
})().catch(e=>{console.error(e);process.exitCode=1});
