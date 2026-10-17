import unittest
from engine import Detector, IncidentTracker, normal_workload, replay, clean_row

class DetectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.detector=Detector().fit(normal_workload(800,17),normal_workload(256,31),'test synthetic')

    def item(self,candidate,step):
        return dict(candidate=candidate,timestamp=str(step),step=step,guardrails=[],score=.7,drivers=[],
                    action='Inspect',guidance='Review',source='simulation')

    def test_short_spike_does_not_open_incident(self):
        tracker=IncidentTracker()
        for step,value in enumerate([False,True,True,False,True,False]):
            tracker.update(self.item(value,step))
        self.assertEqual(tracker.incidents,[])

    def test_confirmation_and_recovery_hysteresis(self):
        tracker=IncidentTracker()
        outputs=[tracker.update(self.item(v,i)) for i,v in enumerate([True,True,True,False,False,False])]
        self.assertEqual([x['confirmed'] for x in outputs],[False,False,True,True,True,False])
        self.assertEqual(len(tracker.incidents),1)
        self.assertEqual(tracker.incidents[0]['status'],'resolved')

    def test_invalid_values_rejected(self):
        row=normal_workload(1,1)[0]
        for bad in [float('nan'),float('inf'),-1,101]:
            with self.assertRaises(ValueError): clean_row({**row,'cpu_pct':bad})

    def test_sustained_failures_create_two_distinct_incidents(self):
        for scenario in ['compound','cpu','memory','network','disk']:
            tracker=IncidentTracker()
            for row in replay(scenario,seed=4001):
                tracker.update({**row,**self.detector.score(row),'source':'simulation','timestamp':str(row['step'])})
            self.assertEqual(len(tracker.incidents),2,scenario)
            self.assertTrue(all(i['status']=='resolved' for i in tracker.incidents),scenario)

    def test_healthy_control_has_no_confirmed_incident(self):
        tracker=IncidentTracker()
        for row in replay('normal',seed=4001):
            tracker.update({**row,**self.detector.score(row),'source':'simulation','timestamp':str(row['step'])})
        self.assertEqual(len(tracker.incidents),0)

    def test_scoring_does_not_mutate_model_baseline(self):
        before=self.detector.info.copy()
        for row in replay('compound',seed=99): self.detector.score(row)
        self.assertEqual(self.detector.info,before)

    def test_multivariate_episode_detected_below_critical_guardrails(self):
        rows=replay('compound',seed=4001)[40:70]
        scores=[self.detector.score(r) for r in rows]
        self.assertTrue(all(s['candidate'] for s in scores))
        self.assertTrue(all(not s['guardrails'] for s in scores))

if __name__=='__main__': unittest.main()
