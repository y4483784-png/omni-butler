from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from app.rag.chunking import chunk_text
from app.rag.parse import ParseResult, ParsedElement, parse_file


def test_parse_markdown_emits_heading_elements():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "guide.md"
        p.write_text("# 第一章\n\n这是引言。\n\n## 第二节\n\n继续说明。", encoding="utf-8")
        result = parse_file(p)

    assert result.text.startswith("# 第一章")
    assert any(el.type == "heading" and el.heading == "第一章" for el in result.elements or [])
    assert any(el.type == "text" and el.heading == "第二节" for el in result.elements or [])


def test_parse_csv_adds_schema_and_row_summaries():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "sales.csv"
        p.write_text("日期,地区,销售额\n2026-08-01,华北,100\n2026-08-02,华东,120\n", encoding="utf-8")
        result = parse_file(p)

    assert "这是表格文件 sales.csv 的摘要" in result.text
    assert "这是第 1 到 2 行的数据片段" in result.text
    assert any(el.type == "schema" for el in result.elements or [])
    assert any(el.type == "table" and el.heading == "rows 1-2" for el in result.elements or [])


def test_parse_xlsx_emits_structured_elements():
    pytest.importorskip("openpyxl")

    fixture_dir = Path(__file__).parent / "fixtures" / "xlsx"
    candidates = sorted(fixture_dir.glob("*汇总表.xlsx"))
    assert candidates, f"未在 {fixture_dir} 找到 xlsx 测试夹具"

    result = parse_file(candidates[0])

    assert "这是表格文件" in result.text
    assert "数据列数：4" in result.text
    assert "工作表：" in result.text
    assert any(el.type == "schema" for el in result.elements or [])
    assert any(el.type == "table" for el in result.elements or [])
    assert any("sheet1" in (el.heading or "") or "sheet1" in (el.source or "") for el in result.elements or [])


def test_parse_xlsx_skips_title_row_and_reads_multiple_sheets():
    openpyxl = pytest.importorskip("openpyxl")

    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "multi_sheet.xlsx"
        wb = openpyxl.Workbook()
        ws1 = wb.active
        ws1.title = "总表"
        ws1.merge_cells("A1:D1")
        ws1["A1"] = "2026考试安排汇总"
        ws1.append(["学院", "课程", "日期", "人数"])
        ws1.append(["计算机学院", "数据库", "2026-08-20", 120])
        ws1.append(["外国语学院", "英语", "2026-08-21", 80])

        ws2 = wb.create_sheet("补充")
        ws2.append(["校区", "教室", "监考"])
        ws2.append(["主校区", "A101", "张三"])
        wb.save(p)

        result = parse_file(p)

    assert "表格标题：2026考试安排汇总" in result.text
    assert "工作表：总表" in result.text
    assert "工作表：补充" in result.text
    assert "数据列数：4" in result.text
    assert "数据列数：3" in result.text
    assert any(el.heading == "总表 · 数据集摘要" for el in result.elements or [])
    assert any(el.heading == "补充 · 数据集摘要" for el in result.elements or [])
    assert any("rows 1-2" in (el.heading or "") for el in result.elements or [])


def test_parse_docx_keeps_headings_and_table_summary():
    docx = pytest.importorskip("docx")

    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "plan.docx"
        doc = docx.Document()
        doc.add_heading("项目计划", level=1)
        doc.add_paragraph("第一阶段是整理需求。")
        table = doc.add_table(rows=2, cols=2)
        table.cell(0, 0).text = "模块"
        table.cell(0, 1).text = "负责人"
        table.cell(1, 0).text = "解析"
        table.cell(1, 1).text = "小王"
        doc.save(p)
        result = parse_file(p)

    assert any(el.type == "heading" and el.heading == "项目计划" for el in result.elements or [])
    assert any(el.type == "table" and "表格 1 摘要" in el.text for el in result.elements or [])


def test_parse_pdf_strips_repeated_header_footer_and_keeps_pages():
    fitz = pytest.importorskip("fitz")

    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "manual.pdf"
        pdf = fitz.open()
        for idx, body in enumerate(("这是第一页正文。", "这是第二页正文。"), start=1):
            page = pdf.new_page()
            page.insert_text((72, 72), "公司资料")
            page.insert_text((72, 110), body)
            page.insert_text((72, 760), "内部页脚")
        pdf.save(p)
        pdf.close()

        result = parse_file(p)

    assert "公司资料" not in result.text
    assert "内部页脚" not in result.text
    assert "--- 第 1 页 ---" in result.text
    assert any(el.type == "page" and el.page == 2 for el in result.elements or [])


def test_chunk_text_merges_tiny_heading_with_following_body():
    result = ParseResult(
        text="# 总览\n\n这里是详细说明，内容足够长，可以和前面的标题组合成一个更完整的检索块。",
        elements=[
            ParsedElement(type="heading", text="# 总览", heading="总览", source="markdown"),
            ParsedElement(
                type="text",
                text="这里是详细说明，内容足够长，可以和前面的标题组合成一个更完整的检索块。",
                heading="总览",
                source="markdown",
            ),
        ],
    )

    chunks = chunk_text(result, chunk_size=200, overlap=0, chunk_min_size=40)

    assert len(chunks) == 1
    assert chunks[0].heading == "总览"
    assert "# 总览" in chunks[0].content
    assert "详细说明" in chunks[0].content


def test_chunk_text_preserves_table_block_when_small():
    result = ParseResult(
        text="表格摘要\n\n--- rows 1-2 ---",
        elements=[
            ParsedElement(type="schema", text="表格摘要", heading="数据集摘要", source="tabular:csv"),
            ParsedElement(type="table", text="这是第 1 到 2 行的数据片段。\n\n--- rows 1-2 ---", heading="rows 1-2", source="tabular:csv"),
        ],
    )

    chunks = chunk_text(result, chunk_size=200, overlap=0, chunk_min_size=80)

    assert len(chunks) == 2
    assert chunks[0].kind == "schema"
    assert chunks[1].kind == "table"


def test_chunk_text_falls_back_without_elements():
    text = "--- 第 3 页 ---\n页面内容关于神经网络。\n\n--- 第 4 页 ---\n下一页。"
    pieces = chunk_text(text, chunk_size=2000, overlap=0)

    assert any(p.page == 3 and "神经网络" in p.content for p in pieces)
    assert any(p.page == 4 for p in pieces)
