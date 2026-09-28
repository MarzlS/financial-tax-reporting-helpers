import csv
import re
import time
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from dotenv import dotenv_values


ROOT_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT_DIR / ".env"
CONFIG = dotenv_values(ENV_FILE)

# Read report settings from the repository-root .env file.
VVV_WALLET_ADDRESS = (CONFIG.get("VVV_WALLET_ADDRESS") or "").strip()
BLOCKSCOUT_API_KEY = (CONFIG.get("BLOCKSCOUT_API_KEY") or "").strip()
VVV_FROM_ADDRESS = (CONFIG.get("VVV_FROM_ADDRESS") or "").strip()
STEUERJAHR = datetime.now().year

VVV_TOKEN_ADDRESS = "0xacfe6019ed1a7dc6f7b508c02d1b04ec88cc21bf"
BLOCKSCOUT_API_URL = "https://base.blockscout.com/api/v2/addresses/{}/token-transfers"
COINGECKO_HISTORY_URL = "https://api.coingecko.com/api/v3/coins/venice-token/history"
COINGECKO_DELAY_SECONDS = 1.5
REQUEST_TIMEOUT_SECONDS = 30
LOCAL_TIMEZONE = ZoneInfo("Europe/Berlin")
INCOME_TYPE = "Staking § 22 Nr. 3 EStG"


def validate_configuration():
    """Check the wallet and optional sender address before making API requests."""
    if not ENV_FILE.is_file():
        raise FileNotFoundError("Create a root .env file from .env.example first.")
    address_pattern = re.compile(r"^0x[a-fA-F0-9]{40}$")
    if not address_pattern.fullmatch(VVV_WALLET_ADDRESS):
        raise ValueError("Set VVV_WALLET_ADDRESS in the root .env to a valid Base address.")
    if not BLOCKSCOUT_API_KEY:
        raise ValueError("Set BLOCKSCOUT_API_KEY in the root .env file.")
    if VVV_FROM_ADDRESS and not address_pattern.fullmatch(VVV_FROM_ADDRESS):
        raise ValueError("VVV_FROM_ADDRESS must be empty or a valid 0x-prefixed address.")
    if not isinstance(STEUERJAHR, int) or STEUERJAHR < 2009:
        raise ValueError("The tax year must be a valid four-digit year.")


def prompt_tax_year():
    """Prompt for a tax year, using the current year when input is empty."""
    current_year = datetime.now().year
    while True:
        answer = input(f"Gewünschtes Steuerjahr [{current_year}]: ").strip()
        if not answer:
            return current_year
        try:
            year = int(answer)
        except ValueError:
            print("Bitte ein gültiges Jahr eingeben.")
            continue
        if year < 2009:
            print("Das Steuerjahr muss 2009 oder später sein.")
            continue
        return year


def get_address(value):
    """Return an address from either a string or a Blockscout address object."""
    if isinstance(value, dict):
        return value.get("hash", "")
    return value or ""


def get_token_details(transfer):
    """Handle token metadata shapes returned by Blockscout API versions."""
    token_instance = transfer.get("token_instance") or {}
    token = transfer.get("token") or token_instance.get("token") or {}
    return token, token_instance


def fetch_vvv_inflows(session):
    """Fetch VVV transfers and retain only incoming transfers in the tax year."""
    url = BLOCKSCOUT_API_URL.format(VVV_WALLET_ADDRESS)
    params = {"apikey": BLOCKSCOUT_API_KEY} if BLOCKSCOUT_API_KEY else {}
    transfers = []
    seen_pages = set()

    while True:
        response = session.get(url, params=params, timeout=REQUEST_TIMEOUT_SECONDS)
        response.raise_for_status()
        payload = response.json()
        items = payload.get("items", [])

        for transfer in items:
            token, token_instance = get_token_details(transfer)
            token_address = token.get("address", "")
            if token_address.lower() != VVV_TOKEN_ADDRESS:
                continue

            if get_address(transfer.get("to")).lower() != VVV_WALLET_ADDRESS.lower():
                continue
            if VVV_FROM_ADDRESS and get_address(transfer.get("from")).lower() != VVV_FROM_ADDRESS.lower():
                continue

            timestamp = transfer.get("timestamp")
            if not timestamp:
                continue
            transfer_time = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            if transfer_time.tzinfo is None:
                transfer_time = transfer_time.replace(tzinfo=timezone.utc)
            local_time = transfer_time.astimezone(LOCAL_TIMEZONE)
            if local_time.year != STEUERJAHR:
                continue

            total = transfer.get("total") or {}
            amount_base_units = transfer.get("value")
            if amount_base_units is None:
                amount_base_units = total.get("value")
            if amount_base_units is None:
                amount_base_units = token_instance.get("value")

            decimals = token.get("decimals", token_instance.get("decimals"))
            if amount_base_units is None or decimals is None:
                continue

            quantity = Decimal(str(amount_base_units)) / (Decimal(10) ** int(decimals))
            if quantity <= 0:
                continue

            transfers.append({"time": local_time, "quantity": quantity})

        next_page_params = payload.get("next_page_params")
        if not next_page_params:
            break

        page_marker = tuple(sorted(next_page_params.items()))
        if page_marker in seen_pages:
            raise RuntimeError("Blockscout returned a repeated pagination cursor.")
        seen_pages.add(page_marker)
        params.update(next_page_params)

    transfers.sort(key=lambda transfer: transfer["time"])
    return transfers


def fetch_eur_price(session, event_date):
    """Fetch CoinGecko's historical EUR price snapshot for a calendar date."""
    response = session.get(
        COINGECKO_HISTORY_URL,
        params={"date": event_date.strftime("%d-%m-%Y"), "localization": "false"},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    payload = response.json()
    try:
        return Decimal(str(payload["market_data"]["current_price"]["eur"]))
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError(
            f"CoinGecko returned no EUR price for {event_date:%d-%m-%Y}."
        ) from error


def format_decimal(value, decimal_places=None):
    """Format a Decimal with German decimal separators and no grouping."""
    if decimal_places is not None:
        value = value.quantize(Decimal(1).scaleb(-decimal_places), rounding=ROUND_HALF_UP)
    formatted = format(value, "f")
    if decimal_places is None and "." in formatted:
        formatted = formatted.rstrip("0").rstrip(".")
    return formatted.replace(".", ",")


def create_report(transfers):
    """Price each transfer, write the German CSV, and return the rounded total."""
    output_path = Path(__file__).with_name(f"vvv_staking_report_{STEUERJAHR}.csv")
    price_cache = {}
    rows = []
    total_income = Decimal("0.00")

    with requests.Session() as session:
        session.headers.update({"User-Agent": "vvv-staking-tax-report/1.0"})
        for transfer in transfers:
            event_date = transfer["time"].date()
            if event_date not in price_cache:
                if price_cache:
                    time.sleep(COINGECKO_DELAY_SECONDS)
                price_cache[event_date] = fetch_eur_price(session, event_date)

            eur_price = price_cache[event_date]
            income_eur = (transfer["quantity"] * eur_price).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            total_income += income_eur
            rows.append(
                [
                    transfer["time"].strftime("%d.%m.%Y %H:%M:%S %Z"),
                    "VVV",
                    format_decimal(transfer["quantity"]),
                    format_decimal(eur_price, 8),
                    format_decimal(income_eur, 2),
                    INCOME_TYPE,
                ]
            )

    with output_path.open("w", newline="", encoding="utf-8-sig") as csv_file:
        writer = csv.writer(csv_file, delimiter=";")
        writer.writerow(
            [
                "Datum & Uhrzeit",
                "Asset",
                "Erhaltene Menge",
                "Wechselkurs (EUR)",
                "Wert in EUR (Zufluss)",
                "Art der Einkünfte",
            ]
        )
        writer.writerows(rows)

    print(f"CSV erstellt: {output_path}")
    print(f"Gesamte Einkünfte {STEUERJAHR}: {format_decimal(total_income, 2)} EUR")
    return total_income


def main():
    global STEUERJAHR
    STEUERJAHR = prompt_tax_year()
    validate_configuration()
    with requests.Session() as session:
        session.headers.update({"User-Agent": "vvv-staking-tax-report/1.0"})
        transfers = fetch_vvv_inflows(session)

    if not transfers:
        print(f"Keine VVV-Zuflüsse für {STEUERJAHR} gefunden.")
    create_report(transfers)


if __name__ == "__main__":
    main()
