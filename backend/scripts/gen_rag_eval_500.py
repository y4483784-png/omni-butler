#!/usr/bin/env python3
"""Deprecated compatibility wrapper for the reviewed RAG generator."""

from gen_rag_eval import main


if __name__ == "__main__":
    print("gen_rag_eval_500.py is deprecated; using gen_rag_eval.py")
    raise SystemExit(main())
