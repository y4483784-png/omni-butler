#!/usr/bin/env python3
"""Generate ~500 RAG retrieval eval cases (naturalistic Chinese queries).

Queries share distinctive keywords with the seeded corpus in
``app.eval.rag_retrieval._CORPUS`` so the offline keyword path can recall gold docs.
"""

from __future__ import annotations

import json
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "data" / "eval" / "rag_retrieval.jsonl"


def case(
    cid: str,
    query: str,
    *,
    gold: list[str],
    tags: list[str] | None = None,
) -> dict:
    return {
        "id": cid,
        "query": query,
        "gold_doc_keys": gold,
        "tags": tags or [],
    }


def _variants(templates: list[str], slots: dict[str, list[str]]) -> list[str]:
    """Cartesian expand: each template may contain {slot} placeholders."""
    out: list[str] = []
    keys = list(slots.keys())
    if not keys:
        return list(templates)

    def rec(i: int, cur: dict[str, str]) -> None:
        if i == len(keys):
            for t in templates:
                try:
                    out.append(t.format(**cur))
                except KeyError:
                    out.append(t)
            return
        k = keys[i]
        for v in slots[k]:
            cur[k] = v
            rec(i + 1, cur)

    rec(0, {})
    # de-dupe preserve order
    seen: set[str] = set()
    uniq: list[str] = []
    for q in out:
        q = q.strip()
        if q and q not in seen:
            seen.add(q)
            uniq.append(q)
    return uniq


def main() -> None:
    rows: list[dict] = []

    # ---- travel_policy ----
    travel_groups: list[tuple[list[str], dict[str, list[str]]]] = [
        (
            [
                "{q}差旅住宿标准{tail}",
                "{q}一线城市住宿限额{tail}",
                "出差住酒店每天能报多少",
                "住宿标准一线城市是多少钱",
                "外地出差酒店费用上限",
            ],
            {
                "q": ["", "请问", "帮我查一下", "我想了解", "制度里"],
                "tail": ["是多少", "怎么规定的", "？", "有没有说明"],
            },
        ),
        (
            [
                "{q}出差餐补{tail}",
                "餐补标准每天多少",
                "差旅餐费怎么报",
                "出差吃饭补贴规则",
                "市内交通能实报实销吗",
            ],
            {
                "q": ["", "请问", "查一下"],
                "tail": ["怎么报", "是多少", "标准"],
            },
        ),
        (
            [
                "机票舱位有什么限制",
                "国内航班只能订经济舱吗",
                "差旅机票舱位规定",
                "国际航班舱位怎么选",
                "出差订机票有舱位要求吗",
            ],
            {},
        ),
        (
            [
                "值班补贴怎么算",
                "出差周末值班补贴",
                "值班补贴按日计算吗",
                "差旅值班补贴标准",
            ],
            {},
        ),
        (
            [
                "高铁二等座可以报销吗",
                "火车卧铺报销要说明理由吗",
                "差旅坐火车什么座位能报",
                "出差高铁座位规定",
            ],
            {},
        ),
        (
            [
                "差旅报销要在多久内提交",
                "归来后多少天提交差旅发票",
                "差旅报销截止时间",
                "出差回来报销时限",
            ],
            {},
        ),
        (
            [
                "因公出差要提前申请吗",
                "差旅总则怎么说的",
                "出差审批通过后才能预订吗",
                "差旅申请流程",
            ],
            {},
        ),
    ]
    for gi, (tmpls, slots) in enumerate(travel_groups):
        for qi, q in enumerate(_variants(tmpls, slots)):
            rows.append(
                case(f"travel_{gi:02d}_{qi:03d}", q, gold=["travel_policy"], tags=["policy", "travel"])
            )

    # ---- hr_handbook ----
    hr_groups = [
        (
            [
                "{q}年假有多少天{tail}",
                "满一年年假几天",
                "年假最多可以到多少天",
                "年假逐年递增规则",
            ],
            {"q": ["", "请问", "手册里"], "tail": ["", "？", "啊"]},
        ),
        (
            [
                "病假需要什么材料",
                "请病假要医院证明吗",
                "病假证明材料要求",
                "事假怎么申请",
                "事假要书面申请吗",
            ],
            {},
        ),
        (
            [
                "远程办公如何申请",
                "远程办公要邮件抄送领导吗",
                "远程办公最长多久",
                "居家办公申请流程",
                "远程办公单次不超过两周吗",
            ],
            {},
        ),
        (
            [
                "试用期考核说明",
                "转正前要述职吗",
                "试用期360评估",
                "转正考核怎么做",
            ],
            {},
        ),
        (
            [
                "加班调休规则",
                "加班优先调休还是加班费",
                "超时加班费怎么发",
                "加班调休怎么算",
            ],
            {},
        ),
        (
            [
                "入职要交哪些材料",
                "入职材料身份证学历证明",
                "入职日提交离职证明吗",
                "新员工入职材料清单",
            ],
            {},
        ),
        (
            [
                "离职交接要注意什么",
                "离职资产归还和账号注销",
                "最后工作日要完成什么交接",
                "离职交接流程",
            ],
            {},
        ),
    ]
    for gi, (tmpls, slots) in enumerate(hr_groups):
        for qi, q in enumerate(_variants(tmpls, slots)):
            rows.append(case(f"hr_{gi:02d}_{qi:03d}", q, gold=["hr_handbook"], tags=["hr"]))

    # ---- security_policy ----
    sec_groups = [
        (
            [
                "密码多长时间必须更换",
                "密码多久换一次",
                "密码至少每90天更换吗",
                "密码长度不少于多少位",
                "公司密码策略是什么",
            ],
            {},
        ),
        (
            [
                "能否使用私人U盘",
                "禁止使用私人U盘吗",
                "外接存储有什么规定",
                "私人U盘能不能用",
                "公司加密盘可以用吗",
            ],
            {},
        ),
        (
            [
                "文档加密要求",
                "机密级文档要加密吗",
                "未脱敏数据能外发吗",
                "文档加密怎么规定的",
            ],
            {},
        ),
        (
            [
                "双因素认证要求",
                "VPN要开MFA吗",
                "邮箱强制开启双因素吗",
                "MFA强制范围",
            ],
            {},
        ),
        (
            [
                "办公电脑锁屏规定",
                "离开座位要锁屏吗",
                "空闲超时多久锁屏",
                "电脑锁屏超时15分钟吗",
            ],
            {},
        ),
        (
            [
                "钓鱼邮件怎么处理",
                "可疑链接要上报安全邮箱吗",
                "收到钓鱼邮件怎么办",
                "钓鱼邮件上报流程",
            ],
            {},
        ),
    ]
    for gi, (tmpls, slots) in enumerate(sec_groups):
        for qi, q in enumerate(_variants(tmpls, slots)):
            rows.append(
                case(f"sec_{gi:02d}_{qi:03d}", q, gold=["security_policy"], tags=["security"])
            )

    # ---- arch_doc ----
    arch_groups = [
        (
            [
                "系统总体架构分几层",
                "架构有交互层逻辑层数据层吗",
                "系统三层架构是什么",
                "总体架构怎么划分",
            ],
            {},
        ),
        (
            [
                "知识库检索用什么数据库",
                "SQLite和Qdrant怎么混合检索",
                "向量库用的是Qdrant吗",
                "关键词检索和向量检索如何结合",
            ],
            {},
        ),
        (
            [
                "向量检索集合名称约定",
                "集合名称是omni_chunks吗",
                "Qdrant集合叫什么",
                "向量集合命名约定",
            ],
            {},
        ),
        (
            [
                "对话流式输出协议",
                "流式输出用SSE吗",
                "前端按什么事件类型渲染",
                "SSE协议怎么用在对话里",
            ],
            {},
        ),
        (
            [
                "数据分析沙箱隔离",
                "沙箱在Docker里运行吗",
                "沙箱能访问生产库吗",
                "数据分析沙箱隔离怎么做的",
            ],
            {},
        ),
        (
            [
                "Agent工作流基于什么编排",
                "LangGraph编排工具吗",
                "工具经Gateway治理吗",
                "Agent和Gateway的关系",
            ],
            {},
        ),
    ]
    for gi, (tmpls, slots) in enumerate(arch_groups):
        for qi, q in enumerate(_variants(tmpls, slots)):
            rows.append(case(f"arch_{gi:02d}_{qi:03d}", q, gold=["arch_doc"], tags=["tech"]))

    # ---- sales_table ----
    sales_groups = [
        (
            [
                "销售额最高的区域是哪个",
                "哪个区销售额最高",
                "华东销售额是多少",
                "销售额最高的区域",
            ],
            {},
        ),
        (
            [
                "西部销售额是多少",
                "西部销售额为多少",
                "查一下西部销售",
                "西部地区销售数据",
            ],
            {},
        ),
        (
            [
                "华北区销售多少",
                "华北区销售数据",
                "华北销售是80吗",
                "华北区销售额",
            ],
            {},
        ),
        (
            [
                "各区销售均值",
                "销售均值大约多少",
                "各区销售均值是多少",
                "区域销售平均值",
            ],
            {},
        ),
        (
            [
                "华东区KPI完成率",
                "华东KPI是多少",
                "华东区KPI完成率95%吗",
                "华东KPI完成情况",
            ],
            {},
        ),
        (
            [
                "华南区销售多少",
                "华南销售同比去年增长",
                "华南区销售数据",
                "华南同比增长多少",
            ],
            {},
        ),
        (
            [
                "西部渠道客户数",
                "西部渠道覆盖要补强吗",
                "西部客户数最少吗",
                "西部渠道情况",
            ],
            {},
        ),
    ]
    for gi, (tmpls, slots) in enumerate(sales_groups):
        for qi, q in enumerate(_variants(tmpls, slots)):
            rows.append(case(f"sales_{gi:02d}_{qi:03d}", q, gold=["sales_table"], tags=["tabular"]))

    # ---- office_guide ----
    office_groups = [
        (
            [
                "会议室预约规则",
                "会议室要提前一天预约吗",
                "会议室最长连续占用多久",
                "怎么预约会议室",
                "会议室预约流程",
            ],
            {},
        ),
        (
            [
                "访客登记流程",
                "访客要前台登记吗",
                "访客临时卡怎么领",
                "访客离开要归还临时卡吗",
            ],
            {},
        ),
        (
            [
                "访客WiFi怎么连",
                "访客WiFi的SSID是什么",
                "Omni-Guest密码在哪看",
                "访客无线网怎么用",
            ],
            {},
        ),
        (
            [
                "门禁卡补办",
                "门禁卡丢了怎么办",
                "门禁卡补办工本费多少",
                "行政部补办门禁卡吗",
            ],
            {},
        ),
        (
            [
                "打印机使用规范",
                "打印机默认双面打印吗",
                "彩色打印要审批吗",
                "打印规范是什么",
            ],
            {},
        ),
        (
            [
                "快递收发怎么弄",
                "大件快递放前台货架吗",
                "个人取件要登记吗",
                "快递收发规定",
            ],
            {},
        ),
    ]
    for gi, (tmpls, slots) in enumerate(office_groups):
        for qi, q in enumerate(_variants(tmpls, slots)):
            rows.append(case(f"office_{gi:02d}_{qi:03d}", q, gold=["office_guide"], tags=["office"]))

    # ---- finance_policy ----
    fin_groups = [
        (
            [
                "发票抬头有什么要求",
                "发票抬头要和公司全称一致吗",
                "税号填报要求",
                "报销发票抬头规范",
            ],
            {},
        ),
        (
            [
                "日常费用报销超过多少要加签",
                "单笔超过2000元要部门负责人加签吗",
                "费用报销加签规则",
                "大额报销审批",
            ],
            {},
        ),
        (
            [
                "预算申请什么时候交",
                "季度预算何时提交财务BP",
                "预算申请流程",
                "上季末提交季度预算吗",
            ],
            {},
        ),
        (
            [
                "对公付款周期",
                "对公付款每月哪几天",
                "付款批次是15日和月底吗",
                "对公打款时间",
            ],
            {},
        ),
        (
            [
                "备用金领用限额",
                "备用金最多5000吗",
                "备用金多少天内冲账",
                "备用金领用规则",
            ],
            {},
        ),
        (
            [
                "合同付款要附什么材料",
                "合同付款要扫描件和验收单吗",
                "合同付款附件要求",
                "对公合同付款材料",
            ],
            {},
        ),
    ]
    for gi, (tmpls, slots) in enumerate(fin_groups):
        for qi, q in enumerate(_variants(tmpls, slots)):
            rows.append(
                case(f"fin_{gi:02d}_{qi:03d}", q, gold=["finance_policy"], tags=["finance"])
            )

    # ---- it_ops ----
    it_groups = [
        (
            [
                "VPN账号怎么申请",
                "VPN开通要多久",
                "VPN工单提交到IT服务台吗",
                "VPN账号申请流程",
            ],
            {},
        ),
        (
            [
                "电脑领用流程",
                "新员工入职当天发笔记本吗",
                "IT发放标准配置笔记本吗",
                "电脑领用找谁",
            ],
            {},
        ),
        (
            [
                "软件安装有什么限制",
                "只能装白名单应用吗",
                "公司镜像以外的软件能装吗",
                "软件安装规范",
            ],
            {},
        ),
        (
            [
                "邮箱容量默认多少",
                "邮箱默认50GB吗",
                "邮箱超限怎么扩容",
                "邮箱容量申请",
            ],
            {},
        ),
        (
            [
                "故障报修怎么提",
                "IT热线还是门户工单",
                "电脑故障报修流程",
                "IT故障工单",
            ],
            {},
        ),
        (
            [
                "账号注销什么时候做",
                "离职当天禁用域账号吗",
                "邮箱转发离职怎么处理",
                "账号注销流程",
            ],
            {},
        ),
    ]
    for gi, (tmpls, slots) in enumerate(it_groups):
        for qi, q in enumerate(_variants(tmpls, slots)):
            rows.append(case(f"it_{gi:02d}_{qi:03d}", q, gold=["it_ops"], tags=["it"]))

    # ---- product_faq ----
    prod_groups = [
        (
            [
                "Omni-Butler支持哪些能力",
                "有没有日历助手和数据分析沙箱",
                "知识库问答功能有吗",
                "产品主要功能有哪些",
            ],
            {},
        ),
        (
            [
                "知识库单文件上限多少",
                "知识库支持PDF Word Excel吗",
                "Markdown能上传知识库吗",
                "知识库文件大小限制50MB吗",
            ],
            {},
        ),
        (
            [
                "引用角标是什么意思",
                "引用角标能打开原文吗",
                "检索片段和角标的关系",
                "引用角标怎么用",
            ],
            {},
        ),
        (
            [
                "日历助手能取消会议吗",
                "日历助手要绑定企业邮箱吗",
                "日历助手支持改期吗",
                "日历助手能创建会议吗",
            ],
            {},
        ),
        (
            [
                "沙箱能跑Python和SQL吗",
                "沙箱结果会话外可见吗",
                "数据分析沙箱执行什么",
                "沙箱结果可见范围",
            ],
            {},
        ),
        (
            [
                "会话记忆保留多少轮",
                "会话记忆默认30轮吗",
                "会话记忆能手动清空吗",
                "记忆保留轮数",
            ],
            {},
        ),
    ]
    for gi, (tmpls, slots) in enumerate(prod_groups):
        for qi, q in enumerate(_variants(tmpls, slots)):
            rows.append(case(f"prod_{gi:02d}_{qi:03d}", q, gold=["product_faq"], tags=["product"]))

    # ---- legal_compliance ----
    legal_groups = [
        (
            [
                "对外合同要法务审核吗",
                "合同金额超50万要会签吗",
                "公司法务会签条件",
                "对外合同审核流程",
            ],
            {},
        ),
        (
            [
                "个人信息处理原则",
                "最小必要原则是什么",
                "能超范围采集个人信息吗",
                "个人信息采集规范",
            ],
            {},
        ),
        (
            [
                "竞业限制多久",
                "核心岗位竞业限制12个月吗",
                "离职后竞业限制适用吗",
                "竞业限制规定",
            ],
            {},
        ),
        (
            [
                "印章使用规定",
                "公章和合同章要分开保管吗",
                "用印要登记吗",
                "印章保管要求",
            ],
            {},
        ),
        (
            [
                "供应商尽调要做什么",
                "新供应商合规问卷",
                "供应商资质核验",
                "供应商尽调流程",
            ],
            {},
        ),
        (
            [
                "数据出境要评估吗",
                "跨境传输安全评估",
                "数据出境备案要求",
                "跨境数据传输合规",
            ],
            {},
        ),
    ]
    for gi, (tmpls, slots) in enumerate(legal_groups):
        for qi, q in enumerate(_variants(tmpls, slots)):
            rows.append(
                case(f"legal_{gi:02d}_{qi:03d}", q, gold=["legal_compliance"], tags=["legal"])
            )

    # Extra paraphrases to pad toward 500 without inventing new facts
    extras: list[tuple[str, str, list[str], list[str]]] = [
        ("ex_travel_01", "一线城市住宿标准每天限额多少", ["travel_policy"], ["policy"]),
        ("ex_travel_02", "其他城市住宿限额是多少", ["travel_policy"], ["policy"]),
        ("ex_travel_03", "餐补每天100元吗", ["travel_policy"], ["policy"]),
        ("ex_travel_04", "差旅市内交通报销方式", ["travel_policy"], ["policy"]),
        ("ex_travel_05", "国际航班舱位限制", ["travel_policy"], ["policy"]),
        ("ex_travel_06", "差旅报销15个工作日", ["travel_policy"], ["policy"]),
        ("ex_hr_01", "年假满一年5天吗", ["hr_handbook"], ["hr"]),
        ("ex_hr_02", "年假最多15天吗", ["hr_handbook"], ["hr"]),
        ("ex_hr_03", "远程办公抄送直属领导", ["hr_handbook"], ["hr"]),
        ("ex_hr_04", "试用期述职要求", ["hr_handbook"], ["hr"]),
        ("ex_hr_05", "加班费发放条件", ["hr_handbook"], ["hr"]),
        ("ex_sec_01", "密码12位要求", ["security_policy"], ["security"]),
        ("ex_sec_02", "禁止外接存储规定", ["security_policy"], ["security"]),
        ("ex_sec_03", "机密级须加密", ["security_policy"], ["security"]),
        ("ex_sec_04", "VPN双因素认证", ["security_policy"], ["security"]),
        ("ex_arch_01", "交互层逻辑层数据层", ["arch_doc"], ["tech"]),
        ("ex_arch_02", "混合检索SQLite Qdrant", ["arch_doc"], ["tech"]),
        ("ex_arch_03", "omni_chunks集合", ["arch_doc"], ["tech"]),
        ("ex_arch_04", "Docker沙箱隔离", ["arch_doc"], ["tech"]),
        ("ex_sales_01", "华东销售120", ["sales_table"], ["tabular"]),
        ("ex_sales_02", "华北销售80", ["sales_table"], ["tabular"]),
        ("ex_sales_03", "西部销售60", ["sales_table"], ["tabular"]),
        ("ex_sales_04", "华南销售90", ["sales_table"], ["tabular"]),
        ("ex_office_01", "会议室连续占用4小时", ["office_guide"], ["office"]),
        ("ex_office_02", "访客临时卡归还", ["office_guide"], ["office"]),
        ("ex_office_03", "SSID Omni-Guest", ["office_guide"], ["office"]),
        ("ex_office_04", "门禁卡工本费50元", ["office_guide"], ["office"]),
        ("ex_fin_01", "发票税号填报", ["finance_policy"], ["finance"]),
        ("ex_fin_02", "2000元加签", ["finance_policy"], ["finance"]),
        ("ex_fin_03", "备用金30天冲账", ["finance_policy"], ["finance"]),
        ("ex_fin_04", "付款批次月底", ["finance_policy"], ["finance"]),
        ("ex_it_01", "VPN开通1个工作日", ["it_ops"], ["it"]),
        ("ex_it_02", "邮箱扩容申请", ["it_ops"], ["it"]),
        ("ex_it_03", "域账号离职禁用", ["it_ops"], ["it"]),
        ("ex_it_04", "白名单应用商店", ["it_ops"], ["it"]),
        ("ex_prod_01", "知识库50MB上限", ["product_faq"], ["product"]),
        ("ex_prod_02", "引用角标打开原文", ["product_faq"], ["product"]),
        ("ex_prod_03", "沙箱会话内可见", ["product_faq"], ["product"]),
        ("ex_prod_04", "记忆清空入口", ["product_faq"], ["product"]),
        ("ex_legal_01", "法务会签50万", ["legal_compliance"], ["legal"]),
        ("ex_legal_02", "竞业限制12个月", ["legal_compliance"], ["legal"]),
        ("ex_legal_03", "用印登记", ["legal_compliance"], ["legal"]),
        ("ex_legal_04", "数据出境备案", ["legal_compliance"], ["legal"]),
        # more natural spoken variants
        ("ex_travel_07", "我想查差旅住宿标准", ["travel_policy"], ["policy"]),
        ("ex_travel_08", "帮我看看出差餐补", ["travel_policy"], ["policy"]),
        ("ex_hr_06", "年假天数是怎么规定的", ["hr_handbook"], ["hr"]),
        ("ex_hr_07", "病假材料清单", ["hr_handbook"], ["hr"]),
        ("ex_sec_05", "私人U盘禁令", ["security_policy"], ["security"]),
        ("ex_sec_06", "电脑锁屏超时", ["security_policy"], ["security"]),
        ("ex_arch_05", "SSE流式协议", ["arch_doc"], ["tech"]),
        ("ex_arch_06", "Gateway治理工具", ["arch_doc"], ["tech"]),
        ("ex_sales_05", "各区销售平均水平", ["sales_table"], ["tabular"]),
        ("ex_sales_06", "华东KPI完成率", ["sales_table"], ["tabular"]),
        ("ex_office_05", "会议室预约提前多久", ["office_guide"], ["office"]),
        ("ex_office_06", "打印机彩色审批", ["office_guide"], ["office"]),
        ("ex_fin_05", "季度预算提交时间", ["finance_policy"], ["finance"]),
        ("ex_fin_06", "合同付款验收单", ["finance_policy"], ["finance"]),
        ("ex_it_05", "IT故障报修热线", ["it_ops"], ["it"]),
        ("ex_it_06", "新员工电脑领用", ["it_ops"], ["it"]),
        ("ex_prod_05", "日历助手绑定邮箱", ["product_faq"], ["product"]),
        ("ex_prod_06", "知识库支持哪些格式", ["product_faq"], ["product"]),
        ("ex_legal_05", "个人信息最小必要", ["legal_compliance"], ["legal"]),
        ("ex_legal_06", "供应商资质核验要求", ["legal_compliance"], ["legal"]),
        ("ex_travel_09", "出差申请要提前吗", ["travel_policy"], ["policy"]),
        ("ex_travel_10", "火车卧铺报销说明", ["travel_policy"], ["policy"]),
        ("ex_hr_08", "离职交接最后工作日", ["hr_handbook"], ["hr"]),
        ("ex_hr_09", "入职材料学历证明", ["hr_handbook"], ["hr"]),
        ("ex_sec_07", "钓鱼邮件上报", ["security_policy"], ["security"]),
        ("ex_sec_08", "邮箱强制MFA", ["security_policy"], ["security"]),
        ("ex_arch_07", "LangGraph工作流", ["arch_doc"], ["tech"]),
        ("ex_arch_08", "三层架构说明", ["arch_doc"], ["tech"]),
        ("ex_sales_07", "西部渠道补强", ["sales_table"], ["tabular"]),
        ("ex_sales_08", "华南同比增长", ["sales_table"], ["tabular"]),
        ("ex_office_07", "快递大件前台货架", ["office_guide"], ["office"]),
        ("ex_office_08", "访客登记领临时卡", ["office_guide"], ["office"]),
        ("ex_fin_07", "备用金领用限额5000", ["finance_policy"], ["finance"]),
        ("ex_fin_08", "对公付款两个批次", ["finance_policy"], ["finance"]),
        ("ex_it_07", "邮箱容量50GB", ["it_ops"], ["it"]),
        ("ex_it_08", "软件白名单限制", ["it_ops"], ["it"]),
        ("ex_prod_07", "会话记忆30轮", ["product_faq"], ["product"]),
        ("ex_prod_08", "沙箱跑SQL", ["product_faq"], ["product"]),
        ("ex_legal_07", "公章合同章保管", ["legal_compliance"], ["legal"]),
        ("ex_legal_08", "跨境传输安全评估", ["legal_compliance"], ["legal"]),
    ]
    for cid, q, gold, tags in extras:
        rows.append(case(cid, q, gold=gold, tags=tags))

    # Deduplicate by query text; keep first id
    seen_q: set[str] = set()
    deduped: list[dict] = []
    for r in rows:
        q = r["query"]
        if q in seen_q:
            continue
        seen_q.add(q)
        deduped.append(r)

    # If still short of 500, add numbered spoken prefixes on base facts
    base_facts: list[tuple[str, list[str], list[str]]] = [
        ("差旅住宿标准", ["travel_policy"], ["policy"]),
        ("出差餐补标准", ["travel_policy"], ["policy"]),
        ("机票舱位限制", ["travel_policy"], ["policy"]),
        ("值班补贴标准", ["travel_policy"], ["policy"]),
        ("年假天数规定", ["hr_handbook"], ["hr"]),
        ("病假材料要求", ["hr_handbook"], ["hr"]),
        ("远程办公申请", ["hr_handbook"], ["hr"]),
        ("加班调休规则", ["hr_handbook"], ["hr"]),
        ("密码更换周期", ["security_policy"], ["security"]),
        ("私人U盘禁令", ["security_policy"], ["security"]),
        ("双因素认证要求", ["security_policy"], ["security"]),
        ("文档加密要求", ["security_policy"], ["security"]),
        ("系统总体架构", ["arch_doc"], ["tech"]),
        ("向量集合omni_chunks", ["arch_doc"], ["tech"]),
        ("SSE流式输出", ["arch_doc"], ["tech"]),
        ("沙箱Docker隔离", ["arch_doc"], ["tech"]),
        ("华东销售额", ["sales_table"], ["tabular"]),
        ("西部销售额", ["sales_table"], ["tabular"]),
        ("各区销售均值", ["sales_table"], ["tabular"]),
        ("华东区KPI", ["sales_table"], ["tabular"]),
        ("会议室预约规则", ["office_guide"], ["office"]),
        ("访客登记流程", ["office_guide"], ["office"]),
        ("访客WiFi连接", ["office_guide"], ["office"]),
        ("门禁卡补办", ["office_guide"], ["office"]),
        ("发票抬头要求", ["finance_policy"], ["finance"]),
        ("备用金领用", ["finance_policy"], ["finance"]),
        ("对公付款周期", ["finance_policy"], ["finance"]),
        ("预算申请时间", ["finance_policy"], ["finance"]),
        ("VPN账号申请", ["it_ops"], ["it"]),
        ("电脑领用流程", ["it_ops"], ["it"]),
        ("邮箱容量扩容", ["it_ops"], ["it"]),
        ("故障报修工单", ["it_ops"], ["it"]),
        ("知识库文件上限", ["product_faq"], ["product"]),
        ("引用角标说明", ["product_faq"], ["product"]),
        ("日历助手能力", ["product_faq"], ["product"]),
        ("会话记忆轮数", ["product_faq"], ["product"]),
        ("对外合同审核", ["legal_compliance"], ["legal"]),
        ("竞业限制期限", ["legal_compliance"], ["legal"]),
        ("供应商尽调", ["legal_compliance"], ["legal"]),
        ("数据出境要求", ["legal_compliance"], ["legal"]),
    ]
    prefixes = [
        "请问",
        "帮我查",
        "查一下",
        "我想了解",
        "制度里写了",
        "手册怎么说",
        "简单说下",
        "详细讲讲",
        "有没有",
        "关于",
    ]
    suffixes = ["", "？", "是怎样的", "分别是什么", "具体规定"]
    pad_i = 0
    while len(deduped) < 500:
        fact, gold, tags = base_facts[pad_i % len(base_facts)]
        pre = prefixes[(pad_i // len(base_facts)) % len(prefixes)]
        suf = suffixes[(pad_i // (len(base_facts) * len(prefixes))) % len(suffixes)]
        q = f"{pre}{fact}{suf}".strip()
        pad_i += 1
        if q in seen_q:
            continue
        seen_q.add(q)
        deduped.append(
            case(f"pad_{len(deduped):04d}", q, gold=gold, tags=tags + ["paraphrase"])
        )
        if pad_i > 5000:
            break

    deduped = deduped[:500]
    # renumber ids for stability
    final: list[dict] = []
    for i, r in enumerate(deduped, start=1):
        final.append(
            {
                "id": f"rag_{i:04d}",
                "query": r["query"],
                "gold_doc_keys": r["gold_doc_keys"],
                "tags": r.get("tags") or [],
            }
        )

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8") as f:
        for r in final:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    by_doc: dict[str, int] = {}
    for r in final:
        for k in r["gold_doc_keys"]:
            by_doc[k] = by_doc.get(k, 0) + 1
    print(f"Wrote {len(final)} cases -> {OUT}")
    print("Per-doc:", json.dumps(by_doc, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
