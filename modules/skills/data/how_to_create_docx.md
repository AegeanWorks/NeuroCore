# How to Create .docx Files

## Overview
This skill provides guidelines for creating Microsoft Word documents (.docx) programmatically using the python-docx library.

## When to Use
Use this skill when the user wants to:
- Create Word documents from Python
- Generate reports, letters, or formatted documents
- Create documents with tables, images, and formatting

## Best Practices

### Document Structure
- Always create a new Document object first
- Add a title at the beginning using `document.add_heading()`
- Use paragraphs for body text
- Organize content with headings (Heading 1, 2, 3)

### Text Formatting
- Use `paragraph.style` for predefined styles
- Use `run.bold = True` for bold text
- Use `run.italic = True` for italic text
- Use `run.underline = True` for underlined text

### Tables
- Create tables with `document.add_table()`
- Always add header row
- Use cell objects to modify content
- Apply borders for visual separation

### Code Examples

```
python
from docx import Document
from docx.shared import Inches, Pt

# Create document
doc = Document()

# Add title
doc.add_heading('Document Title', 0)

# Add paragraph
doc.add_paragraph('This is a paragraph.')

# Add formatted text
paragraph = doc.add_paragraph()
run = paragraph.add_run('Bold text')
run.bold = True

# Add table
table = doc.add_table(rows=2, cols=3)
table.style = 'Light Grid Accent 1'

# Save document
doc.save('output.docx')
```

### Common Issues
- Remember to save the document with `.save()`
- File must be closed before reopening
- Use absolute paths when saving to specific locations
