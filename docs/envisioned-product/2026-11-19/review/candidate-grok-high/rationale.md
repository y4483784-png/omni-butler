grok-4.7-high

拒绝整仓换成 Hermes 或 TypeScript。定案是 Python 后端和现有前端原地演进，Hermes 只提供工具循环的保存顺序。

拒绝把新消息默认放进一条 OpenCode 队列，也拒绝抄它的默认 steer。定案是派活层按句关联到不同任务，不是单队。

拒绝把 PR 93 的 Pro Responses 当成目标。当天打开的 Responses 文档写明 `web_search` 由 Pro 在服务端执行，Flash 忽略。

拒绝在聊天补全里声明服务端工具 `deepseek_web_search` 与 `deepseek_read_url`。搜索会在供应商侧发生，执行端不能在发出前拒绝，守不住「这一轮不搜」。

拒绝为了搜索把 `chat_model` 改成 `deepseek-v4-pro`。

拒绝用关键词表给全部消息分类。禁搜是被关联任务上的 `search_policy`，分类失败时追问，不猜。

拒绝保留 `begin_turn` 停掉同会话其他回合的行为。那正是「新消息杀死旧工作」。

拒绝只藏停止按钮、断开仍调用 `task_run.stop`。U2 拿掉按钮，U5 才让断开不写停止，两步都要在，缺一不可当完成。

拒绝给每一次取消和每一次改向再加一张确认卡。定案是只有歧义会改变动作才问，明确且目标唯一的文字取消直接 `stop`。

拒绝新数据库、新消息队列产品或新的微服务。步骤表进现有库，执行者进已经在跑的 Celery。

拒绝把工具循环留在 SSE 请求的 `asyncio.Task` 里。请求取消会把工作一起取消，进程重启也没有可重入的作业。

拒绝抄 Hermes「最后一个订阅者离开就关闭会话」。定案是网页断开不取消后台。

拒绝把 Hermes 的 300 秒或现有的 20 分钟写成新的停滞阈值。阈值留空时不报告、不停止，所以 U6 不被一个没出处的秒数挡住。

拒绝在 U6 继续 `query(TaskRun).all()`。停止决策不再需要全历史。

拒绝迁移智谱嵌入、重排和入库识图。Agent Zero 的重载只留在未来，不进单元。

拒绝给角色补上没有出处的温度、`top_p` 或 `max_tokens`。省略字段，让供应商用自己的默认。

拒绝聊天原图先走 `vision_ocr.py`。那条路留给资料入库。聊天模型若是不支持 Vision 的 `deepseek-v4-pro`，就拒绝这张图，不偷偷改走智谱。

拒绝用本地 `uuid4` 充当邮件接受编号。现码 `mail_entry.py` 就是这样做的，U7 要改掉。

拒绝抄 DS-Bot 按允许名单跳过确认后直接发信。

拒绝在传输没选定之前接上真实 SMTP 或 Gmail 发送范围。状态机先用假传输合入，活体发送另说。

拒绝用日程标题充当日历幂等键。本仓库已有 `extendedProperties.private.requestId` 和返回的 event id。

拒绝把「拿到 event id」说成完整去重。另一张卡、另一个标题不会因此合并。

拒绝保留 `outcome=placed` 且回声为空仍算已下单。公开回包必须带回 `idempotency_key` 与 `merchant_order_id`。

拒绝用哈希代替武装号。执行端要和自己 arm 的值比较，哈希对不上自己的记录。

拒绝在没有连接器、浏览器关口也未通过时继续调用 compose 的 place。

拒绝把现有 errand-compose 的 Playwright 说成浏览器自治代理，也拒绝按 Poke 或 Vesta 的案例选定依赖。

拒绝抄 OpenClaw 的自动复核和 `allow-always`、FullDots 的规则跳过、Rowboat 的自动允许。

拒绝让共享 `DESKTOP_PACKAGE_TOKEN` 在设备表落地后继续有效。留后门就没有单独作废。

拒绝由网页构造 `PackageProof` 或在 `ChatRequest` 里加一个会生效的 desktop 字段。

拒绝为了让 `.env.example` 好看而修改代码默认模型或重建向量。只加「不填则用代码默认」的说明。

拒绝把 32000、最近十轮、普通轮不回放思考、保留智谱写成缺陷。

拒绝按旧的 14 个刀名再拆一次已经合入的工作。确认单、收件箱、arm、代际拒绝迟到完成，都直接用。

拒绝把 `completed` 事件再 `open_alongside` 改成禁止。邮件本来就不开新回合。非邮件另开任务是现规则，和多任务不冲突。

拒绝在这一轮发真实邮件、写外部日历或下真实订单来证明设计。各单元用假传输验收。整体活体留在全部合入之后。
