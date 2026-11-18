"""对同口径观测和参照形成单期偏离。

海面单指标链路只处理非剖面记录；温度剖面热风险分层见 thermal 模块，
两者口径分离，避免把某一水深的剖面值当作海面指标比较。
"""
from .evidence import records

PROFILE_FIELDS = ('depth_min', 'depth_max')


def _is_profile(payload):
    return all(k in payload for k in PROFILE_FIELDS)


def compare(db, known_at):
    observations = [o for o in records(db, 'observation', known_at)
                    if not _is_profile(o['payload'])]
    references = [r for r in records(db, 'reference', known_at)
                  if not _is_profile(r['payload'])]
    result = []
    for obs in observations:
        p = obs['payload']
        matches = [r for r in references
                   if all(r['payload'][k] == p[k] for k in ('metric', 'region', 'unit'))]
        if not matches:
            result.append({'id': obs['id'], 'status': '无参照'})
            continue
        ref = matches[-1]
        result.append({'id': obs['id'], 'region': p['region'], 'metric': p['metric'],
                       'unit': p['unit'], 'delta': p['value'] - ref['payload']['value'],
                       'sources': [obs['source']['sha256'], ref['source']['sha256']],
                       'status': '可比'})
    return result
