#!/usr/bin/env python3
"""금융 장르 구조 신호 캘리브레이션 — 사람 코퍼스 vs AI 글의 문서 단위 분포.

왜 필요한가
-----------
route_hint 가 카운트형 어휘 티만 봐서, 구조가 AI 꼴(대구 반복·짧고 균일한 문장·
문두 지시어·합쇼체 일색)인 글이 light 로 샜다(개념 글 025: 변경률 0.24% vs 정밀
윤문 22%). `metrics_v2.structural_flags` 의 역치를 사람 글에서 잡고, 그 근거를
이 스크립트가 파생 통계로만 남긴다.

⚠️ **본문은 이 레포에 넣지 않는다.** 사람 글은 증권사·언론사 저작물이므로 건수·
비율·백분위만 커밋한다. 본문은 로컬 코퍼스 경로에서 읽는다.

입력
----
--human  코퍼스 루트 (index.jsonl + text/<id>.txt). ai_flag 가 비어 있지 않은 행은 제외.
--ai     AI 글 JSON 리스트 [{text, model}, ...]

사용:
    python3 scripts/build_structural_calibration.py --human <dir> --ai <json> [-o <out>]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "skills", "humanize-korean", "references"))
import metrics_v2  # noqa: E402

DEFAULT_OUT = os.path.join(
    HERE, "..", "tests", "baselines", "2026-10-21-finance-structural-calibration.json"
)
SIGNALS = ("antithesis", "mean_sent_len", "demonstratives", "nida_share")
PERCENTILES = (5, 25, 50, 75, 95)


def _load_human(root: str) -> list[tuple[str, str]]:
    """(그룹, 본문). 그룹은 index.jsonl 의 kind (report | news)."""
    docs = []
    with open(os.path.join(root, "index.jsonl"), encoding="utf-8") as f:
        for line in f:
            row = json.loads(line)
            if row.get("ai_flag"):
                continue
            path = os.path.join(root, "text", f"{row['id']}.txt")
            with open(path, encoding="utf-8") as g:
                docs.append((str(row.get("kind", "unknown")), g.read()))
    return docs


def _load_ai(path: str) -> list[tuple[str, str]]:
    with open(path, encoding="utf-8") as f:
        return [(str(d.get("model", "unknown")), d["text"]) for d in json.load(f)]


def _pct(values: list[float], p: int) -> float:
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, int(p / 100 * len(ordered)))], 3)


def _group_stats(texts: list[str]) -> dict:
    """>800자 문서만 신호 통계를 낸다 (structural_flags 와 같은 기준)."""
    long_texts = [t for t in texts if len(t.strip()) > metrics_v2.STRUCT_MIN_CHARS]
    out: dict = {"docs_total": len(texts), "docs_over_min_chars": len(long_texts)}
    if not long_texts:
        return out
    results = [metrics_v2.structural_flags(t) for t in long_texts]
    out["signals"] = {
        s: {
            "percentiles": {f"p{p}": _pct([r["values"][s] for r in results], p) for p in PERCENTILES}
        }
        for s in SIGNALS
    }
    n = len(results)
    by_flag: dict[str, int] = defaultdict(int)
    for r in results:
        for fl in r["flags"]:
            by_flag[fl] += 1
    out["flag_doc_counts"] = dict(by_flag)
    out["docs_with_1plus_flags"] = sum(1 for r in results if r["flags"])
    out["docs_with_2plus_flags"] = sum(1 for r in results if len(r["flags"]) >= 2)
    out["rate_1plus"] = round(out["docs_with_1plus_flags"] / n, 3)
    out["rate_2plus"] = round(out["docs_with_2plus_flags"] / n, 3)
    rates = [metrics_v2.inanimate_subject_rate(t) for t in long_texts]
    out["inanimate_subject_rate"] = {
        "mean": round(sum(rates) / n, 4),
        "docs_nonzero": sum(1 for r in rates if r > 0),
    }
    return out


def _by_group(docs: list[tuple[str, str]]) -> dict:
    groups: dict[str, list[str]] = defaultdict(list)
    for g, t in docs:
        groups[g].append(t)
    result = {g: _group_stats(ts) for g, ts in sorted(groups.items())}
    result["_all"] = _group_stats([t for _, t in docs])
    return result


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="구조 신호 캘리브레이션 기준선 생성")
    p.add_argument("--human", required=True, help="사람 코퍼스 루트 (index.jsonl + text/)")
    p.add_argument("--ai", required=True, help="AI 글 JSON 리스트")
    p.add_argument("-o", "--out", default=DEFAULT_OUT)
    args = p.parse_args(argv)

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "provenance": (
            "사람: 금융 증권사 리포트(mirae·samsung) + 경제뉴스(이데일리·연합인포맥스·"
            "연합뉴스 경제·매일경제), 2026-10-21 수집, ai_flag 행 제외. 로컬 코퍼스 "
            "~/corpora/macromalt-human. AI: macromalt 자체 생성글(코스피 브리핑·딥다이브·"
            "개념 정리). 저작권상 본문은 포함하지 않고 파생 통계만 싣는다."
        ),
        "thresholds": {
            "antithesis_min": metrics_v2.STRUCT_ANTITHESIS_MIN,
            "mean_sent_len_max_exclusive": metrics_v2.STRUCT_MEAN_SENT_LEN_MAX,
            "demonstratives_min": metrics_v2.STRUCT_DEMONSTRATIVE_MIN,
            "nida_share_min": metrics_v2.STRUCT_NIDA_SHARE_MIN,
            "min_chars": metrics_v2.STRUCT_MIN_CHARS,
        },
        "route_rule": "플래그 1개 → 최소 standard / 2개 이상 → heavy",
        "human": _by_group(_load_human(args.human)),
        "ai": _by_group(_load_ai(args.ai)),
    }
    out = os.path.abspath(args.out)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
