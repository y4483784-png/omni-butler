# RAG evaluation corpus

Each Markdown file is one stable document. Its filename stem is the
`gold_doc_keys` identifier used by `rag_queries.jsonl`; renaming a file is
therefore a gold-label migration.

Blank-line-separated paragraphs become retrieval chunks. The six documents
covering expense procedures, account security, meeting minutes, procurement,
data retention, and employee benefits deliberately overlap vocabulary with the
original ten documents so hard-negative questions cannot pass through keyword
matching alone.
