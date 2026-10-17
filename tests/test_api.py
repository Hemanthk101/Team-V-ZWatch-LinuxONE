"""Optional integration checks against an already-running deployment."""
import json, os, unittest, urllib.request, urllib.error
from pathlib import Path

@unittest.skipUnless(os.environ.get('ZWATCH_TEST_URL'),'Set ZWATCH_TEST_URL for integration checks')
class APITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.url=os.environ['ZWATCH_TEST_URL'].rstrip('/')
        cls.key=Path(os.environ['ZWATCH_KEY_FILE']).read_text().strip()

    def request(self,path,method='GET',data=None,token=True,origin=None):
        headers={'Content-Type':'application/json'}
        if token: headers['Authorization']='Bearer '+self.key
        if origin: headers['Origin']=origin
        req=urllib.request.Request(self.url+path,data=json.dumps(data).encode() if data is not None else None,headers=headers,method=method)
        try:
            with urllib.request.urlopen(req,timeout=20) as res: return res.status,res.read()
        except urllib.error.HTTPError as err: return err.code,err.read()

    def test_metrics_require_authentication(self):
        self.assertEqual(self.request('/api/state',token=False)[0],401)

    def test_unknown_scenario_rejected(self):
        self.assertEqual(self.request('/api/replay','POST',{'scenario':'not-a-scenario'})[0],422)

    def test_foreign_origin_rejected(self):
        self.assertEqual(self.request('/api/replay','POST',{'scenario':'normal'},origin='https://foreign.invalid')[0],403)

    def test_live_source_and_replay_source_remain_distinct(self):
        status,payload=self.request('/api/state')
        self.assertEqual(status,200)
        state=json.loads(payload)
        self.assertTrue(all(x['source']=='live' for x in state['live']))
        self.assertTrue(all(x['source']=='simulation' for x in state['simulation']))
        self.assertIn('synthetic',state['model']['simulation']['provenance'])

    def test_unsupported_export_source_rejected(self):
        self.assertEqual(self.request('/api/export/unknown')[0],422)

if __name__=='__main__': unittest.main()
