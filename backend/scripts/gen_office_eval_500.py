#!/usr/bin/env python3
"""Generate ~500 office tool-routing eval cases (naturalistic Chinese utterances)."""

from __future__ import annotations

import json
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "data" / "eval" / "office_tool_routing.jsonl"


def case(
    cid: str,
    message: str,
    *,
    intent: str,
    tools: list[str],
    history: list | None = None,
    context: dict | None = None,
    tags: list[str] | None = None,
) -> dict:
    gold: dict = {"intent": intent, "tools": tools}
    return {
        "id": cid,
        "message": message,
        "history": history or [],
        "context": context or {},
        "gold": gold,
        "tags": tags or [],
    }


def main() -> None:
    rows: list[dict] = []

    # ---- chat (~120) ----
    chat_msgs = [
        "你好，今天怎么样？",
        "在吗",
        "嗨",
        "早上好",
        "下午好呀",
        "晚上好",
        "谢谢，辛苦了",
        "收到，明白了",
        "好的",
        "嗯嗯",
        "讲个笑话吧",
        "帮我写一首关于春天的短诗",
        "帮我润色这段话：项目进展顺利",
        "用更正式的语气改写：请尽快回复",
        "什么是 OKR",
        "什么是 SWOT",
        "解释一下什么叫复盘",
        "你觉得远程办公效率怎么样",
        "周末愉快",
        "节日快乐",
        "帮我想三个会议开场白",
        "写个简短的周报提纲",
        "帮我起个项目代号",
        "中英文怎么礼貌拒绝对方延期",
        "你是谁",
        "你能做什么",
        "换个说法再说一遍",
        "再简单一点",
        "再详细一点",
        "用表格形式列出来",
        "今天有点累，聊聊天",
        "随便聊聊工作以外的事",
        "推荐几本管理类的书",
        "如何提高专注力",
        "帮我列个待办模板",
        "写一封感谢邮件草稿，语气轻松",
        "生成三个头脑风暴问题",
        "把这段话翻成英文：请确认会议时间",
        "把这段话翻成中文：Please confirm",
        "帮我检查语法：We has finished the report",
        "一句话总结：沟通很重要",
        "给新手解释什么是 MVP",
        "说个冷知识",
        "陪我练练面试自我介绍",
        "帮我写个朋友圈文案，庆祝上线",
        "有什么好的时间管理方法",
        "如何开好站会",
        "团队冲突怎么处理比较好",
        "帮我列个出差行李清单",
        "写个生日祝福，给同事",
        "今天心情不错",
        "你觉得呢",
        "继续",
        "还有吗",
        "就这样吧",
        "先到这里",
        "哈哈",
        "不错",
        "可以",
        "不行啊这个方案",
        "帮我想个口号",
        "起个活动主题",
        "写个免责声明模板",
        "生成会议纪要结构",
        "帮我排个优先级矩阵示例",
        "解释一下帕累托法则",
        "什么叫沉没成本",
        "如何做用户访谈",
        "产品经理日常做什么",
        "工程师怎么写更好的 commit message",
        "帮我写个 README 大纲",
        "用更委婉的语气：这个需求做不了",
        "帮我列五个开放式问题",
        "生成一个决策树的文字版",
        "今天适合喝什么咖啡（随便聊聊）",
        "讲个职场小故事",
        "安慰一下加班的人",
        "鼓励一下新人",
        "帮我写离职告别信开头",
        "写个入职欢迎辞",
        "如何礼貌催进度",
        "如何拒绝额外工作",
        "如何约领导一对一",
        "帮我想三个 OKR 例子（通用）",
        "解释敏捷和瀑布的区别",
        "什么是技术债",
        "如何做 code review",
        "写个 standup 模板",
        "帮我列个学习计划框架",
        "生成面试问题清单（通用）",
        "如何做复盘会",
        "写个道歉信模板",
        "帮我润色：不好意思迟到了",
        "用一句话介绍我们的产品（通用）",
        "生成电梯演讲 30 秒版",
        "如何设定边界",
        "工作生活怎么平衡",
        "帮我想个团队建设活动",
        "写个问卷开场白",
        "生成满意度调查题干示例",
        "如何做时间盒",
        "解释番茄工作法",
        "帮我列个晨间例行",
        "写个晚安祝福给团队",
        "今天过得怎么样——我先说我这边还行",
        "你忙吗",
        "有空聊聊吗",
        "帮我清醒一下，说点有趣的",
        "随便推荐个播客主题",
        "如何开始写日记",
        "帮我起个笔名",
        "写个短故事开头",
        "用文言文说一句问候",
        "用方言风格写句谢谢（搞笑版）",
        "帮我数到十就行",
        "重复我说的话：测试一下",
        "输出一个空列表示例",
        "给个随机励志短句",
        "今天别聊工作了",
        "先聊点轻松的",
        "你听得懂四川话吗（随便问问）",
        "帮我想个外号，别太正经",
    ]
    for i, m in enumerate(chat_msgs, 1):
        rows.append(
            case(
                f"chat_{i:03d}",
                m,
                intent="chat",
                tools=[],                tags=["chat"],
            )
        )
    # chat with KB library present but no KB hint
    for i, m in enumerate(["你好", "在吗", "写首诗", "解释一下复利", "帮我润色这段话"], 1):
        rows.append(
            case(
                f"chat_lib_{i:03d}",
                m,
                intent="chat",
                tools=[],                context={"has_kb_docs": True},
                tags=["chat", "library_present"],
            )
        )

    # ---- kb (~100) ----
    kb_topics = [
        "差旅住宿标准",
        "报销流程",
        "请假规定",
        "加班补贴",
        "考勤规则",
        "安全规范",
        "信息安全制度",
        "用章流程",
        "合同审批",
        "采购流程",
        "招待标准",
        "员工手册里的福利",
        "离职交接要求",
        "试用期考核",
        "绩效评级说明",
        "培训学分要求",
        "会议室预约规则",
        "访客登记流程",
        "数据分级规定",
        "外包人员管理",
        "股权激励说明",
        "保密协议要点",
        "知识产权归属",
        "远程办公制度",
        "设备领用流程",
        "出差机票舱位",
        "市内交通报销",
        "餐补标准",
        "年假计算方式",
        "病假材料要求",
        "婚假天数",
        "陪产假规定",
        "调休规则",
        "值班补贴",
        "项目立项模板",
        "变更管理流程",
        "事故上报要求",
        "应急预案要点",
        "质量门禁标准",
        "上线 checklist",
        "代码规范摘要",
        "设计规范里的间距",
        "品牌色使用规定",
        "对外发言口径",
        "媒体采访流程",
        "客户投诉处理",
        "SLA 响应时限",
        "权限申请步骤",
        "账号开通流程",
        "备份保留周期",
    ]
    kb_templates = [
        "文档里{topic}是怎么说的",
        "知识库里有没有{topic}",
        "根据材料说明一下{topic}",
        "手册里对{topic}怎么规定",
        "资料里提到的{topic}有哪些",
        "方案里关于{topic}写了什么",
        "附件里能找到{topic}吗",
        "按咱们库里的说法，{topic}是什么",
        "上传的文档里{topic}怎么写的",
        "检索一下文档：{topic}",
        "这份 pdf 里{topic}的要点",
        "这份 docx 对{topic}的要求",
        "文中关于{topic}的条款",
        "根据文档总结{topic}",
        "库里查一下{topic}",
        "差旅相关：{topic}",
        "报销相关：{topic}",
        "制度里{topic}",
        "表格里如果写了{topic}请摘出来",
        "重新解析后文档里的{topic}",
    ]
    ki = 0
    for topic in kb_topics:
        for tmpl in kb_templates[:2]:
            ki += 1
            rows.append(
                case(
                    f"kb_{ki:03d}",
                    tmpl.format(topic=topic),
                    intent="rag",
                    tools=["kb"],
                    context={"has_kb_docs": True},
                    tags=["kb"],
                )
            )
            if ki >= 100:
                break
        if ki >= 100:
            break

    # ---- web (~90) ----
    web_msgs = [
        "帮我搜一下今天最新的行业政策新闻",
        "查一下实时股价",
        "网上有什么热点",
        "官网最新公告是什么",
        "搜索一下明天天气",
        "行情怎么样，帮我查一下",
        "热搜排名前几是什么",
        "联网查一下最新汇率",
        "搜一下最近发布的监管新规",
        "查一下今日油价",
        "网上搜搜竞品动态",
        "最新政策有没有变化",
        "帮我搜索一下开源许可证对比",
        "查一下实时黄金价格",
        "新闻里说的那次更新是什么",
        "官网有没有招聘公告更新",
        "搜一下今天的财经头条",
        "查一下航班准点率新闻",
        "联网找一下最新安全漏洞通报",
        "搜索一下本地天气预警",
        "热点事件目前进展如何",
        "查一下股票开盘情况",
        "网上有没有关于裁员的消息",
        "搜一下最新 AI 产品发布",
        "公告原文能联网找到吗",
        "实时路况相关新闻有吗",
        "查一下热搜榜单",
        "搜索一下最新专利纠纷新闻",
        "行情数据哪里看，帮我搜一下",
        "官网发布页有新版本吗",
        "联网查一下汇率中间价",
        "最新行业报告新闻摘要",
        "搜一下政策解读文章",
        "查一下天气未来三天",
        "网上热点话题有哪些",
        "搜索一下公司财报发布消息",
        "实时资讯：科技板块",
        "查一下新闻发布会时间",
        "官网更新日志在哪",
        "搜一下最新国家标准发布",
        "联网看看有没有召回公告",
        "热搜第一是什么",
        "查一下最新利率调整新闻",
        "搜索一下开源项目 star 排行新闻",
        "行情怎么样啊搜一下",
        "最新疫情相关政策有没有（联网）",
        "官网客服公告更新了吗",
        "搜一下今天体育新闻头条",
        "查一下油价调整消息",
        "网上有什么娱乐热点",
        "搜索一下最新芯片报价新闻",
        "实时天气北京",
        "查一下发布会直播入口新闻",
        "联网找最新漏洞 CVE",
        "搜一下政策补贴申请公告",
        "热搜里有没有科技相关",
        "官网博客最新一篇",
        "查一下股票涨跌幅新闻",
        "搜索一下汇率走势报道",
        "最新监管罚单新闻",
        "联网查天气预警信号",
        "搜一下行业峰会日程新闻",
        "查一下热点舆情",
        "网上搜搜新品发布",
        "实时资讯汇总一下",
        "搜索一下最新招投标公告",
        "官网通知有更新吗",
        "查一下热搜关键词",
        "联网看看政策原文链接",
        "搜一下新闻通稿",
        "行情播报帮我查",
        "最新天气灾害新闻",
        "搜索一下股价异动原因新闻",
        "查一下官网安全公告",
        "网上有没有融资新闻",
        "实时热点榜",
        "搜一下发布更新说明",
        "查一下新闻时间线",
        "联网找政策问答",
        "搜索一下天气雷达相关新闻",
        "官网文档更新公告",
        "查一下行业排名新闻",
        "热搜里的争议话题",
        "搜一下最新并购消息",
        "行情是否有突发新闻",
        "联网查一下节假日调休公告",
        "搜索一下天气指数",
        "查一下政策落地时间新闻",
        "网上最新说法是什么",
        "搜一下实时资讯快讯",
    ]
    for i, m in enumerate(web_msgs[:90], 1):
        rows.append(
            case(
                f"web_{i:03d}",
                m,
                intent="web_search",
                tools=["web"],
                tags=["web"],
            )
        )

    # ---- calendar (~80) ----
    days = ["今天", "明天", "后天", "今晚", "明早", "明天下午", "明天上午"]
    hours = [
        "一点",
        "两点",
        "三点",
        "四点",
        "五点",
        "六点",
        "七点",
        "八点",
        "九点",
        "十点",
        "十一点",
        "3点",
        "9点",
        "10点",
        "两点半",
        "三点半",
        "九点半",
        "四点半",
    ]
    meeting_kinds = ["周会", "月会", "面试", "晨会", "例会", "评审会", "一对一", "同步会", "启动会", "复盘会"]
    cal_templates = [
        "帮我定{day}{hour}的{kind}",
        "安排一下{day}{hour}的{kind}",
        "约个会议，{day}{hour}",
        "日历里加个提醒，{day}{hour}开会",
        "预定{day}{hour}的{kind}",
        "帮我约{day}{hour}的{kind}",
        "{day}{hour}有个{kind}，帮我排进日程",
        "提醒我{day}{hour}要开{kind}",
        "会议订在{day}{hour}，主题{kind}",
        "跟同事约{day}{hour}的{kind}",
    ]
    ci = 0
    for day in days:
        for hour in hours:
            for kind in meeting_kinds:
                ci += 1
                tmpl = cal_templates[ci % len(cal_templates)]
                rows.append(
                    case(
                        f"cal_{ci:03d}",
                        tmpl.format(day=day, hour=hour, kind=kind),
                        intent="calendar",
                        tools=["calendar"],
                        tags=["calendar"],
                    )
                )
                if ci >= 80:
                    break
            if ci >= 80:
                break
        if ci >= 80:
            break

    # ---- sandbox (~70) ----
    sandbox_msgs = [
        "销售表帮我画个柱状图，顺便汇总一下均值",
        "对这个 csv 做分组统计",
        "excel 里销售额趋势画折线图",
        "表格 top10 排名",
        "分析一下上传的数据分布",
        "xlsx 透视一下各部门人数",
        "统计一下各区域销量求和",
        "画个饼图看占比",
        "用 pandas 算一下平均客单价",
        "dataframe 里按月份汇总",
        "可视化一下转化漏斗",
        "图表展示近 7 天活跃",
        "柱状对比各部门 KPI",
        "折线看看趋势变化",
        "求和一下总营收",
        "均值方差都算一下",
        "排名前五的产品",
        "分布直方图画一下",
        "分组统计城市订单数",
        "透视表：渠道×金额",
        "excel 分析一下毛利",
        "csv 里 count 一下用户数",
        "表格里求最大最小值",
        "画图展示库存周转",
        "统计缺货 SKU 数量",
        "汇总本月退货率",
        "分析复购分布",
        "柱状图画各班组产量",
        "折线对比预算与实际",
        "饼图看费用结构",
        "top20 客户贡献",
        "pandas 做个 describe",
        "趋势预测前先画图看历史",
        "表格透视 HR 人数",
        "xlsx 统计加班时长均值",
        "csv 分组看转化率",
        "可视化销售额排名",
        "分析异常值分布",
        "求和各成本中心",
        "平均响应时长统计",
        "画个柱状图：区域销售额",
        "excel 透视品类销量",
        "表格里做个排名榜",
        "统计并画图：日活",
        "csv 分析漏斗各步",
        "xlsx 汇总毛利率",
        "分组均值：门店客流",
        "画折线：周环比",
        "饼图费用占比",
        "top 排名供应商",
        "分布看一下金额区间",
        "pandas 透视交叉表",
        "统计投诉量趋势并画图",
        "表格求合计与平均",
        "可视化库存结构",
        "分析 excel 里的退货",
        "csv 画柱状对比",
        "xlsx 折线看增长",
        "汇总并排名 SKU",
        "统计各部门均值",
        "画图：转化率分布",
        "透视一下地区销量",
        "表格分析环比变化",
        "求 top 并可视化",
        "分组统计后再画图",
        "excel 柱状图费用",
        "csv 饼图结构",
        "xlsx 趋势折线",
        "分析数据并出图",
        "表格做个简单统计报表",
    ]
    for i, m in enumerate(sandbox_msgs[:70], 1):
        rows.append(
            case(
                f"sandbox_{i:03d}",
                m,
                intent="data_analysis",
                tools=["sandbox"],
                context={"has_tabular_docs": True},
                tags=["sandbox"],
            )
        )

    # ---- multi / edge (~40) ----
    multi = [
        case(
            "multi_001",
            "根据文档并搜一下最新政策",
            intent="rag",
            tools=["kb", "web"],
            context={"has_kb_docs": True},
            tags=["multi"],
        ),
        case(
            "multi_002",
            "网上最新说法和文档规定对一下",
            intent="rag",
            tools=["kb", "web"],
            context={"has_kb_docs": True, "forced_kb": True},
            tags=["multi", "forced_kb"],
        ),
        case(
            "multi_003",
            "按知识库制度，再联网查最新公告",
            intent="rag",
            tools=["kb", "web"],
            context={"has_kb_docs": True},
            tags=["multi"],
        ),
        case(
            "multi_004",
            "文档里写的标准，网上有没有更新",
            intent="rag",
            tools=["kb", "web"],
            context={"has_kb_docs": True},
            tags=["multi"],
        ),
        case(
            "multi_005",
            "手册里规定对照一下网上最新发布",
            intent="rag",
            tools=["kb", "web"],
            context={"has_kb_docs": True},
            tags=["multi"],
        ),
        case(
            "edge_pending_001",
            "三点吧",
            intent="chat",
            tools=[],            context={"pending_calendar": {"title": "周会", "missing_fields": ["end_at"]}},
            tags=["calendar", "pending"],
        ),
        case(
            "edge_pending_002",
            "改成四点",
            intent="chat",
            tools=[],            context={"pending_calendar": {"title": "面试", "missing_fields": ["end_at"]}},
            tags=["calendar", "pending"],
        ),
        case(
            "edge_use_kb_001",
            "总结一下",
            intent="rag",
            tools=["kb"],            context={"has_kb_docs": True, "use_kb": True},
            tags=["kb", "forced_ui"],
        ),
        case(
            "edge_use_kb_002",
            "再讲讲重点",
            intent="rag",
            tools=["kb"],            context={"has_kb_docs": True, "use_kb": True},
            tags=["kb", "forced_ui"],
        ),
        case(
            "edge_cancel_001",
            "算了不安排了",
            intent="chat",
            tools=[],            history=[{"role": "user", "content": "帮我定明天三点的会"}],
            tags=["calendar", "cancel"],
        ),
        case(
            "edge_cancel_002",
            "取消吧先不安排",
            intent="chat",
            tools=[],            history=[{"role": "user", "content": "约明天下午的周会"}],
            tags=["calendar", "cancel"],
        ),
        case(
            "edge_neg_sandbox_001",
            "帮我画个柱状图",
            intent="chat",
            tools=[],            context={"has_tabular_docs": False},
            tags=["sandbox", "negative"],
        ),
        case(
            "edge_neg_sandbox_002",
            "统计一下均值",
            intent="chat",
            tools=[],            context={"has_tabular_docs": False},
            tags=["sandbox", "negative"],
        ),
        case(
            "edge_neg_kb_001",
            "根据知识库查报销制度",
            intent="chat",
            tools=[],            context={"has_kb_docs": False},
            tags=["kb", "negative"],
        ),
        case(
            "edge_neg_kb_002",
            "文档里差旅标准是多少",
            intent="chat",
            tools=[],            context={"has_kb_docs": False},
            tags=["kb", "negative"],
        ),
        case(
            "edge_follow_chat_001",
            "还有别的吗",
            intent="chat",
            tools=[],            history=[
                {"role": "user", "content": "你好"},
                {"role": "assistant", "content": "你好！"},
            ],
            tags=["chat", "multi_turn"],
        ),
        case(
            "edge_follow_kb_001",
            "那报销上限呢",
            intent="rag",
            tools=["kb"],
            context={"has_kb_docs": True},
            history=[
                {"role": "user", "content": "差旅制度住宿标准是多少"},
                {"role": "assistant", "content": "..."},
            ],
            tags=["kb", "multi_turn"],
        ),
        case(
            "edge_follow_kb_002",
            "加班呢怎么规定",
            intent="rag",
            tools=["kb"],
            context={"has_kb_docs": True},
            history=[
                {"role": "user", "content": "手册里请假怎么说"},
                {"role": "assistant", "content": "..."},
            ],
            tags=["kb", "multi_turn"],
        ),
    ]
    # pad multi variants
    for i in range(6, 21):
        multi.append(
            case(
                f"multi_{i:03d}",
                f"根据文档对照网上最新公告（样例{i}）",
                intent="rag",
                tools=["kb", "web"],
                context={"has_kb_docs": True},
                tags=["multi"],
            )
        )
    for i in range(3, 11):
        multi.append(
            case(
                f"edge_pending_{i:03d}",
                f"{['两点','四点','五点','六点','七点','八点','九点','十点'][i-3]}吧",
                intent="chat",
                tools=[],                context={"pending_calendar": {"title": "会", "missing_fields": ["end_at"]}},
                tags=["calendar", "pending"],
            )
        )
    rows.extend(multi)

    # Trim / pad to exactly 500 (prefer dropping chat extras if over).
    if len(rows) > 500:
        def _drop_key(r: dict) -> int:
            tid = r["id"]
            if tid.startswith("chat_pad_"):
                return 0
            if tid.startswith("chat_") and r["gold"]["intent"] == "chat":
                return 1
            return 2

        rows.sort(key=_drop_key)
        rows = rows[:500]
        rows.sort(key=lambda r: r["id"])
    elif len(rows) < 500:
        need = 500 - len(rows)
        for i in range(need):
            rows.append(
                case(
                    f"chat_pad_{i+1:03d}",
                    f"随便聊聊，话题{i+1}",
                    intent="chat",
                    tools=[],                    tags=["chat", "pad"],
                )
            )

    assert len(rows) == 500, len(rows)
    ids = [r["id"] for r in rows]
    assert len(ids) == len(set(ids)), "duplicate ids"

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"wrote {len(rows)} cases -> {OUT}")


if __name__ == "__main__":
    main()
