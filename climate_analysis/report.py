"""保留已知时点报告，比较区域判断变化。

报告同时包含海面单期判断（measurements/decisions）与温度剖面热风险分层
（thermal_assessments），整份报告按内容生成指纹，复议可凭指纹追溯旧口径。
"""
import hashlib
import json

from .metrics import compare
from .context import decide
from .thermal import assess


def generate(db, known_at):
    measures = compare(db, known_at)
    decisions = [decide(db, known_at, row) for row in measures]
    thermal_assessments = assess(db, known_at)
    out = {'known_at': known_at, 'measurements': measures, 'decisions': decisions,
           'thermal_assessments': thermal_assessments}
    out['fingerprint'] = hashlib.sha256(
        json.dumps(out, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    return out


def difference(a, b):
    old = {r['id']: r for r in a['decisions']}
    new = {r['id']: r for r in b['decisions']}
    return {'before': a['fingerprint'], 'after': b['fingerprint'],
            'changed': sorted(k for k in old.keys() | new.keys()
                              if old.get(k) != new.get(k))}
