from pathlib import Path

from functions.run_python_file import run_python_file
from functions.write_file import write_file

# Write to a scratch directory so running these checks never mutates the
# `calculator` fixture.
scratch = Path(__file__).parent / ".scratch"
scratch.mkdir(exist_ok=True)

print(write_file(str(scratch), "temp.txt", "this should be allowed"))
print(write_file(str(scratch), "nested/deeper/temp.txt", "nested dirs are created"))
print(write_file(str(scratch), "unicode.txt", "café — 日本語 — файл"))
print(run_python_file(str(scratch), "temp.txt"))
print(write_file("calculator", "/tmp/temp.txt", "this should not be allowed"))
print(write_file("calculator", ".", "cannot write to a directory"))
