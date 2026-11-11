"""Simulation-only sample planning. No study data, release, or sample selection.

Requires existing NumPy for vectorized simulation. Rendering additionally uses
Matplotlib. Actual task identities/classes and the collection runner are untouched.
"""
import argparse
import csv
import hashlib
import json
import math
import random
from pathlib import Path

LABEL = 'SIMULATED PLANNING — NO GEMMA OR HUMAN RESULTS'


def default_spec():
    designs = [(3,1,1),(5,1,1),(5,1,2),(5,2,1),(5,3,1),(5,5,1),(5,5,2),(5,10,1)]
    mechanisms = [
        dict(name='null_independent',delta=0.,q=.8,rho=0.,heterogeneous=False),
        dict(name='small_independent',delta=.1,q=.8,rho=0.,heterogeneous=False),
        dict(name='moderate_independent',delta=.25,q=.8,rho=0.,heterogeneous=False),
        dict(name='large_independent',delta=.4,q=.8,rho=0.,heterogeneous=False),
        dict(name='moderate_shared_quality',delta=.25,q=.8,rho=.6,heterogeneous=False),
        dict(name='moderate_task_variation',delta=.25,q=.8,rho=.6,heterogeneous=True),
        dict(name='low_A_pass',delta=.25,q=.5,rho=.6,heterogeneous=True),
        dict(name='negative_alignment',delta=-.25,q=.8,rho=.6,heterogeneous=True),
        dict(name='future_specialization',delta=.25,q=.8,rho=.6,heterogeneous=True,coupling='opposed')]
    for m in mechanisms:m.setdefault('coupling','shared')
    return dict(schema_version=1,data_kind='simulated_planning',formal_collection_enabled=False,
        human_gate_satisfied=False,master_seed=20261010,replicates=2000,bootstrap_replicates=1000,
        bootstrap_draws=2000,bootstrap_analysis_seed=405,
        designs=[dict(K=k,R=r,S=s) for k,r,s in designs],mechanisms=mechanisms,
        missingness=[dict(name='none',mode='branch',rate=0.),
                     dict(name='branch_05',mode='branch',rate=.05),
                     dict(name='branch_15',mode='branch',rate=.15),
                     dict(name='shared_outage_05',mode='artifact',rate=.05)],
        note='Invented generic tasks: K=3 uses 2 divergent/1 convergent; K=5 uses 3/2. '
             'No real tasks are classified. Parameters are assumptions, not fits to observed outcomes. '
             'Hypothetical tasks are fixed, not sampled from a population. No final sample is selected.')


def validate(spec):
    if (type(spec.get('schema_version')) is not int or spec['schema_version'] != 1 or
        spec.get('data_kind') != 'simulated_planning' or
        spec.get('formal_collection_enabled') is not False or spec.get('human_gate_satisfied') is not False):
        raise ValueError('simulation-only specification required')
    for field in ('master_seed','replicates','bootstrap_replicates','bootstrap_draws','bootstrap_analysis_seed'):
        if type(spec.get(field)) is not int or spec[field] < (0 if 'seed' in field else 2):
            raise ValueError('invalid integer simulation setting')
    if spec['bootstrap_replicates'] > spec['replicates']:
        raise ValueError('bootstrap replicate count exceeds simulated datasets')
    for field in ('designs','mechanisms','missingness'):
        if not spec[field] or len({json.dumps(x,sort_keys=True) for x in spec[field]}) != len(spec[field]):
            raise ValueError('nonempty unique scenarios required')
    for field in ('mechanisms','missingness'):
        if len({x['name'] for x in spec[field]}) != len(spec[field]):
            raise ValueError('unique scenario names required')
        if any(not isinstance(x['name'],str) or not x['name'].replace('_','').isalnum() for x in spec[field]):
            raise ValueError('safe scenario names required')
    for d in spec['designs']:
        if set(d) != {'K','R','S'} or d['K'] not in (3,5) or any(type(v) is not int or v < 1 for v in d.values()):
            raise ValueError('invalid candidate design')
    for m in spec['mechanisms']:
        if (type(m['heterogeneous']) is not bool or m['coupling'] not in ('shared','opposed') or not -.5 <= m['delta'] <= .5 or
            not 0 <= m['q'] <= 1 or not 0 <= m['rho'] <= 1):
            raise ValueError('invalid invented mechanism')
    for m in spec['missingness']:
        if m['mode'] not in ('branch','artifact') or not 0 <= m['rate'] <= 1:
            raise ValueError('invalid missingness scenario')


def seed_for(master, identity):
    encoded=json.dumps(identity,sort_keys=True,separators=(',',':')).encode()
    return int.from_bytes(hashlib.sha256(str(master).encode()+b'\0'+encoded).digest()[:16],'big')


def task_parameters(k, mechanism):
    import numpy as np
    divergent=np.arange(k) < (2 if k == 3 else 3)
    baseline=np.linspace(.4,.6,k) if mechanism['heterogeneous'] else np.full(k,.5)
    effect=divergent.astype(float)*mechanism['delta']
    if mechanism['heterogeneous']:
        effect[divergent] *= np.linspace(.75,1.25,int(divergent.sum()))
    return divergent,baseline,effect


def generate(design, mechanism, n, rng):
    import numpy as np
    k,r,s=(design[key] for key in ('K','R','S'))
    divergent,baseline,effect=task_parameters(k,mechanism)
    matched=np.eye(2,dtype=bool)
    probabilities=baseline[:,None,None]+np.where(matched[None],1.,-1.)*effect[:,None,None]/2
    if np.any((probabilities < 0)|(probabilities > 1)):
        raise ValueError('assumed probabilities outside [0,1]')
    # One A pass, regime indicator and latent uniform per shared upstream artifact.
    a=rng.random((n,k,r,2,1,1)) < mechanism['q']
    shared=rng.random((n,k,r,2,1,1)) < mechanism['rho']
    common=rng.random((n,k,r,2,1,1))
    independent=rng.random((n,k,r,2,2,s))
    if mechanism.get('coupling','shared') == 'opposed':
        common=np.concatenate([common,1-common],axis=4)
    uniforms=np.where(shared,common,independent)
    y=a & (uniforms < probabilities[None,:,None,:,:,None])
    return y,divergent,mechanism['q']*effect


def task_statistics(y, missing):
    import numpy as np
    y=np.asarray(y,dtype=bool);missing=np.broadcast_to(missing,y.shape)
    if y.ndim != 6 or y.shape[3:5] != (2,2):
        raise ValueError('expected replicate/task/block/disclosure/future/repeat array')
    n,r,s=y.shape[0],y.shape[2],y.shape[5]
    successes=(y & ~missing).sum(axis=(2,5))/(r*s)
    miss_rates=missing.sum(axis=(2,5))/(r*s)
    def premium(c):return (c[:,:,0,0]-c[:,:,1,0]+c[:,:,1,1]-c[:,:,0,1])/2
    lower=premium(successes)-(miss_rates[:,:,1,0]+miss_rates[:,:,0,1])/2
    upper=premium(successes)+(miss_rates[:,:,0,0]+miss_rates[:,:,1,1])/2
    full=premium(y.mean(axis=(2,5)))
    # Preserve paired disclosure artifacts and every downstream branch per block.
    b=y.mean(axis=5).astype(float)
    blocks=(b[:,:,:,0,0]-b[:,:,:,1,0]+b[:,:,:,1,1]-b[:,:,:,0,1])/2
    return dict(full=full,lower=lower,upper=upper,blocks=blocks,
                missing_count=missing.sum(axis=(2,3,4,5)),complete=~missing.any(axis=(1,2,3,4,5)))


def bootstrap_weights(r, draws=2000, seed=405):
    import numpy as np
    if r < 2:return None
    rng=random.Random(seed)
    weights=np.zeros((draws,r),dtype=int)
    for i in range(draws):
        for _ in range(r):weights[i,rng.randrange(r)] += 1
    return weights


def bootstrap_intervals(blocks, draws=2000, seed=405):
    import numpy as np
    r=blocks.shape[-1];weights=bootstrap_weights(r,draws,seed)
    if weights is None:return None
    samples=np.sort(np.matmul(blocks,weights.T)/r,axis=-1)
    return samples[...,int(.025*draws)],samples[...,min(draws-1,int(.975*draws))]


def proportion(value):
    import numpy as np
    p=float(np.mean(value));n=len(value)
    return p,math.sqrt(p*(1-p)/n)


def execute(spec, output):
    import numpy as np
    validate(spec);output=Path(output);output.mkdir(parents=True,exist_ok=False)
    (output/'replicates').mkdir()
    (output/'specification.json').write_text(json.dumps(spec,indent=2,sort_keys=True)+'\n')
    manifest=[];records=[];bootstrap_records=[]
    for design in spec['designs']:
        k,r,s=(design[key] for key in ('K','R','S'))
        for mechanism in spec['mechanisms']:
            # Generate full data once, then vary missingness independently without refitting.
            identity=dict(design=design,mechanism=mechanism)
            seed=seed_for(spec['master_seed'],identity);rng=np.random.default_rng(seed)
            initial=rng.bit_generator.state
            y,divergent,truth=generate(design,mechanism,spec['replicates'],rng)
            final=rng.bit_generator.state
            for missing_spec in spec['missingness']:
                case=dict(identity,missingness=missing_spec)
                missing_seed=seed_for(spec['master_seed'],dict(case,stream='missingness'))
                mrng=np.random.default_rng(missing_seed)
                shape=y.shape if missing_spec['mode']=='branch' else (len(y),k,r,2,1,1)
                missing=mrng.random(shape) < missing_spec['rate']
                st=task_statistics(y,missing)
                full=st['full'].mean(axis=1);lower=st['lower'].mean(axis=1);upper=st['upper'].mean(axis=1)
                interaction=st['full'][:,divergent].mean(axis=1)-st['full'][:,~divergent].mean(axis=1)
                ilower=st['lower'][:,divergent].mean(axis=1)-st['upper'][:,~divergent].mean(axis=1)
                iupper=st['upper'][:,divergent].mean(axis=1)-st['lower'][:,~divergent].mean(axis=1)
                theta=float(truth.mean());itheta=float(truth[divergent].mean()-truth[~divergent].mean())
                availability,availability_se=proportion(st['complete'])
                units=4*k*r*s if missing_spec['mode']=='branch' else 2*k*r
                analytic_availability=(1-missing_spec['rate'])**units
                error=full-theta;sd=float(full.std(ddof=1))
                sign,sign_se=proportion(full*np.sign(theta)>0) if theta != 0 else (None,None)
                width=upper-lower;iwidth=iupper-ilower
                n=len(y);case_id=f'K{k}-R{r}-S{s}-{mechanism["name"]}-{missing_spec["name"]}'
                path=output/'replicates'/f'{case_id}.npz'
                np.savez_compressed(path,full_task_premiums=st['full'],task_lower=st['lower'],task_upper=st['upper'],
                                    missing_count=st['missing_count'],point_available=st['complete'])
                record=dict(data_kind='simulated_planning',case_id=case_id,**design,mechanism=mechanism['name'],
                    missingness=missing_spec['name'],replicates=n,upstream_sessions=2*k*r,downstream_sessions=4*k*r*s,
                    total_sessions=2*k*r+4*k*r*s,allowance_sum_hours=.25*(2*k*r+4*k*r*s),
                    assumed_overall_tau=theta,assumed_class_interaction=itheta,
                    full_data_bias=float(error.mean()),bias_MCSE=sd/math.sqrt(n),
                    full_data_sampling_SD=sd,full_data_RMSE=float(np.sqrt(np.mean(error**2))),
                    full_data_empirical_error_p025=float(np.quantile(error,.025)),
                    full_data_empirical_error_p975=float(np.quantile(error,.975)),
                    full_data_class_interaction_SD=float(interaction.std(ddof=1)),
                    full_data_correct_sign_fraction=sign,correct_sign_MCSE=sign_se,
                    complete_point_fraction=availability,complete_point_MCSE=availability_se,
                    analytic_complete_point_fraction=analytic_availability,
                    mean_missing_bound_width=float(width.mean()),bound_width_MCSE=float(width.std(ddof=1)/math.sqrt(n)),
                    mean_class_missing_bound_width=float(iwidth.mean()),
                    simulated_missing_fraction=float(st['missing_count'].sum()/(n*4*k*r*s)),
                    hypothetical_divergent_tasks=int(divergent.sum()),hypothetical_convergent_tasks=int((~divergent).sum()))
                assert np.all(lower<=full+1e-12) and np.all(full<=upper+1e-12)
                assert np.all(ilower<=interaction+1e-12) and np.all(interaction<=iupper+1e-12)
                records.append(record)
                manifest.append(dict(case_id=case_id,full_data_seed=str(seed),missingness_seed=str(missing_seed),
                    full_data_initial_rng_state=initial,full_data_final_rng_state=final,
                    replicate_file=str(path.relative_to(output)),sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
            # Audit the actual task-level bootstrap only, without inventing overall/class CIs.
            if k==5 and s==1 and mechanism['name'] in ('null_independent','moderate_shared_quality'):
                b=task_statistics(y[:spec['bootstrap_replicates']],np.zeros((),dtype=bool))['blocks'][:,0,:]
                ci=bootstrap_intervals(b,spec['bootstrap_draws'],spec['bootstrap_analysis_seed'])
                if ci is None:
                    coverage=coverage_se=exclusion=exclusion_se=mean_width=None
                else:
                    lo,hi=ci
                    coverage,coverage_se=proportion((lo<=truth[0])&(truth[0]<=hi))
                    exclusion,exclusion_se=proportion((lo>0)|(hi<0))
                    mean_width=float((hi-lo).mean())
                bootstrap_records.append(dict(data_kind='simulated_planning',R=r,mechanism=mechanism['name'],
                    replicates=len(b),assumed_task_tau=float(truth[0]),interval_available=ci is not None,
                    percentile_draws=spec['bootstrap_draws'],nominal_coverage=.95,
                    simulated_coverage=coverage,coverage_MCSE=coverage_se,
                    zero_exclusion_fraction=exclusion,zero_exclusion_MCSE=exclusion_se,
                    interpretation='false_positive_frequency' if truth[0]==0 else 'zero_exclusion_under_assumption_not_validated_power',
                    mean_interval_width=mean_width))
                np.savez_compressed(output/'replicates'/f'bootstrap-R{r}-{mechanism["name"]}.npz',
                    paired_blocks=b,lower=ci[0] if ci else np.full(len(b),np.nan),
                    upper=ci[1] if ci else np.full(len(b),np.nan))
    for name,rows in [('sensitivity.csv',records),('bootstrap-diagnostics.csv',bootstrap_records)]:
        with (output/name).open('x',newline='') as stream:
            fields=list(rows[0]) if rows else ['data_kind','R','mechanism','replicates','assumed_task_tau','interval_available','percentile_draws','nominal_coverage','simulated_coverage','coverage_MCSE','zero_exclusion_fraction','zero_exclusion_MCSE','interpretation','mean_interval_width']
            writer=csv.DictWriter(stream,fieldnames=fields);writer.writeheader();writer.writerows(rows)
    (output/'simulation-streams.json').write_text(json.dumps(manifest,indent=2,sort_keys=True)+'\n')
    return records,bootstrap_records


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--specification',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    records,bootstrap=execute(json.loads(args.specification.read_bytes()),args.output)
    print(json.dumps(dict(data_kind='simulated_planning',scenarios=len(records),bootstrap_scenarios=len(bootstrap),
                         formal_collection_enabled=False,human_gate_satisfied=False)))


if __name__=='__main__':main()
