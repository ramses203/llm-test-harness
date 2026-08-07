# -*- coding: utf-8 -*-
"""
발주 접수 테스트 UI - 로컬 서버

  python serve.py            → http://localhost:8777 열기
  python serve.py --port 9000
  python serve.py --provider claude-cli --model claude-haiku-4-5

API 키는 로컬 파일에서만 읽고 브라우저로 절대 내보내지 않는다.
학습(과거매칭)은 data/learned.json 에 쌓인다.
"""
import argparse, io, json, os, sys, threading, urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
import run_test as rt

LEARNED_FILE = os.path.join(BASE, "data", "learned.json")
LOCK = threading.Lock()
CFG = {"provider": "claude-cli", "model": "claude-haiku-4-5"}


# ---------------------------------------------------------------- 학습 저장소
def load_learned():
    if not os.path.exists(LEARNED_FILE):
        return []
    try:
        return json.load(io.open(LEARNED_FILE, encoding="utf-8"))
    except Exception:
        return []


def save_learned(rows):
    io.open(LEARNED_FILE, "w", encoding="utf-8").write(
        json.dumps(rows, ensure_ascii=False, indent=1))


def learn(expr, code):
    expr = (expr or "").strip()
    code = (code or "").strip()
    if not expr or not code:
        raise ValueError("표현과 품목코드가 모두 필요합니다")
    with LOCK:
        rows = [r for r in load_learned() if r.get("표현") != expr]
        rows.append({"표현": expr, "품목코드": code})
        save_learned(rows)
        return rows


def unlearn(expr):
    with LOCK:
        rows = [r for r in load_learned() if r.get("표현") != expr]
        save_learned(rows)
        return rows


def history_pairs():
    return [[r["표현"], r["품목코드"]] for r in load_learned()
            if r.get("표현") and r.get("품목코드")]


# ---------------------------------------------------------------- 품목마스터
def load_master_rows():
    rows = []
    with io.open(os.path.join(BASE, "data", "item_master.csv"), encoding="utf-8") as f:
        head = f.readline().rstrip("\n").split(",")
        for line in f:
            line = line.rstrip("\n")
            if not line.strip():
                continue
            rows.append(dict(zip(head, line.split(","))))
    return rows


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        b = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _json(self, code, obj):
        return self._send(code, json.dumps(obj, ensure_ascii=False))

    def _body(self):
        n = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(n).decode("utf-8"))

    # ------------------------------------------------------------ GET
    def do_GET(self):
        p = self.path.split("?")[0]
        if p in ("/", "/index.html"):
            html = io.open(os.path.join(BASE, "web", "index.html"), encoding="utf-8").read()
            return self._send(200, html, "text/html; charset=utf-8")
        if p == "/api/config":
            return self._json(200, CFG)
        if p == "/api/master":
            return self._json(200, load_master_rows())
        if p == "/api/learned":
            return self._json(200, load_learned())
        if p == "/api/samples":
            cases = json.load(io.open(os.path.join(BASE, "data", "cases.json"), encoding="utf-8"))
            return self._json(200, [{"id": c["id"], "group": c["group"], "message": c["message"]}
                                    for c in cases])
        return self._json(404, {"error": "not found"})

    # ------------------------------------------------------------ POST
    def do_POST(self):
        p = self.path.split("?")[0]
        try:
            req = self._body()
        except Exception:
            return self._json(400, {"error": "본문 파싱 실패"})

        if p == "/api/learn":
            try:
                return self._json(200, {"learned": learn(req.get("표현"), req.get("품목코드"))})
            except ValueError as e:
                return self._json(400, {"error": str(e)})
        if p == "/api/unlearn":
            return self._json(200, {"learned": unlearn((req.get("표현") or "").strip())})
        if p == "/api/forget-all":
            with LOCK:
                save_learned([])
            return self._json(200, {"learned": []})
        if p != "/api/parse":
            return self._json(404, {"error": "not found"})

        msg = (req.get("message") or "").strip()
        if not msg:
            return self._json(400, {"error": "메시지가 비어 있습니다"})

        prompt_md, master_csv, _ = rt.load_inputs()
        hist = history_pairs()
        text = rt.build_prompt(prompt_md, master_csv, msg, hist, None)
        provider = req.get("provider") or CFG["provider"]
        model = req.get("model") or CFG["model"]

        try:
            raw = rt.call(provider, model, text)
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            hint = ""
            if e.code == 429:
                hint = "무료 등급은 모델당 하루 20건입니다. claude-cli 로 바꾸면 이 제한이 없습니다."
            elif e.code == 503:
                hint = "모델이 혼잡합니다. 잠시 뒤 다시 시도하세요."
            try:
                m = json.loads(body)["error"]["message"]
            except Exception:
                m = body[:300]
            return self._json(200, {"error": "HTTP %s" % e.code, "detail": m, "hint": hint})
        except Exception as e:
            return self._json(200, {"error": str(e)[:400]})

        try:
            res = rt.parse_json(raw)
        except Exception as e:
            return self._json(200, {"error": "JSON 파싱 실패: %s" % e, "raw": raw[:2000]})
        return self._json(200, {"result": res, "source": "%s / %s" % (provider, model),
                                "history_used": len(hist)})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8777)
    ap.add_argument("--provider", default="claude-cli", choices=["gemini", "claude", "claude-cli"])
    ap.add_argument("--model", default="claude-haiku-4-5")
    a = ap.parse_args()
    CFG["provider"], CFG["model"] = a.provider, a.model
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), H)
    print("발주 접수 테스트 UI")
    print("  http://localhost:%d" % a.port)
    print("  %s / %s" % (a.provider, a.model))
    print("  학습: %s (%d건)" % (LEARNED_FILE, len(load_learned())))
    print("  종료: Ctrl+C")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n종료")


if __name__ == "__main__":
    main()
