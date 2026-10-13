# 架构说明

系统总体架构分交互层、逻辑层、数据层三层。

知识库检索采用SQLite关键词与Qdrant向量库混合方案。

向量检索集合名称约定为omni_chunks。

对话流式输出协议采用SSE，前端按事件类型渲染。

数据分析沙箱隔离在Docker中运行，禁止访问内网生产库。

Agent工作流基于LangGraph编排，工具统一经过Gateway治理。
