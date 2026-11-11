import math
import unittest
import numpy as np
from scipy.stats import ttest_1samp,ttest_ind
from analysis.interval_comparison import candidate_interval,bootstrap_samples,metrics
from analysis.design_sensitivity import bootstrap_intervals


class IntervalComparisonTests(unittest.TestCase):
    def test_bootstrap_reproduces_current_task_arithmetic(self):
        blocks=np.array([[[1.,0.,.5,-.5,0.],[-1.,0.,0.,.5,.5]]])
        ci=candidate_interval(blocks,[1,0],'paired_percentile')
        lo,hi=bootstrap_intervals(blocks[:,0,:])
        np.testing.assert_allclose(ci['lower'],lo);np.testing.assert_allclose(ci['upper'],hi)

    def test_task_t_matches_scipy_and_zero_variance_guard(self):
        blocks=np.array([[[.1,.2,.3,.4,.5]]])
        actual=candidate_interval(blocks,[1],'stratified_t')
        reference=ttest_1samp(blocks[:,0,:],0,axis=1).confidence_interval()
        np.testing.assert_allclose(actual['lower'],reference.low)
        np.testing.assert_allclose(actual['upper'],reference.high)
        blocks=np.ones((2,1,3))*.5
        plain=candidate_interval(blocks,[1],'stratified_t')
        guarded=candidate_interval(blocks,[1],'stratified_t_zero_guard')
        np.testing.assert_equal(plain['lower'],.5)
        np.testing.assert_equal(guarded['lower'],-1)
        np.testing.assert_equal(guarded['upper'],1)
        self.assertTrue(guarded['zero_variance_fallback'].all())

    def test_two_strata_t_matches_welch_reference(self):
        blocks=np.array([[[.1,.2,.3,.4,.5],[-.2,-.1,0.,.1,.1]]])
        ci=candidate_interval(blocks,[1,-1],'stratified_t')
        expected=ttest_ind(blocks[:,0,:],blocks[:,1,:],axis=1,equal_var=False).confidence_interval()
        np.testing.assert_allclose(ci['lower'],expected.low)
        np.testing.assert_allclose(ci['upper'],expected.high)

    def test_weighted_hoeffding_radius_and_negative_coefficients(self):
        blocks=np.zeros((1,5,10));c=np.ones(5)/5
        ci=candidate_interval(blocks,c,'hoeffding_independent_blocks')
        radius=math.sqrt(2*math.log(40)/50)
        self.assertAlmostEqual(ci['lower'][0],-radius)
        lo=np.array([[-.2,-.1,.1,.2,.3]]);hi=lo+.3
        # Choose actual latent means inside every bound.
        blocks=np.repeat(((lo+hi)/2)[:,:,None],10,axis=2)
        c=np.array([1/3,1/3,1/3,-1/2,-1/2])
        ci=candidate_interval(blocks,c,'hoeffding_independent_blocks',lower=lo,upper=hi)
        radius=math.sqrt(2*math.log(40)*np.sum(c*c)/10)
        expected_low=sum(np.where(c>=0,lo[0],hi[0])*c)-radius
        self.assertAlmostEqual(ci['lower'][0],expected_low)
        self.assertTrue(np.isnan(ci['point'][0]))

    def test_one_block_and_missingness_keep_unknowns(self):
        blocks=np.zeros((2,1,1))
        for method in ('paired_percentile','stratified_t','stratified_t_zero_guard'):
            self.assertFalse(candidate_interval(blocks,[1],method)['available'].any())
        self.assertTrue(candidate_interval(blocks,[1],'hoeffding_independent_blocks')['available'].all())
        blocks=np.zeros((2,1,3));lower=np.array([[0],[-.5]]);upper=np.array([[0],[.5]])
        for method in ('paired_percentile','stratified_t','stratified_t_zero_guard'):
            ci=candidate_interval(blocks,[1],method,lower=lower,upper=upper)
            np.testing.assert_equal(ci['available'],[True,False])
            self.assertTrue(np.isnan(ci['point'][1]))
            self.assertEqual(metrics(ci,0)['available_intervals'],1)

    def test_all_missing_full_support_and_invalid_bounds(self):
        blocks=np.zeros((2,2,5));lo=np.full((2,2),-1);hi=-lo
        ci=candidate_interval(blocks,[1,-1],'hoeffding_independent_blocks',lower=lo,upper=hi)
        np.testing.assert_equal(ci['lower'],-2);np.testing.assert_equal(ci['upper'],2)
        self.assertTrue(np.isnan(ci['point']).all())
        for bad in (np.ones((2,2))*2,np.ones((2,2))*.1):
            with self.assertRaises(ValueError):candidate_interval(blocks,[1,-1],'hoeffding_independent_blocks',lower=bad,upper=hi)
        with self.assertRaises(ValueError):candidate_interval(blocks,[0,0],'stratified_t')


if __name__=='__main__':unittest.main()
