"""Equal-weight task contrasts; infrastructure is opt-in and never study data."""
import json
import random
from collections import defaultdict
from pathlib import Path
from statistics import mean

CELLS=[("B1","B1"),("B1","B2"),("B2","B1"),("B2","B2")]


def contrast(values):
    return ((values[("B1","B1")]-values[("B2","B1")])+
            (values[("B2","B2")]-values[("B1","B2")]))/2


def cells(rows):
    grouped=defaultdict(list)
    for row in rows: grouped[(row["disclosed_future"],row["realized_future"])].append(row["trajectory_success"])
    if set(grouped)!=set(CELLS): raise ValueError("incomplete crossover: all four cells are required")
    return grouped


def estimate(rows,include_infrastructure=False,draws=2000,seed=405):
    if not rows: raise ValueError("no observations")
    if type(draws) is not int or draws < 1:
        raise ValueError('bootstrap draws must be a positive integer')
    if not include_infrastructure and any(row["data_kind"]!="study" for row in rows):
        raise ValueError("non-study observations excluded; explicitly opt in only for infrastructure diagnostics")
    if any(r['trajectory_success'] is not None and type(r['trajectory_success']) is not bool for r in rows):
        raise ValueError('trajectory success must be boolean or null')
    keys=[(r["artifact_id"],r["realized_future"],r["downstream_seed"]) for r in rows]
    if len(keys)!=len(set(keys)): raise ValueError("duplicate downstream assignment")
    tasks=defaultdict(list)
    for row in rows: tasks[row["task_id"]].append(row)
    summaries={}
    rng=random.Random(seed)
    for task,observations in tasks.items():
        if len({r['task_class'] for r in observations}) != 1:
            raise ValueError('inconsistent task classification within task')
        grouped=cells(observations)
        statistics={}
        lo,hi={},{}
        for key,values in grouped.items():
            measured=[int(v) for v in values if v is not None]
            n=len(values);missing=n-len(measured)
            lo[key]=sum(measured)/n;hi[key]=(sum(measured)+missing)/n
            statistics["".join(k[-1] for k in key)]={"n":n,"missing":missing,"mean":mean(measured) if measured else None,
                "assigned_success_lower":lo[key],"assigned_success_upper":hi[key]}
        complete=all(item["missing"]==0 for item in statistics.values())
        lower=(lo[("B1","B1")]-hi[("B2","B1")]+lo[("B2","B2")]-hi[("B1","B2")])/2
        upper=(hi[("B1","B1")]-lo[("B2","B1")]+hi[("B2","B2")]-lo[("B1","B2")])/2
        premium=contrast(lo) if complete else None
        blocks=defaultdict(list)
        for row in observations: blocks[row["upstream_seed"]].append(row)
        ci=None
        if complete and len(blocks)>=2:
            for block in blocks.values(): cells(block)
            estimates=[]
            for _ in range(draws):
                sample=[row for _ in blocks for row in rng.choice(list(blocks.values()))]
                estimates.append(contrast({key:mean(map(int,values)) for key,values in cells(sample).items()}))
            estimates.sort()
            ci=[estimates[int(.025*draws)],estimates[min(draws-1,int(.975*draws))]]
        summaries[task]={"cells":statistics,"tau_ID":premium,"tau_ID_missing_bounds":[lower,upper],
                         "conditional_seed_block_bootstrap_95":ci,"upstream_seed_blocks":len(blocks),
                         "task_class":observations[0]["task_class"]}
    premiums=[s["tau_ID"] for s in summaries.values()]
    class_values={category:[s["tau_ID"] for s in summaries.values() if s["task_class"]==category]
                  for category in ["divergent","convergent"]}
    interaction=(mean(class_values["divergent"])-mean(class_values["convergent"])) if (
        all(class_values.values()) and all(p is not None for values in class_values.values() for p in values)) else None
    return {"data_kind":"infrastructure_diagnostic" if include_infrastructure else "study",
            "tasks":summaries,"equal_weight_tau_ID":mean(premiums) if all(p is not None for p in premiums) else None,
            "equal_weight_tau_ID_missing_bounds":[mean(s['tau_ID_missing_bounds'][i] for s in summaries.values()) for i in (0,1)],
            "divergent_minus_convergent":interaction,
            "limitations":"Seed-block intervals condition on these tasks; no architectural mediation or population generalization is identified."}


def load(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]
