# financial-tax-reporting-helpers

Collection of small python helper scripts for financial and (German) tax reporting

## Convert EquatePlus to Captrader

### Set up the environment (first time only)

From the repository directory, create a virtual environment and install the tracked dependencies:

```bat
py -3.12 -m venv .venv
.venv\Scripts\activate.bat
python -m pip install -r requirements.txt
```

The `.venv` directory is local and is not tracked by Git. Dependencies are listed in `requirements.txt`.

### Convert a file

1. Copy the CSV file downloaded from EquatePlus to `./transfer_computershare_equateplus`.
2. Rename the file to `Transfer YYYY-MM-DD Equateplus.csv`, using the transfer date for `YYYY-MM-DD`.
3. In `convert.py`, set `DATESTAMP` to the same date.
4. Open a command prompt in the repository directory, activate the environment, and run:

```bat
.venv\Scripts\activate.bat
cd transfer_computershare_equateplus
python convert.py
```

The script creates `Transfer YYYY-MM-DD Captrader.csv` in the `transfer_computershare_equateplus` directory.
