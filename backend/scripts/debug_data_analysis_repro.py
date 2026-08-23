"""One-shot debug repro for data_analysis workflow (local)."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agents.workflow import build_pool_prompt, plan_tools
from app.services.data_analysis import AnalysisOutcome, run_analysis

MSG = "按报考专业分类画出所有考生四门科目的平均值的柱状图"


def main() -> None:
    plan = plan_tools(
        MSG,
        [],
        has_kb_docs=True,
        forced_kb=False,
        has_tabular_docs=True,
    )
    print("PLAN", json.dumps(plan, ensure_ascii=False))

    kb_evidence = [
        {
            "index": 1,
            "source_type": "kb",
            "filename": "深圳大学2024年硕士研究生招生一志愿复试名单(计算机类).xlsx",
            "title": "xlsx",
            "snippet": "前5名考生成绩...",
            "content": "394名考生，081200计算机科学与技术。前5名：政治80外语70...",
            "heading": "数据集摘要",
        }
    ]
    sandbox_evidence = [
        {
            "index": 2,
            "source_type": "sandbox",
            "filename": "test.xlsx",
            "title": "沙箱执行结果",
            "content": "rows=394\n【执行代码】\nimport pandas...\nplt.show()",
            "snippet": "rows=394",
        }
    ]
    pool, included = build_pool_prompt(kb_evidence + sandbox_evidence)
    print("INCLUDED_SOURCES", [e.get("source_type") for e in included])
    print("POOL_HAS_SANDBOX", "数据分析 ·" in pool)


if __name__ == "__main__":
    main()
