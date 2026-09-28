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

## Create a VVV staking report

Copy `.env.example` to `.env` in the repository root and set `VVV_WALLET_ADDRESS` and `BLOCKSCOUT_API_KEY` there. `VVV_FROM_ADDRESS` defaults to the observed Venice StakingV2 contract and can be changed to another sender address. The main report includes only VVV transfers from this address; all incoming VVV transfers remain in the review CSV, with other senders labelled for review. When the script starts, enter the desired tax year or press Enter to use the current year. The `.env` file is ignored by Git. Run the script from the repository directory:

```bat
python vvv_staking_report/generate_report.py
```

The script creates `vvv_staking_report/vvv_staking_report_[YEAR].csv` and a separate `vvv_staking_review_[YEAR].csv` containing only transfers excluded from the main report, with sender, recipient, transaction hash, and method. Both files start with the wallet-address column. Review the second file manually and copy any entries that belong in the report. CoinGecko's free historical endpoint returns a price snapshot at 00:00 UTC for the selected date, not a guaranteed daily closing price. Review the source data and tax treatment with a qualified tax adviser before filing; this export is a calculation aid, not tax advice.

## Run tests

Install the development dependencies and run the offline unit tests from the repository directory:

```bat
python -m pip install -r requirements-dev.txt
python -m pytest
```
