"""Production Lua app -> real HTTP -> SQLite; CSP itself remains doubled."""
import json
import sys
import tempfile
import threading
import unittest
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from server.app import Store, make_handler
from test_network import harness, config
from test_app_hud import STATION


class FieldHttp(unittest.TestCase):
    def test_real_app_http_independent_lost_ack_restart_and_both_orders(self):
        for penalty_first in (False, True):
            with self.subTest(penalty_first=penalty_first), tempfile.TemporaryDirectory() as directory:
                store = Store(Path(directory) / 'field.sqlite', penalties_v2_enabled=True)
                http = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(store, 'admin-test', {'ST01': 'local-only'}))
                worker = threading.Thread(target=http.serve_forever, daemon=True)
                worker.start()
                base = 'http://127.0.0.1:' + str(http.server_port)
                def request(method, path, body=None, token='local-only'):
                    conn = HTTPConnection('127.0.0.1', http.server_port, timeout=3)
                    conn.request(method, path, body, {'Authorization': 'Bearer '+token, 'Content-Type':'application/json'})
                    response = conn.getresponse()
                    code, text = response.status, response.read().decode()
                    conn.close()
                    return code, json.loads(text)
                def admin(path, body):
                    code, reply = request('POST', '/api/admin/'+path, json.dumps(body), 'admin-test')
                    self.assertEqual(code, 200, reply)
                try:
                    admin('sessions', {'session_id':'S1','name':'Session'})
                    admin('pilots', {'pilot_id':'P1','name':'Pilot'})
                    admin('assignments', {'station_id':'ST01','session_id':'S1','pilot_id':'P1'})
                    delivered, held = [], []
                    lose_ack = [True]
                    def new_app(disk=None, hold_native=False):
                        def pre(lua):
                            lua.execute((STATION % ",protocol_v2=true,track_config='FSAEJ_Endurance'").replace('http://127.0.0.1:8080',base))
                            lua.execute("CAR={lapCount=0,previousLapTimeMs=60000,isLastLapValid=true,position={x=0,z=1}}; ac.getCar=function() return CAR end")
                        lua, disk, _, _, _, cv = harness(disk,pre)
                        def transport(method):
                            def call(url, headers, *args):
                                body, cb = args if method=='POST' else (None,args[0])
                                path=url[len(base):]
                                if hold_native and path.endswith('/native-laps'):
                                    held.append((path,body))
                                    return
                                code, reply = request(method,path,body)
                                if '/api/v2/' in path:
                                    delivered.append((path,body,code))
                                    self.assertEqual(code,200,reply)
                                    if path.endswith('/native-laps') and lose_ack[0]:
                                        lose_ack[0]=False
                                        cb('lost response after server commit',None)
                                        return
                                cb(None,cv({'status':code,'body':json.dumps(reply)}))
                            return call
                        lua.globals().web=lua.table_from({'get':transport('GET'),'post':transport('POST')})
                        return lua,disk,cv
                    lua,disk,cv=new_app(hold_native=penalty_first)
                    script=lua.globals().script
                    script.update(.1)
                    self.assertTrue(script.configure(config(cv)))
                    for i,z in enumerate((1,-1,-4,-6,1,-1,-4,-6,1,-1)):
                        lua.execute('CAR.position.z=%s' % z)
                        if i==5: lua.execute('CAR.lapCount=1')
                        if i==9: lua.execute('CAR.lapCount=2;CAR.previousLapTimeMs=400')
                        script.update(.1)
                    self.assertEqual(script.fieldStatus().penalty_pending,0)
                    self.assertGreater(script.fieldStatus().native_pending,0)
                    lua2,_,_=new_app(disk)
                    lua2.globals().script.update(.1)
                    for _ in range(180): lua2.globals().script.update(.1)
                    rows=request('GET','/api/admin/v2/field-laps',token='admin-test')[1]['laps']
                    matched=[r for r in rows if r['penalty_received']]
                    self.assertEqual(len(matched),1)
                    self.assertTrue(matched[0]['native_received'])
                    self.assertEqual(matched[0]['raw_ms'],400)
                    self.assertIsNone(matched[0]['final_ms'])
                    self.assertEqual(lua2.globals().script.fieldStatus().native_pending,0)
                    payloads=[body for path,body,_ in delivered if path.endswith('/native-laps')]
                    self.assertEqual(payloads[0],payloads[1])
                    if penalty_first:
                        self.assertTrue(delivered[0][0].endswith('/penalty-laps'))
                finally:
                    http.shutdown(); http.server_close(); worker.join(timeout=3)


if __name__=='__main__': unittest.main()
