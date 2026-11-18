"""温度剖面 × 渔种活动深度 × 历史参照区间 × 捕捞规则的可解释热风险分层。

设计约束：
- 只用本站（同 region）实测，邻近海区的剖面异常只登记不采用，绝不补成本站实测；
- 栖息深度带内参照层覆盖不足时，风险层级判为“信息不足”，不确定性逐层保留；
- 每个结论携带来源 sha256 与逐层口径，补采/复核引发的复议可追溯旧口径与原因。
"""
from .evidence import records

DEPTH_FIELDS = ('depth_min', 'depth_max')
TIER_RANK = {'低': 0, '中': 1, '高': 2}
INSUFFICIENT = '信息不足'


def _is_profile(payload):
    return all(k in payload for k in DEPTH_FIELDS)


def _require(payload, keys, where):
    missing = [k for k in keys if k not in payload]
    if missing:
        raise ValueError(f'{where} 缺少必填字段: {", ".join(missing)}')


def _overlaps(a_min, a_max, b_min, b_max):
    """两个深度闭区间是否有实际交叠（仅端点相接不算同层）。"""
    return a_min < b_max and b_min < a_max


def _dedup(items):
    seen = set()
    out = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _layer_tier(delta, rule):
    if delta >= rule['high_delta']:
        return '高'
    if delta >= rule['medium_delta']:
        return '中'
    return '低'


def _find_assessment(report, region):
    for item in report.get('thermal_assessments', []):
        if item['region'] == region:
            return item
    raise ValueError(f'报告中找不到区域 {region} 的热风险评估')


def assess(db, known_at, region=None):
    """按已知时点对各渔种栖息深度带做热风险分层。"""
    observations = records(db, 'observation', known_at)
    references = records(db, 'reference', known_at)
    habitats = [h for h in records(db, 'habitat', known_at)
                if region is None or h['payload']['region'] == region]
    rule_rows = records(db, 'thermal_rule', known_at)

    results = []
    for habitat in habitats:
        hp = habitat['payload']
        _require(hp, ('region', 'species', 'depth_min', 'depth_max'),
                 f"栖息记录 {habitat['id']}")
        h_region = hp['region']
        metric = hp.get('metric', 'temperature')
        rule_matches = [r for r in rule_rows
                        if r['payload'].get('region') == h_region
                        and r['payload'].get('metric') == metric]
        if not rule_matches:
            results.append(_blank(hp, habitat, known_at, ['无适用捕捞规则，无法分层']))
            continue
        rule_row = rule_matches[-1]
        rule = rule_row['payload']
        _require(rule, ('unit', 'minimum_coverage', 'medium_delta', 'high_delta',
                        'action_low', 'action_medium', 'action_high'),
                 f"热风险规则 {rule_row['id']}")

        local_obs, wrong_unit_obs, neighbor_obs = [], [], []
        for obs in observations:
            op = obs['payload']
            if not _is_profile(op) or op.get('metric') != rule['metric']:
                continue
            _require(op, ('region', 'unit', 'value'), f"剖面观测 {obs['id']}")
            if not _overlaps(op['depth_min'], op['depth_max'],
                             hp['depth_min'], hp['depth_max']):
                continue
            if op['region'] != h_region:
                # 只登记、不采用：邻近海区异常不得补成本站实测
                neighbor_obs.append(obs)
            elif op.get('unit') != rule['unit']:
                wrong_unit_obs.append(obs)
            else:
                local_obs.append(obs)

        refs = [r for r in references
                if _is_profile(r['payload'])
                and r['payload'].get('metric') == rule['metric']
                and r['payload'].get('region') == h_region
                and r['payload'].get('unit') == rule['unit']
                and _overlaps(r['payload']['depth_min'], r['payload']['depth_max'],
                              hp['depth_min'], hp['depth_max'])]
        for ref in refs:
            _require(ref['payload'], ('range_min', 'range_max'),
                     f"剖面参照 {ref['id']}")

        reasons = []
        if wrong_unit_obs:
            reasons.append('存在口径（单位）不符的本站剖面，未参与分层：'
                           + ','.join(o['id'] for o in wrong_unit_obs))
        if not refs:
            reasons.append('栖息深度带内无历史参照区间')
            results.append(_blank(hp, habitat, known_at, reasons, rule_row))
            continue

        layers = []
        for ref in refs:
            rp = ref['payload']
            matched = [o for o in local_obs
                       if _overlaps(o['payload']['depth_min'], o['payload']['depth_max'],
                                    rp['depth_min'], rp['depth_max'])]
            if not matched:
                layers.append({
                    'depth_min': rp['depth_min'], 'depth_max': rp['depth_max'],
                    'reference_id': ref['id'], 'status': '缺测',
                    'observation_id': None, 'observation_revision': None,
                    'value': None, 'delta': None, 'in_range': None, 'tier': None,
                    'uncertainty_reasons': ['该深度层无本站实测，保留缺测，不以邻近海区异常补值'],
                    'sources': [ref['source']['sha256']],
                })
                continue
            obs = matched[-1]
            op = obs['payload']
            low, high = rp['range_min'], rp['range_max']
            midpoint = (low + high) / 2
            delta = op['value'] - midpoint
            layers.append({
                'depth_min': rp['depth_min'], 'depth_max': rp['depth_max'],
                'reference_id': ref['id'], 'status': '实测',
                'observation_id': obs['id'], 'observation_revision': obs['revision'],
                'value': op['value'], 'delta': round(delta, 6),
                'in_range': low <= op['value'] <= high,
                'tier': _layer_tier(delta, rule),
                'uncertainty_reasons': [] if low <= op['value'] <= high
                else ['实测值落在历史参照区间之外'],
                'sources': [obs['source']['sha256'], ref['source']['sha256']],
            })

        required = len(layers)
        covered = sum(1 for layer in layers if layer['status'] == '实测')
        ratio = round(covered / required, 6) if required else 0.0
        missing = [f"{layer['depth_min']}-{layer['depth_max']}米"
                   for layer in layers if layer['status'] == '缺测']
        if missing:
            reasons.append('栖息深度带内缺测深度层：' + '、'.join(missing)
                           + '；缺测层不以邻近海区异常补值')

        sources = [habitat['source']['sha256'], rule_row['source']['sha256']]
        for layer in layers:
            sources.extend(layer['sources'])

        if ratio < rule['minimum_coverage']:
            tier, action = INSUFFICIENT, None
            reasons.append(f"覆盖率 {ratio:.2f} 低于规则下限 {rule['minimum_coverage']:.2f}，不做确定分层")
        else:
            tier = max((layer['tier'] for layer in layers
                        if layer['tier'] is not None),
                       key=lambda t: TIER_RANK[t], default=INSUFFICIENT)
            action = rule[f'action_{ {"低": "low", "中": "medium", "高": "high"}[tier] }']

        results.append({
            'known_at': known_at,
            'region': h_region,
            'species': hp['species'],
            'habitat_id': habitat['id'],
            'depth_min': hp['depth_min'],
            'depth_max': hp['depth_max'],
            'rule_id': rule_row['id'],
            'coverage': {'covered': covered, 'required': required,
                         'ratio': ratio, 'minimum_coverage': rule['minimum_coverage']},
            'tier': tier,
            'action': action,
            'excluded_other_region': [
                {'observation_id': o['id'], 'region': o['payload']['region'],
                 'depth_min': o['payload']['depth_min'],
                 'depth_max': o['payload']['depth_max']}
                for o in neighbor_obs],
            'layers': layers,
            'uncertainty_reasons': reasons,
            'sources': _dedup(sources),
        })
    return sorted(results, key=lambda item: (item['region'], item['species']))


def _blank(habitat_payload, habitat, known_at, reasons, rule_row=None):
    sources = [habitat['source']['sha256']]
    if rule_row is not None:
        sources.append(rule_row['source']['sha256'])
    return {
        'known_at': known_at,
        'region': habitat_payload['region'],
        'species': habitat_payload['species'],
        'habitat_id': habitat['id'],
        'depth_min': habitat_payload['depth_min'],
        'depth_max': habitat_payload['depth_max'],
        'rule_id': rule_row['id'] if rule_row else None,
        'coverage': {'covered': 0, 'required': 0, 'ratio': 0.0,
                     'minimum_coverage': None},
        'tier': INSUFFICIENT,
        'action': None,
        'excluded_other_region': [],
        'layers': [],
        'uncertainty_reasons': reasons,
        'sources': sources,
    }


def reconsider(previous_report, current_report, review):
    """一次补采或质量复核的复议：保留旧口径、新口径与复议原因。"""
    payload = review['payload']
    _require(payload, ('region', 'reason', 'changed_inputs',
                       'before_known_at', 'after_known_at'),
             f"复议记录 {review['id']}")
    before = _find_assessment(previous_report, payload['region'])
    after = _find_assessment(current_report, payload['region'])
    if previous_report['known_at'] != payload['before_known_at'] \
            or current_report['known_at'] != payload['after_known_at']:
        raise ValueError('复议记录引用的已知时点与报告不一致')

    old_layers = {layer['reference_id']: layer for layer in before['layers']}
    new_layers = {layer['reference_id']: layer for layer in after['layers']}
    changes = []
    for ref_id in sorted(set(old_layers) | set(new_layers)):
        old, new = old_layers.get(ref_id), new_layers.get(ref_id)
        if old is not None and new is not None and old == new:
            continue
        change = _layer_change(old, new)
        if change is None:
            continue
        changes.append({
            'reference_id': ref_id,
            'depth_min': (new or old)['depth_min'],
            'depth_max': (new or old)['depth_max'],
            'change': change,
            'before': _layer_snapshot(old),
            'after': _layer_snapshot(new),
        })

    sources = [review['source']['sha256']] + before['sources'] + after['sources']
    return {
        'review_id': review['id'],
        'review_revision': review['revision'],
        'region': payload['region'],
        'reason': payload['reason'],
        'changed_inputs': list(payload['changed_inputs']),
        'before_known_at': previous_report['known_at'],
        'after_known_at': current_report['known_at'],
        'tier_before': before['tier'],
        'tier_after': after['tier'],
        'tier_changed': before['tier'] != after['tier'],
        'action_before': before['action'],
        'action_after': after['action'],
        'coverage_before': before['coverage'],
        'coverage_after': after['coverage'],
        'caliber_changes': changes,
        'uncertainty_before': list(before['uncertainty_reasons']),
        'uncertainty_after': list(after['uncertainty_reasons']),
        'previous_fingerprint': previous_report['fingerprint'],
        'current_fingerprint': current_report['fingerprint'],
        'sources': _dedup(sources),
    }


def _layer_change(old, new):
    if old is None or old['status'] == '缺测':
        return '新增本站实测覆盖（补采）'
    if new is None or new['status'] == '缺测':
        return '本站实测撤回（质量复核）'
    if old['observation_revision'] != new['observation_revision'] \
            or old['value'] != new['value']:
        return '观测订正（质量复核），保留旧口径'
    if old['tier'] != new['tier']:
        return '参照或规则口径变化导致层级调整'
    return None


def _layer_snapshot(layer):
    if layer is None:
        return None
    return {
        'status': layer['status'],
        'observation_id': layer['observation_id'],
        'observation_revision': layer['observation_revision'],
        'value': layer['value'],
        'delta': layer['delta'],
        'tier': layer['tier'],
        'in_range': layer['in_range'],
    }


def reconsider_all(db, reports):
    """按时间顺序处理复议记录，每条复议只在其到达后的首个时点处理一次。"""
    by_known = {report['known_at']: report for report in reports}
    seen = set()
    out = []
    for report in reports:
        for review in records(db, 'review', report['known_at']):
            key = (review['id'], review['revision'])
            if key in seen:
                continue
            seen.add(key)
            payload = review['payload']
            if payload['before_known_at'] not in by_known \
                    or payload['after_known_at'] not in by_known:
                continue
            out.append(reconsider(by_known[payload['before_known_at']],
                                  by_known[payload['after_known_at']], review))
    return out
