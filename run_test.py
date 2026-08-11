# -*- coding: utf-8 -*-
"""
발주 접수 프롬프트 테스트 러너

사용법:
  python run_test.py --list-models                  # 쓸 수 있는 모델 확인
  python run_test.py --provider gemini --model X    # 전체 22건 실행
  python run_test.py --only B-6                     # 한 건만
  python run_test.py --provider claude --model claude-sonnet-5

채점 심각도 (아래로 갈수록 안전)
  🔴 치명  주문아닌걸 주문으로 / 변경·취소를 신규주문으로 / 품목 오매칭
  🟠 위험  애매한걸 확인필요로 못 넘김
  🟡 누락  주문을 놓침
  🟢 무해  확인필요 남발
"""
import argparse, io, json, os, shutil, subprocess, sys, time, urllib.request, urllib.error

BASE = os.path.dirname(os.path.abspath(__file__))
# 환경변수 GEMINI_API_KEY 를 먼저 보고, 없으면 이 파일을 읽는다.
LOCAL_KEY_FILE = os.path.join(BASE, ".gemini_key")


# ---------------------------------------------------------------- 입력 로드
def load_inputs():
    prompt = io.open(os.path.join(BASE, "prompts", "intake.md"), encoding="utf-8").read()
    master = io.open(os.path.join(BASE, "data", "item_master.csv"), encoding="utf-8").read()
    cases = json.load(io.open(os.path.join(BASE, "data", "cases.json"), encoding="utf-8"))
    return prompt, master, cases


def fmt_history(rows):
    """[[표현, 품목코드], ...] → 표"""
    if not rows:
        return "(없음 - 첫 거래처)\n"
    out = "| 이 거래처가 쓴 표현 | 확정된 품목코드 |\n|---|---|\n"
    for expr, code in rows:
        out += "| %s | %s |\n" % (expr, code)
    return out


def fmt_recent(rows):
    """[[일자, 품목코드, 품목명, 수량], ...] → 표"""
    if not rows:
        return "(없음)\n"
    out = "| 일자 | 품목코드 | 품목명 | 수량 |\n|---|---|---|---|\n"
    for r in rows:
        out += "| %s |\n" % " | ".join(str(x) for x in r)
    return out


def build_prompt(prompt_md, master_csv, message, history=None, recent=None):
    return (
        prompt_md
        + "\n\n---\n\n# 품목마스터\n\n```csv\n" + master_csv + "```\n"
        + "\n# 과거매칭\n\n" + fmt_history(history)
        + "\n# 최근주문\n\n" + fmt_recent(recent)
        + "\n# 메시지\n\n```\n" + message + "\n```\n"
        + "\n위 규칙대로 JSON 하나만 출력한다. 설명 문장을 붙이지 않는다.\n"
    )


# ---------------------------------------------------------------- 프로바이더
def gemini_key():
    if os.environ.get("GEMINI_API_KEY"):
        return os.environ["GEMINI_API_KEY"].strip()
    for f in (LOCAL_KEY_FILE,):
        if os.path.exists(f):
            k = io.open(f, encoding="utf-8").read().strip()
            if k:
                return k
    raise SystemExit("Gemini 키가 없습니다. %s 에 넣으세요." % LOCAL_KEY_FILE)


def gemini_list():
    url = "https://generativelanguage.googleapis.com/v1beta/models"
    req = urllib.request.Request(url, headers={"x-goog-api-key": gemini_key()})
    d = json.load(urllib.request.urlopen(req, timeout=60))
    out = []
    for m in d.get("models", []):
        if "generateContent" in m.get("supportedGenerationMethods", []):
            out.append(m["name"].replace("models/", ""))
    return out


def gemini_call(model, text):
    url = "https://generativelanguage.googleapis.com/v1beta/models/%s:generateContent" % model
    body = json.dumps({
        "contents": [{"parts": [{"text": text}]}],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0},
    }).encode("utf-8")
    req = urllib.request.Request(
        url, data=body,
        headers={"x-goog-api-key": gemini_key(), "content-type": "application/json"},
    )
    d = json.load(urllib.request.urlopen(req, timeout=180))
    return d["candidates"][0]["content"]["parts"][0]["text"]


def claudecli_call(model, text, timeout=300):
    """Claude Code 헤드리스 모드로 호출한다. API 키가 아니라 구독으로 돈다.

    --safe-mode      CLAUDE.md·스킬·플러그인·훅·MCP를 끈다. 인증은 정상 동작
    --tools ""       도구 정의를 뺀다
    두 개를 빼면 시스템 프롬프트가 33,706 토큰 붙어서 테스트가 오염되고 비싸진다.
    """
    # Windows에서는 확장자 없는 sh 스크립트가 먼저 잡혀 WinError 193이 난다. .cmd 를 우선한다.
    names = ["claude.cmd", "claude.exe", "claude.bat", "claude"] if os.name == "nt" else ["claude"]
    exe = next((p for p in (shutil.which(n) for n in names) if p), None)
    if not exe:
        raise SystemExit("claude CLI 를 찾을 수 없습니다.")
    cmd = [exe, "-p",
           "--safe-mode", "--tools", "", "--strict-mcp-config",
           "--no-session-persistence", "--disable-slash-commands",
           "--system-prompt", "너는 발주 메시지를 읽고 JSON 하나만 출력하는 파서다.",
           "--output-format", "json", "--model", model]
    p = subprocess.run(cmd, input=text, capture_output=True,
                       encoding="utf-8", errors="replace", timeout=timeout)
    if p.returncode != 0:
        raise RuntimeError("claude CLI 실패 (rc=%s): %s" % (p.returncode, (p.stderr or "")[:400]))
    d = json.loads(p.stdout)
    if d.get("is_error"):
        raise RuntimeError("claude CLI 오류: %s" % str(d.get("result"))[:400])
    return d["result"]


def claude_call(model, text):
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        raise SystemExit("ANTHROPIC_API_KEY 가 없습니다. set ANTHROPIC_API_KEY=... 후 다시 실행하세요.")
    body = json.dumps({
        "model": model,
        "max_tokens": 4096,
        "messages": [{"role": "user", "content": text}],
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages", data=body,
        headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
    )
    d = json.load(urllib.request.urlopen(req, timeout=180))
    return "".join(b.get("text", "") for b in d.get("content", []) if b.get("type") == "text")


def call(provider, model, text, tries=4):
    """503(혼잡)만 재시도한다.

    ⚠️ 429는 재시도하지 않는다. 무료 등급 쿼터는 '하루 20건'이라
    재시도 한 번이 남은 할당량을 1건씩 까먹는다. 분당 제한이면 기다리면 되지만
    일일 제한이면 재시도가 순수한 낭비다. 구분이 안 되므로 아예 올린다.
    """
    if provider == "claude-cli":
        return claudecli_call(model, text)
    for i in range(tries):
        try:
            return gemini_call(model, text) if provider == "gemini" else claude_call(model, text)
        except urllib.error.HTTPError as e:
            if e.code != 503 or i == tries - 1:
                raise
            wait = 8 * (i + 1)
            print("      · HTTP 503 혼잡 — %d초 뒤 재시도 (%d/%d)" % (wait, i + 2, tries))
            time.sleep(wait)


# ---------------------------------------------------------------- 파싱·채점
STATUS = ("확인필요", "이력필요", "정보부족")


def parse_json(raw):
    s = raw.strip()
    if s.startswith("```"):
        s = s.split("```")[1]
        if s.lstrip().lower().startswith("json"):
            s = s.lstrip()[4:]
    i = s.find("{")
    if i < 0:
        raise ValueError("JSON 없음")
    j = s.rfind("}")
    if j > i:
        try:
            return json.loads(s[i:j + 1])
        except Exception:
            pass
    # 잘린 응답 복구: 열린 괄호만큼 닫아준다 (문자열 안은 세지 않음)
    body, depth, instr, esc = s[i:], 0, False, False
    for ch in body:
        if esc:
            esc = False
        elif ch == "\\":
            esc = True
        elif ch == '"':
            instr = not instr
        elif not instr:
            if ch in "{[":
                depth += 1
            elif ch in "}]":
                depth -= 1
    repaired = body.rstrip().rstrip(",")
    if instr:
        repaired += '"'
    for _ in range(max(depth, 0)):
        repaired += "}"
    return json.loads(repaired)


def actual_orders(res):
    """주문 배열 + 맞춤제작을 (코드|상태, 수량) 목록으로 정규화

    ⚠️ 판정이 우선이다. 확인필요이면서 품목코드 칸에 유력 후보를 같이 채우는
    모델이 있는데(Sonnet 5), 품목코드를 먼저 보면 확정한 것으로 잘못 읽는다.
    """
    out = []
    for o in res.get("주문") or []:
        j = o.get("판정")
        code = j if j in STATUS else (o.get("품목코드") or j)
        out.append((code, o.get("수량")))
    for m in res.get("맞춤제작") or []:
        out.append(("정보부족", None))
    return out


def score(case, res):
    exp = case["expect"]
    exp_orders = [tuple(x) for x in exp["orders"]]
    act = actual_orders(res)
    issues = []

    exp_codes = [c for c, _ in exp_orders]
    act_codes = [c for c, _ in act]
    # 답안이 확인필요로 넘기며 제시한 후보 전체 (물어본 건 판별에 쓴다)
    cand_blob = " ".join(str(x) for o in (res.get("주문") or []) for x in (o.get("후보") or []))

    # 🔴 치명 1 — 주문 아닌 걸 주문으로
    if not exp_orders and act:
        issues.append(("치명", "주문이 아닌데 주문 %d건을 만듦 (%s)" % (len(act), act_codes)))

    # 🔴 치명 2 — 품목 오매칭 / 수량 오류
    # ⚠️ 확정을 기대한 건을 답안이 "확인필요 + 후보"로 넘겼다면 누락이 아니다.
    #    누락의 정의는 "놓쳤다"인데, 잡아서 물어본 것이므로 무해(과한 확인)다.
    #    (AI 채점관 대조 실험에서 잡은 채점기 실수 3호)
    asked_instead = 0
    for ec, eq in exp_orders:
        if ec in STATUS:
            continue
        hit = [(c, q) for c, q in act if c == ec]
        if not hit:
            if ec in cand_blob:
                issues.append(("무해", "%s 확정 대신 확인필요로 물어봄 (후보에는 있음)" % ec))
                asked_instead += 1
            elif any(c not in STATUS and c not in exp_codes for c, _ in act):
                issues.append(("치명", "%s 를 못 잡고 엉뚱한 코드를 냄" % ec))
            else:
                issues.append(("누락", "%s 누락" % ec))
        elif eq is not None and hit[0][1] != eq:
            issues.append(("치명", "%s 수량 오류: 기대 %s / 실제 %s" % (ec, eq, hit[0][1])))

    # 🟠 위험 — 확인필요로 넘겨야 하는데 확정해버림
    n_status_exp = sum(1 for c in exp_codes if c in STATUS)
    n_status_act = sum(1 for c in act_codes if c in STATUS)
    if n_status_exp > n_status_act:
        issues.append(("위험", "확인필요로 넘겼어야 할 %d건을 확정함" % (n_status_exp - n_status_act)))

    # 🟢 무해 — 확인필요 남발 (물어봄으로 이미 센 건은 중복으로 세지 않는다)
    if n_status_act - n_status_exp - asked_instead > 0:
        issues.append(("무해", "확인필요 %d건 남발" % (n_status_act - n_status_exp - asked_instead)))

    # 🟢 무해 — 주문·맞춤제작 밖 영역의 확인 요청 (채점기 사각지대 보완)
    #    답안이 별도 배열(예: "추가")에 판정=확인필요 항목을 남기는 경우가 있다.
    #    사고는 아니지만 정답지에 없는 확인 절차이므로 무해로 센다.
    extra_asked = sum(
        1
        for k, v in res.items()
        if k not in ("주문", "맞춤제작", "분류", "비주문") and isinstance(v, list)
        for o in v
        if isinstance(o, dict) and o.get("판정") in STATUS
    )
    if extra_asked:
        issues.append(("무해", "주문 외 영역에 확인 요청 %d건" % extra_asked))

    # 🟡 누락 — 건수 부족
    if len(act) < len(exp_orders) and not any(t == "누락" for t, _ in issues):
        issues.append(("누락", "주문 %d건 중 %d건만 잡음" % (len(exp_orders), len(act))))

    # 후보 검증 — 확인필요·이력필요로 넘길 때 올바른 후보를 제시했는가
    want = exp.get("candidates")
    if want:
        blob = " ".join(str(x) for o in (res.get("주문") or []) for x in (o.get("후보") or []))
        for code in want:
            if code not in blob:
                issues.append(("위험", "후보에 %s 를 제시하지 않음" % code))

    return issues


MARK = {"치명": "🔴", "위험": "🟠", "누락": "🟡", "무해": "🟢"}


# ---------------------------------------------------------------- 메인
def main():
    p = argparse.ArgumentParser()
    p.add_argument("--provider", default="gemini", choices=["gemini", "claude", "claude-cli"])
    p.add_argument("--model", default=None)
    p.add_argument("--only", default=None, help="케이스 ID 지정 실행, 쉼표 구분 (예: B-6 또는 D-1,D-2)")
    p.add_argument("--list-models", action="store_true")
    p.add_argument("--sleep", type=float, default=0.5)
    p.add_argument("--out", default="out", help="결과 폴더. 모델별 비교 시 분리한다")
    p.add_argument("--rescore", action="store_true",
                   help="모델을 다시 부르지 않고 --out 에 저장된 응답만 재채점한다")
    a = p.parse_args()

    if a.list_models:
        for m in gemini_list():
            print(m)
        return

    if not a.model:
        raise SystemExit("--model 을 지정하세요. 먼저 --list-models 로 확인.")

    prompt_md, master_csv, cases = load_inputs()
    if a.only:
        want = [x.strip() for x in a.only.split(",") if x.strip()]
        cases = [c for c in cases if c["id"] in want]
        if not cases:
            raise SystemExit("케이스 %s 없음" % a.only)

    OUT = os.path.join(BASE, a.out)
    os.makedirs(OUT, exist_ok=True)
    tally = {"치명": 0, "위험": 0, "누락": 0, "무해": 0}
    clean = 0
    rows = []

    for c in cases:
        saved = os.path.join(OUT, "%s.json" % c["id"])
        if a.rescore:
            if not os.path.exists(saved):
                print("-  [%s] 저장된 응답 없음" % c["id"])
                continue
            raw = io.open(saved, encoding="utf-8").read()
        else:
            text = build_prompt(prompt_md, master_csv, c["message"],
                                c.get("history"), c.get("recent"))
            try:
                raw = call(a.provider, a.model, text)
            except urllib.error.HTTPError as e:
                print("[%s] HTTP %s — %s" % (c["id"], e.code, e.read().decode("utf-8", "replace")[:300]))
                rows.append((c["id"], c["group"], "HTTP %s" % e.code))
                continue
            except Exception as e:
                print("[%s] 호출 실패 — %s" % (c["id"], str(e)[:300]))
                rows.append((c["id"], c["group"], "호출 실패"))
                continue
            io.open(saved, "w", encoding="utf-8").write(raw)

        try:
            res = parse_json(raw)
        except Exception as e:
            print("🔴 [%s] JSON 파싱 실패: %s" % (c["id"], e))
            tally["치명"] += 1
            rows.append((c["id"], c["group"], "JSON 파싱 실패"))
            continue

        issues = score(c, res)
        if not issues:
            clean += 1
            print("✅ [%s] %s" % (c["id"], c["group"]))
        else:
            worst = min(issues, key=lambda x: ["치명", "위험", "누락", "무해"].index(x[0]))
            print("%s [%s] %s" % (MARK[worst[0]], c["id"], c["group"]))
            for t, m in issues:
                tally[t] += 1
                print("      %s %s" % (MARK[t], m))
                rows.append((c["id"], c["group"], "%s %s" % (MARK[t], m)))
        time.sleep(a.sleep)

    n = len(cases)
    print("\n" + "=" * 56)
    print("모델: %s / %s      케이스 %d건" % (a.provider, a.model, n))
    print("무결점 %d건 (%.0f%%)" % (clean, 100.0 * clean / n if n else 0))
    print("🔴 치명 %d   🟠 위험 %d   🟡 누락 %d   🟢 무해 %d"
          % (tally["치명"], tally["위험"], tally["누락"], tally["무해"]))
    print("=" * 56)
    print("원본 응답: %s/*.json" % a.out)

    with io.open(os.path.join(OUT, "_summary.tsv"), "w", encoding="utf-8") as f:
        f.write("id\tgroup\tissue\n")
        for r in rows:
            f.write("\t".join(r) + "\n")


if __name__ == "__main__":
    main()
