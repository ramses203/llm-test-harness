# -*- coding: utf-8 -*-
"""
모델별 결과 비교

  python run_test.py --provider claude-cli --model claude-haiku-4-5 --out out_haiku
  python run_test.py --provider claude-cli --model claude-sonnet-5  --out out_sonnet
  python compare.py out_haiku out_sonnet

--out 폴더의 _summary.tsv 를 읽어 케이스 × 모델 표를 만든다.
_summary.tsv 에는 문제가 있는 케이스만 들어 있으므로 없으면 통과로 본다.
"""
import io, json, os, sys

BASE = os.path.dirname(os.path.abspath(__file__))
ORDER = {"🔴": 0, "🟠": 1, "🟡": 2, "🟢": 3}


def load(d):
    """out 폴더 → {case_id: [문제 문자열, ...]}"""
    f = os.path.join(BASE, d, "_summary.tsv")
    issues = {}
    if not os.path.exists(f):
        return issues
    with io.open(f, encoding="utf-8") as fh:
        fh.readline()
        for line in fh:
            parts = line.rstrip("\n").split("\t")
            if len(parts) >= 3:
                issues.setdefault(parts[0], []).append(parts[2])
    return issues


def worst(msgs):
    if not msgs:
        return "✅", ""
    mark = min((m[0] for m in msgs if m and m[0] in ORDER), key=lambda x: ORDER[x], default="🟢")
    return mark, " / ".join(m[2:] if m[:1] in ORDER else m for m in msgs)


def main():
    dirs = sys.argv[1:]
    if not dirs:
        dirs = sorted(d for d in os.listdir(BASE)
                      if d.startswith("out") and os.path.isdir(os.path.join(BASE, d)))
    cases = json.load(io.open(os.path.join(BASE, "data", "cases.json"), encoding="utf-8"))
    data = {d: load(d) for d in dirs}

    w = max(len(d) for d in dirs) + 2
    print("케이스   그룹      " + "".join(d.ljust(w) for d in dirs))
    print("-" * (18 + w * len(dirs)))

    tally = {d: {"🔴": 0, "🟠": 0, "🟡": 0, "🟢": 0, "✅": 0, "-": 0} for d in dirs}
    detail = []
    for c in cases:
        cid, grp = c["id"], c["group"]
        row = ""
        for d in dirs:
            ran = os.path.exists(os.path.join(BASE, d, "%s.json" % cid))
            if not ran:
                mark, why = "-", ""
            else:
                mark, why = worst(data[d].get(cid, []))
            tally[d][mark] += 1
            row += mark.ljust(w - 1)
            if why:
                detail.append("  %-5s %-12s %s %s" % (cid, d, mark, why))
        print("%-8s %-9s %s" % (cid, grp, row))

    print("-" * (18 + w * len(dirs)))
    for k, label in [("✅", "무결점"), ("🔴", "치명"), ("🟠", "위험"),
                     ("🟡", "누락"), ("🟢", "무해"), ("-", "미실행")]:
        print("%-17s %s" % (label, "".join(str(tally[d][k]).ljust(w) for d in dirs)))

    if detail:
        print("\n문제 상세")
        for line in detail:
            print(line)


if __name__ == "__main__":
    main()
