import copy
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
from analysis.design_sensitivity import (bootstrap_intervals,default_spec,execute,generate,
    seed_for,task_parameters,task_statistics,validate)
from analysis.estimate import estimate


class DesignSensitivityTests(unittest.TestCase):
    def test_planning_only_and_integer_design_guards(self):
        base=default_spec();validate(base)
        for field,value in [('data_kind','study'),('formal_collection_enabled',True),('human_gate_satisfied',True),('replicates',True)]:
            bad=copy.deepcopy(base);bad[field]=value
            with self.assertRaises(ValueError):validate(bad)
        bad=copy.deepcopy(base);bad['designs'][0]['R']=True
        with self.assertRaises(ValueError):validate(bad)

    def test_shared_upstream_failure_and_correct_expectation(self):
        d=dict(K=5,R=3,S=2);m=default_spec()['mechanisms'][4]
        y,div,truth=generate(d,m,12000,np.random.default_rng(31))
        st=task_statistics(y,np.zeros((),dtype=bool))
        np.testing.assert_allclose(st['full'].mean(axis=0),truth,atol=.012)
        self.assertAlmostEqual(truth.mean(),.12)
        failed=dict(m,q=0.)
        y,_,_=generate(d,failed,20,np.random.default_rng(31))
        self.assertFalse(y.any())

    def test_opposed_future_coupling_preserves_shared_repetitions(self):
        d=dict(K=3,R=2,S=3);m=dict(default_spec()['mechanisms'][-1],q=1.,rho=1.,delta=0.,heterogeneous=False)
        y,_,_=generate(d,m,30,np.random.default_rng(42))
        self.assertTrue(np.all(y[:,:,:,:,0,:] != y[:,:,:,:,1,:]))
        self.assertTrue(np.all(y[:,:,:,:,:,0] == y[:,:,:,:,:,1]))

    def test_task_heterogeneity_changes_neither_assumed_class_mean(self):
        for k in (3,5):
            div,_,effect=task_parameters(k,default_spec()['mechanisms'][5])
            self.assertAlmostEqual(effect[div].mean(),.25)
            self.assertEqual(effect[~div].sum(),0)

    def test_missing_bounds_and_shared_blocks_hand_calculated(self):
        y=np.zeros((1,3,2,2,2,1),dtype=bool)
        y[:,:,:,0,0,:]=True;y[:,:,:,1,1,:]=True
        st=task_statistics(y,np.zeros((),dtype=bool))
        np.testing.assert_equal(st['full'],1);np.testing.assert_equal(st['blocks'],1)
        missing=np.ones_like(y)
        st=task_statistics(y,missing)
        np.testing.assert_equal(st['lower'],-1);np.testing.assert_equal(st['upper'],1)
        self.assertFalse(st['complete'][0])

    def test_bootstrap_matches_existing_estimator(self):
        d=dict(K=3,R=5,S=2);y,_,_=generate(d,default_spec()['mechanisms'][4],1,np.random.default_rng(4))
        st=task_statistics(y,np.zeros((),dtype=bool));lo,hi=bootstrap_intervals(st['blocks'][:,0,:])
        rows=[]
        for r in range(5):
            for i in range(2):
                for j in range(2):
                    for s in range(2):
                        rows.append(dict(task_id='invented',task_class='unvalidated',artifact_id=f'{r}-{i}',
                            upstream_seed=r,downstream_seed=s,disclosed_future=f'B{i+1}',realized_future=f'B{j+1}',
                            data_kind='synthetic',trajectory_success=bool(y[0,0,r,i,j,s])))
        actual=estimate(rows,include_infrastructure=True)['tasks']['invented']
        np.testing.assert_allclose([lo[0],hi[0]],actual['conditional_seed_block_bootstrap_95'])
        self.assertAlmostEqual(st['full'][0,0],actual['tau_ID'])
        self.assertIsNone(bootstrap_intervals(np.zeros((2,1))))

    def test_stable_streams_no_overwrite_and_replay(self):
        self.assertEqual(seed_for(1,dict(a=2,b=3)),seed_for(1,dict(b=3,a=2)))
        self.assertNotEqual(seed_for(1,dict(a=2)),seed_for(1,dict(a=3)))
        spec=default_spec();spec.update(replicates=20,bootstrap_replicates=10)
        spec['designs']=spec['designs'][:1];spec['mechanisms']=spec['mechanisms'][:1]
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory);first,_=execute(spec,p/'one');second,_=execute(spec,p/'two')
            self.assertEqual(first,second)
            for file in (p/'one/replicates').glob('*.npz'):
                with np.load(file) as a,np.load(p/'two/replicates'/file.name) as b:
                    for name in a.files:np.testing.assert_equal(a[name],b[name])
            with self.assertRaises(FileExistsError):execute(spec,p/'one')


if __name__=='__main__':unittest.main()
