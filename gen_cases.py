# -*- coding: utf-8 -*-
"""실험 ② — 시험 문제를 AI한테 출제시키면 어디서 무너지나.

3단계:
  1. 생성   AI 출제자가 함정 섞인 문제 + 정답지를 10개씩 배치로 지어낸다
  2. 구조검사 코드가 기계적으로 거른다 (없는 품목코드, rule_decidable 모순 등)
  3. 정답검수 AI 검수관이 문제별로 정답이 자료 기준으로 옳은지 본다

사용:
    python gen_cases.py --batches 5            # 10개씩 5배치 = 50문제 생성+검사
    python gen_cases.py --report               # 저장된 결과로 집계만
"""
import argparse
import json
import re
import sys
from pathlib import Path

from run_test import claudecli_call, parse_json

HERE = Path(__file__).resolve().parent
GEN_DIR = HERE / "gen_cases"
GEN_DIR.mkdir(exist_ok=True)

MASTER = (HERE / "data" / "item_master.csv").read_text(encoding="utf-8")
MASTER_CODES = set(re.findall(r"^([A-Z][A-Z0-9-]+),", MASTER, re.M))

STATUS = ("확인필요", "이력필요", "정보부족")

GEN_PROMPT = """너는 주문 읽기 AI 시험의 출제자다. 아래 상품 목록을 보고, 거래처가 카톡으로 보낼 법한 메시지 5개를 지어라.

구성 (5개):
- 정상 주문 1개
- 주문이 아닌 것 1개 (가격·재고·배송 문의, 인사, 세금계산서 요청 등)
- 변경·취소·추가 요청 1개
- 함정 문제 2개 (폭·규격 미지정, 단위 없음, 비슷한 상품 혼동 유도, 말 바꾸기(자기 정정), 극단 축약 중에서)

각 문제에 정답을 붙여라. 출력은 JSON 배열 하나만:
[{"message": "메시지 원문", "함정": "이 문제가 노리는 것 한 줄", "rule_decidable": true, "expect": {"orders": [["품목코드", 수량]], "non_order": [], "candidates": []}}]

정답 작성 규칙:
- expect.orders: 확정 가능한 건은 [품목코드, 수량]. 자료만으로 특정할 수 없는 건은 ["확인필요", 수량]으로 넘기고 candidates에 후보 품목코드를 적는다
- 주문이 아닌 메시지는 orders를 비우고 non_order에 분류를 적는다 (가격문의/재고문의/배송문의/상품문의/취소/변경/추가/기타)
- rule_decidable: 상품 목록과 메시지만 보고 정답이 하나로 정해지면 true. 정답에 확인필요가 들어가면 반드시 false
- 품목코드는 반드시 아래 상품 목록에 실재하는 코드만 쓴다
- 메시지는 실제 사장님들 말투로: 오타, 줄임말, 존댓말 섞기

[상품 목록]
%s
"""

REVIEW_PROMPT = """너는 시험 문제 검수관이다. 아래 상품 목록을 근거로, 출제된 문제 하나의 정답이 옳은지 검수하라.

검수 항목:
1. 정답의 품목코드·수량이 메시지와 상품 목록에 비추어 맞는가
2. "확정"으로 처리한 건이 실제로 자료만으로 특정 가능한가 (비슷한 상품이 더 있는데 확정했으면 오답)
3. "확인필요"로 처리한 건이 실제로 특정 불가능한가 (자료만으로 정해지는데 확인필요면 오답)
4. rule_decidable 표시가 정답 내용과 일치하는가
5. 주문/비주문 분류가 맞는가

출력은 JSON 하나만:
{"합격": true/false, "문제점": ["틀린 점 한 줄씩. 없으면 빈 배열"]}

[상품 목록]
%s

[출제된 문제]
%s
"""


def parse_array(raw):
    """응답에서 JSON 배열 하나를 꺼낸다 (코드펜스·앞뒤 잡담 무시)."""
    s = raw.strip()
    i, j = s.find("["), s.rfind("]")
    if i < 0 or j <= i:
        raise ValueError("JSON 배열 없음")
    return json.loads(s[i:j + 1])


def structural_check(case):
    errs = []
    if not case.get("message"):
        errs.append("message 없음")
    exp = case.get("expect") or {}
    orders = exp.get("orders") or []
    has_status = False
    for o in orders:
        if not isinstance(o, list) or len(o) != 2:
            errs.append("orders 항목 형식 오류: %s" % o)
            continue
        code = o[0]
        if code in STATUS:
            has_status = True
        elif code not in MASTER_CODES:
            errs.append("없는 품목코드: %s" % code)
    for c in exp.get("candidates") or []:
        code = c.split()[0] if isinstance(c, str) else c
        if code not in MASTER_CODES:
            errs.append("없는 후보 코드: %s" % c)
    rd = case.get("rule_decidable")
    if has_status and rd is True:
        errs.append("모순: 정답에 확인필요가 있는데 rule_decidable=true")
    if not has_status and orders and rd is False:
        errs.append("모순: 전부 확정인데 rule_decidable=false")
    return errs


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    ap = argparse.ArgumentParser()
    ap.add_argument("--batches", type=int, default=10)
    ap.add_argument("--gen-model", default="claude-sonnet-5")
    ap.add_argument("--review-model", default="claude-sonnet-5")
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args()

    # 1) 생성 (배치마다 저장, 있으면 재사용)
    cases = []
    for b in range(args.batches):
        bp = GEN_DIR / ("batch_%d.json" % b)
        if bp.exists():
            batch = json.loads(bp.read_text(encoding="utf-8"))
        elif args.report:
            continue
        else:
            print("배치 %d 생성 중..." % b)
            raw = claudecli_call(args.gen_model, GEN_PROMPT % MASTER, timeout=600)
            (GEN_DIR / ("raw_%d.txt" % b)).write_text(raw, encoding="utf-8")
            try:
                batch = parse_array(raw)
            except Exception as e:
                print("  파싱 실패: %s" % e)
                batch = []
            bp.write_text(json.dumps(batch, ensure_ascii=False, indent=2), encoding="utf-8")
        for i, c in enumerate(batch):
            c["id"] = "G%d-%d" % (b, i)
        cases.extend(batch)
    print("생성된 문제: %d개" % len(cases))

    # 2) 구조 검사
    passed, failed = [], []
    for c in cases:
        errs = structural_check(c)
        if errs:
            failed.append((c, errs))
        else:
            passed.append(c)
    print("구조 검사: 통과 %d / 탈락 %d" % (len(passed), len(failed)))
    for c, errs in failed:
        print("  ✗ %s %r" % (c["id"], c.get("message", "")[:30]))
        for e in errs:
            print("      %s" % e)

    # 3) AI 검수관 (통과분만, 결과 저장·재사용)
    review_ok, review_bad = [], []
    for c in passed:
        rp = GEN_DIR / ("review_%s.json" % c["id"])
        if rp.exists():
            rv = json.loads(rp.read_text(encoding="utf-8"))
        elif args.report:
            continue
        else:
            print("  %s 검수 중..." % c["id"])
            try:
                out = claudecli_call(args.review_model,
                                     REVIEW_PROMPT % (MASTER, json.dumps(c, ensure_ascii=False)))
                rv = parse_json(out)
            except Exception as e:
                rv = {"오류": str(e)[:200]}
            rp.write_text(json.dumps(rv, ensure_ascii=False, indent=2), encoding="utf-8")
        (review_ok if rv.get("합격") else review_bad).append((c, rv))

    # 집계
    print()
    print("=" * 60)
    print("생성 %d → 구조 통과 %d → 검수 합격 %d" % (len(cases), len(passed), len(review_ok)))
    if cases:
        print("최종 합격률: %.0f%%" % (100 * len(review_ok) / len(cases)))
    print("=" * 60)
    for c, rv in review_bad:
        print("✗ %s %r" % (c["id"], c.get("message", "")[:40]))
        for p in rv.get("문제점") or ([rv.get("오류")] if rv.get("오류") else []):
            print("    %s" % p)
    out = {
        "생성": len(cases), "구조통과": len(passed), "검수합격": len(review_ok),
        "구조탈락": [{"id": c["id"], "message": c.get("message"), "오류": e} for c, e in failed],
        "검수불합격": [{"id": c["id"], "message": c.get("message"), "판정": rv} for c, rv in review_bad],
    }
    (GEN_DIR / "summary.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
