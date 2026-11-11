"""Simulation-only interval comparison; the collection estimator is untouched."""
import argparse
import csv
from functools import lru_cache
import hashlib
import json
import math
from pathlib import Path
import random

METHODS=('paired_percentile','stratified_t','stratified_t_zero_guard','hoeffding_independent_blocks')


@lru_cache(maxsize=64)
def weights(k,r,draws=2000,seed=405):
    import numpy as np
    rng=random.Random(seed);w=np.zeros((k,draws,r),dtype=int)
    for task in range(k):
        for i in range(draws):
            for _ in range(r):w[task,i,rng.randrange(r)] += 1
    w.setflags(write=False)
    return w


def bootstrap_samples(blocks,draws=2000,seed=405):
    import numpy as np
    n,k,r=blocks.shape
    if r<2:return None
    w=weights(k,r,draws,seed)
    return np.stack([blocks[:,i,:] @ w[i].T/r for i in range(k)],axis=1)


def candidate_interval(blocks,coefficients,method,alpha=.05,lower=None,upper=None,samples=None):
    import numpy as np
    from scipy.stats import t
    blocks=np.asarray(blocks,dtype=float);c=np.asarray(coefficients,dtype=float)
    if (blocks.ndim!=3 or blocks.shape[2]<1 or blocks.shape[1]!=len(c) or c.ndim!=1 or
        not np.isfinite(blocks).all() or np.any(np.abs(blocks)>1+1e-12) or
        not np.isfinite(c).all() or not np.any(c!=0) or isinstance(alpha,bool) or not 0<alpha<1 or
        method not in METHODS):raise ValueError('invalid bounded-block candidate input')
    n,k,r=blocks.shape;selected=c!=0;support=float(np.abs(c).sum())
    means=blocks.mean(axis=2)
    lower=means if lower is None else np.asarray(lower,dtype=float)
    upper=means if upper is None else np.asarray(upper,dtype=float)
    if (lower.shape!=(n,k) or upper.shape!=(n,k) or not np.isfinite(lower).all() or
        not np.isfinite(upper).all() or np.any(lower>upper+1e-12) or
        np.any(lower < -1-1e-12) or np.any(upper > 1+1e-12) or
        np.any(lower>means+1e-12) or np.any(upper<means-1e-12)):
        raise ValueError('invalid assigned-outcome bounds')
    complete=np.all(lower[:,selected]==upper[:,selected],axis=1)
    point=means @ c
    point=np.where(complete,point,np.nan)
    lo=np.full(n,np.nan);hi=np.full(n,np.nan);fallback=np.zeros(n,dtype=bool)
    if method=='hoeffding_independent_blocks':
        radius=math.sqrt(2*math.log(2/alpha)*float(np.sum(c*c))/r)
        lo=np.sum(np.where(c>=0,lower,upper)*c,axis=1)-radius
        hi=np.sum(np.where(c>=0,upper,lower)*c,axis=1)+radius
    elif r>=2:
        if method=='paired_percentile':
            samples=bootstrap_samples(blocks) if samples is None else samples
            if samples.shape[:2]!=(n,k) or samples.shape[2]<2:
                raise ValueError('invalid paired bootstrap draws')
            draws=np.sort(np.einsum('nkd,k->nd',samples,c),axis=1);count=draws.shape[1]
            lo=draws[:,int(alpha/2*count)];hi=draws[:,min(count-1,int((1-alpha/2)*count))]
        else:
            task_variance=blocks.var(axis=2,ddof=1)
            contributions=task_variance*c*c/r
            variance=contributions.sum(axis=1)
            denominator=(contributions**2/(r-1)).sum(axis=1)
            df=np.divide(variance**2,denominator,out=np.ones(n),where=denominator>0)
            radius=t.ppf(1-alpha/2,df)*np.sqrt(variance)
            lo=means @ c-radius;hi=means @ c+radius
            if method=='stratified_t_zero_guard':
                fallback=np.any(task_variance[:,selected]==0,axis=1)
                lo=np.where(fallback,-support,lo);hi=np.where(fallback,support,hi)
        lo=np.where(complete,lo,np.nan);hi=np.where(complete,hi,np.nan)
    lo=np.clip(lo,-support,support);hi=np.clip(hi,-support,support)
    return dict(lower=lo,upper=hi,point=point,available=np.isfinite(lo)&np.isfinite(hi),
                zero_variance_fallback=fallback&complete)


def targets(divergent):
    import numpy as np
    k=len(divergent);first=np.zeros(k);first[0]=1
    return dict(task_01=first,overall=np.full(k,1/k),
        fixed_class_contrast=np.where(divergent,1/int(divergent.sum()),-1/int((~divergent).sum())))


def metrics(interval,truth):
    import numpy as np
    available=interval['available'];n=len(available);m=int(available.sum())
    if m:
        lo=interval['lower'][available];hi=interval['upper'][available]
        covered=(lo<=truth+1e-12)&(truth<=hi+1e-12)
        excluded=(lo>1e-12)|(hi<-1e-12)
        coverage=float(covered.mean());exclusion=float(excluded.mean())
        coverage_se=math.sqrt(coverage*(1-coverage)/m)
        exclusion_se=math.sqrt(exclusion*(1-exclusion)/m)
        width=float((hi-lo).mean())
    else:coverage=exclusion=coverage_se=exclusion_se=width=None
    return dict(replicates=n,available_intervals=m,availability_fraction=m/n,
        conditional_coverage=coverage,coverage_MCSE=coverage_se,
        covered_and_available_fraction=float(np.sum(available &
            (interval['lower']<=truth+1e-12)&(interval['upper']>=truth-1e-12))/n),
        conditional_zero_exclusion=exclusion,zero_exclusion_MCSE=exclusion_se,
        zero_exclusion_and_available_fraction=float(np.sum(available &
            ((interval['lower']>1e-12)|(interval['upper']<-1e-12)))/n),
        mean_available_width=width,zero_variance_fallback_fraction=float(interval['zero_variance_fallback'].mean()),
        point_availability_fraction=float(np.isfinite(interval['point']).mean()))


def execute(spec,input_root,output):
    import numpy as np
    from analysis.design_sensitivity import generate,seed_for,task_statistics
    if (spec.get('data_kind')!='simulated_method_comparison' or spec.get('formal_collection_enabled') is not False or
        spec.get('human_gate_satisfied') is not False or spec.get('methods')!=list(METHODS) or
        type(spec.get('replicates')) is not int or spec['replicates']<2 or
        type(spec.get('start_index')) is not int or spec['start_index']<0 or spec.get('alpha')!=.05):
        raise ValueError('simulation-only comparison specification required')
    input_root=Path(input_root);base_path=input_root/'specification.json'
    if hashlib.sha256(base_path.read_bytes()).hexdigest()!=spec['input_specification_sha256']:
        raise ValueError('input simulation specification hash mismatch')
    base=json.loads(base_path.read_bytes());n=spec['replicates'];start=spec['start_index']
    if start+n>base['replicates']:raise ValueError('comparison exceeds saved simulation')
    output=Path(output);output.mkdir(parents=True,exist_ok=False);(output/'replicates').mkdir()
    (output/'comparison-specification.json').write_text(json.dumps(spec,indent=2,sort_keys=True)+'\n')
    results=[];replay=[]
    for design in base['designs']:
        k,r,s=(design[key] for key in ('K','R','S'))
        for mechanism in base['mechanisms']:
            identity=dict(design=design,mechanism=mechanism)
            seed=seed_for(base['master_seed'],identity)
            y,div,truth=generate(design,mechanism,base['replicates'],np.random.default_rng(seed))
            full=task_statistics(y,np.zeros((),dtype=bool))
            # Replay all 2,000 before slicing; changing the vectorized generation size changes the stream layout.
            original_case=f'K{k}-R{r}-S{s}-{mechanism["name"]}-none'
            with np.load(input_root/'replicates'/f'{original_case}.npz',allow_pickle=False) as saved:
                np.testing.assert_array_equal(full['full'],saved['full_task_premiums'])
            blocks=full['blocks'][start:start+n];bs=bootstrap_samples(blocks,base['bootstrap_draws'],base['bootstrap_analysis_seed'])
            coefficients=targets(div);raw_lo=[];raw_hi=[];raw_point=[];raw_bounds_lo=[];raw_bounds_hi=[];raw_missing=[]
            for miss in spec['missingness']:
                case=dict(identity,missingness=miss)
                mseed=seed_for(base['master_seed'],dict(case,stream='missingness'))
                rng=np.random.default_rng(mseed)
                shape=y.shape if miss['mode']!='artifact' else (len(y),k,r,2,1,1)
                if miss['mode']=='outcome_dependent':
                    probability=np.where(y,miss['success_rate'],miss['failure_rate'])
                else:probability=miss['rate']
                missing=rng.random(shape)<probability
                st=task_statistics(y,missing)
                if miss in base['missingness']:
                    old=f'K{k}-R{r}-S{s}-{mechanism["name"]}-{miss["name"]}'
                    with np.load(input_root/'replicates'/f'{old}.npz',allow_pickle=False) as saved:
                        for a,b in [('lower','task_lower'),('upper','task_upper'),('missing_count','missing_count')]:
                            np.testing.assert_array_equal(st[a],saved[b])
                lower=st['lower'][start:start+n];upper=st['upper'][start:start+n]
                raw_bounds_lo.append(lower);raw_bounds_hi.append(upper);raw_missing.append(st['missing_count'][start:start+n])
                target_lo=[];target_hi=[];target_point=[]
                for target,c in coefficients.items():
                    target_truth=float(truth @ c);ml=[];mh=[];mp=[]
                    for method in METHODS:
                        ci=candidate_interval(blocks,c,method,spec['alpha'],lower,upper,samples=bs)
                        result=metrics(ci,target_truth)
                        results.append(dict(data_kind='simulated_method_comparison',K=k,R=r,S=s,mechanism=mechanism['name'],
                            missingness=miss['name'],target=target,method=method,assumed_target=target_truth,**result,
                            guarantee='independent bounded blocks; fixed task set; pointwise' if method=='hoeffding_independent_blocks' else 'no distribution-free coverage guarantee',
                            inference_status='candidate_not_released'))
                        ml.append(ci['lower']);mh.append(ci['upper']);mp.append(ci['point'])
                    target_lo.append(ml);target_hi.append(mh);target_point.append(mp)
                raw_lo.append(target_lo);raw_hi.append(target_hi);raw_point.append(target_point)
            name=f'K{k}-R{r}-S{s}-{mechanism["name"]}.npz';path=output/'replicates'/name
            np.savez_compressed(path,blocks=blocks,task_truth=truth,coefficients=np.stack(list(coefficients.values())),
                lower=np.asarray(raw_lo),upper=np.asarray(raw_hi),points=np.asarray(raw_point),
                task_lower=np.asarray(raw_bounds_lo),task_upper=np.asarray(raw_bounds_hi),missing_count=np.asarray(raw_missing))
            replay.append(dict(design=design,mechanism=mechanism['name'],seed=str(seed),
                input_case_replay_pass=True,replicate_path=str(path.relative_to(output)),sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
    with (output/'comparison.csv').open('x',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(results[0]));writer.writeheader();writer.writerows(results)
    (output/'replay-manifest.json').write_text(json.dumps(replay,indent=2,sort_keys=True)+'\n')
    return dict(data_kind='simulated_method_comparison',comparison_rows=len(results),source_cases_replayed=len(replay),
                formal_collection_enabled=False,human_gate_satisfied=False,method_selected=False)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--specification',type=Path,required=True)
    p.add_argument('--input',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();print(json.dumps(execute(json.loads(a.specification.read_bytes()),a.input,a.output)))
