"""温度剖面热风险分层与复议追溯的单元与集成测试。"""
import json
import unittest
from pathlib import Path

from climate_analysis.evidence import connect, receive, records
from climate_analysis.report import generate
from climate_analysis.thermal import assess, reconsider, reconsider_all

DATA = json.loads((Path(__file__).resolve().parents[1]
                   / 'data/example.json').read_text(encoding='utf8'))
T1, T2, T3 = DATA['known_at']


def build_db(documents):
    db = connect(':memory:')
    for doc in documents:
        receive(db, doc)
    return db


def doc(source, document_id, records, received_at, revision='r1'):
    return {'source': source, 'document_id': document_id, 'revision': revision,
            'received_at': received_at,
            'records': [dict(kind=kind, **row) for kind, row in records]}


def profile_doc(document_id, rows, received_at, source='观测机构', revision='r1'):
    return doc(source, document_id,
               [('observation', {
                   'id': rid, 'revision': rev, 'valid_from': '2026-07-01',
                   'payload': {'metric': 'temperature', 'region': region,
                               'unit': unit, 'depth_min': dmin, 'depth_max': dmax,
                               'value': value}})
                for rid, rev, region, unit, dmin, dmax, value in rows],
               received_at, revision)


HABITAT_A = ('habitat', {'id': 'h1', 'revision': 'r1', 'valid_from': '2026-07-01',
                         'payload': {'metric': 'temperature', 'region': 'fishery-A',
                                     'species': '大黄鱼', 'depth_min': 0, 'depth_max': 100}})
RULE_A = ('thermal_rule', {'id': 'tr1', 'revision': 'r1', 'valid_from': '2026-07-01',
                           'payload': {'metric': 'temperature', 'region': 'fishery-A',
                                       'unit': '摄氏度', 'minimum_coverage': 0.75,
                                       'medium_delta': 1, 'high_delta': 3,
                                       'action_low': '正常作业', 'action_medium': '限捕',
                                       'action_high': '停捕'}})
REF_A = ('reference', {'id': 'r10-30', 'revision': 'r1', 'valid_from': '1991-01-01',
                       'payload': {'metric': 'temperature', 'region': 'fishery-A',
                                   'unit': '摄氏度', 'depth_min': 10, 'depth_max': 30,
                                   'range_min': 17, 'range_max': 21}})


class ThermalFromExample(unittest.TestCase):
    def setUp(self):
        self.db = build_db(DATA['documents'])

    def test_coverage_gap_keeps_uncertainty(self):
        result = assess(self.db, T1, region='fishery-A')
        self.assertEqual(len(result), 1)
        a = result[0]
        self.assertEqual(a['tier'], '信息不足')
        self.assertIsNone(a['action'])
        self.assertEqual(a['coverage'], {'covered': 2, 'required': 4, 'ratio': 0.5,
                                         'minimum_coverage': 0.75})
        missing = [l for l in a['layers'] if l['status'] == '缺测']
        self.assertEqual({l['reference_id'] for l in missing},
                         {'ref-0-10', 'ref-60-100'})
        for layer in missing:
            self.assertIsNone(layer['value'])
            self.assertIsNone(layer['tier'])
            self.assertTrue(any('邻近海区' in r for r in layer['uncertainty_reasons']))
        self.assertTrue(any('覆盖率' in r for r in a['uncertainty_reasons']))

    def test_neighbor_region_never_substitutes(self):
        a = assess(self.db, T1, region='fishery-A')[0]
        excluded = {x['observation_id']: x['region']
                    for x in a['excluded_other_region']}
        self.assertEqual(excluded, {'profile-neighbor': 'fishery-B'})
        # 邻区 25 摄氏度的异常绝不能成为 fishery-A 10-30 米或任何缺测层的取值
        for layer in a['layers']:
            self.assertNotEqual(layer['value'], 25)
        self.assertEqual(len(a['layers']), 4)

    def test_full_coverage_tier_and_actions(self):
        t2 = assess(self.db, T2, region='fishery-A')[0]
        self.assertEqual(t2['coverage']['ratio'], 1.0)
        self.assertEqual(t2['tier'], '高')
        self.assertEqual(t2['action'], '停捕')
        t3 = assess(self.db, T3, region='fishery-A')[0]
        self.assertEqual(t3['tier'], '中')
        self.assertEqual(t3['action'], '限捕')

    def test_reconsideration_supplement_and_qc(self):
        reports = {t: generate(self.db, t) for t in (T1, T2, T3)}
        reviews = {}
        for t in (T1, T2, T3):
            for review in records(self.db, 'review', t):
                reviews[review['id']] = review

        rc1 = reconsider(reports[T1], reports[T2], reviews['review-1'])
        self.assertTrue(rc1['tier_changed'])
        self.assertEqual((rc1['tier_before'], rc1['tier_after']), ('信息不足', '高'))
        self.assertEqual({c['change'] for c in rc1['caliber_changes']},
                         {'新增本站实测覆盖（补采）'})
        for change in rc1['caliber_changes']:
            self.assertEqual(change['before']['status'], '缺测')
            self.assertIsNotNone(change['after']['observation_id'])

        rc2 = reconsider(reports[T2], reports[T3], reviews['review-2'])
        self.assertEqual((rc2['tier_before'], rc2['tier_after']), ('高', '中'))
        self.assertEqual(rc2['action_before'], '停捕')
        self.assertEqual(rc2['action_after'], '限捕')
        layer = rc2['caliber_changes'][0]
        self.assertIn('观测订正', layer['change'])
        # 旧口径原样保留：r1 的 22.5 与 r2 的 20.5 都可查
        self.assertEqual(layer['before']['observation_revision'], 'r1')
        self.assertEqual(layer['before']['value'], 22.5)
        self.assertEqual(layer['after']['observation_revision'], 'r2')
        self.assertEqual(layer['after']['value'], 20.5)
        # 复议结果可回到两份原始报告指纹与原始资料
        self.assertEqual(rc2['previous_fingerprint'], reports[T2]['fingerprint'])
        self.assertEqual(rc2['current_fingerprint'], reports[T3]['fingerprint'])
        self.assertTrue(rc2['sources'])

    def test_reconsider_all_each_review_once(self):
        reports = [generate(self.db, t) for t in (T1, T2, T3)]
        rc = reconsider_all(self.db, reports)
        self.assertEqual([r['review_id'] for r in rc], ['review-1', 'review-2'])
        # 重复运行不产生重复复议
        self.assertEqual(len(reconsider_all(self.db, reports)), 2)

    def test_reconsider_time_mismatch_raises(self):
        reports = {t: generate(self.db, t) for t in (T1, T2, T3)}
        review = next(r for t in (T1, T2, T3)
                      for r in records(self.db, 'review', t)
                      if r['id'] == 'review-1')
        with self.assertRaises(ValueError):
            reconsider(reports[T2], reports[T3], review)


class ThermalSynthetic(unittest.TestCase):
    def test_unit_mismatch_excluded_with_reason(self):
        db = build_db([
            doc('渔业资源站', 'hab', [HABITAT_A], '2026-07-25T00:00:00+00:00'),
            doc('业务部门', 'rule', [RULE_A], '2026-07-27T00:00:00+00:00'),
            doc('海洋资料中心', 'ref', [REF_A], '2026-07-26T00:00:00+00:00'),
            profile_doc('obs-wrong-unit', [
                ('o1', 'r1', 'fishery-A', '华氏度', 10, 30, 72.5)],
                '2026-07-31T00:00:00+00:00'),
        ])
        a = assess(db, T1)[0]
        self.assertEqual(a['tier'], '信息不足')
        self.assertEqual(a['coverage']['covered'], 0)
        self.assertTrue(any('口径' in r for r in a['uncertainty_reasons']))

    def test_no_rule_means_unclassified(self):
        db = build_db([
            doc('渔业资源站', 'hab', [
                ('habitat', {'id': 'h2', 'revision': 'r1', 'valid_from': '2026-07-01',
                             'payload': {'metric': 'temperature', 'region': 'fishery-C',
                                         'species': '带鱼', 'depth_min': 0,
                                         'depth_max': 50}})],
                '2026-07-25T00:00:00+00:00')])
        a = assess(db, T1)[0]
        self.assertEqual(a['region'], 'fishery-C')
        self.assertEqual(a['tier'], '信息不足')
        self.assertTrue(any('无适用捕捞规则' in r for r in a['uncertainty_reasons']))

    def test_no_reference_means_unclassified(self):
        db = build_db([
            doc('渔业资源站', 'hab', [HABITAT_A], '2026-07-25T00:00:00+00:00'),
            doc('业务部门', 'rule', [RULE_A], '2026-07-27T00:00:00+00:00'),
        ])
        a = assess(db, T1)[0]
        self.assertEqual(a['tier'], '信息不足')
        self.assertTrue(any('历史参照' in r for r in a['uncertainty_reasons']))

    def test_tier_bands_and_in_range_flag(self):
        # 唯一参照层 10-30 米，区间 17-21（中点 19）：中阈 +1、高阈 +3
        db = build_db([
            doc('渔业资源站', 'hab', [
                ('habitat', {'id': 'h3', 'revision': 'r1', 'valid_from': '2026-07-01',
                             'payload': {'metric': 'temperature', 'region': 'fishery-A',
                                         'species': '大黄鱼', 'depth_min': 10,
                                         'depth_max': 30}})],
                '2026-07-25T00:00:00+00:00'),
            doc('业务部门', 'rule', [RULE_A], '2026-07-27T00:00:00+00:00'),
            doc('海洋资料中心', 'ref', [REF_A], '2026-07-26T00:00:00+00:00'),
            profile_doc('obs', [('o2', 'r1', 'fishery-A', '摄氏度', 10, 30, 22)],
                        '2026-07-31T00:00:00+00:00'),
        ])
        a = assess(db, T1)[0]
        layer = a['layers'][0]
        self.assertEqual(layer['delta'], 3.0)
        self.assertEqual(layer['tier'], '高')
        self.assertFalse(layer['in_range'])
        self.assertEqual(a['tier'], '高')
        self.assertEqual(a['action'], '停捕')

    def test_missing_rule_field_raises_clear_error(self):
        broken = ('thermal_rule', {'id': 'tr1', 'revision': 'r1',
                                   'valid_from': '2026-07-01',
                                   'payload': {'metric': 'temperature',
                                               'region': 'fishery-A'}})
        db = build_db([
            doc('渔业资源站', 'hab', [HABITAT_A], '2026-07-25T00:00:00+00:00'),
            doc('业务部门', 'rule', [broken], '2026-07-27T00:00:00+00:00'),
        ])
        with self.assertRaisesRegex(ValueError, '缺少必填字段'):
            assess(db, T1)

    def test_report_carries_thermal_and_stable_fingerprint(self):
        db = build_db(DATA['documents'])
        r1, r2 = generate(db, T1), generate(db, T1)
        self.assertEqual(r1['fingerprint'], r2['fingerprint'])
        self.assertEqual(len(r1['thermal_assessments']), 1)
        self.assertNotEqual(generate(db, T1)['fingerprint'],
                            generate(db, T2)['fingerprint'])
        # 每个评估与每个深度层都携带来源 sha256
        a = r1['thermal_assessments'][0]
        self.assertTrue(all(len(s) == 64 for s in a['sources']))
        for layer in a['layers']:
            self.assertTrue(all(len(s) == 64 for s in layer['sources']))


if __name__ == '__main__':
    unittest.main()
