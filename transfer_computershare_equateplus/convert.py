# Python-Skript: EquatePlus CSV → Zielformat

import pandas as pd
from pathlib import Path

# Eingabedateien
DATESTAMP = "2026-06-09"
INPUT_FILE = "Transfer " + DATESTAMP + " Equateplus.csv"
OUTPUT_FILE = "Transfer " + DATESTAMP + " Captrader.csv"

# CSV laden
# EquatePlus verwendet Semikolon und deutsche Dezimalwerte
src = pd.read_csv(
    INPUT_FILE,
    sep=';',
    decimal=',',
    encoding='utf-8-sig'
)

# Spalten ins Zielformat umwandeln
result = pd.DataFrame()

# Zielstruktur laut Sample_TransferDates.csv
result['Data Discriminator'] = 'Lot'
result['Entry ID'] = ''
result['Symbol'] = 'IBM'
result['Currency'] = 'USD'

# Quantity übernehmen
result['Quantity'] = src['Quantity']

# Datum von DD.MM.YYYY -> YYYY-MM-DD
result['Acquisition Date'] = pd.to_datetime(
    src['Allocation date'],
    format='%d.%m.%Y'
).dt.strftime('%Y-%m-%d')

# Cost basis numerisch sicherstellen
result['TotalCost'] = src['Cost basis']

# Editable Spalte
result['Data Discriminator'] = 'Lot'
result['Symbol'] = 'IBM'
result['Currency'] = 'USD'
result['Editable'] = 'Y'

# CSV im gewünschten Format speichern
result.to_csv(
    OUTPUT_FILE,
    index=False,
    quoting=1,
    header=['Data Discriminator','Entry ID','Symbol','Currency','Quantity','Acquisition Date','TotalCost','Editable']
)

print(f'Konvertierung abgeschlossen: {OUTPUT_FILE}')
