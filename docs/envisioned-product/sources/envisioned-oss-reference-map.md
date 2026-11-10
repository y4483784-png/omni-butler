# Omni-Butler 预想点 → 可抄源码（合并索引）

生成：2026-11-06T11:44:44.764335+08:00（Asia/Singapore）

用途：给 Cursor 云端 agent 写计划用。计划写清「抄哪段 / 别抄什么」；实现时钉 Omni-Butler 本仓锚点。优先抄、少自研。

## 分册

| 半边 | Markdown | JSON | 覆盖 |
|---|---|---|---|
| 产品 | [envisioned-oss-map-product.md](./envisioned-oss-map-product.md) | [envisioned-oss-map-product.json](./envisioned-oss-map-product.json) | ①④⑥⑨⑪ |
| 运行时/壳子 | [envisioned-oss-map-harness.md](./envisioned-oss-map-harness.md) | [envisioned-oss-map-harness.json](./envisioned-oss-map-harness.json) | ②③⑤⑦⑧⑩ |
| 合并 | 本文件 | [envisioned-oss-reference-map.json](./envisioned-oss-reference-map.json) | 产品 27 + 壳子 6 = 33 |

## 固定版本

- OpenMausBot `083fd7b51405e6d0c01e189b2f65ab0322509b5a`
- DS-Bot `71d344bf193f3f18185f25de3d8e5c483046910a`
- 先前 PA：`/workspace/research/personal-agent-oss-comparison.md` + Page「开源 Personal Agent 源码对比」

## 硬约束

- 不做外接聊天总机 / 手机端 / 云手机
- 原地演进，不整仓换底座
- Khoj AGPL 仅侧车，不并入闭源根仓
- Stop ≠ 撤销已发出外部动作；隐藏工具 ≠ 授权/沙箱

## 缺口

见分册 `gaps`；合并 JSON 字段 `gaps.product` / `gaps.harness`。
