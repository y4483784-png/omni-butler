"""RAG retrieval eval: seed mini corpus + Precision@k / Recall@k / MRR."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import SessionLocal, init_db
from app.eval.metrics import retrieval_report
from app.models.models import Chunk, Document
from app.rag.retrieval import retrieve

DEFAULT_DATASET = Path(__file__).resolve().parents[2] / "data" / "eval" / "rag_retrieval.jsonl"
EVAL_USER_ID = 92001

_CORPUS: dict[str, dict[str, Any]] = {
    "travel_policy": {
        "filename": "差旅制度.md",
        "chunks": [
            "差旅总则：因公出差须提前申请，审批通过后方可预订。",
            "住宿标准：一线城市每天限额500元，其他城市400元。",
            "餐补标准：每天100元；市内交通可实报实销。",
            "机票舱位：国内航班经济舱；国际航班经济舱或同等舱位。",
            "值班补贴按日计算；出差期间周末值班可叠加。",
            "高铁二等座可报；火车卧铺需说明理由。",
            "差旅报销须在归来后15个工作日内提交发票。",
        ],
    },
    "hr_handbook": {
        "filename": "员工手册.md",
        "chunks": [
            "年假：满一年享有5天年假，之后逐年递增至最多15天。",
            "病假需医院证明材料；事假提前书面申请。",
            "远程办公须邮件申请并抄送直属领导，单次不超过两周。",
            "试用期考核说明：转正前完成述职与360评估。",
            "加班调休规则：优先调休，超时发放加班费。",
            "入职材料：身份证、学历证明、离职证明须在入职日提交。",
            "离职交接：资产归还与账号注销在最后工作日完成。",
        ],
    },
    "security_policy": {
        "filename": "信息安全制度.md",
        "chunks": [
            "密码多长时间必须更换：至少每90天更换一次，长度不少于12位。",
            "禁止使用私人U盘等外接存储；公司发放加密盘除外。",
            "文档加密要求：机密级须加密，禁止外发未脱敏数据。",
            "双因素认证要求：VPN与邮箱强制开启MFA。",
            "办公电脑锁屏：离开座位须立即锁屏，空闲超时15分钟。",
            "钓鱼邮件：可疑链接勿点击，上报至安全邮箱。",
        ],
    },
    "arch_doc": {
        "filename": "架构说明.md",
        "chunks": [
            "系统总体架构分交互层、逻辑层、数据层三层。",
            "知识库检索用SQLite关键词与Qdrant向量库混合。",
            "向量检索集合名称约定：omni_chunks。",
            "对话流式输出协议采用SSE；前端按事件类型渲染。",
            "数据分析沙箱隔离在Docker中运行，禁止访问内网生产库。",
            "Agent工作流基于LangGraph编排，工具经Gateway治理。",
        ],
    },
    "sales_table": {
        "filename": "销售汇总.csv",
        "chunks": [
            "region,sales\n华东,120\n华北,80\n华南,90\n西部,60",
            "销售额最高的区域是华东；西部销售额为60；华北区销售80。",
            "各区销售均值约为86.7；华东区KPI完成率95%。",
            "华南区销售90；同比去年增长12%。",
            "西部渠道客户数最少，需补强渠道覆盖。",
        ],
    },
    "office_guide": {
        "filename": "办公指南.md",
        "chunks": [
            "会议室预约规则：提前一天在系统提交，最长连续占用4小时。",
            "访客登记流程：前台登记并领取临时卡，离开时归还。",
            "访客WiFi怎么连：SSID Omni-Guest，密码见前台。",
            "门禁卡补办：行政部办理，工本费50元。",
            "打印机使用规范：默认双面打印，彩色需审批。",
            "快递收发：大件放前台货架，个人取件需登记。",
        ],
    },
    "finance_policy": {
        "filename": "财务报销制度.md",
        "chunks": [
            "发票抬头须与公司全称一致，税号填报完整。",
            "日常费用报销：单笔超过2000元须部门负责人加签。",
            "预算申请：季度预算于上季末提交财务BP。",
            "对公付款周期：每月15日与月底两个批次。",
            "备用金领用：限额5000元，须在30天内冲账。",
            "合同付款须附合同扫描件与验收单。",
        ],
    },
    "it_ops": {
        "filename": "IT运维手册.md",
        "chunks": [
            "VPN账号申请：工单提交至IT服务台，开通时效1个工作日。",
            "电脑领用：新员工入职当天由IT发放标准配置笔记本。",
            "软件安装：仅允许公司镜像与白名单应用商店。",
            "邮箱容量：默认50GB，超限可申请扩容。",
            "故障报修：拨打IT热线或在门户提交工单。",
            "账号注销：离职当日禁用域账号与邮箱转发。",
        ],
    },
    "product_faq": {
        "filename": "产品FAQ.md",
        "chunks": [
            "Omni-Butler支持知识库问答、日历助手与数据分析沙箱。",
            "知识库单文件上限50MB，支持PDF、Word、Excel与Markdown。",
            "引用角标对应检索到的文档片段，可点击打开原文。",
            "日历助手可创建、改期与取消会议，需绑定企业邮箱。",
            "沙箱执行Python与SQL，结果仅会话内可见。",
            "会话记忆默认保留最近30轮，可手动清空。",
        ],
    },
    "legal_compliance": {
        "filename": "合规与合同指引.md",
        "chunks": [
            "对外合同须法务审核，金额超50万需公司法务会签。",
            "个人信息处理遵循最小必要原则，不得超范围采集。",
            "竞业限制：核心岗位离职后12个月内适用。",
            "印章使用：公章与合同章分开保管，用印须登记。",
            "供应商尽调：新供应商须完成合规问卷与资质核验。",
            "数据出境：跨境传输须完成安全评估或备案。",
        ],
    },
}


@dataclass
class RagEvalCase:
    id: str
    query: str
    gold_doc_keys: list[str]
    tags: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "RagEvalCase":
        return cls(
            id=str(raw["id"]),
            query=str(raw.get("query") or ""),
            gold_doc_keys=list(raw.get("gold_doc_keys") or []),
            tags=list(raw.get("tags") or []),
        )


def load_cases(path: str | Path | None = None) -> list[RagEvalCase]:
    p = Path(path) if path else DEFAULT_DATASET
    out: list[RagEvalCase] = []
    with p.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            out.append(RagEvalCase.from_dict(json.loads(line)))
    return out


def clear_eval_corpus(db: Session) -> None:
    docs = db.query(Document).filter(Document.user_id == EVAL_USER_ID).all()
    for d in docs:
        db.query(Chunk).filter(Chunk.document_id == d.id).delete()
        db.delete(d)
    db.commit()


def seed_eval_corpus(db: Session) -> dict[str, int]:
    """Insert mini corpus; return map doc_key -> document_id."""
    clear_eval_corpus(db)
    key_to_id: dict[str, int] = {}
    for key, spec in _CORPUS.items():
        doc = Document(
            user_id=EVAL_USER_ID,
            filename=spec["filename"],
            status="ready",
            stored_path="",
            char_count=sum(len(c) for c in spec["chunks"]),
        )
        db.add(doc)
        db.flush()
        for i, text in enumerate(spec["chunks"]):
            db.add(
                Chunk(
                    document_id=doc.id,
                    chunk_index=i,
                    content=text,
                    kind="text",
                    heading=key,
                )
            )
        key_to_id[key] = doc.id
    db.commit()
    return key_to_id


@dataclass
class RagEvalReport:
    n: int
    metrics: dict[str, float]
    failures: list[dict[str, Any]] = field(default_factory=list)

    def format_text(self) -> str:
        lines = [
            f"=== Omni-Butler RAG Retrieval Eval (n={self.n}) ===",
            f"  precision@k  {self.metrics.get('precision_at_k', 0):.2%}",
            f"  recall@k     {self.metrics.get('recall_at_k', 0):.2%}",
            f"  MRR          {self.metrics.get('mrr', 0):.4f}",
        ]
        if self.failures:
            lines.append(f"Failures ({len(self.failures)}):")
            for f in self.failures[:10]:
                lines.append(
                    f"  - {f['id']}: gold={f['gold']} pred={f['pred']}"
                )
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {"n": self.n, "metrics": self.metrics, "failures": self.failures}


def run_rag_retrieval_eval(
    cases: list[RagEvalCase] | None = None,
    *,
    dataset_path: str | Path | None = None,
    k: int | None = None,
    use_zhipu_rerank: bool = False,
) -> RagEvalReport:
    """Offline keyword+expand+heuristic (or zhipu) eval on seeded corpus."""
    items = cases if cases is not None else load_cases(dataset_path)
    top_k = int(k if k is not None else settings.rag_top_k)

    prev_hybrid = settings.rag_hybrid_enabled
    prev_provider = settings.rag_rerank_provider
    prev_rerank = settings.rag_rerank_enabled
    # Deterministic CI path: keyword only + expand + heuristic unless opted in
    settings.rag_hybrid_enabled = False
    settings.rag_rerank_enabled = True
    settings.rag_rerank_provider = "zhipu" if use_zhipu_rerank else "none"

    init_db()
    db = SessionLocal()
    try:
        key_to_id = seed_eval_corpus(db)
        gold_sets: list[set[str]] = []
        pred_ranked: list[list[str]] = []
        failures: list[dict[str, Any]] = []

        for case in items:
            gold_ids = {str(key_to_id[k]) for k in case.gold_doc_keys if k in key_to_id}
            hits = retrieve(db, case.query, user_id=EVAL_USER_ID, top_k=top_k)
            ranked_docs: list[str] = []
            seen: set[str] = set()
            for h in hits:
                did = str(h.document_id)
                if did not in seen:
                    seen.add(did)
                    ranked_docs.append(did)
            gold_sets.append(gold_ids)
            pred_ranked.append(ranked_docs)
            if not gold_ids.intersection(ranked_docs[:top_k]):
                failures.append(
                    {
                        "id": case.id,
                        "query": case.query,
                        "gold": sorted(gold_ids),
                        "pred": ranked_docs[:top_k],
                    }
                )

        metrics = retrieval_report(gold_sets, pred_ranked, k=top_k)
        return RagEvalReport(n=len(items), metrics=metrics, failures=failures)
    finally:
        clear_eval_corpus(db)
        db.close()
        settings.rag_hybrid_enabled = prev_hybrid
        settings.rag_rerank_provider = prev_provider
        settings.rag_rerank_enabled = prev_rerank
