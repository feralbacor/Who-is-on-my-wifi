"""Home Network Monitor: scans your home network every few seconds and shows it live in a web dashboard.

Usage: pythonw monitor.py  ->  http://localhost:8765      (python monitor.py --test = self-check)
"""
import hashlib
import json
import os
import random
import re
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

AQUI = os.path.dirname(os.path.abspath(__file__))
DATOS = os.path.join(AQUI, 'dispositivos.json')
CONFIG = os.path.join(AQUI, 'config.json')  # Telegram y router (ver config.example.json); no se sube a git
PUERTO = 8765
PAUSA = 10  # segundos entre escaneos (cada escaneo tarda unos 8 s)
FALLOS_PARA_DESCONECTAR = 2  # los móviles en reposo a veces no contestan a un escaneo suelto
PAGINAS_BANDA = {'2.4G': 'net_wlanm_assoc1_t.gch', '5G': 'net_wlanm_assoc2_t.gch'}  # "Associated Devices" de cada wifi
PAUSA_ROUTER = 60

lock = threading.Lock()
# asociados: lo último que dijo el router de cada wifi ({mac: {'banda', 'ip', 'senal'}}), None si no se pudo leer
estado = {'red': '', 'gateway': '', 'ultimo_escaneo': None, 'error': '', 'dispositivos': {}, 'eventos': [],
          'asociados': None, 'router': 'off'}  # router: 'off' (sin configurar), 'ok' o 'error'


def config():
    """config.json, o {} si no existe o está mal escrito (el monitor funciona sin él)."""
    try:
        with open(CONFIG, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def cargar():
    if os.path.exists(DATOS):
        with open(DATOS, encoding='utf-8') as f:
            guardado = json.load(f)
        estado['dispositivos'] = guardado.get('dispositivos', {})
        estado['eventos'] = guardado.get('eventos', [])
    ingles = {'Este PC': 'This PC', 'MAC privada': 'Private MAC', 'Fabricante desconocido': 'Unknown vendor'}
    for d in estado['dispositivos'].values():
        d['online'] = False  # hasta que el primer escaneo diga lo contrario
        d['nombre'] = ingles.get(d['nombre'], d['nombre'])  # datos guardados por la versión en español
        d['fabricante'] = ingles.get(d['fabricante'], d['fabricante'])


def guardar():
    tmp = DATOS + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump({'dispositivos': estado['dispositivos'], 'eventos': estado['eventos']}, f, ensure_ascii=False)
    os.replace(tmp, DATOS)


def es_mac_privada(mac):
    return mac[1].upper() in '26AE'  # bit de "administrada localmente": móviles que ocultan su MAC real


def evento(mac, tipo, ip, cuando):
    estado['eventos'].append({'t': cuando, 'mac': mac, 'ip': ip, 'tipo': tipo})
    del estado['eventos'][:-300]


def aplicar(r, ahora=None):
    """Actualiza dispositivos y eventos con un escaneo más lo último que dijo el router.
    Devuelve los desconocidos que entran por primera vez."""
    if ahora is None:
        ahora = time.time()
    nuevos = []
    with lock:
        router_ok = estado['asociados'] is not None
        asociados = estado['asociados'] or {}
        # el router sabe quién sigue en el wifi aunque esté dormido y no conteste al escaneo
        vistos = {mac: a['ip'] for mac, a in asociados.items() if a['ip'] not in ('', '0.0.0.0')}
        vistos.update({v['mac']: v['ip'] for v in r['vecinos']})
        vistos[r['mac']] = r['ip']
        silencioso = estado['ultimo_escaneo'] is None  # al arrancar no avisamos de lo que ya estaba conectado
        ds = estado['dispositivos']
        estreno = not ds  # el primer uso del monitor: todo es "nuevo", no avisamos
        for mac, ip in vistos.items():
            d = ds.get(mac)
            if d is None:
                d = ds[mac] = {'mac': mac, 'nombre': '', 'conocido': False, 'online': False, 'primera_vez': ahora,
                               'fabricante': 'Private MAC' if es_mac_privada(mac) else ''}
                if mac == r['mac']:
                    d.update(nombre='This PC', conocido=True)
                elif ip == r['gateway']:
                    d.update(nombre='Router', conocido=True)
                if not estreno:  # aunque sea al arrancar: un intruso conectado al encender el PC también cuenta
                    evento(mac, 'nuevo', ip, ahora)
                    if not d['conocido']:
                        nuevos.append(d)
            elif not d['online'] and not silencioso:
                evento(mac, 'conectado', ip, ahora)
            if not d['online']:
                d['desde'] = ahora
            d.update(ip=ip, online=True, ultima_vez=ahora, fallos=0)
        for mac, d in ds.items():
            if d['online'] and mac not in vistos:
                d['fallos'] = d.get('fallos', 0) + 1
                if d['fallos'] >= FALLOS_PARA_DESCONECTAR:
                    d['online'] = False
                    evento(mac, 'desconectado', d['ip'], ahora)
            a = asociados.get(mac)
            # shortcut: conectado y en ninguna wifi = cable; uno que acaba de irse del wifi puede salir "cable" un momento
            cable = 'cable' if router_ok and d['online'] and d['ip'] != r['gateway'] else ''
            d.update(banda=a['banda'] if a else cable, senal=a['senal'] if a else None)
        estado.update(red=r['red'], gateway=r['gateway'], ultimo_escaneo=ahora, error='')
        guardar()
    return nuevos


def avisar_telegram(d):
    """Manda a Telegram (config.json) que ha entrado un aparato desconocido."""
    try:
        s = config()
        texto = (f"📡 Unknown device on your network: {d['ip']} · {d['mac']}"
                 + (' (private MAC, probably a phone)' if es_mac_privada(d['mac']) else '')
                 + (f"\nWi-Fi {d['banda']}, signal {d['senal']} dBm (below -80 usually means far away)"
                    if d.get('senal') is not None else '')
                 + f"\nOpen http://localhost:{PUERTO} and click “Mark as mine” if it's yours.")
        urllib.request.urlopen(urllib.request.Request(
            f"https://api.telegram.org/bot{s['telegram_token']}/sendMessage",
            json.dumps({'chat_id': s['telegram_chat_id'], 'text': texto}).encode(),
            {'Content-Type': 'application/json'}), timeout=10).close()
    except (OSError, ValueError, KeyError):
        pass  # shortcut: sin internet o sin Telegram en Jarvis el aviso se pierde (la web lo sigue mostrando)


def leer_asociados(html):
    """Aparatos de una página "Associated Devices" del router: {mac: {'ip', 'senal'}}.
    Vienen como Transfer_meaning('ADMACAddress0','b0\\x3af7...'), 'ADIPAddress0' y 'RSSI0' (dBm)."""
    campos = {k: re.sub(r'\\x([0-9a-fA-F]{2})', lambda m: chr(int(m.group(1), 16)), v)
              for k, v in re.findall(r"Transfer_meaning\('(\w+)','([^']*)'\)", html)}
    asociados = {}
    for k, mac in campos.items():
        if k.startswith('ADMACAddress'):
            i = k[len('ADMACAddress'):]
            rssi = campos.get('RSSI' + i, '')
            asociados[mac.upper().replace(':', '-')] = {
                'ip': campos.get('ADIPAddress' + i, ''), 'senal': int(rssi) if rssi.lstrip('-').isdigit() else None}
    return asociados


def asociados_router(router, cfg):
    """Entra en el router como hace su web y devuelve lo conectado a cada wifi: {mac: {'banda', 'ip', 'senal'}}."""
    web = urllib.request.build_opener(urllib.request.HTTPCookieProcessor())
    raiz = f'http://{router}/'
    portada = web.open(raiz, timeout=10).read().decode('utf-8', 'replace')
    tokens = dict(re.findall(r'"(Frm_Logintoken|Frm_Loginchecktoken)", "(\d*)"', portada))
    azar = str(random.randint(10_000_000, 99_999_999))
    web.open(raiz, urllib.parse.urlencode({
        'action': 'login', 'Username': cfg['router_user'], 'UserRandomNum': azar, 'port': '',
        'Password': hashlib.sha256((cfg['router_password'] + azar).encode()).hexdigest(),
        'Frm_Logintoken': tokens['Frm_Logintoken'], 'Frm_Loginchecktoken': tokens['Frm_Loginchecktoken']}).encode(),
        timeout=10).read()
    try:
        asociados = {}
        for banda, pagina in PAGINAS_BANDA.items():
            html = web.open(f'{raiz}getpage.gch?pid=1002&nextpage={pagina}', timeout=10).read().decode('utf-8', 'replace')
            if 'WLAN_SSID0' not in html:  # sin sesión (o sin ser admin) el router devuelve la página vacía
                raise RuntimeError('el router no ha dejado entrar')
            for mac, a in leer_asociados(html).items():
                asociados[mac] = {**a, 'banda': banda}
        return asociados
    finally:
        web.open(raiz, b'logout=1', timeout=10).read()  # el router solo admite una sesión: la soltamos enseguida


def bucle_router():
    time.sleep(15)  # espera al primer escaneo, que es el que dice cuál es el router
    while True:
        cfg, asociados, como = config(), None, 'off'
        if cfg.get('router_password'):
            try:
                asociados, como = asociados_router(estado['gateway'], cfg), 'ok'
            except Exception:  # contraseña mal, router ocupado u otro modelo: el monitor sigue como si no hubiera router
                como = 'error'
        with lock:
            estado.update(asociados=asociados, router=como)  # aplicar() usa asociados en cada escaneo
        time.sleep(PAUSA_ROUTER)


def escanear():
    salida = subprocess.run(
        ['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', os.path.join(AQUI, 'escanear.ps1')],
        capture_output=True, text=True, timeout=90, creationflags=subprocess.CREATE_NO_WINDOW).stdout
    r = json.loads(salida)
    if 'error' in r:
        raise RuntimeError(r['error'])
    return r


def bucle_escaneo():
    while True:
        try:
            for d in aplicar(escanear()):
                avisar_telegram(d)
        except Exception as e:  # un escaneo fallido no debe tumbar el monitor
            with lock:
                estado['error'] = str(e)[:200]
        time.sleep(PAUSA)


def bucle_fabricantes():
    """Pone nombre de fabricante a cada MAC (a la API solo se le manda el prefijo del fabricante)."""
    while True:
        with lock:
            pendientes = [d for d in estado['dispositivos'].values() if not d['fabricante']]
        for d in pendientes:
            try:
                with urllib.request.urlopen(f"https://api.macvendors.com/{d['mac'][:8]}", timeout=10) as resp:
                    nombre = resp.read().decode()[:80]
            except urllib.error.HTTPError as e:
                nombre = 'Unknown vendor' if e.code == 404 else ''
            except OSError:
                nombre = ''  # sin internet: se reintenta en la siguiente vuelta
            if nombre:
                with lock:
                    d['fabricante'] = nombre
                    guardar()
            time.sleep(1.2)  # la API gratuita admite ~1 consulta por segundo
        time.sleep(5)


class Web(BaseHTTPRequestHandler):
    def responder(self, codigo, cuerpo=b'', tipo='application/json'):
        self.send_response(codigo)
        self.send_header('Content-Type', tipo)
        self.send_header('Content-Length', str(len(cuerpo)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(cuerpo)

    def host_local(self):
        # bloquea webs ajenas que intenten leer el monitor con trucos de DNS
        return self.headers.get('Host', '').rsplit(':', 1)[0] in ('localhost', '127.0.0.1')

    def do_GET(self):
        if not self.host_local():
            return self.responder(403)
        if self.path == '/api/estado':
            cfg = config()
            with lock:
                cuerpo = json.dumps({
                    'red': estado['red'], 'gateway': estado['gateway'], 'ultimo_escaneo': estado['ultimo_escaneo'],
                    'error': estado['error'], 'router': estado['router'], 'ahora': time.time(),
                    'telegram': bool(cfg.get('telegram_token') and cfg.get('telegram_chat_id')),
                    'dispositivos': list(estado['dispositivos'].values()),
                    'eventos': estado['eventos'][::-1]}, ensure_ascii=False).encode()
            self.responder(200, cuerpo)
        elif self.path == '/':
            with open(os.path.join(AQUI, 'index.html'), 'rb') as f:
                self.responder(200, f.read(), 'text/html; charset=utf-8')
        else:
            self.responder(404)

    def do_POST(self):
        # exigir JSON obliga al navegador a pedir permiso CORS, así otra web no puede cambiar tus datos
        if not self.host_local() or self.path != '/api/dispositivo' \
                or self.headers.get('Content-Type') != 'application/json':
            return self.responder(400)
        try:
            datos = json.loads(self.rfile.read(min(int(self.headers.get('Content-Length', 0)), 10_000)))
        except ValueError:
            return self.responder(400)
        if not isinstance(datos, dict):
            return self.responder(400)
        with lock:
            d = estado['dispositivos'].get(str(datos.get('mac')))
            if d is None:
                return self.responder(404)
            if 'nombre' in datos:
                d['nombre'] = str(datos['nombre']).strip()[:60]
            if 'conocido' in datos:
                d['conocido'] = bool(datos['conocido'])
            guardar()
        self.responder(204)

    def log_message(self, *args):
        pass  # con pythonw no hay consola donde escribir


def _prueba():
    global DATOS
    DATOS = os.path.join(tempfile.mkdtemp(), 'prueba.json')
    yo = {'ip': '10.0.0.5', 'mac': 'AA-00-00-00-00-05', 'gateway': '10.0.0.1', 'red': '10.0.0.0/24'}
    router, movil = {'ip': '10.0.0.1', 'mac': 'AA-00-00-00-00-01'}, {'ip': '10.0.0.9', 'mac': '92-00-00-00-00-09'}
    ds, tipos = estado['dispositivos'], lambda: [e['tipo'] for e in estado['eventos']]
    aplicar({**yo, 'vecinos': [router]}, ahora=1)
    assert tipos() == [] and ds[router['mac']]['nombre'] == 'Router' and ds[yo['mac']]['nombre'] == 'This PC'
    assert aplicar({**yo, 'vecinos': [router, movil]}, ahora=2) == [ds[movil['mac']]]
    assert tipos() == ['nuevo'] and ds[movil['mac']]['fabricante'] == 'Private MAC'
    aplicar({**yo, 'vecinos': [router]}, ahora=3)
    assert ds[movil['mac']]['online'], 'un solo fallo no debe desconectar'
    aplicar({**yo, 'vecinos': [router]}, ahora=4)
    assert not ds[movil['mac']]['online'] and tipos()[-1] == 'desconectado'
    aplicar({**yo, 'vecinos': [router, movil]}, ahora=5)
    assert tipos() == ['nuevo', 'desconectado', 'conectado'] and ds[movil['mac']]['desde'] == 5
    estado['ultimo_escaneo'] = None  # como al reiniciar el PC
    otro = {'ip': '10.0.0.7', 'mac': 'AA-00-00-00-00-07'}
    assert [d['mac'] for d in aplicar({**yo, 'vecinos': [router, movil, otro]}, ahora=6)] == [otro['mac']]
    assert tipos()[-1] == 'nuevo', 'un desconocido que ya estaba al arrancar también avisa'
    # líneas con el formato que manda el router (página "Associated Devices" de 5G), con datos inventados
    html = r"""<script language="javascript">Transfer_meaning('RSSI','');</script>
<script language="javascript">Transfer_meaning('ADMACAddress0','f2\x3a00\x3a00\x3a00\x3a00\x3a66');</script>
<script language="javascript">Transfer_meaning('ADIPAddress0','10\x2e0\x2e0\x2e66');</script>
<script language="javascript">Transfer_meaning('RSSI0','\x2d67');</script>"""
    intruso = 'F2-00-00-00-00-66'
    assert leer_asociados(html) == {intruso: {'ip': '10.0.0.66', 'senal': -67}}
    estado['asociados'] = {movil['mac']: {'banda': '5G', 'ip': movil['ip'], 'senal': -60},
                           intruso: {'banda': '2.4G', 'ip': '10.0.0.66', 'senal': -85}}
    # el móvil se duerme y deja de contestar; el intruso no contesta nunca: los dos solo los ve el router
    assert [d['mac'] for d in aplicar({**yo, 'vecinos': [router, otro]}, ahora=7)] == [intruso]
    aplicar({**yo, 'vecinos': [router, otro]}, ahora=8)
    assert ds[movil['mac']]['online'], 'dormido pero en el wifi sigue conectado'
    assert (ds[movil['mac']]['banda'], ds[movil['mac']]['senal'], ds[intruso]['ip']) == ('5G', -60, '10.0.0.66')
    assert (ds[otro['mac']]['banda'], ds[router['mac']]['banda']) == ('cable', '')
    estado['asociados'] = {}  # se van del wifi
    aplicar({**yo, 'vecinos': [router, otro]}, ahora=9)
    aplicar({**yo, 'vecinos': [router, otro]}, ahora=10)
    assert not ds[intruso]['online'] and not ds[movil['mac']]['online'] and tipos()[-1] == 'desconectado'
    estado['asociados'] = None  # el router no se puede leer
    aplicar({**yo, 'vecinos': [router, otro]}, ahora=11)
    assert not any(d['banda'] for d in ds.values()), 'si el router falla no se enseña banda'
    print('OK')


if __name__ == '__main__':
    if '--test' in sys.argv:
        _prueba()
    else:
        cargar()
        ThreadingHTTPServer.allow_reuse_address = False  # en Windows SO_REUSEADDR deja abrir dos monitores en el mismo puerto
        servidor = ThreadingHTTPServer(('127.0.0.1', PUERTO), Web)  # antes que los hilos: si ya hay otro abierto, salimos sin tocar nada
        threading.Thread(target=bucle_escaneo, daemon=True).start()
        threading.Thread(target=bucle_fabricantes, daemon=True).start()
        threading.Thread(target=bucle_router, daemon=True).start()
        print(f'Home Network Monitor on http://localhost:{PUERTO}')
        servidor.serve_forever()
