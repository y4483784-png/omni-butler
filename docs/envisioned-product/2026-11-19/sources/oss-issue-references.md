# 开源对照与问题页修订稿

这份稿给父级用来改原来的「已知问题与建议方案」页。本轮没有改那一页，没有改仓库，没有开拉取请求，没有部署，没有发外部消息，也没有跑测试。

发布版远端 `origin/release` 与本地 HEAD 都是 `d7710d687d147aeeda502d412454ab8a3829af66`，2026-11-11 16:01:11 +0800，说明 `feat(desktop): open a file from the install package (#92)`。拉取请求 93 在 GitHub 上仍是 OPEN，`mergedAt` 为空，头 `b41e74dcc0ca8c4800d4fd5861957e7d9445fcaf`，分支 `cursor/ev-net-responses-5880`，更新时间 2026-11-12。GitHub 没有「暂停」字段。暂停是产品说法，不是合并状态。主现状一律按 release，不按 PR 93。

下面凡写「提议」的，都还不是批准实施。Flash 不因能上网就升到 Pro、边做边聊、真实邮件接受、日历回执、订单对账、桌面逐步确认、设备单独作废，都按你已经定过的方向来找现成机制。32000、最近十轮、普通轮不回放思考、保留智谱本身，仍不记成缺陷。

## 1. 目录范围

公开接口是 `GET https://my-tierroom.party/catalog.json`，说明在 `https://my-tierroom.party/api.md`。无需登录。返回的是已发布档案，不是独立实测。档案里的功能「yes」多数注明来自 README，不能当成源码已接线。

这次拉到的 `catalog.json` 有 95 个档案。

- 54 个带仓库链接。
- 41 个没有仓库链接，是托管产品或未公开源码。没有仓库的包括 Omnibots、Pally、Cue、OpenAI Dots、Grok Bot、Muse Bot、Instinct、Orchid、Gemini Spark、Hark Pro、Kylon、Tamekoshi、agentes.chat、Notis、Martin、Gumloop、Zapia、Brea、Caddy、Folk、Ollie、Luke、Orbits、Claude Duet、Turnstone、HireAlpha、Alfi、Today AI、Tomo、szn、Boba、Migoo、Persona、Crew、Bots · AraraHQ、Kyku、OrionSpace、Tob、Zé Bot、Mexerica、Tasklet。

探索页 `https://my-tierroom.party/explore?view=list` 另有一节「Not considered」。那些名字不在这 95 条里。其中 OpenClaw 被编辑标成通用运行时，不在助手目录。nanoMuse 被标成设置过重。这两条不在目录里，不等于没有公开仓库。本轮单独核过，见下文。

目录功能勾选只用来排队，不用来证明实现。本轮真正读过调用链的仓库写在第 2 节。其余带仓库的档案只停在目录层，标成未对本机制深读。

### 点名候选

| 名字 | 目录 | 仓库 | 许可 | 成熟度 | 本轮 |
| --- | --- | --- | --- | --- | --- |
| SuperBot | 在，id `superbot` | https://github.com/davidondrej/superbot | MIT | prototype | 读过。`652e100fc3412239fd5a07ca7bb8566213c5b6fa`，2026-11-15。源码检索里的批准是绕过沙箱的参数，没有邮件、日历、关联号、设备哈希 |
| OpenSuperBot | 在，id `opengrokbot` | https://github.com/wolfqing/OpenSuperBot | MIT | beta | 读过批准。`f0726d772047cc5211fec9a3f136539bf28064de`，2026-11-17 |
| Tucano | 在，id `tucano` | https://github.com/eduwxyz/tucano | Apache-2.0 | released | 只在目录。功能勾选很多。本轮没有打开它的排队或看门狗源码。未读不等于没有 |
| FullDots | 在，id `fulldots` | https://github.com/asasemahmed/FullDots | MIT | prototype | 读过逐步批准。`e625654e779149738847ea286fa21a3d5d43b803`，2026-11-17 |
| Grokked Bot | 在，id `grokked-bot` | https://github.com/alnutile/grokked-bot | 仓库上没有 LICENSE | unknown | 读过检索。`1b0400dedf2bb2731247c5289641d8e3747876ed`，2026-11-04。没有可借用的 A–E 实现。无许可证，不要拷贝它的代码 |
| Open Super Agent | 在，id `open-super-agent` | https://github.com/kadaliao/open-super-agent | MIT | prototype | 读过设备链接哈希。`3a0303559c7dc3d3b83142e59da636c872df6e65`，2026-11-18 |
| Rowboat | 在，id `rowboat` | https://github.com/rowboatlabs/rowboat | Apache-2.0 | released | 读过 Harbor 批准记录。`c37ffda3b002499da5b0d76785cc7c3275be1a7f`，2026-11-17。没有读到邮件发送或日历插入 |
| Agent Zero | 在，id `agent-zero` | https://github.com/agent0ai/agent-zero | 文件写 MIT，GitHub API 为 NOASSERTION | released | 读过嵌入切换与 SMTP。`2b0b3a416d80638379dc575798a4174ab4d3e1d9`，2026-11-17 |
| Hermes Agent | 在，id `hermes-agent` | https://github.com/NousResearch/hermes-agent | MIT | released | 读过工具环、搜索、用量、看门狗、排队。`0e8e42164063bf426af328ef94e2001dcaf9db1f`，2026-11-18 |
| nanoMuse | 不在 95 条里 | https://github.com/nano-muse/nanoMuse | GPL-3.0 | 未用目录成熟度 | 公开仓库存在。`5020df29cf717208b84d7014856d0846f4794d0f`，2026-11-18。只借用机制说明，不把 GPL 代码搬进本仓库 |
| MausBot | 在，id `openmausbot` | https://github.com/milind-soni/OpenMausBot | Apache-2.0 | beta | 读过设备登记。HEAD `35d044295697ef2b79b889461af6902c85a1b456`。旧钉 `083fd7b51405e6d0c01e189b2f65ab0322509b5a` 的哈希与单独作废仍在 |
| DS Bot | 在，id `deepseek-bot` | https://github.com/Fei2411/DS-Bot | Apache-2.0 | beta | 读过改向与邮件。HEAD `5e9b7da8882da3b68d8a0624342708c2150a9156`。旧钉 `71d344bf193f3f18185f25de3d8e5c483046910a` 的改向仍会取消正在跑的模型调用 |
| OpenCode | 不在这 95 条助手目录 | https://github.com/anomalyco/opencode | MIT | 默认分支 `dev` | 读过排队。HEAD `79bd2c6f11fd5ad011dbe5a1c31343c46e1e5294`，2026-11-18。旧钉 `3d308eae2e68dc39dfc12aba9dd408f9ce1cc0a1` 的 `queue.ts` 与 HEAD 是同一份 blob |
| OpenClaw | 探索页标为未纳入助手目录 | https://github.com/openclaw/openclaw | MIT | 通用运行时 | 读过执行批准。HEAD `069c3e2b02bcdac1f9030e4a4eb6d021e2607176`。旧钉 `c42515649217cb221ffa3beaf577d0320f0ff254` 的生命周期文件与 HEAD 文本相同 |
| DeepSeek Harness | 不在助手目录，是官方工具库 | https://github.com/deepseek-ai/deepseek-harness | MIT | 官方 | 读过独立搜索。HEAD `ca33fd6843b205e279c7ccb6cb73a472c78f5760`，2026-11-17。旧钉 `e8f7b93a344964de3dca1ace9e72b6e4d6de87cb` 仍在，HEAD 仍 POST `/v1/tools/search` |

许可约束：MIT 与 Apache-2.0 可以按机制用 Python 重写。GPL-3.0 的 nanoMuse 只作行为对照，不复制源码。没有 LICENSE 的 Grokked Bot 不复制源码。目录里还有 AGPL、PolyForm 非商业、FSL、改写过的 Apache。本轮推荐的接法都不依赖那些许可证的代码。

## 2. 本轮读过的源码边界

读的是函数和调用方，不是 README 承诺。测试文件只读不跑。没有活体请求。

已读调用链的仓库就是上表「读过」那些，加上本仓库 release 上与各条问题直接相关的函数。Tucano、以及 54 个仓库里没点名的大多数，停在目录。不要把「本轮没打开」写成「那里没有」。

目录勾了 background、approvals、connectors 的，仍可能只是 README。下面每条只把读到函数的仓库写成参考。

## 3. 主清单

### M1 默认回答留在 Flash，上网走独立搜索

现状。release 的网页搜索是服务器另调智谱，回答仍走对话补全，默认 `deepseek-flash`。请求不带搜索工具。PR 93 才把带 web 的回答改到 Pro 的 Responses。那条未合并。官方文档写明 Responses 的 `web_search` 只在 Pro 上执行，Flash 会忽略。独立 Search / Fetch 与档位无关。这条产品改法仍未批准。

可借用的机制。DeepSeek Harness 在 HEAD `ca33fd68` 把搜索做成单独的 HTTP POST，不改对话模型。

- `DeepSeekSearchApiProvider.search`：https://github.com/deepseek-ai/deepseek-harness/blob/ca33fd6843b205e279c7ccb6cb73a472c78f5760/packages/web/web-deepseek-api/src/search.ts#L98
- 传输 `dispatchDeepSeekWeb`：https://github.com/deepseek-ai/deepseek-harness/blob/ca33fd6843b205e279c7ccb6cb73a472c78f5760/packages/web/web-deepseek-api/src/transport.ts#L116
- 默认基址 `https://api.deepseek.com/v1/tools`，再接 `/search` 或 `/fetch`。Fetch 在同目录 `fetch.ts`。
- 调用链：模型发出 `web_search`，工具执行调用 `ctx.web.search`，结果作为工具结果回到同一轮的下一步。`ReactLoopAgent.prepareRequest` 若有钩子改掉 provider 或 model 会抛错。对话模型保持进入这一步时的选择。
- 测试只读未跑：`packages/web/web-deepseek-api/tests/search.spec.ts`，断言 POST 到 `https://api.deepseek.com/v1/tools/search`。

Hermes `0e8e4216` 的 `web_search_tool` 也是另选搜索提供方，聊天请求仍用 `agent.model`。https://github.com/NousResearch/hermes-agent/blob/0e8e42164063bf426af328ef94e2001dcaf9db1f/tools/web_tools.py#L302

最小 Python 接法。在现有 `execute_web` 里，把智谱调用换成对 `/v1/tools/search` 的 POST，查询、条数、是否要正文分开传。结果仍作为证据交给同一次回合里的 `deepseek-flash`。不要为搜索改 `chat_model`。确认闸不在搜索这一步，搜索是读。写操作仍走卡片。

许可。MIT。

验收。寒暄仍是 Flash。「今天有什么新闻」能带回带链接的结果，回答模型名仍是 Flash。同一轮不再调用智谱网页搜索。无搜索的轮次不按 Pro 计。

仍要你定。是否批准用独立 Search / Fetch 替换 PR 93 的 Pro Responses。未点头前不合 PR 93。Harness 不负责「必须用 Flash」这个产品决定，那仍是你的选择。

### M2 工具循环、流式续写、有工具时回传思考

现状。release 的 `_refuse_outbound` 拒绝带工具或思考内容的出站请求。工具在服务器上先跑完，再请模型写回答。生产聊天不把 `tools` 和工具结果送回模型。普通多轮不回放思考，按官方规则是合理现状，不是缺陷。带工具的后续轮若走聊天补全，官方要求完整回传 `reasoning_content`，否则可能 400。这条接入还没做。

可借用的机制。优先 Hermes，它是 Python，和本仓库同语言。

- 一轮循环：`_run_conversation_turn` https://github.com/NousResearch/hermes-agent/blob/0e8e42164063bf426af328ef94e2001dcaf9db1f/agent/conversation_loop.py#L1617
- 工具轮：`run_tool_round` 先校验，把带工具调用的助手行写入会话，再执行。写入失败则不执行工具。https://github.com/NousResearch/hermes-agent/blob/0e8e42164063bf426af328ef94e2001dcaf9db1f/agent/turn_tool_round.py#L50
- 思考回传：`build_assistant_message` 保存 `reasoning_content`。DeepSeek 等路线在有工具调用却没有思考文本时垫一个空格，空字符串会被拒。https://github.com/NousResearch/hermes-agent/blob/0e8e42164063bf426af328ef94e2001dcaf9db1f/agent/chat_completion_helpers.py#L1617
- 下一次请求前再贴回去：`message_sanitization.py` 约第 946 行，`turn_api_request.py` 约第 115 行。
- 测试只读未跑：`tests/agent/test_deepseek_reasoning_content_echo.py`。

Harness 的官方 Messages 路径用的是 `thinking` 块加签名，不是字段名 `reasoning_content`。`serialize.ts` 把思考和 `tool_use` 放在同一条助手消息，下一条用户消息放 `tool_result`。https://github.com/deepseek-ai/deepseek-harness/blob/ca33fd6843b205e279c7ccb6cb73a472c78f5760/packages/llm/llm-deepseek/src/serialize.ts#L27

插件可以在工具执行前退回一段错误并跳过执行。那是插件否决，不是本产品的确认卡。写日历、发信、下单、本机动作仍必须先出卡。结果不明不盲目重试，见 X1 至 X3。

最小 Python 接法。只在「这一轮真有工具调用」时，把非空 `reasoning_content` 和 `tool_calls` 一起存下，工具结果作为后续消息，再请同一个对话模型继续。没有工具的普通轮保持现在的不回放。`tool_choice` 为 required 或指定工具时，按官方要求先关思考或改用 auto。确认类工具在执行前停住，等卡片。

许可。Hermes 与 Harness 都是 MIT。

验收。一轮里能看见模型提出工具、应用执行、结果回填、再写成文。需要确认的写入不会在卡片前落地。带工具的多步不因缺思考回传而 400。

仍缺。生产上哪几类工具允许进这个循环，搜索与写操作的分界，流式输出在两步之间怎么接。这些是细则，方向可以沿 Hermes 的「先存再执行、失败则不执行」。不要整仓换成 Hermes。

### M3 按角色写清模型、接口、上下文、用量与多模态

现状。这是策略缺口，不是已证明的缺陷。回答请求常省略 temperature、top_p、max_output_tokens，省略后用供应商默认。上下文 32000 与最近十轮是现行裁剪，注释里的智谱比例不是供应商窗口，也不记成 bug。识图现在在入库，不在回答请求里。应用并发和供应商额度不是同一口径。

可借用的机制。Hermes 把主对话和辅助任务拆开。

- 辅助任务 `call_llm(task=...)` 按任务解析 provider、model、温度、max tokens、reasoning。https://github.com/NousResearch/hermes-agent/blob/0e8e42164063bf426af328ef94e2001dcaf9db1f/agent/auxiliary_client.py#L7856
- 主回合仍用 `agent.model` 与 `agent.max_tokens`，辅助任务不改主模型。
- 用量：`record_response_usage` https://github.com/NousResearch/hermes-agent/blob/0e8e42164063bf426af328ef94e2001dcaf9db1f/agent/turn_usage.py#L143 。没有 usage 字段时只计调用次数，不计 token。
- 图片：模型支持视觉就把图片留在请求里，否则先走单独的 vision 任务换成文字。https://github.com/NousResearch/hermes-agent/blob/0e8e42164063bf426af328ef94e2001dcaf9db1f/agent/vision_message_prep.py#L188

测试。`test_turn_usage_log_line.py` 存在，本轮未读完，未跑。

最小 Python 接法。给现有角色各写一行配置：模型名、接口、思考开关、输出预算、温度。省略也写明「用供应商默认」。用量先存供应商返回的 usage，是否展示给用户另定。识图是否进入回答请求另定。不要为接全字段去接用不到的参数。

许可。MIT。

验收。配置里能对出每个角色的实际参数。裁剪写明不是 1M 窗口。若展示用量，数字来自供应商返回。

仍要你定。各角色默认值、用量是否可见、回答请求是否带图片。不阻挡 M1。

### M4 智谱网页搜索可清。嵌入、重排、识图要先有替代再迁索引

现状。保留智谱本身不是缺陷。已定的是同一轮不走智谱网页搜索。嵌入、重排、识图仍是智谱，直到你另选。

可借用的机制。清网页搜索不需要开源样例，接 M1 的独立搜索即可。嵌入切换可以看 Agent Zero，不是整仓替换。

- 配置槽 `embedding_model` 与对话模型分开：`get_embedding_model_config` https://github.com/agent0ai/agent-zero/blob/2b0b3a416d80638379dc575798a4174ab4d3e1d9/plugins/_model_config/helpers/model_config.py#L825
- 保存时若嵌入配置变了，发出 `embedding_model_changed`，再 `memory_reload()`。https://github.com/agent0ai/agent-zero/blob/2b0b3a416d80638379dc575798a4174ab4d3e1d9/plugins/_model_config/helpers/model_config.py#L200
- 测试只读未跑：`tests/test_embedding_model_preload.py`，未读完全部断言。

重排。在 Hermes 的 `agent/`、`tools/` 文件名、Agent Zero 路径、Rowboat 路径上搜 `rerank`，没有读到重排提供方切换。这是这几处未找到，不是全网没有。

最小 Python 接法。先删聊天路径上的智谱网页搜索调用和死引用。嵌入、识图保持现状，直到选定替代。选定后换配置并重建向量，记下模型名和版本。维度相同也要重建。不要一次删光智谱。

许可。Agent Zero 文件写 MIT。

验收。聊天路径不再调用智谱网页搜索。检索和识图仍可用，或在已批准方案下迁完并重建索引。

仍要你定。替代谁、何时迁。

### M5 示例文件与代码默认不一致

本轮对 release 重新看过，不再是「未重证」。

`.env.example` 第 25 行 `EMBEDDING_MODEL=embedding-3-pro`，第 31 行 `VISION_MODEL=glm-4v-flash`。`backend/app/core/config.py` 第 36 行默认 `embedding-3`，第 39 行默认 `glm-4.6v`。没配环境变量时跑的是代码默认。示例第 109 行的并发名单也写着 `embedding-3-pro` 和 `glm-4v-flash`。

不需要开源参考。

最小改法。让示例与代码默认一致，或在示例写明不填则走代码里的哪个名字。若真改嵌入模型，与 M4 一起写重建索引。这是提议，尚未改仓库。

验收。未配环境变量时，实际模型名与示例说明一致。

### M6 「这次别联网」要不要硬关闭

现状。这句话现在只交给模型。允许名单里有 web 时，模型没遵守仍可能搜。已定方向是默认能联网，不要地球图标开关。是否为本句加硬关闭，还没定。

本轮在已打开的文件里没有找到「只从这一次请求的工具名单拿掉 web，下一句恢复」的开关。Hermes 有会话级或配置级的工具集刷新，运行中的会话会冻住工具集。那不是单句临时关闭。搜过的词包括 `web_search`、`disable`、`disabled_toolsets`、`search_enabled`。未在这些文件找到，不等于别处没有。

最小 Python 接法，仅当我们对做硬关闭。这一轮组允许名单时去掉 web，下一轮恢复。不要做成常驻开关。未批准前不要加。

验收。与最终规则一致。要么文档写明只靠提示，要么这一轮确实不发出搜索请求。

仍要你定。做不做。

## 4. 边做边聊

### T1 进行中可以继续聊，新消息不自动停掉旧工作

现状。release 上，这一会话忙时页面拒绝再发。服务器 `begin_turn` 会停掉同一会话里还开着、且不是确认单标记的回合。你已定的目标是去掉常驻停止按钮，进行中可发，新消息不自动杀旧工作。只藏按钮、每句新话仍停上一轮，不是目标。

可借用的机制。OpenCode 把排队和改向分开，排队不打断正在跑的执行。

- 默认分支 `dev`，HEAD `79bd2c6f11fd5ad011dbe5a1c31343c46e1e5294`。
- 排队视图：https://github.com/anomalyco/opencode/blob/79bd2c6f11fd5ad011dbe5a1c31343c46e1e5294/packages/app/src/session/composer/queue.ts
- 调用方是 `createSessionComposerController`。`delivery: "queue"` 只写入收件箱，不调用 interrupt。空闲边界才取出一条。正在跑时再唤醒只记一个门铃。
- 显式改向是把那一行改成 `delivery: "steer"`，在下一步边界注入。当前这一次模型请求不中止。
- 显式移除是删掉还没处理的那一行，正在跑的执行不动。
- 真正的停止是另一条 `execution.interrupt`。
- 注意：设置默认是 steer，不是 queue。https://github.com/anomalyco/opencode/blob/79bd2c6f11fd5ad011dbe5a1c31343c46e1e5294/packages/app/src/settings/model.tsx 一类的 `followUpBehavior: "steer"`。照搬默认会在下一步把新话注入同一轮，虽然不杀当前请求，也不是「先排队」。
- 测试文件找到了，本轮未读完也未跑：`queue-machine.test.ts`、`queue.test.ts`、`session-queue.spec.ts`。

Hermes 的会话提交只接受 `intent == "queue"`，写入后不打断。https://github.com/NousResearch/hermes-agent/blob/0e8e42164063bf426af328ef94e2001dcaf9db1f/gateway/session_authority.py#L746 聊天适配器另有 `busy_input_mode`，注释写默认是 interrupt。那条默认不要抄。

DS-Bot 的 `maybeCut` 在私聊、模型还在跑、没有工具在飞时，直接 `agent.cancel`。旧钉和 HEAD 都这样。https://github.com/Fei2411/DS-Bot/blob/5e9b7da8882da3b68d8a0624342708c2150a9156/ds-bot/src/host/steer.js#L52 这是反例，不要当默认。

最小 Python 接法。新消息先落成队列行，不调用现在的「停掉其他开着的回合」。只有人明确选择停止或改向，才动正在跑的那一轮。确认单标记的回合本来就不会被新消息停掉，这条保持。

许可。OpenCode 与 Hermes 为 MIT。DS-Bot 为 Apache-2.0，只作反例。

验收。长任务进行中可以再发一句，旧工作不被自动标成停止。界面没有常驻停止按钮。明确的取消和停滞看门狗仍在。

仍缺。四类归类见 T2。OpenCode 的排队没有第二张确认卡。我们仍要求取消和改向要人确认。

### T2 补充、改向、独立话题、取消

现状。`begin_turn` 会停上一轮普通回合。确认卡出现后页面可以再发，下一句仍会停掉那个等人的代际。这和 T1 冲突。

参考能支持的只有三段，没有四类分类器。

- 排队且不杀当前请求：OpenCode `delivery: "queue"`，见 T1。
- 显式改向且不中止当前模型请求：OpenCode steer，以及 Hermes `AIAgent.steer()`。https://github.com/NousResearch/hermes-agent/blob/0e8e42164063bf426af328ef94e2001dcaf9db1f/agent/interrupt_control.py#L310 Hermes 的 `redirect()` 会中止当前模型请求，不要当补充。
- 显式删掉还没跑的一句：OpenCode `session.inbox.cancel`。
- Hermes 里搜 `supplement`、`new topic`、`new_topic`，代码搜索为 0。现成的类是 queue、steer、redirect、interrupt。`/new` 是斜杠命令，不是句子分类器。

最小 Python 接法。默认新消息进队列。只有归成改向并经确认，才在下一步边界注入。取消必须带对象：本轮生成、整个任务、或某一张卡，并让人看见范围。不明时先问，不默认杀。

许可。MIT。

验收。四类在测试里行为不同。取消不伤未点名的任务和卡。

仍要你定。四类怎么判定，不明时问什么，和 T5 怎么共用。参考项目不会替你做这个分类。

### T3 取消要指明对象。断线不等于取消。用量要诚实

现状。输入框里打「停止」不会被当成取消。停止按钮或浏览器断开，停的是本轮状态和这条连接。release 上没有单独的供应商取消调用。去掉按钮后，若仍把断线当取消，会更难看见。

可借用的机制。

- Hermes `attach` 增加订阅者，`detach` 只去掉这一路。还有别的订阅者时，运行继续。最后一个订阅者离开才会排一次「等队列空闲再关」，期间重新连上可以取消这次关闭。https://github.com/NousResearch/hermes-agent/blob/0e8e42164063bf426af328ef94e2001dcaf9db1f/gateway/session_authority.py#L373
- 真正的中止是单独的 `interrupt`，还要带当前代际。代际已经结束的停止不会伤到下一轮。
- 用量：Hermes 在回合结束且结果带 `interrupted` 时，把逻辑调用记成 cancelled。没有读到「套接字中止之后再补记半次补全用量」的专门函数。OpenCode 在步骤失败且记录里有 usage 时会记下。这是读到的边界，不是完整账单。
- 迟到片段：没有读到「取消后丢掉已经缓冲的 token」的专用函数。最近的是代际栅栏，以及 redirect 把已完成的消息留下。

R-B3 的偶发 artifact/done、缓冲片段冲出，本轮没有重读 `stream_chat` 的缓冲路径。仍标未重证。

最小 Python 接法。SSE 断开只减订阅，不把任务标成取消。明确取消走 T2，并丢掉此后才到的片段，不再发后续模型请求。供应商已经接受的用量照记，记不住的部分标成未知，不要写成 0。

许可。MIT。

验收。断线不把任务暗中标成已取消。迟到片段不再显示成继续回答。供应商仍计费时，记录与返回一致，或标明未知。

仍缺。DeepSeek 这条请求能否真正撤回，本轮没有查供应商取消接口。标成未查，不是不存在。

### T4 看门狗按进展，不按创建时间砍还在输出的一轮

现状。本轮重读了 release。`watchdog.py` 的 `_silence` 用 `now - created_at - 扫表空档`。满 20 分钟且状态是 running 就停。`load_runs` 读出全部 `TaskRun` 再在内存里滤。waiting_person 不在停止集合里。仍在输出但创建已超过 20 分钟的一轮会被停。这和页面上的已知行为一致，本轮复核后仍在。

可借用的机制。Hermes 用最近活动，不用创建时间。

- `session_activity.py` 写 `last_activity_at`，最多每 60 秒落一次。
- `session_stall.py` 从这份快照算空闲，并写明不用回合开始时间和入站时间。https://github.com/NousResearch/hermes-agent/blob/0e8e42164063bf426af328ef94e2001dcaf9db1f/gateway/session_stall.py
- 默认空闲 300 秒只通知人去 `/stop` 或 `/new`，不杀进程。0 表示关闭通知。
- 测试文件在：`tests/gateway/test_session_stall_watchdog.py`、`tests/agent/test_session_activity.py`。本轮未读完，未跑。

最小 Python 接法。每次真正写出 token 或工具进展时更新 `last_progress_at`。看门狗比较这个时间。waiting_person 与等外部查询不走这条停滞。硬超时保留，但是停滞超时，不是创建超时。Hermes 只通知不杀。我们是否在停滞后再停，仍要你定。通知本身不够，若你要安全停下，停滞之后的停止要单独写。

许可。MIT。

验收。超过 20 分钟但仍在输出的一轮不停。没有进展的 running 仍可被安全停下。waiting_person 不当成停滞。

仍缺。心跳写在哪一列、停滞多久、看板是否落盘。落盘见 R4。`load_runs` 扫全表是另一件事，不靠这个心跳解决。

### T5 同一会话两个窗口不要互相打断

现状。页面忙碌在浏览器内存里，两个窗口不共享，都能发送。后到的 `begin_turn` 会停先发的普通回合。换到另一会话不会中止当前这一轮。本轮没有新做多窗口活体。

可借用的机制。就是 T1 的服务端队列，加上 T3 的多订阅者。Hermes 两个客户端 attach 同一会话时，一边断开不取消运行。第二句若走 queue，不 interrupt。

最小 Python 接法。忙碌与「有一轮在跑」放在服务端回合状态上，两个窗口读同一行。第二窗口的话按 T2 归类，默认排队。

许可。MIT。

验收。两个窗口都能看见同一任务还在跑。第二窗口的补充不把第一窗口的工作标成停止。

仍缺。与 T2 一起定。本轮没有另找专门的「多窗口产品」仓库，因为会话权限和排队已经覆盖这段。

## 5. 执行闭环

人的确认保持在写入之前。参考里的自动允许、始终允许、记住规则，都不抄。

### X1 邮箱服务接受之后才显示已发送

现状。release 的 `commit_send` 在本进程里生成 `uuid4`，写入 `MailAcceptance`，再 `note_external_success`。没有把信交给 Gmail 或 SMTP。`note_external_success` 要求对方编号非空，且确认单已是 written、编号一致。邮件路径会调它。日历确认路径本轮没有找到调用。本地接受行不是发出。

旧参考更正。DS-Bot 不能再当成「邮箱已接受才标记」的完整样例，但本轮把它的发送函数读清了，比「完全没有发送」更准确。

- `mail_send` 先存 pending。用户选 Send 之后才 `await session.send`。
- `transport.js` 的 `send()` 等待 `smtp.sendMail` 或 Gmail `users.me.messages.send` 返回，再带 `messageId`。抛错则 failed。
- 它不读 nodemailer 的 accepted 与 rejected，也不等投递回执。返回成功只是传输调用返回。
- 若机器人不在私聊，且在 `noDraftFor` 里，或每个收件人都在允许名单，会跳过确认卡直接发。这违反我们的确认规则，不要抄。
- 测试文件在 `ds-bot/test/connectors-mail-transport.test.ts` 一类路径。本轮未读未跑。

Hermes `EmailAdapter._smtp_send` 也是 `send_message` 返回后才 `success=True`。它的 Message-ID 在 SMTP 接受前就由客户端生成，不能当接受证明。没有确认单，第二次调用会再发。Gmail 脚本在 `users.messages.send` 返回 `id` 之后打印 sent，不落库，重试会再插一封。

nanoMuse `SendEmail.execute` 在 `smtp.send_message` 之后才说已发送，失败返回 fail。没有提供方编号，也没有未知先查。GPL，只作行为对照。

Agent Zero `send_email` 成功返回 None，失败返回错误字符串。没有编号，没有确认单。

没有任何一处同时做到：接受后才标记、失败照实、重复确认不发第二封、未知先查询。

最小 Python 接法。沿用现有确认单。人确认后 `consume`，再调 Gmail `messages.send` 或 SMTP。只有返回里的提供方 id 写入 `remote_request_id`，然后才 `note_external_success`。明确拒绝则不标记已发送，并按现有拒绝路径放开或保持不可重发，按你已定的「未知不重发」。超时或连接中断标未知，用已存的 id 去查，查不到就不发第二封。不要用本地 uuid 冒充接受编号。

许可。DS-Bot Apache-2.0，Hermes MIT。nanoMuse 不复制代码。

验收。只有服务端接受后才显示已发送。失败和未知不冒充。同一确认再点不产生第二封。

仍缺。用哪家邮箱、凭证放哪、未知时查哪一个接口。活体要另授权。

### X2 日历真实写入成功后再记 external_success，并且幂等

现状。`note_external_success` 已在 `event_inbox.py` 第 164 行。调用方本轮只看到 `mail_entry._seal` 和测试。日历确认 HTTP 仍没有接上。这和页面上的已定缺口一致，本轮复核后仍在。

可借用的机制只有「拿到远程 id 再报成功」这一段。Hermes `calendar_create` 在 `events.insert` 返回 `result["id"]` 之后才打印 created。删除按 `eventId`。第二次 create 会再插入。没有确认单，也不按 id 重入。https://github.com/NousResearch/hermes-agent/blob/0e8e42164063bf426af328ef94e2001dcaf9db1f/skills/productivity/google-workspace/scripts/google_api.py#L579

OpenClaw 里搜 `events.insert` 为 0。Agent Zero 没有读到日历插入。

最小 Python 接法。确认单消耗之后插入一次。把返回的 event id 写在这张单上，再 `note_external_success`。重入时按这个 id 获取或更新，不用标题当键。插入响应丢了，先按已存 id 查，不要再插入。失败不记 external_success。

许可。MIT。

验收。真实写入成功后，收件箱对这张单有且仅有一条对应的 external_success。失败不记成功。重复确认不产生第二个事件。

仍缺。确认路径上具体哪一行调用收件箱。指纹继续用现有确认单，不用标题。

### X3 武装号穿过公开边界，只对账不重下

现状。本轮重读了 release。`errands.py` 在下单前 `arm_remote_claim`，请求头带 `Idempotency-Key`。`services/errand-compose/server.py` 的 `_public` 只回 `outcome`、`order_id`、`told`、`clicks_after_wall`，不回武装号。`interpret` 若收到 `idempotency_key` 且与武装号不同，就不把「已下单」正文当成对上了。`outcome == "placed"` 且回声为空或相符时，仍做成 placed。所以公开边界洗掉号之后，靠 outcome 字段仍可能记成已下单。靠正文里的「已下单」且没有回声时，对不上。未知走 `_reconcile`，本轮没有把 lookup 再读成「会再下一单」。页面上的缺口仍然成立：洗掉号时可能对不上账。没有在已读的开源仓库里找到「公开回包仍带可对账的关联号，之后只查不重下」的现成订单实现。

附近但不是这件事的：nanoMuse 的投递出站用 `source_key` 在自己的 sqlite 里去重，钥匙不出公开边界。Agent Zero 的 `correlation_id` 是 websocket 跟踪。Rowboat 的注释写明 `postMessage` 没有幂等键。

最小 Python 接法。武装号继续只存在确认单和内部请求里。公开回包至少带回一个不含支付材料的关联句柄，或固定回 `idempotency_key` 的哈希。对账只认这个句柄加 `order_id`。对不上就报未知，不自动再下。`_public` 仍要丢掉 password、pan、cvv、cookie、secret、pay、login。

许可。没有可整段搬入的实现。自写这一小段。

验收。浏览器通道的已下单能对上原武装。对不上时报告未知，不自动再下单。

仍缺。公开字段用原号、哈希、还是只靠 order_id。本轮没有活体下单。

### X4 按实际步骤出动作卡，确认后执行再继续

现状。release 已有确认后才 `perform`。安装包上是两个测试按钮，打开固定文件 `/tmp/ev-desktop-probe.txt`，或运行 `/usr/bin/true`。网页聊天不渲染 desktop 卡。这和「按真实步骤出卡」的产品目标仍有距离。本轮对按钮和固定路径复核过，与上一轮结构图一致。

可借用的是「卡片写明这一下要做什么，确认一次，然后同一任务继续」。不是自动允许。

- OpenClaw `ExecApprovalLifecycle.settleLocalEntry` 第二次结算直接返回 false。`prepareExecApprovalConsumption` 只接受 `allow-once`。卡片显示 `request.command`。同一网关调用在决定之后进入 `runExecProcess`。https://github.com/openclaw/openclaw/blob/069c3e2b02bcdac1f9030e4a4eb6d021e2607176/src/gateway/exec-approval-lifecycle.ts#L418
- 同一条路径还有自动复核和 `allow-always`。不要抄。低风险可以不经人就 allow-once。那违反我们的确认规则。
- FullDots `approvalGate` 用「谁要做什么」加参数指纹。`decide` 之后用同一 `threadId` 恢复，并说只做那一个动作。`consume` 把 approved 改成 consumed，再来一次得不到。https://github.com/asasemahmed/FullDots/blob/e625654e779149738847ea286fa21a3d5d43b803/src/server/approval-gate.ts#L609
- 恢复方式是再开一轮模型，告诉它去做。门本身不执行邮件或本机命令。规则模式还可以跳过门。跳过的部分不要抄。
- OpenSuperBot `resolveApproval` 只更新 pending。第二次返回 null。然后 `startTurn` 同一线程。匹配到记住的规则会直接标成已批准。邮件类目的记住规则不要抄。https://github.com/wolfqing/OpenSuperBot/blob/f0726d772047cc5211fec9a3f136539bf28064de/gateway/src/approvals.ts#L242
- Rowboat Harbor 把批准存成 open，人决定后 `allow_once` 等，第一次决定生效，然后把调用从 waiting 设回 working。Harbor 自己不执行工具。https://github.com/rowboatlabs/rowboat/blob/c37ffda3b002499da5b0d76785cc7c3275be1a7f/apps/harbor/packages/server/src/core/invocations.ts#L419 测试只读未跑：`apps/harbor/packages/server/test/approvals.test.ts`。
- DS-Bot 的 allowed-once 仍只说明「一次允许」，不生成按步骤的卡。

最小 Python 接法。计划里的每一步本机动作写成一张卡，卡上的字就是即将执行的那一步。沿用 `ScheduleConfirmClaim`，确认后消耗一次再 `perform`，然后回到同一回合的下一步。不要自动复核，不要始终允许，不要记住规则。测试按钮可以留下当探测，产品任务不要再只出那两个固定探针。

许可。OpenClaw、FullDots、OpenSuperBot、Rowboat 为 MIT 或 Apache-2.0。

验收。测试任务里的卡与即将执行的动作一致。未确认不执行。确认后能继续下一步。同一令牌第二次不执行第二遍。

仍缺。交互文案和「一步一张」的切分规则。活体要授权。参考都不是桌面文件打开器的完整产品。

### X5 每台设备单独登记，可单独作废

现状。release 仍是共享 `DESKTOP_PACKAGE_TOKEN`。`ChatRequest` 自报 desktop 仍会被拒。网页登录不是本机许可。

可借用的机制。OpenMausBot 的登记表，不搬手机配对和端到端。

- HEAD `35d044295697ef2b79b889461af6902c85a1b456`。旧钉 `083fd7b` 的哈希与 `revoke(id)` 仍在。
- `authenticate` 对来令牌做 SHA-256，用恒定时间比较。`revoke(id)` 只删这个 id，保存失败会回滚。
- 落盘字段含 `id`、`tokenHash`、名字、时间、权限。`list()` 不返回哈希。
- 测试只读未跑：`companion/test/devices.test.ts`，标题包括发出的令牌能通过认证且从不落原始令牌，作废一台不影响另一台。
- 另一份同类实现：Open Super Agent `osa/computer/link.py` 的 `check` 与 `revoke(lid)`。https://github.com/kadaliao/open-super-agent/blob/3a0303559c7dc3d3b83142e59da636c872df6e65/osa/computer/link.py#L42 它还有按设备字符串删掉旧行的行为，不要抄，以免误伤别的登记。同一文件附近的自动允许规则也不抄。

最小 Python 接法。设备表只存令牌哈希。认证用 `hmac.compare_digest`。设置里按 id 作废。作废一台不影响其他行。这张凭证只证明「这是登记过的桌面」，不代替确认单。网页端自报 desktop 继续拒绝。

许可。OpenMausBot Apache-2.0。Open Super Agent MIT。

验收。新设备登记后才能做本机动作。作废一台不影响其他设备。网页冒充 desktop 仍被拒。

仍缺。配对时谁颁发令牌、存在哪张表、设置页放哪。比 X1 至 X3 更接近可写，仍不是已经落地的补丁。

## 6. 历史行

本轮没有把这些升成主清单新缺陷。标了「本轮复核」的，是对 release 对应函数又看了一遍。

| 编号 | 本轮 | 结论 |
| --- | --- | --- |
| R-B3 | 未重读缓冲与偶发 SSE | 仍待复核。已知的断线不等于取消已写进 T3 |
| R1 | 未重读形式模型与 Actions | 仍是历史残留，不当合并阻断，也不当已关闭 |
| R2 | 复核了 `event_inbox._route_act` | 点名的代际若是 completed，非邮件来源会 `_open_task`，其中调用 `open_alongside`，从而再开一个 running。邮件来源在 `_open_task` 里直接记 received，不再开回合。第 239 至 243 行，第 430 至 443 行。旧记仍符合当前源码。与 T1 的多任务一起看，本轮不改产品 |
| R3 | 部分 | `list_memories` 在 `memory.py` 第 587 行把空用户号收成 1。`settle_pending` 仍在。放弃是否改原行、省略 generation 是否仍抽取，本轮没有逐行重读。仍待复核 |
| R4 | 复核了时钟与扫表 | 时钟已并入 T4，创建时间逻辑仍在。`load_runs` 仍 `query(TaskRun).all()`。进程内账本是否重启即丢，本轮没有再追内存对象的生命周期，标部分未核 |
| R5 | 未重读全部边界测试 | 服务端执行面仍是 WEB。与 X4、X5 相关。`route_intent` 只在 `workflow.py` 第 877 行定义，本轮没有找到调用方 |
| R6 | 未改图 | 图的表达问题。本轮不覆盖已有流程图 |
| R7 | 未逐条重证 | 空 403、arm 之后崩溃保持未知、生产工具不填 `unknown_write`、确认句泄露，仍待做 X1、X2 时再看。`UnknownWrite` 类型还在，测试会构造它。本轮没有证明生产工具已经会填它 |

## 7. 旧说法怎么改

- 「还没有方案或参考的只有邮件、日历、办事三项」维持作废。本轮之后，完全没有读到对口函数的主项只剩 M6 的单句硬关闭，以及 X3 的「公开回包带回关联号」。其余主项都有一段读过的源码，但都只覆盖机制的一段，不能写成已经解决。
- DS-Bot 邮件参考的撤回保持。更准确的说法是：它会等 SMTP 或 Gmail 发送调用返回再标 sent，不查投递，也可能跳过确认卡。不能当本产品的样例。
- M5「未对最新源码重证」改为已复核，不一致仍在 `d7710d68`。
- OpenCode 排队文件在新 HEAD 上与旧钉 `3d308eae` 是同一 blob。可以继续引用，并加上新 HEAD。
- Harness 搜索文件在新 HEAD `ca33fd68` 仍 POST `/v1/tools/search`。旧钉 `e8f7b93a` 仍存在。
- OpenMausBot 设备哈希在新 HEAD 仍在。可以继续引用 `083fd7b`，并注明 HEAD `35d04429`。
- OpenClaw 执行批准生命周期在 HEAD 与 `c4251564` 文本相同。同一路径含自动允许，引用时要写明不抄那一段。
- 目录里的 yes 不是源码证据。Tucano 本轮未打开。
- PR 93 仍未合并。GitHub 状态是 OPEN。不要写成已暂停的平台状态。

## 8. 仍是决定，还是已经能写的细节

仍要你点头，不能当成实施批准。

- M1 是否改成 Flash 加独立 Search / Fetch，从而不合 PR 93 的现方案。
- M3 各角色参数、用量是否可见、图片是否进入回答。
- M4 嵌入、重排、识图换谁、何时重建。
- M6 做不做单句硬关闭。
- T2 四类怎么判定。
- T4 停滞之后是只通知，还是再安全停止，以及多久算停滞。
- X1 用哪家邮箱。
- X3 公开回包保留哪一种关联句柄。

已经有最小接法、仍未写进仓库的细节。

- M2 只在有工具的轮次回传非空 `reasoning_content`，先存再执行。
- M5 对齐两个模型名的说明。
- T1 新消息默认入库排队，不调用停掉上一轮。
- T3 断开只减订阅。
- T4 用 `last_progress_at` 代替只看 `created_at`。
- X1 提供方 id 返回之后才 `note_external_success`。
- X2 日历插入返回的 event id 再记外部成功，重入按 id。
- X4 一卡一步，消耗一次，回到同一回合。不抄自动允许。
- X5 只存哈希，按 id 作废。不代替确认单。

没有任何一条可以写成「参考项目已经把 Omni-Butler 的问题解决了」。

## 9. 建议的同一页结构

不要另起一页。沿用 M1 至 M6、T1 至 T5、X1 至 X5、R-B3、R1 至 R7，以及 27 项去向表。

建议父级只改这些字段。

1. 页眉时间改成这次研究之后的时间。摘要仍写 release `d7710d68`，PR 93 未合并。
2. 每条主项保持五段：问题与证据、改法、开源参考、尚缺、验收。开源参考改成上面的仓库、全长 SHA、函数链接，并写明它只支持哪一段。
3. 「还没有完整的已核实源码参考」表按第 7 节重算。M5 不进该表。X5 仍可写参考较完整。M6 与 X3 的关联号回传保持「已查范围没有对口」。M1、M2、T1、T2、T3、T4、T5、X1、X2、X4 改为「有一段已读源码，不是完整方案」。
4. M5 的徽章从「未重证」改成「已对 d7710d68 复核，不一致仍在」。
5. R2 的说明加上「本轮复核，completed 仍会 open_alongside」。不要把它升成主缺陷。
6. 来源列表补上本文件用到的永久链接。目录来源写 `https://my-tierroom.party/catalog.json` 与 `https://my-tierroom.party/api.md`。
7. 架构边界那段保持讨论，不改成实施批准。语言不同只说明要重写，不说明不能借鉴。不要新增微服务。
8. 不把本轮研究写成活体已通过。测试一律是读过文件或未跑。

页面上的方案图仍应标明那是目标，不是当前实现。当前实现仍是忙则不让发，或 `begin_turn` 停掉上一轮普通回合。
