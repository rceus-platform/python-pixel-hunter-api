"""Module for automatically fixing docstring formatting in Python files."""

import os
import re


def fix_docstrings(content):
    """Ensure an empty line follows every docstring in the provided content."""

    # Rule 1: Module docstring (starts at top of file)
    # Match triple double quotes at the very beginning of the string
    match = re.match(r'^(""".*?""")(\n*)', content, re.DOTALL)
    if match:
        docstring = match.group(1)
        # Ensure exactly one empty line after the module docstring
        new_prefix = f"{docstring}\n\n"
        content = new_prefix + content[match.end() :].lstrip("\n")

    # Rule 2: Function/Class docstrings
    # Look for patterns like:
    # def func(...):
    #     """docstring"""
    # or
    # class Class:
    #     """docstring"""

    # Since regex with overlapping/repeated patterns can be tricky, we do it carefully.
    # We'll find all docstrings and add an empty line if not already there.

    lines = content.split("\n")
    new_lines = []
    i = 0
    while i < len(lines):
        line = lines[i]
        new_lines.append(line)

        # Check if this line contains the start of a docstring
        stripped = line.strip()
        if (
            stripped.startswith('"""')
            and stripped.endswith('"""')
            and len(stripped) >= 6
        ):
            # Check the next line
            if i + 1 < len(lines):
                next_line = lines[i + 1]
                if next_line.strip() and not next_line.startswith("#"):
                    # Add an empty line if not already there.
                    new_lines.append("")
        elif stripped.startswith('"""'):
            # Multi-line docstring start. Find the end.
            j = i + 1
            while j < len(lines) and '"""' not in lines[j]:
                new_lines.append(lines[j])
                j += 1
            if j < len(lines):
                new_lines.append(lines[j])
                # Now check after the closing triple quotes
                if j + 1 < len(lines):
                    next_line = lines[j + 1]
                    if next_line.strip() and not next_line.startswith("#"):
                        new_lines.append("")
                i = j
        i += 1

    return "\n".join(new_lines)


def process_directory(path):
    """Recursively process .py files in a directory to fix docstring formatting."""

    for root, _, files in os.walk(path):
        for file in files:
            if file.endswith(".py"):
                full_path = os.path.join(root, file)
                with open(full_path, "r", encoding="utf-8") as f:
                    content = f.read()

                new_content = fix_docstrings(content)

                if new_content != content:
                    with open(full_path, "w", encoding="utf-8") as f:
                        f.write(new_content)
                    print(f"Updated {full_path}")


if __name__ == "__main__":
    process_directory("app")
    # Also process the test script in the parent dir (it's in application-source/)
    if os.path.exists("test_google_scraper.py"):
        # This handles test_google_scraper.py if it's in the same dir
        process_directory(".")
