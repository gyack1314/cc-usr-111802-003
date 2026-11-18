"""从本地合成资料形成各已知时间的来源化报告与热风险复议。"""
import argparse
import json
from pathlib import Path

from .evidence import connect, receive
from .report import generate, difference
from .thermal import reconsider_all


def main():
    p = argparse.ArgumentParser()
    p.add_argument('command', choices=['inspect'])
    p.add_argument('input')
    args = p.parse_args()
    data = json.loads(Path(args.input).read_text(encoding='utf8'))
    db = connect(':memory:')
    for doc in data['documents']:
        receive(db, doc)
    reports = [generate(db, t) for t in data['known_at']]
    reconsiderations = reconsider_all(db, reports)
    output = {
        # 保留两期对比的旧口径输出
        'before': reports[0],
        'after': reports[-1],
        'change': difference(reports[0], reports[-1]),
        # 各已知时点的完整来源化报告
        'reports': reports,
        # 补采/质量复核引发的复议（含旧口径、新口径、原因与原始资料指纹）
        'reconsiderations': reconsiderations,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    db.close()


if __name__ == '__main__':
    main()
