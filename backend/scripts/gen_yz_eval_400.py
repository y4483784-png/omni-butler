#!/usr/bin/env python3
"""Deprecated compatibility wrapper for the reviewed YZ generator."""

from gen_yz_eval import main


if __name__ == "__main__":
    print("gen_yz_eval_400.py is deprecated; using gen_yz_eval.py")
    raise SystemExit(main())
