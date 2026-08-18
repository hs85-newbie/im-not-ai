#!/usr/bin/env python3
"""한국어 문학 산문 문체 기준선 측정 (baseline.json 의 fiction_ko 장르 산출용).

우리 연재(AI 집필)와 저작권 만료 근대소설(사람 집필)을 같은 자로 재서
scripts/data/ko-fiction-baseline.json 을 갱신한다.

실행: python3 scripts/measure_fiction_baseline.py --fetch
  --fetch 를 주면 위키문헌에서 PD 코퍼스를 다시 내려받는다(_workspace/pd-corpus/).

WHY: humanize-korean 의 기본 기준선은 에세이(KatFish 연구)라 소설 산문에 그대로 대면
     화자의 리듬까지 AI 티로 잡힌다. 반대로 우리 실제 버릇은 놓친다. 장르 기준선이 있어야
     둘을 가른다.
"""
import argparse, glob, json, os, re, statistics, sys, time
import urllib.parse, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
METRICS_DIR = os.path.join(ROOT, ".claude", "skills", "humanize-korean", "references")
PD_DIR = os.path.join(ROOT, "_workspace", "pd-corpus")
OUT = os.path.join(ROOT, "scripts", "data", "ko-fiction-baseline.json")

# 사망 후 70년이 지나 저작권이 만료된 작가들의 단편·중편. 위키문헌 수록분.
PD_TITLES = [
    "운수 좋은 날", "B사감과 러브레터", "술 권하는 사회", "고향 (현진건)",   # 현진건 1943
    "메밀꽃 필 무렵",                                                        # 이효석 1942
    "봄봄", "동백꽃",                                                        # 김유정 1937
    "탈출기",                                                                # 최서해 1932
    "레디메이드 인생", "치숙",                                               # 채만식 1950
    "붉은 산", "광염 소나타",                                                # 김동인 1951
]
UA = "pnakotic-style-research/1.0 (https://pnakotic.uk) python-urllib"
CHUNK = 3100          # 우리 회차 평균 길이에 맞춘다(길이 편향 제거)
MIN_CHUNK = 1200

sys.path.insert(0, METRICS_DIR)
import metrics  # noqa: E402


def fetch_pd():
    """위키문헌에서 PD 소설 본문을 내려받는다."""
    os.makedirs(PD_DIR, exist_ok=True)
    got = 0
    for title in PD_TITLES:
        q = urllib.parse.urlencode({"action": "query", "prop": "extracts", "explaintext": 1,
                                    "format": "json", "titles": title, "redirects": 1})
        req = urllib.request.Request(f"https://ko.wikisource.org/w/api.php?{q}", headers={"User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=25) as r:
                pages = json.load(r)["query"]["pages"]
        except Exception as exc:                                  # 네트워크·차단은 치명적이지 않다
            print(f"  ! {title}: {exc}", file=sys.stderr)
            continue
        for page in pages.values():
            text = page.get("extract") or ""
            if len(text) > 1500:
                name = re.sub(r"[^가-힣A-Za-z]", "_", title)
                with open(os.path.join(PD_DIR, f"{name}.txt"), "w", encoding="utf-8") as fh:
                    fh.write(text)
                got += 1
        time.sleep(0.4)
    print(f"  PD 코퍼스 {got}편 수집")


def split_chunks(text):
    """문단 경계를 지키며 회차 길이로 자른다."""
    out, cur = [], ""
    for para in re.sub(r"\n{3,}", "\n\n", text.strip()).split("\n\n"):
        if len(cur) + len(para) > CHUNK and len(cur) > MIN_CHUNK:
            out.append(cur); cur = para
        else:
            cur += ("\n\n" if cur else "") + para
    if len(cur) > MIN_CHUNK:
        out.append(cur)
    return out


def our_prose(path):
    """MDX에서 서술 산문만 남긴다. 일지·분절 같은 컴포넌트는 메모체라 문체 통계에서 뺀다."""
    src = open(path, encoding="utf-8").read()
    src = re.sub(r"<(Diary|Recall|Erosion|Engraved|EditorNote|Latin|Thought|Mosaic|Redacted)\b[^>]*>[\s\S]*?</\1>", "", src)
    src = re.sub(r"<[A-Z][A-Za-z]*\b[^>]*/>", "", src)
    return "\n\n".join(p.strip() for p in src.split("\n\n") if p.strip())


def measure(texts):
    acc = {}
    for text in texts:
        for key, val in metrics.compute_all(text)["metrics"].items():
            acc.setdefault(key, []).append(val)
    return {k: {"mean": round(statistics.mean(v), 4),
                "stdev": round(statistics.pstdev(v), 4),
                "n": len(v)} for k, v in acc.items()}


def drift_by_band(bands=((1, 20), (21, 40), (41, 60), (61, 999))):
    """본편 회차 구간별 연결어미-쉼표 비율. 버릇이 굳어 가는지 본다."""
    pattern = os.path.join(ROOT, "app", "[[]locale[]]", "(reader)", "series",
                           "number-beneath-ledger", "chapter-*", "*.ko.mdx")
    rows = []
    for path in glob.glob(pattern):
        num = int(re.search(r"chapter-(\d+)", path).group(1))
        text = our_prose(path)
        if len(text) >= MIN_CHUNK:
            rows.append((num, metrics.compute_all(text)["metrics"]["ending_comma_rate"]))
    out = {}
    for lo, hi in bands:
        vals = [r for n, r in rows if lo <= n <= hi]
        if vals:
            out[f"ch{lo}-{hi}"] = {"mean": round(statistics.mean(vals), 4), "n": len(vals)}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fetch", action="store_true", help="PD 코퍼스를 다시 내려받는다")
    args = ap.parse_args()
    if args.fetch or not glob.glob(os.path.join(PD_DIR, "*.txt")):
        fetch_pd()

    human = [c for f in sorted(glob.glob(os.path.join(PD_DIR, "*.txt")))
             for c in split_chunks(open(f, encoding="utf-8").read())]
    # glob 은 대괄호를 문자클래스로 읽는다. 라우트 폴더명 [locale] 은 [[]locale[]] 로 이스케이프한다.
    pattern = os.path.join(ROOT, "app", "[[]locale[]]", "(reader)", "series", "*", "chapter-*", "*.ko.mdx")
    ai = [t for f in sorted(glob.glob(pattern))
          for t in [our_prose(f)] if len(t) >= MIN_CHUNK]
    if not human or not ai:
        sys.exit("표본이 비었다. --fetch 로 PD 코퍼스를 먼저 받아라.")

    payload = {
        "genre": "fiction_ko",
        "measured_on": time.strftime("%Y-%m-%d"),
        "metrics_version": metrics.VERSION,
        "human": {"source": "위키문헌 저작권 만료 근대소설 12편(1920~30년대)", "samples": len(human), **{"stats": measure(human)}},
        "ai": {"source": "본 레포 한국어 연재 원고(Opus 집필)", "samples": len(ai), **{"stats": measure(ai)}},
        "drift": drift_by_band(),
        "caveats": [
            "사람 표본이 1920~30년대 작품이라 당대 구두점 관행이 섞여 있다. 현대 한국 소설과의 차이는 별도 표본이 필요하다.",
            "표본 수가 적다(사람 12편). 추세 판단용이지 정밀 기준선은 아니다.",
            "lexical_diversity는 길이에 민감해 사람·우리 표본을 같은 길이로 잘라 비교했다.",
        ],
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.write("\n")

    print(f"{'지표':<24}{'사람':>12}{'우리':>12}{'배수':>8}")
    for key in ("comma_inclusion_rate", "comma_usage_rate", "ending_comma_rate",
                "comma_segment_length", "lexical_diversity"):
        h = payload["human"]["stats"][key]["mean"]; a = payload["ai"]["stats"][key]["mean"]
        print(f"{key:<24}{h:>12.3f}{a:>12.3f}{(a / h if h else 0):>8.1f}x")
    print(f"\n→ {os.path.relpath(OUT, ROOT)}")


if __name__ == "__main__":
    main()
