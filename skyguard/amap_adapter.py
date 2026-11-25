"""Loopback-only AMap boundary. No credentials enter domain state or exports."""
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from threading import Thread,RLock
from urllib.parse import urlsplit,parse_qsl,urlencode
import ctypes
from ctypes import wintypes
import json,mimetypes,os,secrets,re
import httpx

def display_payload(payload):
    observation_fields={'id','name','lat','lon','age','priority','prediction','accuracy_m','history','track_state','linked_track'}
    area_fields={'id','name','lat','lon','radius_m','index'}
    result={'observations':[{k:v for k,v in o.items() if k in observation_fields} for o in payload['observations']],
            'areas':[{k:v for k,v in a.items() if k in area_fields} for a in payload['areas']],'grid':payload['grid']}
    if 'sandbox_edit' in payload:
        result['sandbox_edit'] = payload['sandbox_edit']
    if payload.get('aircraft'):
        result['aircraft']=payload['aircraft']
    if payload.get('flight_control'):
        result['flight_control']=True
        result['flight_follow']=bool(payload.get('flight_follow'))
    if 'city' in payload:
        result.update({key:payload[key] for key in ('city','center','public')})
        result['zones']=[{k:v for k,v in zone.items() if k in {'id','name','rings','kind','highlight','filing_style'}}
                         for zone in payload.get('zones',[])]
        result['pins']=[{k:v for k,v in pin.items() if k in {'name','lon','lat'}}
                        for pin in payload.get('pins',[])]
        if payload.get('routes'):
            result['routes'] = payload['routes']
        tactical_fields={'formations':{'id','level','members','hull','points'},
                         'agent_links':{'start','end','label','target','walking','post_id'},
                         'observation_posts':{'id','name','lon','lat'},
                         'active_highlights':{'point','name','radius','recovery'},
                         'staff_markers':{'id','name','role','lon','lat','status','eta_s','route','target_point'},
                         'civic_links':{'start','end','label','recovery','opacity'}}
        for key, fields in tactical_fields.items():
            if payload.get(key):
                result[key]=[{name:row[name] for name in fields if name in row} for row in payload[key]]
        for key in ('route_epoch', 'route_ack', 'route_step', 'route_height', 'point_mode',
                    'subject_pick', 'grid_visible', 'training_bounds', 'training_scene', 'selected_waypoint',
                    'photo_preview', 'civic_replay'):
            if key in payload:
                result[key] = payload[key]
        if payload.get('route_editing'):
            result['route_editing'] = True
        if 'draft' in payload:
            result['draft']=payload['draft']
        if payload.get('aerial'):
            result['aerial']=payload['aerial']
    return result

class MapCredentials:
    def __init__(self,key='',code=''):
        self.key=key.strip();self.code=code.strip()
    @property
    def configured(self):return bool(self.key and self.code)
    def validate(self):
        if not all(re.fullmatch(r'[A-Za-z0-9_-]{16,128}',s) for s in (self.key,self.code)):
            raise ValueError('请填写完整的Web端Key及配套安全密钥，不要填写Web服务Key或其他密码。')

def _dpapi(data,protect=True):
    if os.name!='nt':raise RuntimeError('记住密钥功能需要Windows；可取消勾选，仅保留本次会话。')
    class Blob(ctypes.Structure):_fields_=[('cbData',wintypes.DWORD),('pbData',ctypes.POINTER(ctypes.c_ubyte))]
    memory=ctypes.create_string_buffer(data);incoming=Blob(len(data),ctypes.cast(memory,ctypes.POINTER(ctypes.c_ubyte)));out=Blob()
    crypt=ctypes.WinDLL('crypt32',use_last_error=True);kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.LocalFree.argtypes=[ctypes.c_void_p];kernel.LocalFree.restype=ctypes.c_void_p
    fn=crypt.CryptProtectData if protect else crypt.CryptUnprotectData
    fn.argtypes=[ctypes.POINTER(Blob),ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(Blob)]
    fn.restype=wintypes.BOOL
    if not fn(ctypes.byref(incoming),None,None,None,None,1,ctypes.byref(out)):
        raise RuntimeError('Windows无法保护或读取地图密钥，请在本机重新填写。')
    try:return ctypes.string_at(out.pbData,out.cbData)
    finally:kernel.LocalFree(ctypes.cast(out.pbData,ctypes.c_void_p))

class CredentialStore:
    def __init__(self,root):self.path=Path(root)/'map-credentials.bin'
    def load(self):
        key=os.getenv('SKYGUARD_AMAP_KEY','');code=os.getenv('SKYGUARD_AMAP_SECURITY_CODE','')
        if key and code:return MapCredentials(key,code)
        if not self.path.exists():return MapCredentials()
        data=json.loads(_dpapi(self.path.read_bytes(),False));return MapCredentials(data['key'],data['code'])
    def save(self,value,remember):
        value.validate()
        if remember:
            encrypted=_dpapi(json.dumps({'key':value.key,'code':value.code}).encode())
            self.path.parent.mkdir(parents=True,exist_ok=True)
            temp=self.path.with_suffix('.tmp');temp.write_bytes(encrypted);temp.replace(self.path)
        else:self.path.unlink(missing_ok=True)
    def clear(self):self.path.unlink(missing_ok=True)

class MapServer:
    def __init__(self,assets,credentials,channel_script,transport=None):
        self.assets=Path(assets);self.credentials=credentials;self.lock=RLock();self.token=secrets.token_urlsafe(24)
        self.channel_script=channel_script;self.client=httpx.Client(timeout=12,follow_redirects=False,transport=transport)
        owner=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):owner.handle(self)
        self.http=ThreadingHTTPServer(('127.0.0.1',0),Handler);self.http.daemon_threads=True
        self.origin=f'http://127.0.0.1:{self.http.server_port}'
        self.url=f'{self.origin}/{self.token}/amap.html'
        self.thread=Thread(target=self.http.serve_forever,daemon=True);self.thread.start()
    def configure(self,credentials):
        with self.lock:self.credentials=credentials
    def close(self):
        self.http.shutdown();self.http.server_close();self.client.close()
    def reply(self,h,status,data,kind='application/json; charset=utf-8'):
        h.send_response(status);h.send_header('Content-Type',kind);h.send_header('Cache-Control','no-store')
        h.send_header('Referrer-Policy','strict-origin-when-cross-origin');h.send_header('X-Content-Type-Options','nosniff')
        h.send_header('Content-Length',str(len(data)));h.end_headers()
        try:h.wfile.write(data)
        except (BrokenPipeError,ConnectionResetError):pass
    def handle(self,h):
        url=urlsplit(h.path);origin=h.headers.get('Origin')
        if h.headers.get('Host')!=self.origin.removeprefix('http://') or (origin and origin!=self.origin):
            return self.reply(h,403,b'{}')
        with self.lock:credentials=self.credentials
        prefix=f'/{self.token}/'
        if url.path.startswith('/_AMapService/'):
            if not credentials.configured or not h.headers.get('Referer','').startswith(self.origin+prefix):
                return self.reply(h,403,b'{}')
            path=url.path.removeprefix('/_AMapService')
            if not re.fullmatch(r'/v[34]/[A-Za-z0-9_./-]+',path) or '..' in path:
                return self.reply(h,404,b'{}')
            host='https://webapi.amap.com' if path.startswith('/v4/map/styles') else 'https://restapi.amap.com'
            params=[(k,v) for k,v in parse_qsl(url.query,keep_blank_values=True) if k.lower() not in ('jscode','key')]
            params.extend([('key',credentials.key),('jscode',credentials.code)])
            try:
                r=self.client.get(host+path,params=params,headers={'Referer':self.origin+'/'})
                return self.reply(h,r.status_code,r.content,r.headers.get('content-type','application/json'))
            except httpx.HTTPError:return self.reply(h,502,b'{"error":"upstream_unavailable"}')
        if not url.path.startswith(prefix):return self.reply(h,404,b'{}')
        name=url.path[len(prefix):]
        if name=='config.json':
            return self.reply(h,200,json.dumps({'configured':credentials.configured,'key':credentials.key if credentials.configured else '',
                    'serviceHost':self.origin+'/_AMapService'}).encode())
        if name=='qwebchannel.js':return self.reply(h,200,self.channel_script,'application/javascript')
        if name not in {'amap.html','amap.js','map-core.js','training-map.js','aircraft.svg','NotoSansSC.ttf'}:return self.reply(h,404,b'{}')
        file=self.assets/name
        if not file.is_file():return self.reply(h,404,b'{}')
        data=file.read_bytes()
        if name=='amap.html':
            frame=(self.assets/'aircraft.svg').read_bytes()
            data=data.replace(b'<!-- photo-airframe -->',frame)
        self.reply(h,200,data,mimetypes.guess_type(name)[0] or 'application/octet-stream')
