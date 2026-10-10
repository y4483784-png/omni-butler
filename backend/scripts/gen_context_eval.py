#!/usr/bin/env python3
"""Generate office NIAH-style context recall eval dataset (deterministic)."""

from __future__ import annotations

import argparse
import hashlib
import json
import random
from pathlib import Path
from typing import Any

OUT = Path(__file__).resolve().parents[1] / "data" / "eval" / "context_recall.jsonl"

BUCKETS = (12, 30, 60, 120)
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
    ("帮我整理一下本周待办清单的写法", "可以用「事项 / 负责人 / 截止」多列清单。"),
    ("周报段落之间怎么衔接更顺", "先摘要后细节，每段用一个过渡句即可。"),
    ("会议室预约流程提醒一下", "先查空闲再提交申请，通过后同步参与者。"),
    ("邮件标题怎么写更清晰", "用「对象 + 事项 + 时间」分段表达。"),
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
    ("客户问题升级标准是什么", "影响面大或超时待解再升级。"),
    ("知识库文章如何保持新鲜", "设定期回顾人与失效标记。"),
    ("远程协作时区怎么处理", "用对方工作时段约会议，异步写纪要。"),
    ("看板卡片拆太碎怎么办", "合并为可交付的用户故事粒度。"),
    ("测试用例评审看什么", "覆盖边界、异常路径与可判定期望。"),
    ("发布前检查清单有哪些", "回滚方案、监控、权限与配置核对。"),
    ("告警噪声太大怎么治理", "分级采样，关键路径单独告警。"),
    ("接口文档缺字段怎么办", "先补示例再改契约，避免口头约定。"),
    ("需求评审准备什么材料", "背景、目标用户、验收标准与风险。"),
    ("项目延期如何沟通", "先给影响面与可选方案，再给新时间。"),
    ("周会纪要发给谁", "参与者与相关干系人，抄送决策人。"),
    ("如何避免重复造轮子", "先搜内部组件库与已有方案。"),
    ("性能问题先查哪里", "先看瓶颈指标，再定位热点路径。"),
    ("安全扫描误报怎么处理", "复核证据，确认后加白并留记录。"),
    ("文档翻译口径怎么统一", "维护术语表，专名不随意改译。"),
    ("培训材料如何分层", "按入门、进阶、排障分层，避免一次讲完。"),
    ("跨团队依赖怎么跟踪", "建立依赖表，定期同步阻塞项。"),
    ("用户意见怎么归类", "按功能、体验、缺陷分桶统计。"),
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
    ("报销单被退回怎么查", "先看退回理由，再按缺失材料逐项补齐。"),
    ("会议纪要谁来确认", "由主持人确认决定，行动项由负责人认领。"),
    ("客户需求口径变了怎么办", "记录变更来源，并重新确认验收范围。"),
    ("代码合并前要看什么", "确认评审通过、检查通过且不存在待解冲突。"),
    ("测试环境数据乱了怎么办", "清理临时数据，再从固定夹具恢复。"),
    ("内部公告怎么写更清楚", "先写影响范围，再列生效时间与联系人。"),
    ("工单一直没响应怎么办", "补充影响等级并联系对应值班人。"),
    ("文件误删后如何处理", "先查回收站和版本历史，再联系管理员恢复。"),
    ("电子表公式被覆盖怎么办", "从历史版本恢复公式并锁定关键单元格。"),
    ("权限申请被拒绝怎么办", "核对用途与审批人，补充最小权限说明。"),
    ("需求验收有争议怎么办", "回到已确认的验收标准，逐条核对证据。"),
    ("项目风险如何登记", "写清概率、影响、责任人和缓解动作。"),
    ("临时会议如何减少打扰", "先异步说明背景，只邀请必要决策人。"),
    ("邮件附件太大怎么发", "上传受控网盘并发送有期限的访问链接。"),
    ("新人权限漏开怎么办", "按岗位模板补申请，并核对最小权限。"),
    ("报表数字突然变化怎么办", "先核对数据时间窗，再检查源表与清洗规则。"),
    ("接口字段准备废弃怎么做", "先标记弃用并提供迁移期，最后再移除。"),
    ("数据库变更如何评审", "说明兼容性、回滚方式和数据修复方案。"),
    ("线上问题怎么留证据", "保存时间、请求标识、运行记录片段和复现步骤。"),
    ("客户演示前准备什么", "锁定演示数据，预跑流程并准备降级方案。"),
    ("采购需求怎么提交", "列明规格、数量、预算归属和期望时间。"),
    ("合同审批卡住怎么办", "确认当前节点与缺件，联系对应审批人。"),
    ("发票信息填错怎么办", "作废错误记录，按财务要求重新开具。"),
    ("知识库答案过期怎么办", "标记失效内容，更新来源并重新审核。"),
    ("共享账号安全吗", "尽量使用个人账号，公共能力改用受控服务账号。"),
    ("敏感文件如何传输", "使用批准的加密通道，并限制接收人范围。"),
    ("离职交接容易漏什么", "核对资产、权限、文档和在办事项。"),
    ("服务降级怎么通知用户", "说明受影响功能、临时方案和恢复进展。"),
    ("搜索结果太多怎么筛", "增加业务范围、时间和文档类型条件。"),
    ("指标口径变更怎么留痕", "记录旧新定义、切换时点和影响报表。"),
    ("数据导出要审批吗", "按数据等级和用途走对应审批流程。"),
    ("供应商延期如何跟进", "确认原因、补救计划和新的验收节点。"),
    ("培训签到漏记怎么办", "由组织人核实后补录并保留说明。"),
    ("会议决策反复怎么办", "固化决策记录，后续变更必须说明新证据。"),
    ("产品缺陷优先级谁定", "按用户影响和发生频率由产品与研发共同确认。"),
    ("运行记录中有敏感信息怎么办", "先停止扩散，脱敏后再用于排障。"),
    ("自动化任务失败怎么重跑", "先确认幂等性与失败位置，再从安全节点恢复。"),
    ("发布窗口冲突怎么办", "按业务优先级协调，避免共享依赖同时变更。"),
    ("容量快满了怎么处理", "先清理可回收数据，再评估扩容和限流。"),
    ("缓存数据不一致怎么办", "确认数据源后失效相关键，避免全量清空。"),
    ("文档负责人离职怎么办", "移交所有权并重新指定复核周期。"),
    ("需求缺少验收标准怎么办", "暂停开发，先补充可观察且可判定的结果。"),
    ("告警恢复后还要处理吗", "要复盘根因，并确认监控能覆盖再次发生。"),
    ("客户名单怎么共享", "使用受控目录并按最小范围授权。"),
    ("表单字段太难填怎么办", "提供示例和默认值，移除非必要字段。"),
    ("消息通知太频繁怎么办", "合并同类事件并允许用户配置优先级。"),
    ("跨系统数据对不上怎么办", "对齐主键、时区和同步完成时间。"),
    ("任务缺少负责人怎么办", "在排期前明确唯一责任人和协作人。"),
    ("代码注释写到什么程度", "解释原因和约束，不重复描述显而易见的语句。"),
    ("评审会议缺少结果怎么办", "明确待决项、补充材料和再次决策条件。"),
    ("生产数据能拿来测试吗", "优先用脱敏样本或合成数据，避免直接复制。"),
    ("用户权限异常怎么排查", "核对身份、角色、资源范围和缓存状态。"),
    ("批处理卡住怎么办", "查看最后进度点，判断能否安全续跑。"),
    ("搜索索引延迟怎么办", "确认写入队列和刷新状态，并提示数据时效。"),
    ("证书快过期怎么处理", "提前续签、验证部署并保留回滚证书。"),
    ("接口返回空数据怎么办", "区分真实为空与查询失败，给出明确状态。"),
    ("文档引用失效怎么办", "更新稳定链接，并补充来源标题和版本。"),
    ("审批人休假怎么办", "按代理规则转交，避免绕过必要审批。"),
    ("临时账号怎么回收", "设置到期时间并由申请人确认使用结束。"),
    ("图表颜色看不清怎么办", "提高对比度，并用标签辅助区分类别。"),
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
    filler_pool = list(FILLERS)
    rng.shuffle(filler_pool)
    if filler_needed > len(filler_pool):
        raise ValueError(
            f"bucket={bucket} needs {filler_needed} unique filler turns, "
            f"only {len(filler_pool)} configured"
        )

    def next_filler() -> tuple[str, str]:
        return filler_pool.pop()

    start = _insert_depth(bucket, depth_pct)
    # Ensure needle fits
    start = min(start, max(0, filler_needed))

    for _ in range(start):
        u, a = next_filler()
        add_turn(u, a, is_needle=False)
    for block in needle_turns:
        history.extend(block)
    while (len(history) // 2) < bucket:
        u, a = next_filler()
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


def build_cases(*, seeds: tuple[int, ...] = (7, 21, 43)) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for seed in seeds:
        for ntype in TYPES:
            variants = NEEDLE_BANK[ntype]
            for bucket in BUCKETS:
                for depth in DEPTHS:
                    digest = hashlib.sha256(
                        f"{seed}|{ntype}|{bucket}|{depth}".encode("utf-8")
                    ).digest()
                    rng = random.Random(int.from_bytes(digest[:8], "big"))
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

    # Mark smoke: four per type over the cheap 12/30-turn buckets only.
    by_type: dict[str, list[dict[str, Any]]] = {t: [] for t in TYPES}
    for c in cases:
        by_type[c["type"]].append(c)
    smoke_ids: set[str] = set()
    for t, rows in by_type.items():
        pick = [
            r
            for r in rows
            if r["bucket_turns"] in (12, 30)
            and r["depth_pct"] in (0, 50)
            and r["seed"] == 7
        ]
        if len(pick) != 4:
            raise ValueError(f"unexpected context smoke candidates for {t}: {len(pick)}")
        for r in pick:
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
        "\n".join(json.dumps(c, ensure_ascii=False, allow_nan=False) for c in cases) + "\n",
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
