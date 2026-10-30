# Documents

Use this skill to read, edit or build Word, PowerPoint, PDF and Excel files in the workspace.

## Read

```python
import craft

text = craft.read("inputs/plan.docx")          # Markdown: headings, lists, tables
tables = craft.docs.word_tables("inputs/plan.docx")
slides = craft.read("inputs/review.pptx")       # list of {slide, title, text, notes}
pages = craft.read("inputs/report.pdf")         # list of page texts
book = craft.docs.sheets("inputs/budget.xlsx")  # {sheet name: DataFrame}
```

A PDF page without a text layer comes back empty; say so instead of guessing its content.

## Edit

1. Work on a personal copy. Library sources are read-only; import one before editing.
2. Save under a new name unless the person asks to replace the file.

```python
working = craft.docs.editable_copy("inputs/plan.docx")
count = craft.docs.replace_in_word(working, "Draft", "Final")
doc = craft.docs.open_word(working)
doc.add_paragraph("Reviewed on 20 October 2026.")
craft.save(doc, working)
```

`replace_in_word` also matches text split across differently formatted runs; the new text takes the formatting of the run the match starts in. Report the count it returns.

## Build

```python
craft.docs.report(
    "outputs/summary.docx", "Mass summary",
    "# Findings", "- Mission 01 is lightest.", table, figure,
)
craft.docs.slides("outputs/summary.pptx", "Mass summary", ("Findings", "Mission 01 is lightest"), ("Table", table))
craft.excel("outputs/summary.xlsx", Summary=table, Detail=detail)
craft.preview("outputs/summary.docx")   # shows the first two pages
```

After saving, tell the person each file name and what it holds. `craft.check()` says whether LibreOffice, Poppler and the fonts that rendering needs are installed.
