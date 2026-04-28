"""一键跑攻击用例并生成测试报告。

运行：
    python -m tests.attack_cases.run_all
输出：
    - 终端表格（pass/fail）
    - data/attack_report.md（Markdown 报告）
    - data/attack_report.json（JSON 原始数据）
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]  # 0427_test01/
sys.path.insert(0, str(ROOT))

os.environ.setdefault("AGENT_GUARD_HTTP_DRYRUN", "1")
os.environ.setdefault("AGENT_GUARD_SHELL_DRYRUN", "1")

from guard import Guard, ToolCall  # noqa: E402
from tests.attack_cases.cases import ATTACK_CASES  # noqa: E402


def deny_user_authz(call, pending) -> bool:
    """自动化测试场景下，用户授权一律拒绝（保守评估）。"""
    return False


def run_one(guard: Guard, case: dict) -> dict:
    start = time.perf_counter()
    if case["kind"] == "input":
        result = guard.check_input(case["payload"], source="test_input")
    else:
        # 准备测试用文件（仅对 B05 有用）
        if case["aid"] == "B05":
            Path("/tmp/agent_guard_test_file.txt").write_text("hello")
        call = ToolCall(tool_name=case["tool"], args=dict(case["args"]))
        result = guard.check_tool_call(call)
    elapsed_ms = (time.perf_counter() - start) * 1000

    actual = result.decision.value
    passed = actual == case["expected"]
    return {
        "aid": case["aid"],
        "category": case["category"],
        "description": case["description"],
        "expected": case["expected"],
        "actual": actual,
        "passed": passed,
        "reason": result.reason,
        "risk_score": round(result.risk_score, 3),
        "rules": result.triggered_rules[:5],
        "elapsed_ms": round(elapsed_ms, 2),
    }


def main():
    guard = Guard.from_yaml(
        str(ROOT / "config" / "policies.yaml"),
        audit_db_path=str(ROOT / "data" / "attack_audit.db"),
        ask_user_callback=deny_user_authz,
    )

    print(f"\n开始跑 {len(ATTACK_CASES)} 条攻击用例 …\n")
    print(f"{'AID':<6}{'类别':<16}{'期望':<10}{'实际':<10}{'结果':<6}{'耗时 ms':<10}描述")
    print("-" * 116)

    results = []
    by_category: dict[str, dict[str, int]] = {}
    for case in ATTACK_CASES:
        r = run_one(guard, case)
        flag = "OK" if r["passed"] else "XX"
        cat = r["category"]
        cat_disp = cat if _vwidth(cat) <= 14 else cat[:7] + "…"
        print(
            f"{r['aid']:<6}{cat_disp:<16}{r['expected']:<10}{r['actual']:<10}{flag:<6}"
            f"{r['elapsed_ms']:<10}{r['description']}"
        )
        results.append(r)
        by_category.setdefault(cat, {"total": 0, "passed": 0})
        by_category[cat]["total"] += 1
        if r["passed"]:
            by_category[cat]["passed"] += 1

    total = len(results)
    passed = sum(1 for r in results if r["passed"])
    print("-" * 116)
    print(f"\n通过 {passed}/{total} 条 ({100 * passed / total:.1f}%)")
    avg_ms = sum(r["elapsed_ms"] for r in results) / total
    p99_ms = sorted([r["elapsed_ms"] for r in results])[max(int(total * 0.99) - 1, 0)]
    print(f"平均耗时 {avg_ms:.2f} ms · P99 {p99_ms:.2f} ms")

    # 按一级类别（A/B/C/D/E）汇总
    print("\n按字母前缀汇总：")
    by_prefix: dict[str, dict[str, int]] = {}
    for r in results:
        p = r["aid"][0]
        by_prefix.setdefault(p, {"total": 0, "passed": 0})
        by_prefix[p]["total"] += 1
        if r["passed"]:
            by_prefix[p]["passed"] += 1
    for p in sorted(by_prefix):
        s = by_prefix[p]
        print(f"  {p}: {s['passed']}/{s['total']}")
    print()

    out_dir = ROOT / "data"
    out_dir.mkdir(exist_ok=True)
    (out_dir / "attack_report.json").write_text(
        json.dumps(
            {
                "summary": {
                    "total": total,
                    "passed": passed,
                    "pass_rate": passed / total,
                    "avg_ms": avg_ms,
                    "p99_ms": p99_ms,
                    "by_prefix": by_prefix,
                },
                "results": results,
            },
            ensure_ascii=False,
            indent=2,
        )
    )

    md = ["# 攻击用例测试报告（v0.2）", ""]
    md.append(f"- 共 **{total}** 条，通过 **{passed}** 条（**{100 * passed / total:.1f}%**）")
    md.append(f"- 平均耗时 {avg_ms:.2f} ms · P99 {p99_ms:.2f} ms")
    md.append("")
    md.append("## 按攻击大类汇总")
    md.append("")
    md.append("| 前缀 | 描述 | 通过 / 总数 |")
    md.append("|------|------|------------|")
    prefix_desc = {
        "A": "Prompt 注入 / 越狱",
        "B": "文件系统 / 路径穿越",
        "C": "网络外发 / SSRF",
        "D": "命令注入 / 危险 shell",
        "E": "敏感工具 / 兜底匹配",
    }
    for p in sorted(by_prefix):
        s = by_prefix[p]
        md.append(f"| {p} | {prefix_desc.get(p, '其它')} | {s['passed']} / {s['total']} |")
    md.append("")
    md.append("## 详细结果")
    md.append("")
    md.append("| AID | 类别 | 描述 | 期望 | 实际 | 结果 | 耗时 ms | 命中规则 |")
    md.append("|-----|------|------|------|------|------|--------|----------|")
    for r in results:
        flag = "✅" if r["passed"] else "❌"
        rules = "; ".join(r["rules"][:2])
        md.append(
            f"| {r['aid']} | {r['category']} | {r['description']} | "
            f"{r['expected']} | {r['actual']} | {flag} | {r['elapsed_ms']} | {rules} |"
        )
    (out_dir / "attack_report.md").write_text("\n".join(md), encoding="utf-8")

    print(f"报告已写入 {out_dir / 'attack_report.md'} 与 {out_dir / 'attack_report.json'}")
    sys.exit(0 if passed == total else 1)


def _vwidth(s: str) -> int:
    """估算字符串可视宽度（中文按 2 计）"""
    w = 0
    for ch in s:
        w += 2 if ord(ch) > 127 else 1
    return w


if __name__ == "__main__":
    main()
