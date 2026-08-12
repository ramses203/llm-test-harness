# -*- coding: utf-8 -*-
"""이식 실험 — 주문 읽기 시험지의 5단계를 유튜브 댓글 분류에 옮긴 시험지.

이 저장소의 시험지(cases.json)는 주문 읽기에서 태어났다. 같은 5단계(최악 사고 →
등급표 → 함정 → 정답지 → 채점)가 다른 일에도 옮겨지는지 확인하려고 만든 두 번째
시험지가 이 파일이다. 도메인은 "유튜브 댓글에서 니즈·지불신호 골라내기".

검증 대상이었던 정규식 분류기는 별도 비공개 프로젝트라 여기 없다. 대신 그 채점
결과를 comment_exam_result_regex.json 으로 동봉했다. LLM 분류기 채점은 직접
실행할 수 있다 (claude CLI 필요 — run_test.py와 같은 방식).

[1단계 — 최악 사고]  이 분류 결과는 뭘 만들지/팔지 정하는 데 쓰인다
  사고 1  잡담·논평·후기를 니즈로 승격 → 가짜 수요로 결정을 내림
  사고 2  지불 단어만 있는 비지불(후기·비꼼)을 지불신호로 → 팔 물건 오판
  사고 3  진짜 니즈를 패턴 불일치로 버림 → 버린 쪽은 아무도 재검토 안 함
  사고 4  키워드 우연 일치로 카테고리 오배정 → 우선순위 왜곡

[2단계 — 등급표]  기준: 사람이 되돌릴 수 있는가
  치명   아닌 것을 니즈·지불신호로 확정 (결정 오염)
  위험   애매한 것을 확정 / 카테고리 명백 오배정
  누락   진짜 니즈를 버림 — 이 도메인엔 재검토 절차가 없어 사실상 치명급
  무해   확인필요로 넘김 (안전하지만 사람이 조금 귀찮아짐)

사용:
    python comment_exam.py --engine llm     # LLM 분류기 채점 (claude CLI 필요)
    python comment_exam.py --compare        # 동봉된 정규식 결과와 나란히 비교
"""
import argparse
import json
import sys
from pathlib import Path

from run_test import claudecli_call, parse_json

HERE = Path(__file__).resolve().parent
LLM_DIR = HERE / "llm_exam"

# 실험 당시 정규식 분류기가 쓰던 카테고리 이름 그대로 (프롬프트 재현용)
CATEGORIES = [
    "설치·환경설정", "에러·디버깅", "초보자·기초설명", "AI도구·활용법",
    "실전·프로젝트", "언어·프레임워크", "취업·커리어", "추천·비교",
    "후속·심화요청", "가격·결제",
]

LLM_PROMPT = """너는 유튜브 댓글을 분류하는 분석기다. 댓글 하나를 보고 JSON 하나만 출력한다.

[정의]
- 니즈: 댓글 작성자가 궁금증·요청·막힘을 표현하며 답이나 해결을 원하는 것.
  경험담·후기·인증·감상·사회 논평은 니즈가 아니다 (댓글 안에 이미 답이나 결론이 있다).
  "왜 ~해야 하죠" 같은 항의성 수사 반문도 니즈가 아니다 (답을 원하는 게 아니라 의견 표명).
- 지불: 작성자가 실제로 돈을 냈거나(결제·구독 중) 내겠다는 의사를 표현한 것.
  가격·요금 단어를 언급만 한 것은 지불이 아니다.
- 확인필요: 댓글 원문만으로는 니즈인지 아닌지 판정할 수 없을 때 (답글이라 원문 맥락이 없거나,
  불편만 있고 요청이 없어 애매하거나). 억지로 확정하지 말고 확인필요로 넘겨라.

[카테고리 목록] %s

[출력 — JSON 하나만]
{"니즈": true/false/"확인필요", "지불": true/false, "카테고리": ["목록에 있는 이름만, 니즈일 때만"]}

[댓글]
%s
"""

# [3~4단계 — 함정 심은 시험지 15문제 + 정답지]
# 댓글 텍스트는 실제 수집 데이터에서 패턴만 가져와 새로 쓴 것이다 (작성자 정보 없음).
# expect.need: true=니즈다 / false=니즈 아님 / "확인필요"=자료만으로 판정 불가(사람 몫)
# rule_decidable: 댓글 원문만 보고 답이 하나로 정해지는가
CASES = [
    # --- 정상 (통과 확인용) ---
    {"id": "C-1", "trap": "정상 니즈", "text": "커서로 리팩토링하다 자꾸 막히는데 큰 프로젝트에서 컨텍스트 유지하는 방법 없나요?",
     "expect": {"need": True, "pays": False, "cats_include": ["AI도구·활용법"], "cats_exclude": []}, "rule_decidable": True},
    {"id": "C-2", "trap": "정상 니즈+지불", "text": "클로드 코드 월 결제해서 쓰는데 토큰이 반나절이면 동나요. 아껴 쓰는 방법 좀 알려주세요",
     "expect": {"need": True, "pays": True, "cats_include": ["AI도구·활용법", "가격·결제"], "cats_exclude": []}, "rule_decidable": True},
    {"id": "C-3", "trap": "정상 니즈+지불 2", "text": "플러스 요금제 쓰는데 사용량 한도가 너무 짧아요. 한도 늘리는 옵션 없나요?",
     "expect": {"need": True, "pays": True, "cats_include": ["가격·결제"], "cats_exclude": []}, "rule_decidable": True},
    # --- 함정: 지불 단어 있는 비니즈 (사고 1·2) ---
    {"id": "C-4", "trap": "감상+지불단어", "text": "이래서 다들 유료 쓰는구나 ㅋㅋ 나는 무료로 버틴다",
     "expect": {"need": False, "pays": False, "cats_include": [], "cats_exclude": []}, "rule_decidable": True},
    {"id": "C-5", "trap": "만족 후기", "text": "50달러 결제했는데 진짜 인생템입니다 최고예요",
     "expect": {"need": False, "pays": False, "cats_include": [], "cats_exclude": []}, "rule_decidable": True},
    {"id": "C-6", "trap": "가치 후기", "text": "비싸긴 한데 그만한 값을 해요 돈값 합니다",
     "expect": {"need": False, "pays": False, "cats_include": [], "cats_exclude": []}, "rule_decidable": True},
    {"id": "C-7", "trap": "수사 반문(항의)", "text": "왜 이런 걸 돈 주고 사야 하죠? 오픈소스로 다 되는데",
     "expect": {"need": False, "pays": False, "cats_include": [], "cats_exclude": []}, "rule_decidable": True},
    # --- 함정: 키워드 우연 일치 (사고 4) ---
    {"id": "C-8", "trap": "사회 논평 속 '안 되'", "text": "문과는 돈이 안 되기 때문에 대체하려고 하지 않을 거라는 예측이 맞았네요",
     "expect": {"need": False, "pays": False, "cats_include": [], "cats_exclude": ["에러·디버깅"]}, "rule_decidable": True},
    {"id": "C-9", "trap": "잡담 속 '안되겠다'", "text": "안되겠다 오늘은 그만하고 자야지 ㅋㅋㅋ",
     "expect": {"need": False, "pays": False, "cats_include": [], "cats_exclude": ["에러·디버깅"]}, "rule_decidable": True},
    {"id": "C-10", "trap": "경험담 속 '오류'", "text": "저도 그 오류 때문에 이틀 날렸어요",
     "expect": {"need": False, "pays": False, "cats_include": [], "cats_exclude": []}, "rule_decidable": True},
    # --- 함정: 진짜 니즈인데 패턴 밖 (사고 3) ---
    {"id": "C-11", "trap": "패턴 밖 니즈", "text": "결과를 매번 눈으로 검증하는 게 제일 큰 일이네요. 이 부분 자동화하는 팁 공유해주실 분",
     "expect": {"need": True, "pays": False, "cats_include": [], "cats_exclude": []}, "rule_decidable": True},
    {"id": "C-12", "trap": "말 바꾸기", "text": "살까 고민했는데 그냥 무료로 버티기로 했어요. 근데 무료 한도 확인은 어디서 하나요?",
     "expect": {"need": True, "pays": False, "cats_include": [], "cats_exclude": []}, "rule_decidable": True},
    # --- 판정 불가 (사람 몫 — 정답이 "확인필요"인 문제) ---
    {"id": "C-13", "trap": "답글(원문 없음)", "text": "저도요 ㅠㅠ 매번 그래요",
     "expect": {"need": "확인필요", "pays": False, "cats_include": [], "cats_exclude": []}, "rule_decidable": False},
    {"id": "C-14", "trap": "신조어 불편", "text": "ㄹㅇ 이거 때문에 개고생함",
     "expect": {"need": "확인필요", "pays": False, "cats_include": [], "cats_exclude": []}, "rule_decidable": False},
    {"id": "C-15", "trap": "불편 표명(요청 없음)", "text": "verification이 제일 골치아픈데 다들 그냥 스킵하더라",
     "expect": {"need": "확인필요", "pays": False, "cats_include": [], "cats_exclude": []}, "rule_decidable": False},
]


def run_llm(case, model="claude-sonnet-5"):
    LLM_DIR.mkdir(exist_ok=True)
    cache = LLM_DIR / ("%s.json" % case["id"])
    if cache.exists():
        d = json.loads(cache.read_text(encoding="utf-8"))
    else:
        prompt = LLM_PROMPT % (", ".join(CATEGORIES), case["text"])
        d = parse_json(claudecli_call(model, prompt))
        cache.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    need = d.get("니즈")
    pays = bool(d.get("지불"))
    cats = d.get("카테고리") or []
    return need, pays, cats


def grade(case, got_need, got_pays, got_cats):
    """[5단계 — 등급 채점] 결점 목록을 돌려준다.

    got_need는 True / False / "확인필요" 셋 중 하나다.
    (실험 당시 정규식 분류기는 앞의 둘만 낼 수 있었다 — 그 자체가 결함이었다.)
    """
    exp = case["expect"]
    issues = []
    if exp["need"] is False:
        if got_need is True:
            issues.append(("치명", "니즈 아닌 것을 니즈로 승격"))
        elif got_need == "확인필요":
            issues.append(("무해", "비니즈를 확인필요로 남발"))
    elif exp["need"] is True:
        if got_need is False:
            issues.append(("누락", "진짜 니즈를 버림 (재검토 절차 없음 — 사실상 치명급)"))
        elif got_need == "확인필요":
            issues.append(("무해", "니즈를 확인필요로 넘김 (안전하지만 비효율)"))
    else:  # 기대 = 확인필요
        if got_need in (True, False):
            issues.append(("위험", "판정 불가 건을 %s(으)로 확정"
                           % ("니즈" if got_need is True else "비니즈")))
    if exp["pays"] is False and got_pays and got_need is True:
        issues.append(("치명", "지불신호 오탐 (니즈 동반 → 팔 물건 목록에 오름)"))
    if exp["pays"] is True and not got_pays:
        issues.append(("누락", "진짜 지불신호를 놓침"))
    for cat in exp.get("cats_exclude", []):
        if cat in got_cats:
            issues.append(("위험", "카테고리 오배정: %s" % cat))
    for cat in exp.get("cats_include", []):
        if got_need is True and cat not in got_cats:
            issues.append(("누락", "기대 카테고리 빠짐: %s" % cat))
    return issues


MARK = {"치명": "🔴", "위험": "🟠", "누락": "🟡", "무해": "🟢"}
SEV_ORDER = ["무해", "누락", "위험", "치명"]


def run_engine():
    total = {"치명": 0, "위험": 0, "누락": 0, "무해": 0}
    clean = 0
    rows = []
    for c in CASES:
        print("  %s LLM 호출..." % c["id"])
        got_need, got_pays, got_cats = run_llm(c)
        issues = grade(c, got_need, got_pays, got_cats)
        rows.append({"id": c["id"], "trap": c["trap"], "text": c["text"],
                     "판정": {"니즈": got_need, "지불": got_pays, "카테고리": got_cats},
                     "결점": issues})
        if not issues:
            clean += 1
        for sev, _ in issues:
            total[sev] += 1
    return rows, clean, total


def print_result(engine, rows, clean, total):
    for r in rows:
        issues = r["결점"]
        worst = max((s for s, _ in issues), key=SEV_ORDER.index) if issues else None
        print("%s [%s] %s" % (MARK.get(worst, "✅"), r["id"], r["trap"]))
        for sev, msg in issues:
            print("      %s %s" % (MARK[sev], msg))
    print()
    print("=" * 60)
    print("[%s] 케이스 %d건 · 무결점 %d건 (%d%%)" % (engine, len(rows), clean, 100 * clean // len(rows)))
    print("🔴 치명 %(치명)d   🟠 위험 %(위험)d   🟡 누락 %(누락)d   🟢 무해 %(무해)d" % total)
    print("=" * 60)


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", default="llm", choices=["regex", "llm"])
    ap.add_argument("--compare", action="store_true")
    args = ap.parse_args()

    if args.compare:
        for eng in ("regex", "llm"):
            p = HERE / ("comment_exam_result_%s.json" % eng)
            if not p.exists():
                print("%s 결과 없음 — regex는 동봉 파일, llm은 --engine llm 으로 생성" % eng)
                return
        rx = json.loads((HERE / "comment_exam_result_regex.json").read_text(encoding="utf-8"))
        lm = json.loads((HERE / "comment_exam_result_llm.json").read_text(encoding="utf-8"))
        print("%-6s %-22s %-14s %s" % ("케이스", "함정", "정규식", "LLM"))
        for a, b in zip(rx, lm):
            wa = max((s for s, _ in a["결점"]), key=SEV_ORDER.index) if a["결점"] else "무결점"
            wb = max((s for s, _ in b["결점"]), key=SEV_ORDER.index) if b["결점"] else "무결점"
            print("%-6s %-22s %-14s %s" % (a["id"], a["trap"][:20], wa, wb))
        return

    if args.engine == "regex":
        print("정규식 분류기는 별도 비공개 프로젝트라 이 저장소에서는 실행할 수 없다.")
        print("당시 채점 결과는 comment_exam_result_regex.json 으로 동봉돼 있다 (--compare 로 비교).")
        return

    rows, clean, total = run_engine()
    print_result("llm", rows, clean, total)
    (HERE / "comment_exam_result_llm.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
