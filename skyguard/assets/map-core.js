/* SDK-independent batching and reverse solving, testable without an AMap account. */
(function(root){
function positionKey(p){return p[0].toFixed(7)+','+p[1].toFixed(7)}
async function convertBatch(points,convert,cache){
 const unique=new Map();for(const p of points)if(!cache.has(positionKey(p)))unique.set(positionKey(p),p);
 const list=[...unique.values()];
 for(let i=0;i<list.length;i+=40){const batch=list.slice(i,i+40),mapped=await convert(batch);
  if(mapped.length!==batch.length)throw Error('conversion_failed');
  for(let j=0;j<batch.length;j++)cache.set(positionKey(batch[j]),mapped[j]);}
 if(cache.size>5000){const keep=points.map(p=>[positionKey(p),cache.get(positionKey(p))]);cache.clear();for(const p of keep)cache.set(...p)}
 return points.map(p=>cache.get(positionKey(p)));
}
async function inverse(target,convert){
 let estimate=[...target];
 for(let i=0;i<5;i++){const [mapped]=await convert([estimate]);const error=[mapped[0]-target[0],mapped[1]-target[1]];
  if(Math.max(...error.map(Math.abs))<1e-7)return estimate;
  estimate=[estimate[0]-error[0],estimate[1]-error[1]];}
 const [check]=await convert([estimate]);if(Math.max(Math.abs(check[0]-target[0]),Math.abs(check[1]-target[1]))>2e-6)throw Error('conversion_failed');
 return estimate;
}
async function inverseBatch(targets,convert){
 let estimates=targets.map(point=>[...point]);
 for(let attempt=0;attempt<5;attempt++){
  const mapped=[];
  for(let i=0;i<estimates.length;i+=40)mapped.push(...await convert(estimates.slice(i,i+40)));
  if(mapped.length!==targets.length)throw Error('conversion_failed');
  const offsets=mapped.map((point,i)=>[point[0]-targets[i][0],point[1]-targets[i][1]]);
  if(offsets.every(offset=>offset.every(value=>Math.abs(value)<=1e-6)))return estimates;
  estimates=estimates.map((point,i)=>[point[0]-offsets[i][0],point[1]-offsets[i][1]]);
 }
 throw Error('conversion_failed');
}
function viewportWarp(bounds, anchors){
 const [west,south,east,north]=bounds;
 return ([lon,lat])=>{
  if(lon<west||lon>east||lat<south||lat>north)return null;
  const u=(lon-west)/(east-west),v=(lat-south)/(north-south);
  return [0,1].map(i=>anchors[0][i]*(1-u)*(1-v)+anchors[1][i]*u*(1-v)+
                      anchors[2][i]*(1-u)*v+anchors[3][i]*u*v);
 };
}
root.SkyMapCore={convertBatch,inverse,inverseBatch,positionKey,viewportWarp};
})(typeof window==='undefined'?globalThis:window);
