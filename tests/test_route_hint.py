"""route_hint 경로 판정 + 청킹 임계 상향(v2.1) 회귀 검증.

배경: 9,817자 실측 run(_workspace/2026-07-18-001)은 카운트형 어휘·피동 티가
전부 0, 구조 티(ending_comma z=+6.29)만 있는 글이었는데 6청크 정밀 경로가
610K 토큰을 썼다(단일 콜 134K·품질 동등). shim이 route_hint 로 "손댈 양"을
판정해 경로를 권고하고, 청킹은 진짜 장문(>15,000자)에서만 의미를 갖게 한다.

핵심 회귀 계약:
  1. 실측 글 프로파일(카운트형 0 · risk high · 9,817자)은 절대 heavy 가 아니다.
  2. route_hint 는 권고다 — 산출 실패가 파이프라인을 막지 않는다.
  3. 청킹 임계는 7,000/9,000 이상 — 1만자급 헤딩 없는 글은 1~2청크.

fresh clone 에서도 돌도록 합성 데이터 기반. 실측 run 파일은 있을 때만 검증.
pytest / unittest 양쪽 실행 가능.
"""

from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, "..", "scripts")
WORKSPACE_METRICS = os.path.join(
    HERE, "..", "_workspace", "2026-07-18-001", "00_metrics.json"
)


def _load_prep():
    path = os.path.join(SCRIPTS, "prepare_monolith_input.py")
    spec = importlib.util.spec_from_file_location("prepare_monolith_input", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


PREP = _load_prep()


def _metrics_stub(
    risk_band: str = "low",
    char_count: int = 5000,
    pivots: int = 0,
    balances: int = 0,
    double_passive: int = 0,
    by_passive: int = 0,
    have_make: int = 0,
    double_particle: int = 0,
) -> dict:
    """compute_all 산출 형태를 흉내 낸 최소 stub."""
    return {
        "risk_band": risk_band,
        "char_count": char_count,
        "metrics": {
            "conclusion_pivot_count": pivots,
            "safe_balance_count": balances,
        },
        "v2_metrics": {
            "double_passive_count": double_passive,
            "by_passive_count": by_passive,
            "have_make_literal_count": have_make,
            "double_particle_count": double_particle,
        },
    }


class RouteHintTests(unittest.TestCase):
    # --- 실측 글 프로파일 회귀 (이 테스트가 이 모듈의 존재 이유) -----------

    def test_well_written_but_structurally_risky_is_standard(self) -> None:
        """실측 글: 카운트형 0 · risk high(구조 지표만) · 9,817자 → standard.

        절대 heavy 가 아니어야 한다 — heavy 였던 것이 610K 토큰 사고의 원인.
        """
        obj = _metrics_stub(risk_band="high", char_count=9817)
        out = PREP.compute_route_hint(obj)
        self.assertEqual(out["route_hint"], "standard")
        self.assertNotEqual(out["route_hint"], "heavy")
        self.assertEqual(out["route_signals"]["lexical_tell_count"], 0)

    @unittest.skipUnless(
        os.path.exists(WORKSPACE_METRICS), "실측 run 없음 (fresh clone)"
    )
    def test_live_workspace_run_routes_light_or_standard(self) -> None:
        with open(WORKSPACE_METRICS, encoding="utf-8") as f:
            obj = json.load(f)
        out = PREP.compute_route_hint(obj)
        self.assertIn(out["route_hint"], ("light", "standard"))

    # --- 3-tier 경계 -------------------------------------------------------

    def test_clean_low_risk_is_light(self) -> None:
        out = PREP.compute_route_hint(_metrics_stub(risk_band="low"))
        self.assertEqual(out["route_hint"], "light")

    def test_light_boundary_two_tells_medium(self) -> None:
        obj = _metrics_stub(risk_band="medium", pivots=1, double_passive=1)
        self.assertEqual(PREP.compute_route_hint(obj)["route_hint"], "light")

    def test_three_tells_medium_is_standard(self) -> None:
        obj = _metrics_stub(risk_band="medium", pivots=2, by_passive=1)
        self.assertEqual(PREP.compute_route_hint(obj)["route_hint"], "standard")

    def test_dense_slop_high_risk_is_heavy(self) -> None:
        obj = _metrics_stub(
            risk_band="high",
            pivots=3,
            balances=2,
            double_passive=2,
            have_make=1,
        )
        out = PREP.compute_route_hint(obj)
        self.assertEqual(out["route_hint"], "heavy")
        self.assertEqual(out["route_signals"]["lexical_tell_count"], 8)

    def test_seven_tells_high_risk_is_standard_not_heavy(self) -> None:
        obj = _metrics_stub(risk_band="high", pivots=4, double_passive=3)
        self.assertEqual(PREP.compute_route_hint(obj)["route_hint"], "standard")

    def test_very_long_text_is_heavy_regardless(self) -> None:
        obj = _metrics_stub(risk_band="low", char_count=22000)
        out = PREP.compute_route_hint(obj)
        self.assertEqual(out["route_hint"], "heavy")

    def test_partial_metrics_degrade_to_standard(self) -> None:
        """키 누락·빈 입력에도 죽지 않고 보수적으로 standard."""
        out = PREP.compute_route_hint({})
        self.assertEqual(out["route_hint"], "standard")

    def test_reason_is_single_line(self) -> None:
        for stub in (
            _metrics_stub(),
            _metrics_stub(risk_band="high", char_count=9817),
            _metrics_stub(risk_band="high", pivots=9),
            _metrics_stub(char_count=30000),
        ):
            out = PREP.compute_route_hint(stub)
            self.assertNotIn("\n", out["route_reason"])
            self.assertTrue(out["route_reason"])

    # --- 렌더: metrics 블록에 권고로 표기 ----------------------------------

    def test_render_block_includes_advisory_route_hint(self) -> None:
        obj = _metrics_stub(risk_band="low")
        obj.update(PREP.compute_route_hint(obj))
        block = PREP._render_block(obj)
        self.assertIn("route_hint: light", block)
        self.assertIn("권고", block)
        self.assertIn("route_reason:", block)

    def test_render_block_without_route_hint_is_clean(self) -> None:
        """route 산출 실패(부재) 시 블록에 route 줄이 없어야 한다."""
        block = PREP._render_block(_metrics_stub())
        self.assertNotIn("route_hint", block)


class RouteCoverageTests(unittest.TestCase):
    """#104 — 카운트형 키가 못 보는 전형적 AI 초안이 light 로 빠지던 회귀."""

    ISSUE_104 = (
        "결론부터 말씀드리면, 이번 업데이트는 매우 중요한 의미를 갖습니다. "
        "첫째, 사용자 경험이 개선되었습니다. 둘째, 성능이 향상되었습니다. "
        "셋째, 안정성이 강화되었습니다. 이러한 변화들은 단순한 기능 추가를 넘어, "
        "제품의 방향성 자체가 재정의되었음을 보여줍니다. 물론 아직 해결해야 할 "
        "과제들이 남아있는 것도 사실입니다. 하지만 중요한 것은, 우리가 올바른 "
        "방향으로 나아가고 있다는 점입니다. 앞으로도 지속적인 개선을 통해 더 나은 "
        "서비스를 제공할 수 있도록 최선을 다하겠습니다."
    )

    def test_issue_104_sample_is_not_light(self) -> None:
        obj = _metrics_stub(risk_band="low", char_count=len(self.ISSUE_104))
        obj["_text"] = self.ISSUE_104
        out = PREP.compute_route_hint(obj)
        self.assertEqual(out["route_hint"], "standard")
        per = out["route_signals"]["v26_tells"]
        self.assertEqual(per.get("서수열거(C-1)"), 3)
        self.assertEqual(per.get("결산변이(D-1)"), 1)

    def test_long_enumeration_contribution_is_capped(self) -> None:
        """서수 8개 + risk high 가 열거만으로 heavy 가 되면 안 된다."""
        text = " ".join(f"{o}, 항목이 있다." for o in
                        ("첫째", "둘째", "셋째", "넷째", "다섯째",
                         "여섯 번째", "일곱 번째", "여덟 번째"))
        total, per = PREP.count_v26_tells(text)
        self.assertEqual(total, PREP.ORDINAL_ENUM_MIN)
        obj = _metrics_stub(risk_band="high", char_count=len(text))
        obj["_text"] = text
        self.assertEqual(PREP.compute_route_hint(obj)["route_hint"], "standard")

    def test_two_ordinals_not_counted(self) -> None:
        """C-1 은 기본 보존 — 2항 열거는 사람 글에도 흔해 세지 않는다."""
        _, per = PREP.count_v26_tells("첫째, 값이 싸다. 둘째, 가깝다.")
        self.assertNotIn("서수열거(C-1)", per)

    def test_ordinal_mid_sentence_not_counted(self) -> None:
        _, per = PREP.count_v26_tells(
            "그는 첫째 아들이다. 둘째 딸은 서울에 산다. 셋째 아이는 아직 어리다."
        )
        self.assertNotIn("서수열거(C-1)", per)

    def test_bare_tonghae_not_counted(self) -> None:
        """'~를 통해' 일반형은 사람 글이 더 쓴다 — 결산 변이형으로 세지 않는다."""
        _, per = PREP.count_v26_tells("꾸준한 연습을 통해 실력이 늘었다.")
        self.assertNotIn("결산변이(D-1)", per)

    def test_light_reason_does_not_claim_well_written(self) -> None:
        out = PREP.compute_route_hint(_metrics_stub(risk_band="low"))
        self.assertEqual(out["route_hint"], "light")
        self.assertNotIn("잘 쓴 글", out["route_reason"])
        self.assertIn("라우터가 세는 항목", out["route_reason"])


FIXTURE_025 = os.path.join(HERE, "fixtures_finance", "concepts_025_excerpt.txt")

# 사람이 쓴 듯한 합성 문단 — 긴 문장·다양한 종결·지시어 없음. 구조 플래그 0 이어야 한다.
_HUMAN_STYLE = (
    "지난주 코스피는 외국인이 반도체 대형주를 사들인 덕에 사흘 연속 올랐지만, 금요일에는 "
    "미국 고용지표가 예상보다 강하게 나오면서 오름폭을 대부분 반납한 채 장을 마쳤다. "
    "시장에서는 금리 인하 시점이 다시 늦춰질 수 있다는 걱정이 번졌고, 원화는 달러당 "
    "1,380원대까지 밀렸다. 증권가는 이번 주 발표될 소비자물가를 변곡점으로 보는 분위기다. "
    "한 자산운용사 관계자는 물가가 한 번 더 높게 나오면 연내 인하 기대를 접어야 할 수도 "
    "있다고 말했다. 반면 채권 쪽은 비교적 차분했는데, 국고채 3년물 금리가 하루 만에 "
    "0.03%포인트 오르는 데 그쳤기 때문이다. 업종별로는 조선과 방산이 강세였고 2차전지는 "
    "약했다. 개인 투자자는 이틀 연속 순매도에 나섰다. 다음 주에는 주요 기업의 실적 발표가 "
    "몰려 있어 종목별 차별화가 더 뚜렷해질 가능성이 높다. 연기금의 리밸런싱 물량이 "
    "월말에 몰릴 것이라는 관측도 있어서, 지수가 단기간에 한 방향으로 쏠리기는 쉽지 않아 "
    "보인다. 한편 유가는 중동 정세가 다소 누그러진 영향으로 배럴당 2달러 가까이 내려 "
    "정유주에는 부담이 됐고, 항공과 해운처럼 연료비 비중이 큰 업종에서는 오히려 반등을 "
    "노리는 매수세가 들어왔다. 거래대금은 이틀째 줄어 시장의 관망 심리가 여전함을 "
    "드러냈다. 변동성지수가 소폭 올랐다는 점도 눈여겨볼 대목이다. 환율 쪽으로 눈을 돌리면 수출업체의 달러 매도가 "
    "생각보다 적었고, 그 빈자리를 역외 세력의 매수가 채우면서 원화 약세 폭이 커졌다는 "
    "해석이 나온다. 다만 이런 흐름이 추세로 굳을지는 아직 두고 봐야 한다는 신중론도 "
    "적지 않다. 어느 쪽이든 이번 주 후반의 미국 지표가 방향을 정할 공산이 크다."
)


def _route_for(text: str) -> dict:
    """compute_all 산출 + 원문으로 route 를 낸다 (shim 단일 모드와 같은 경로)."""
    obj = PREP._metrics_mod.compute_all(text, genre="blog")
    obj["_text"] = text
    return PREP.compute_route_hint(obj)


class StructuralBumpTests(unittest.TestCase):
    def test_one_flag_bumps_light_to_standard(self) -> None:
        obj = _metrics_stub(risk_band="low")
        obj["_text"] = "금리가 올랐다. 환율도 올랐다. 증시는 내렸다. 채권은 보합이었다. " * 30
        out = PREP.compute_route_hint(obj)
        self.assertEqual(out["route_hint"], "standard")
        self.assertIn("구조 신호 1건", out["route_reason"])
        self.assertEqual(out["route_signals"]["structural_flags"], ["짧은 균일 문장"])

    def test_two_flags_bump_to_heavy(self) -> None:
        obj = _metrics_stub(risk_band="low")
        obj["_text"] = (
            "문제는 금리가 아니라 환율이다. 핵심은 속도가 아니라 방향이다. "
            "원인은 수요가 아니라 공급이다. 이는 오래 갈 수 있다. "
        ) * 20
        out = PREP.compute_route_hint(obj)
        self.assertEqual(out["route_hint"], "heavy")
        self.assertGreaterEqual(len(out["route_signals"]["structural_flags"]), 2)

    def test_no_text_keeps_previous_behavior(self) -> None:
        out = PREP.compute_route_hint(_metrics_stub(risk_band="low"))
        self.assertEqual(out["route_hint"], "light")
        self.assertEqual(out["route_signals"]["structural_flags"], [])

    def test_bump_never_lowers_route(self) -> None:
        obj = _metrics_stub(risk_band="high", pivots=3, balances=2, double_passive=2, have_make=1)
        obj["_text"] = _HUMAN_STYLE
        self.assertEqual(PREP.compute_route_hint(obj)["route_hint"], "heavy")

    @unittest.skipUnless(PREP._metrics_mod is not None, "metrics 모듈 없음")
    def test_concept_025_excerpt_is_not_light(self) -> None:
        """회귀: 어휘 티는 없는데 구조가 AI 꼴이라 light 로 새던 글 (변경률 0.24% vs 22%)."""
        with open(FIXTURE_025, encoding="utf-8") as f:
            text = f.read()
        self.assertLessEqual(len(text), 1500)
        out = _route_for(text)
        self.assertNotEqual(out["route_hint"], "light")
        self.assertIn("구조 신호", out["route_reason"])

    @unittest.skipUnless(PREP._metrics_mod is not None, "metrics 모듈 없음")
    def test_human_style_paragraph_stays_light(self) -> None:
        self.assertGreater(len(_HUMAN_STYLE), 800)
        out = _route_for(_HUMAN_STYLE)
        self.assertEqual(out["route_hint"], "light")
        self.assertEqual(out["route_signals"]["structural_flags"], [])


class RouteHintCliTests(unittest.TestCase):
    @unittest.skipUnless(PREP._metrics_mod is not None, "metrics 모듈 없음")
    def test_single_mode_writes_route_hint(self) -> None:
        text = (
            "오늘 아침 골목에서 커피를 샀다. 주인이 새 원두 이야기를 한참 했다.\n\n"
            "돌아오는 길에는 비가 조금 내렸고, 나는 그게 싫지 않았다.\n"
        )
        with tempfile.TemporaryDirectory() as td:
            with open(os.path.join(td, "01_input.txt"), "w", encoding="utf-8") as f:
                f.write(text)
            rc = PREP.main(["--run-dir", td])
            self.assertEqual(rc, 0)
            with open(os.path.join(td, "00_metrics.json"), encoding="utf-8") as f:
                obj = json.load(f)
            self.assertIn(obj.get("route_hint"), ("light", "standard", "heavy"))
            self.assertTrue(obj.get("route_reason"))
            with open(
                os.path.join(td, "01_input_with_metrics.txt"), encoding="utf-8"
            ) as f:
                combined = f.read()
            self.assertIn("route_hint:", combined)

    @unittest.skipUnless(PREP._metrics_mod is not None, "metrics 모듈 없음")
    def test_chunk_mode_manifest_carries_route_and_advisory(self) -> None:
        """1.5만자 이하 --chunk 는 manifest 에 route_hint + 비권장 경고를 남긴다."""
        para = "청킹 게이트를 검증하는 합성 문단이다. 문장을 이어 붙인다. " * 8
        text = "\n\n".join(para.strip() for _ in range(12)) + "\n"
        self.assertLess(len(text), PREP.CHUNK_RECOMMEND_MIN_CHARS)
        with tempfile.TemporaryDirectory() as td:
            with open(os.path.join(td, "01_input.txt"), "w", encoding="utf-8") as f:
                f.write(text)
            rc = PREP.main(["--chunk", "--run-dir", td])
            self.assertEqual(rc, 0)
            with open(os.path.join(td, "chunk_manifest.json"), encoding="utf-8") as f:
                manifest = json.load(f)
            self.assertIn(manifest["route_hint"], ("light", "standard", "heavy"))
            self.assertTrue(
                any("비권장" in w for w in manifest["warnings"]),
                "1.5만자 이하 청킹인데 비권장 권고 경고가 없다",
            )


class ChunkThresholdTests(unittest.TestCase):
    """청킹 임계 상향(3,000/4,000 → 7,000/9,000) 회귀 가드."""

    def test_thresholds_raised(self) -> None:
        self.assertGreaterEqual(PREP.TARGET_CHUNK_CHARS, 7000)
        self.assertGreaterEqual(PREP.MAX_CHUNK_CHARS, 9000)
        self.assertGreater(PREP.MAX_CHUNK_CHARS, PREP.TARGET_CHUNK_CHARS)
        self.assertGreaterEqual(PREP.CHUNK_RECOMMEND_MIN_CHARS, 15000)

    def test_10k_headingless_doc_splits_into_at_most_two_chunks(self) -> None:
        """1만자급 · 헤딩 없는 글은 1~2청크 (종전 임계에선 3~4청크)."""
        para = (
            "구조 신호는 문단을 가로지르므로 청크가 커야 잡힌다. "
            "이 문단은 청킹 임계 검증용 합성 문장으로 이루어져 있다. "
        )
        parts = [(para * 6).strip() for _ in range(26)]
        text = "\n\n".join(parts) + "\n"
        self.assertGreaterEqual(len(text), 9000)
        self.assertLessEqual(len(text), 12000)
        spans, warnings = PREP.compute_chunk_spans(text)
        self.assertLessEqual(len(spans), 2, f"청크 {len(spans)}개 — 임계 상향 무효")
        self.assertEqual(warnings, [])

    def test_heading_dense_doc_still_cuts_at_headings(self) -> None:
        """헤딩 강제 컷 불변식은 임계 상향과 무관하게 유지된다(무결성 가드)."""
        text = (
            "## 1. 서론\n\n첫 섹션 본문이다. 짧다.\n\n"
            "## 2. 본론\n\n둘째 섹션 본문이다. 역시 짧다.\n\n"
            "## 3. 결론\n\n셋째 섹션 본문이다.\n"
        )
        spans, _ = PREP.compute_chunk_spans(text)
        body = [sp for sp in spans if not sp["passthrough"]]
        self.assertEqual(len(body), 3, "헤딩 강제 컷이 사라졌다 — 병합 윤문 버그 위험")


if __name__ == "__main__":
    unittest.main()
