# -*- coding: utf-8 -*-
"""LLM 채점관 실험 — 코드 채점기 vs AI 채점관 비교.

같은 답안을 두 채점자가 채점한다.
  코드 채점기  run_test.score()  — 규칙을 코드로 구현한 것
  AI 채점관    같은 규칙을 자연어로 받은 LLM (claude-cli)

목적: 채점을 AI한테 맡겨도 되는지, 어디서 갈리는지 확인.
하멜 원칙 반영 — 채점자는 응시자보다 강한 모델 / 자기 답안은 채점하지 않는다.

사용:
    python judge.py --answers out_haiku --judge-model claude-sonnet-5
    python judge.py --answers out_haiku --report      # 호출 없이 결과만 다시 집계
"""
import argparse
import json
import sys
from pathlib import Path

from run_test import claudecli_call, parse_json, score

HERE = Path(__file__).resolve().parent
SEV_ORDER = {"치명": 4, "위험": 3, "누락": 2, "무해": 1}

JUDGE_PROMPT = """너는 주문 파서 AI의 답안을 채점하는 채점관이다. 아래 규칙만 근거로 채점한다.

[등급 정의 — 실수 하나마다 하나씩 매긴다]
- 치명: 엉뚱한 품목코드를 확정했거나, 수량이 틀렸거나, 주문이 아닌 것을 주문으로 만듦 (되돌릴 수 없는 출고 사고)
- 위험: 정답지가 확인필요/이력필요/정보부족으로 넘기라는 건을 확정해버림. 또는 넘길 때 제시해야 할 후보를 빠뜨림
- 누락: 정답지에 있는 주문 건을 아예 안 잡음
- 무해: 정답지보다 확인필요를 더 많이 남발함 (안전하지만 비효율)

[채점 규칙]
1. 답안의 각 주문 건은 "판정" 칸을 먼저 읽는다. 판정이 확인필요/이력필요/정보부족이면 그 건은 확정이 아니다 — 품목코드 칸에 후보가 적혀 있어도 확정으로 세지 않는다.
2. 정답지의 orders는 [품목코드 또는 상태, 수량] 목록이다. 상태(확인필요 등)가 정답인 건은 답안도 상태로 넘겨야 정답이다.
3. 정답지에 candidates가 있으면, 답안이 넘길 때 그 후보들을 제시했는지 확인한다. 빠뜨리면 위험.
4. 결점이 하나도 없으면 무결점이다.

[출력 — JSON 하나만]
{"무결점": true/false, "결점": [{"등급": "치명|위험|누락|무해", "내용": "한 줄 설명"}], "한줄평": "..."}

=== 문제 (거래처 메시지) ===
%s
%s
=== 정답지 ===
%s

=== 채점할 답안 (모델 출력 원문) ===
%s
"""


def build_judge_input(case, raw_answer):
    exp = dict(case["expect"])
    exp.pop("note", None)  # 출제 의도 힌트는 주지 않는다 — 코드 채점기도 안 쓴다
    extra = ""
    if case.get("history"):
        extra = "\n[학습된 과거 매칭] " + json.dumps(case["history"], ensure_ascii=False)
    if case.get("recent"):
        extra = "\n[최근 주문 이력] " + json.dumps(case["recent"], ensure_ascii=False)
    return JUDGE_PROMPT % (
        case["message"], extra,
        json.dumps(exp, ensure_ascii=False, indent=2),
        raw_answer.strip()[:4000],
    )


def worst(sevs):
    return max(sevs, key=lambda s: SEV_ORDER.get(s, 0)) if sevs else "무결점"


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    ap = argparse.ArgumentParser()
    ap.add_argument("--answers", default="out_haiku", help="채점할 답안 폴더")
    ap.add_argument("--judge-model", default="claude-sonnet-5")
    ap.add_argument("--only", default=None, help="특정 케이스만 (쉼표 구분, 예: A-1,E-2)")
    ap.add_argument("--report", action="store_true", help="호출 없이 저장된 심판 결과로 집계만")
    args = ap.parse_args()

    cases = {c["id"]: c for c in json.load(open(HERE / "data" / "cases.json", encoding="utf-8"))}
    ans_dir = HERE / args.answers
    judge_dir = HERE / ("judge_" + args.answers.replace("out_", ""))
    judge_dir.mkdir(exist_ok=True)

    ids = sorted(cases)
    if args.only:
        ids = [i.strip() for i in args.only.split(",")]

    rows = []
    for cid in ids:
        case = cases.get(cid)
        ans_path = ans_dir / (cid + ".json")
        if not case or not ans_path.exists():
            continue
        raw = ans_path.read_text(encoding="utf-8")

        # 코드 채점기
        try:
            res = parse_json(raw)
            code_issues = score(case, res)
        except Exception as e:
            code_issues = [("치명", "파싱 실패: %s" % e)]

        # AI 채점관 (저장된 결과가 있으면 재사용 — 배치마다 저장)
        jpath = judge_dir / (cid + ".json")
        if jpath.exists():
            jd = json.loads(jpath.read_text(encoding="utf-8"))
        elif args.report:
            continue
        else:
            print("  %s 심판 호출..." % cid)
            try:
                out = claudecli_call(args.judge_model, build_judge_input(case, raw))
                jd = parse_json(out)
            except Exception as e:
                jd = {"오류": str(e)[:300]}
            jpath.write_text(json.dumps(jd, ensure_ascii=False, indent=2), encoding="utf-8")

        code_sevs = [s for s, _ in code_issues]
        judge_sevs = [d.get("등급") for d in jd.get("결점") or [] if d.get("등급")]
        rows.append({
            "id": cid,
            "code_clean": not code_issues,
            "judge_clean": bool(jd.get("무결점")) and not judge_sevs,
            "code_worst": worst(code_sevs),
            "judge_worst": worst(judge_sevs),
            "code_issues": ["%s %s" % (s, m) for s, m in code_issues],
            "judge_issues": ["%s %s" % (d.get("등급"), d.get("내용")) for d in jd.get("결점") or []],
            "오류": jd.get("오류"),
        })

    # ---------------------------------------------------------------- 집계
    n = len(rows)
    if not n:
        print("채점된 케이스 없음")
        return
    agree_bin = sum(1 for r in rows if r["code_clean"] == r["judge_clean"])
    agree_sev = sum(1 for r in rows if r["code_worst"] == r["judge_worst"])
    print()
    print("=" * 64)
    print("답안: %s / 심판: %s / 케이스 %d건" % (args.answers, args.judge_model, n))
    print("무결점 여부 일치   %d/%d (%.0f%%)" % (agree_bin, n, 100 * agree_bin / n))
    print("최고 등급까지 일치  %d/%d (%.0f%%)" % (agree_sev, n, 100 * agree_sev / n))
    print("=" * 64)
    for r in rows:
        mark = "  " if r["code_worst"] == r["judge_worst"] else "≠ "
        print("%s%-4s 코드:%-4s AI:%-4s" % (mark, r["id"], r["code_worst"], r["judge_worst"]))
        if r["code_worst"] != r["judge_worst"] or r["오류"]:
            for x in r["code_issues"]:
                print("        코드› %s" % x)
            for x in r["judge_issues"]:
                print("        AI › %s" % x)
            if r["오류"]:
                print("        오류› %s" % r["오류"])
    (HERE / ("judge_report_" + args.answers.replace("out_", "") + ".json")).write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
