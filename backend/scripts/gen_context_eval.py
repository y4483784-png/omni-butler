#!/usr/bin/env python3
"""Generate office NIAH-style context recall eval dataset (deterministic)."""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path
from typing import Any

OUT = Path(__file__).resolve().parents[1] / "data" / "eval" / "context_recall.jsonl"

BUCKETS = (12, 30, 60)
DEPTHS = (0, 25, 50, 75)
TYPES = (
    "user_fact",
    "assistant_fact",
    "knowledge_update",
    "multi_hop",
    "temporal",
    "pending_task",
    "preference",
    "abstention",
)

# Filler pairs: no digits / dates that collide with needles.
FILLERS: list[tuple[str, str]] = [
    ("帮我整理一下本周待办清单的写法", "可以用「事项 / 负责人 / 截止」三列表格。"),
    ("周报段落之间怎么衔接更顺", "先结论后细节，每段用一个过渡句即可。"),
    ("会议室预约流程提醒一下", "先查空闲再提交申请，通过后同步参会人。"),
    ("邮件标题怎么写更清晰", "用「对象 + 事项 + 时间」三段式。"),
    ("如何减少无效会议", "会前发议程，会后发纪要与行动项。"),
    ("文档命名有什么建议", "项目名-主题-版本号，避免空格。"),
    ("跨部门协作容易卡在哪", "职责边界不清；建议先对齐接口人。"),
    ("分享材料太多怎么办", "先给一页摘要，细节放附录链接。"),
    ("请假申请要注意什么", "提前告知直接上级，并交接在办事项。"),
    ("站会超时怎么控场", "每人限时，超时议题另约专题会。"),
    ("复盘会怎么开更有效", "只谈事实、原因、改进，避免追责语气。"),
    ("任务优先级怎么排", "紧急且重要优先，其余可排期或委派。"),
    ("新同事入职第一周做什么", "熟悉规范、对接导师、完成环境与权限。"),
    ("代码评审意见怎么写", "指出影响与建议改法，避免纯否定。"),
    ("产品需求变更怎么同步", "更新文档并通知相关研发与测试。"),
    ("客户问题升级标准是什么", "影响面大或超时未解再升级。"),
    ("知识库文章如何保持新鲜", "设定期回顾人与失效标记。"),
    ("远程协作时区怎么处理", "用对方工作时段约会议，异步写纪要。"),
    ("看板卡片拆太碎怎么办", "合并为可交付的用户故事粒度。"),
    ("测试用例评审看什么", "覆盖边界、异常路径与可判定期望。"),
    ("发布前检查清单有哪些", "回滚方案、监控、权限与配置核对。"),
    ("告警噪声太大怎么治理", "分级采样，关键路径单独告警。"),
    ("接口文档缺字段怎么办", "先补示例再改契约，避免口头约定。"),
    ("需求评审准备什么材料", "背景、目标用户、验收标准与风险。"),
    ("项目延期如何沟通", "先给影响面与可选方案，再给新时间。"),
    ("周会纪要发给谁", "参会人与相关干系人，抄送决策人。"),
    ("如何避免重复造轮子", "先搜内部组件库与已有方案。"),
    ("性能问题先查哪里", "先看瓶颈指标，再定位热点路径。"),
    ("安全扫描误报怎么处理", "复核证据，确认后加白并留记录。"),
    ("文档翻译口径怎么统一", "维护术语表，专名不随意改译。"),
    ("培训材料如何分层", "入门、进阶、排障三套，避免一次讲完。"),
    ("跨团队依赖怎么跟踪", "建立依赖表，定期同步阻塞项。"),
    ("用户反馈怎么归类", "按功能、体验、缺陷分桶统计。"),
    ("设计稿标注不清怎么办", "会前澄清关键交互，会后补注释。"),
    ("灰度发布观察什么", "错误率、延迟与核心转化是否漂移。"),
    ("值班交接要写哪些", "在办告警、变更窗口与联系人。"),
    ("数据口径不一致怎么办", "对齐定义与取数时间窗。"),
    ("报表字段太多怎么呈现", "默认关键列，其余折叠或下钻。"),
    ("会议邀请缺少议程", "补发议程或改期，避免空会。"),
    ("协作文档权限怎么设", "默认可读，编辑给责任人。"),
    ("需求冻结后还能改吗", "走变更流程并评估工期影响。"),
    ("缺陷重开怎么记录", "关联原单，写清复现步骤。"),
    ("接口超时用户提示", "明确重试与稍后再查，避免空白。"),
    ("版本号怎么升", "不兼容升主版本，兼容功能升次版本。"),
    ("埋点缺失怎么补", "先补关键漏斗，再扩展细节事件。"),
    ("活动文案审核看什么", "合规用语、夸大宣传与链接有效性。"),
    ("供应商对接注意点", "明确交付物、验收标准与联系窗口。"),
    ("办公室座位调整流程", "提申请，行政确认后更新平面图。"),
    ("团建报名怎么统计", "表单截止后导出名单核对。"),
    ("打印机故障找谁", "先自助重启，不行找行政报修。"),
    ("访客预约要提前多久", "建议尽早提交预约申请。"),
    ("食堂菜单哪里看", "行政群置顶或内网公告。"),
    ("停车位怎么申请", "行政系统提交，按余量分配。"),
    ("名片怎么加印", "品牌模板申请，走行政印刷。"),
    ("快递存放规定", "前台代收，超期自行取回。"),
    ("会议室茶水谁准备", "预订人自备或行政协助。"),
    ("下班最后离开注意", "关灯关窗，门禁落锁。"),
    ("共享网盘容量满了", "清理过期包，或申请扩容。"),
    ("VPN连不上先做什么", "检查账号有效期与本地时间。"),
    ("显示器闪烁怎么办", "换线材与刷新率，再报IT。"),
]


NEEDLE_BANK: dict[str, list[dict[str, Any]]] = {
    "user_fact": [
        {
            "user": "这次活动预算上限是八万元，不能超。",
            "assistant": "已记录：预算上限八万元。",
            "question": "我们最早约定的预算上限是多少？",
            "gold_answer": "八万元",
            "gold_keys": ["八万", "80000", "8万"],
            "judge_mode": "strict",
        },
        {
            "user": "会议室固定用三号会议室。",
            "assistant": "好的，地点按三号会议室安排。",
            "question": "会议地点最早定在哪里？",
            "gold_answer": "三号会议室",
            "gold_keys": ["三号会议室"],
            "judge_mode": "strict",
        },
        {
            "user": "差旅住宿标准按城市甲档执行。",
            "assistant": "已按城市甲档记录住宿标准。",
            "question": "住宿标准按哪一档？",
            "gold_answer": "城市甲档",
            "gold_keys": ["甲档", "城市甲档"],
            "judge_mode": "strict",
        },
    ],
    "assistant_fact": [
        {
            "user": "请根据表格算一下退货率。",
            "assistant": "按全量计算，退货率是百分之八点二，合小数零点零八二。",
            "question": "刚才算出的退货率是多少？",
            "gold_answer": "8.2%",
            "gold_keys": ["8.2%", "8.2", "0.082", "百分之八点二"],
            "judge_mode": "strict",
        },
        {
            "user": "华东区销售额合计是多少？",
            "assistant": "华东区销售额合计为一百二十万元。",
            "question": "你之前给出的华东销售额是多少？",
            "gold_answer": "120万元",
            "gold_keys": ["120万", "一百二十万", "1200000"],
            "judge_mode": "strict",
        },
        {
            "user": "迟到次数阈值是多少？",
            "assistant": "制度里写的是月度迟到超过三次取消全勤奖。",
            "question": "月度迟到超过几次取消全勤？",
            "gold_answer": "三次",
            "gold_keys": ["三", "3"],
            "judge_mode": "strict",
        },
    ],
    "knowledge_update": [
        {
            "user": "周会先定在周三下午。",
            "assistant": "已记下周三下午周会。",
            "update_user": "改一下，周会改到周四十五点。",
            "update_assistant": "已更新为周四十五点。",
            "question": "周会最终定在什么时候？",
            "gold_answer": "周四十五点",
            "gold_keys": ["周四", "十五点", "15:00", "15点"],
            "judge_mode": "update",
        },
        {
            "user": "对接人先写张敏。",
            "assistant": "对接人记录为张敏。",
            "update_user": "对接人改成李强。",
            "update_assistant": "已把对接人更新为李强。",
            "question": "现在的对接人是谁？",
            "gold_answer": "李强",
            "gold_keys": ["李强"],
            "judge_mode": "update",
        },
        {
            "user": "交付日先按五月八日。",
            "assistant": "交付日记为五月八日。",
            "update_user": "交付日改为五月九日。",
            "update_assistant": "已改为五月九日。",
            "question": "交付日最终是哪天？",
            "gold_answer": "五月九日",
            "gold_keys": ["五月九日", "5月9", "09日"],
            "judge_mode": "update",
        },
    ],
    "multi_hop": [
        {
            "user": "参会人数定为二十五人。",
            "assistant": "人数二十五已记录。",
            "user2": "人均预算三百二十元。",
            "assistant2": "人均三百二十元已记录。",
            "question": "按人数和人均，总预算大概多少？",
            "gold_answer": "八千元",
            "gold_keys": ["8000", "八千", "二十五", "三百二十"],
            "judge_mode": "strict",
        },
        {
            "user": "项目代号是海燕。",
            "assistant": "代号海燕已记下。",
            "user2": "海燕项目的负责人是赵倩。",
            "assistant2": "负责人赵倩已关联到海燕。",
            "question": "海燕项目的负责人是谁？",
            "gold_answer": "赵倩",
            "gold_keys": ["赵倩", "海燕"],
            "judge_mode": "strict",
        },
    ],
    "temporal": [
        {
            "user": "启动会在五月六日开过了，复盘会安排在三天后。",
            "assistant": "也就是五月九日安排复盘会。",
            "question": "复盘会是哪一天？",
            "gold_answer": "五月九日",
            "gold_keys": ["五月九日", "5月9", "三天"],
            "judge_mode": "temporal",
        },
        {
            "user": "上周五提交了初稿，这周一给了反馈。",
            "assistant": "已记录：上周五初稿，本周一反馈。",
            "question": "反馈是在初稿之后第几个工作日左右？",
            "gold_answer": "约一个工作日之后（周一）",
            "gold_keys": ["周一", "反馈", "初稿"],
            "judge_mode": "temporal",
        },
    ],
    "pending_task": [
        {
            "user": "帮我建一个叫产品评审的日程草稿。",
            "assistant": "已创建草稿「产品评审」，还缺地点和参会人。",
            "pending_calendar": {
                "title": "产品评审",
                "missing_fields": ["location", "attendees"],
            },
            "question": "未完成的日程草稿标题是什么？还缺什么？",
            "gold_answer": "产品评审，缺地点和参会人",
            "gold_keys": ["产品评审", "地点", "参会"],
            "judge_mode": "strict",
        },
        {
            "user": "先起草「供应商访谈」。",
            "assistant": "草稿「供应商访谈」已建，待补时间。",
            "pending_calendar": {
                "title": "供应商访谈",
                "missing_fields": ["start_time"],
            },
            "question": "待补的日程叫什么？",
            "gold_answer": "供应商访谈",
            "gold_keys": ["供应商访谈"],
            "judge_mode": "strict",
        },
    ],
    "preference": [
        {
            "user": "以后分析结论请优先用表格输出。",
            "assistant": "好的，分析结论会优先用表格。",
            "question": "我偏好的输出形式是什么？",
            "gold_answer": "表格",
            "gold_keys": ["表格"],
            "judge_mode": "rubric",
        },
        {
            "user": "回复请尽量简短，先给结论。",
            "assistant": "收到，之后先结论后细节。",
            "question": "我对回答风格的要求是什么？",
            "gold_answer": "先给结论、尽量简短",
            "gold_keys": ["结论", "简短"],
            "judge_mode": "rubric",
        },
    ],
    "abstention": [
        {
            "user": "我们只讨论报销流程，不要扯别的。",
            "assistant": "好的，聚焦报销流程。",
            "question": "我有没有提过配偶的生日？",
            "gold_answer": "未提及",
            "gold_keys": ["未提及", "没有", "不知道"],
            "judge_mode": "abstain",
            "expect_abstain": True,
        },
        {
            "user": "先核对考勤制度。",
            "assistant": "好的，围绕考勤制度。",
            "question": "我的护照号码是多少？",
            "gold_answer": "会话中未提供",
            "gold_keys": ["未", "没有", "不知"],
            "judge_mode": "abstain",
            "expect_abstain": True,
        },
    ],
}


def _fill_pair(rng: random.Random) -> tuple[str, str]:
    return FILLERS[rng.randrange(len(FILLERS))]


def _insert_depth(total_turns: int, depth_pct: int) -> int:
    """Return needle start turn index in [0, total_turns-3]."""
    span = max(0, total_turns - 3)
    idx = int(round(span * (depth_pct / 100.0)))
    return max(0, min(span, idx))


def _build_history(
    rng: random.Random,
    *,
    bucket: int,
    depth_pct: int,
    ntype: str,
    variant: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    history: list[dict[str, Any]] = []
    pending = variant.get("pending_calendar")
    needle_turns: list[list[dict[str, Any]]] = []

    def add_turn(user: str, assistant: str, *, is_needle: bool = False) -> None:
        history.append({"role": "user", "content": user, "is_needle": is_needle})
        history.append({"role": "assistant", "content": assistant, "is_needle": is_needle})

    if ntype == "knowledge_update":
        needle_turns.append(
            [
                {"role": "user", "content": variant["user"], "is_needle": True},
                {"role": "assistant", "content": variant["assistant"], "is_needle": True},
            ]
        )
        needle_turns.append(
            [
                {"role": "user", "content": variant["update_user"], "is_needle": True},
                {"role": "assistant", "content": variant["update_assistant"], "is_needle": True},
            ]
        )
    elif ntype == "multi_hop":
        needle_turns.append(
            [
                {"role": "user", "content": variant["user"], "is_needle": True},
                {"role": "assistant", "content": variant["assistant"], "is_needle": True},
            ]
        )
        needle_turns.append(
            [
                {"role": "user", "content": variant["user2"], "is_needle": True},
                {"role": "assistant", "content": variant["assistant2"], "is_needle": True},
            ]
        )
    else:
        needle_turns.append(
            [
                {"role": "user", "content": variant["user"], "is_needle": True},
                {"role": "assistant", "content": variant["assistant"], "is_needle": True},
            ]
        )

    needle_turn_count = len(needle_turns)
    filler_needed = bucket - needle_turn_count
    start = _insert_depth(bucket, depth_pct)
    # Ensure needle fits
    start = min(start, max(0, filler_needed))

    for _ in range(start):
        u, a = _fill_pair(rng)
        add_turn(u, a, is_needle=False)
    for block in needle_turns:
        history.extend(block)
    while (len(history) // 2) < bucket:
        u, a = _fill_pair(rng)
        add_turn(u, a, is_needle=False)
    # trim to exact bucket turns
    history = history[: bucket * 2]
    return history, pending


def _evidence_pool(rng: random.Random, *, force: bool = False) -> list[dict[str, Any]] | None:
    if not force and rng.random() > 0.35:
        return None
    n = rng.randint(4, 8)
    rows = []
    for i in range(1, n + 1):
        rows.append(
            {
                "index": i,
                "filename": f"policy_{i}.md",
                "snippet": ("办公制度摘录段落。" * 40) + f"片段{i}",
                "source_type": "kb",
            }
        )
    return rows


def build_cases(*, seeds: tuple[int, ...] = (7, 21)) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for seed in seeds:
        rng = random.Random(seed)
        for ntype in TYPES:
            variants = NEEDLE_BANK[ntype]
            for bucket in BUCKETS:
                for depth in DEPTHS:
                    variant = variants[rng.randrange(len(variants))]
                    history, pending = _build_history(
                        rng, bucket=bucket, depth_pct=depth, ntype=ntype, variant=variant
                    )
                    cid = f"ctx_{ntype}_b{bucket}_d{depth}_s{seed}"
                    probe = {
                        "question": variant["question"],
                        "gold_answer": variant["gold_answer"],
                        "gold_keys": list(variant["gold_keys"]),
                        "judge_mode": variant.get("judge_mode") or "strict",
                        "expect_abstain": bool(variant.get("expect_abstain")),
                    }
                    cases.append(
                        {
                            "id": cid,
                            "split": "full",
                            "type": ntype,
                            "bucket_turns": bucket,
                            "depth_pct": depth,
                            "seed": seed,
                            "history": history,
                            "pending_calendar": pending,
                            "evidence_pool": _evidence_pool(rng, force=(bucket >= 30 and depth <= 25)),
                            "probe": probe,
                            "tags": [ntype, f"b{bucket}", f"d{depth}"],
                        }
                    )

    # Mark smoke: 3 per type (prefer mid depth / mid bucket diversity)
    by_type: dict[str, list[dict[str, Any]]] = {t: [] for t in TYPES}
    for c in cases:
        by_type[c["type"]].append(c)
    smoke_ids: set[str] = set()
    for t, rows in by_type.items():
        pick = [r for r in rows if r["bucket_turns"] == 30 and r["depth_pct"] in (0, 50, 75)]
        if len(pick) < 3:
            pick = rows
        for r in pick[:3]:
            r["split"] = "smoke"
            smoke_ids.add(r["id"])
    # ensure exactly smoke or full
    for c in cases:
        if c["id"] not in smoke_ids and c["split"] == "smoke":
            c["split"] = "full"
    return cases


def write_dataset(path: Path | None = None) -> Path:
    out = path or OUT
    cases = build_cases()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        "\n".join(json.dumps(c, ensure_ascii=False) for c in cases) + "\n",
        encoding="utf-8",
    )
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate context recall eval JSONL")
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args()
    path = write_dataset(args.out)
    cases = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    smoke = sum(1 for c in cases if c.get("split") == "smoke")
    print(f"wrote {len(cases)} cases ({smoke} smoke) -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
