#!/usr/bin/env python3
"""Deprecated compatibility wrapper for the reviewed routing generator."""

from gen_office_eval import main


if __name__ == "__main__":
    print("gen_office_eval_500.py is deprecated; using gen_office_eval.py")
    raise SystemExit(main())
