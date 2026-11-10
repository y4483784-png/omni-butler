# 预想产品资料

这棵目录留下预想产品的图、定案、可抄对照和实现计划，供后面整段构建使用。

实现计划在 `envisioned-product-plan.md`。后面的实现 PR 必须按那份计划做。

不要删除，也不要在功能 PR 里悄悄换掉下面这些文件。

- `envisioned-product-complete-chat.png` 和 `envisioned-product-complete.pdf`
- `mermaid-rows/` 里的五行图源
- `envisioned-final-brief.md`
- `envisioned-oss-reference-map.md` 和同名 JSON
- `envisioned-oss-map-product.md` 和同名 JSON
- `envisioned-oss-map-harness.md` 和同名 JSON
- `personal-agent-oss-comparison.md`
- `envisioned-product-plan.md`
- `decision-trail.md`

要改其中任何一份，就单独做一次修订，在 PR 说明里写明改了哪一份、为什么。压缩包里没有合并后的整图 mermaid。PNG 和 PDF 仍是上传时的原件，模块⑤在那两份图上仍是四种状态。2026-11-10 的计划审阅把第五种状态 `completed` 写进了 `mermaid-rows/02-dispatch.mmd`、`mermaid-rows/03-gate.mmd` 和实现计划。实现以计划和 `decision-trail.md` 为准。

本目录只保存计划和依据。它不是实现许可，也不是合并许可。实现要等操作者另行说开始。每一刀的代码做完后，还要做 pstack 形式化验证并画出状态图。操作者看过代码、验证结果和图，说「合」才合并。没有操作者明说的部署请求，不部署。
