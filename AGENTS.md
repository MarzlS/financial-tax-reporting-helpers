# Project Guidelines

## Project Structure

- This repository is a collection of small Python helpers for financial and German tax reporting; see [README.md](README.md).
- Scripts are standalone. The EquatePlus-to-Captrader converter is [transfer_computershare_equateplus/convert.py](transfer_computershare_equateplus/convert.py).

## Data and Execution

- Runtime dependencies are listed in the tracked [requirements.txt](requirements.txt); install them in the local `.venv` using the setup instructions in [README.md](README.md).
- The README documents environment setup with Python 3.12. The repository does not define a test command.
- Its input and output filenames are relative to the current working directory. Run it from the directory containing the CSV files, not from the repository root unless the files are there.
- Preserve the source CSV contract: semicolon delimiter, comma decimal separator, UTF-8 with BOM support, and the `Quantity`, `Allocation date`, and `Cost basis` columns. `Allocation date` is parsed as `DD.MM.YYYY`.
- The transfer date and fixed output values are currently hard-coded in the converter. Confirm them against the intended report before changing them; the output path is overwritten when it already exists.

## Validation

- There is no repository-defined test suite. At minimum, syntax-check changed scripts with `py -m py_compile <script>`.
- For conversion changes, use a representative input CSV and inspect the generated columns, date formatting, and numeric values. The converter writes output files, so run this check in a suitable working directory.
- Follow the existing lightweight, direct Python style unless a concrete need justifies adding project infrastructure.
