import copy
import csv
import json
from pathlib import Path
import tempfile
import unittest
from analysis.session_cost import digest, summarize, render


def fixtures(downstream=(3,9)):
    plan=dict(schema_version=1,data_kind='synthetic',formal_collection_enabled=False,
        retry_policy='zero-automatic-retries-v1',task_ids=['invented-one','invented-two'],
        upstream_repetitions=[7,11],downstream_repetitions=list(downstream))
    sessions=[];branches=[]
    def session(phase,task,r,d,future=None,s=None):
        row=dict(phase=phase,task_id=task,upstream_repetition=r,disclosed_future=d,
            realized_future=future,downstream_repetition=s,data_kind='synthetic',
            session_id=f'{phase}-{task}-{r}-{d}-{future}-{s}',attempt_index=0,
            invocation_state='finished',outcome='success',usage_complete=True,
            metrics=dict(tokens=100 if phase=='A' else 20,input_tokens=1000 if phase=='A' else 200,
                tool_calls=2 if phase=='A' else 1,wall_seconds=10 if phase=='A' else 5),
            observed_usage=dict(tokens=None,input_tokens=None))
        sessions.append(row);return row
    for task in plan['task_ids']:
        for r in plan['upstream_repetitions']:
            for d in ('B1','B2'):
                a=session('A',task,r,d)
                for future in ('B1','B2'):
                    for s in downstream:
                        b=session('B',task,r,d,future,s)
                        branches.append({k:b[k] for k in ('phase','task_id','upstream_repetition',
                            'disclosed_future','realized_future','downstream_repetition','data_kind')} |
                            dict(session_A=a['session_id'],session_B=b['session_id'],
                                cost_A=copy.deepcopy(a['metrics']),cost_B=copy.deepcopy(b['metrics'])))
    return plan,sessions,branches


def sync(sessions,branches):
    by_id={r['session_id']:r for r in sessions}
    for b in branches:
        for phase in ('A','B'):b['cost_'+phase]=copy.deepcopy(by_id[b['session_'+phase]]['metrics'])


class SessionCost(unittest.TestCase):
    def report(self, data):
        plan,sessions,branches=data
        return summarize(plan,sessions,branches,digest(plan))

    def test_shared_upstream_is_counted_once_for_multiple_futures_and_repeats(self):
        data=fixtures();report=self.report(data)
        self.assertEqual(report['phases']['A']['assigned'],8)
        self.assertEqual(report['phases']['B']['assigned'],32)
        self.assertEqual(report['overall']['tokens']['total'],1440)
        self.assertEqual(report['overall']['input_tokens']['total'],14400)
        self.assertEqual(report['overall']['tool_calls']['total'],48)
        self.assertEqual(report['overall']['wall_seconds']['total'],240)
        naive=sum(b['cost_A']['tokens']+b['cost_B']['tokens'] for b in data[2])
        self.assertEqual(naive,3840);self.assertNotEqual(naive,1440)

    def test_downstream_repetition_changes_B_cost_without_recharging_A(self):
        a=self.report(fixtures((3,)));b=self.report(fixtures())
        self.assertEqual(a['phases']['A'],b['phases']['A'])
        self.assertEqual(a['phases']['B']['tokens']['total'],320)
        self.assertEqual(b['phases']['B']['tokens']['total'],640)

    def test_failures_and_infrastructure_attempts_remain_charged(self):
        data=fixtures();data[1][0]['outcome']='failure';data[1][1]['outcome']='infrastructure'
        r=self.report(data)
        self.assertEqual(r['overall']['tokens']['total'],1440)
        self.assertEqual(r['overall']['outcome_counts'],{'success':38,'failure':1,'infrastructure':1})

    def test_interrupted_partial_usage_remains_unknown_with_observed_lower_bound(self):
        plan,sessions,branches=fixtures();b=sessions[1]
        b.update(invocation_state='interrupted',outcome='unresolved',usage_complete=False)
        b['metrics'].update(tokens=None,input_tokens=None,tool_calls=None,wall_seconds=2.5)
        b['observed_usage'].update(tokens=5,input_tokens=50);sync(sessions,branches)
        r=self.report((plan,sessions,branches));tokens=r['overall']['tokens']
        self.assertIsNone(tokens['total']);self.assertEqual(tokens['unknown_sessions'],1)
        self.assertEqual(tokens['known_subtotal'],1420)
        self.assertEqual(tokens['observed_lower_bound'],1425)
        self.assertEqual(r['overall']['input_tokens']['observed_lower_bound'],14250)
        self.assertIsNone(r['overall']['tool_calls']['total'])
        self.assertEqual(r['overall']['wall_seconds']['total'],237.5)

    def test_uninvoked_placeholders_stay_assigned_without_invented_measured_usage(self):
        plan,sessions,branches=fixtures()
        for r in sessions:
            r.update(invocation_state='not_started',outcome='not_started',usage_complete=False)
            r['metrics']={k:None for k in r['metrics']}
        sync(sessions,branches);report=self.report((plan,sessions,branches))
        self.assertEqual(report['overall']['assigned'],40)
        self.assertEqual(report['overall']['attempted'],0)
        self.assertEqual(report['overall']['tokens']['total'],0)
        self.assertEqual(report['branches'],32)
        sessions[0]['metrics']['tokens']=0;sync(sessions,branches)
        with self.assertRaises(ValueError):self.report((plan,sessions,branches))

    def test_whole_omitted_sessions_and_branches_are_rejected(self):
        for what in ('sessions','branches'):
            plan,sessions,branches=fixtures()
            (sessions if what=='sessions' else branches).pop()
            with self.subTest(what=what),self.assertRaises(ValueError):self.report((plan,sessions,branches))

    def test_duplicate_retried_or_wrongly_bound_identities_are_rejected(self):
        changes=[lambda s,b:s.append(copy.deepcopy(s[0])),
            lambda s,b:s[1].update(session_id=s[0]['session_id']),
            lambda s,b:s[0].update(attempt_index=1),
            lambda s,b:s[0].update(attempt_index=True),
            lambda s,b:s[0].update(upstream_repetition=True),
            lambda s,b:b[0].update(session_A=s[5]['session_id']),
            lambda s,b:b.append(copy.deepcopy(b[0]))]
        for i,change in enumerate(changes):
            plan,sessions,branches=fixtures();change(sessions,branches)
            with self.subTest(case=i),self.assertRaises(ValueError):self.report((plan,sessions,branches))

    def test_copied_usage_must_match_origin_including_boolean_integer_distinction(self):
        for value in (101,True,None):
            data=fixtures();data[2][0]['cost_A']['tokens']=value
            with self.subTest(value=value),self.assertRaises(ValueError):self.report(data)

    def test_invalid_measured_cost_and_partial_usage_are_rejected(self):
        for field,value in [('tokens',True),('tokens',1.5),('tokens',-1),('wall_seconds',float('nan')),
                            ('wall_seconds',float('inf')),('wall_seconds',False)]:
            plan,sessions,branches=fixtures();sessions[0]['metrics'][field]=value
            with self.subTest(field=field,value=value),self.assertRaises(ValueError):
                self.report((plan,sessions,branches))
        data=fixtures();data[1][0]['observed_usage']['tokens']=99
        with self.assertRaises(ValueError):self.report(data)
        data=fixtures();data[1][0]['usage_complete']=False
        with self.assertRaises(ValueError):self.report(data)

    def test_mixed_study_data_and_unpinned_plan_are_refused(self):
        for index in (0,1,2):
            data=fixtures()
            (data[0] if index==0 else data[index][0])['data_kind']='study'
            with self.subTest(index=index),self.assertRaises(ValueError):self.report(data)
        plan,sessions,branches=fixtures();pin=digest(plan);plan['task_ids'].pop()
        with self.assertRaises(ValueError):summarize(plan,sessions,branches,pin)

    def test_order_does_not_change_totals_or_mutate_inputs(self):
        data=fixtures();before=copy.deepcopy(data);expected=self.report(data)
        self.assertEqual(data,before)
        data[1].reverse();data[2].reverse();self.assertEqual(self.report(data),expected)

    def test_renderer_preserves_nulls_refuses_overwrite_and_validates_before_output(self):
        plan,sessions,branches=fixtures();r=sessions[1]
        r.update(usage_complete=False);r['metrics'].update(tokens=None,input_tokens=None)
        sync(sessions,branches)
        with tempfile.TemporaryDirectory() as t:
            out=Path(t)/'render';render(plan,sessions,branches,out,digest(plan))
            report=json.loads((out/'summary.json').read_text())
            self.assertIsNone(report['overall']['tokens']['total'])
            with (out/'sessions.csv').open() as stream:
                rows=list(csv.DictReader(stream))
            self.assertEqual(next(x for x in rows if x['session_id']==r['session_id'])['tokens'],'')
            self.assertIn('Unavailable',(out/'README.md').read_text())
            with self.assertRaises(FileExistsError):render(plan,sessions,branches,out,digest(plan))
            bad=Path(t)/'invalid'
            with self.assertRaises(ValueError):render(plan,sessions,branches[:-1],bad,digest(plan))
            self.assertFalse(bad.exists())


if __name__=='__main__':unittest.main()
