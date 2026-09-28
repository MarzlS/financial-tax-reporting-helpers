import csv
import re
import time
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from email.utils import parsedate_to_datetime
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
VVV_STAKING_CONTRACT_ADDRESS = "0x321b7ff75154472b18edb199033ff4d116f340ff"
VVV_FROM_ADDRESS = (
    CONFIG.get("VVV_FROM_ADDRESS") or VVV_STAKING_CONTRACT_ADDRESS
).strip()
STEUERJAHR = datetime.now().year

VVV_TOKEN_ADDRESS = "0xacfe6019ed1a7dc6f7b508c02d1b04ec88cc21bf"
BLOCKSCOUT_API_URL = "https://base.blockscout.com/api/v2/addresses/{}/token-transfers"
COINGECKO_HISTORY_URL = "https://api.coingecko.com/api/v3/coins/venice-token/history"
COINGECKO_DELAY_SECONDS = 2.1
COINGECKO_MAX_RETRIES = 5
COINGECKO_RETRY_DELAY_SECONDS = 5.0
BLOCKSCOUT_MAX_RETRIES = 3
BLOCKSCOUT_RETRY_DELAY_SECONDS = 2.0
REQUEST_TIMEOUT_SECONDS = 30
LOCAL_TIMEZONE = ZoneInfo("Europe/Berlin")


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


def get_blockscout_page(session, url, params):
    """Request a Blockscout page, retry transient failures, and report error bodies."""
    for attempt in range(BLOCKSCOUT_MAX_RETRIES + 1):
        try:
            response = session.get(url, params=params, timeout=REQUEST_TIMEOUT_SECONDS)
        except requests.RequestException as error:
            if attempt >= BLOCKSCOUT_MAX_RETRIES:
                raise
            delay = BLOCKSCOUT_RETRY_DELAY_SECONDS * (2**attempt)
            print(
                f"Blockscout request failed ({type(error).__name__}); "
                f"retrying in {delay:g}s ({attempt + 1}/{BLOCKSCOUT_MAX_RETRIES})."
            )
            time.sleep(delay)
            continue

        if response.status_code >= 400:
            response_body = response.text.strip()
            if response_body:
                print(f"Blockscout error response (HTTP {response.status_code}):")
                print(response_body)

            retryable = response.status_code == 429 or response.status_code >= 500
            if retryable and attempt < BLOCKSCOUT_MAX_RETRIES:
                delay = BLOCKSCOUT_RETRY_DELAY_SECONDS * (2**attempt)
                print(
                    f"Retrying Blockscout request in {delay:g}s "
                    f"({attempt + 1}/{BLOCKSCOUT_MAX_RETRIES})."
                )
                time.sleep(delay)
                continue

        response.raise_for_status()
        return response


def fetch_vvv_inflows(session):
    """Fetch VVV transfers and retain only incoming transfers in the tax year."""
    url = BLOCKSCOUT_API_URL.format(VVV_WALLET_ADDRESS)
    params = {"apikey": BLOCKSCOUT_API_KEY} if BLOCKSCOUT_API_KEY else {}
    transfers = []
    seen_pages = set()

    while True:
        response = get_blockscout_page(session, url, params)
        payload = response.json()
        items = payload.get("items", [])

        for transfer in items:
            token, token_instance = get_token_details(transfer)
            token_address = token.get("address_hash", "")
            if token_address.lower() != VVV_TOKEN_ADDRESS:
                continue

            if get_address(transfer.get("to")).lower() != VVV_WALLET_ADDRESS.lower():
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

            token_symbol = token.get("symbol", token_instance.get("symbol", ""))
            transaction_hash = transfer.get("transaction_hash") or transfer.get("hash", "")

            transfers.append(
                {
                    "time": local_time,
                    "quantity": quantity,
                    "token_symbol": token_symbol,
                    "from_address": get_address(transfer.get("from")),
                    "to_address": get_address(transfer.get("to")),
                    "transaction_hash": transaction_hash,
                    "method": transfer.get("method") or "",
                }
            )

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


def get_retry_after_delay(response, fallback_delay):
    """Return a safe delay using Retry-After when the server provides it."""
    retry_after = response.headers.get("Retry-After", "")
    if not retry_after:
        return fallback_delay

    try:
        server_delay = float(retry_after)
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(retry_after)
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=timezone.utc)
            server_delay = (retry_at - datetime.now(timezone.utc)).total_seconds()
        except (TypeError, ValueError, OverflowError):
            return fallback_delay

    return max(fallback_delay, server_delay, 0.0)


def get_coingecko_response(session, params):
    """Request CoinGecko data with bounded retries for transient failures."""
    for attempt in range(COINGECKO_MAX_RETRIES + 1):
        try:
            response = session.get(
                COINGECKO_HISTORY_URL,
                params=params,
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        except requests.RequestException as error:
            if attempt >= COINGECKO_MAX_RETRIES:
                raise
            delay = COINGECKO_RETRY_DELAY_SECONDS * (2**attempt)
            print(
                f"CoinGecko request failed ({type(error).__name__}); "
                f"retrying in {delay:g}s ({attempt + 1}/{COINGECKO_MAX_RETRIES})."
            )
            time.sleep(delay)
            continue

        if response.status_code >= 400:
            response_body = response.text.strip()
            if response_body:
                print(f"CoinGecko error response (HTTP {response.status_code}):")
                print(response_body)

            retryable = response.status_code == 429 or response.status_code >= 500
            if retryable and attempt < COINGECKO_MAX_RETRIES:
                fallback_delay = COINGECKO_RETRY_DELAY_SECONDS * (2**attempt)
                delay = get_retry_after_delay(response, fallback_delay)
                print(
                    f"Retrying CoinGecko request in {delay:g}s "
                    f"({attempt + 1}/{COINGECKO_MAX_RETRIES})."
                )
                time.sleep(delay)
                continue

        response.raise_for_status()
        return response


def fetch_eur_price(session, event_date):
    """Fetch CoinGecko's historical EUR price snapshot for a calendar date."""
    response = get_coingecko_response(
        session,
        {"date": event_date.strftime("%d-%m-%Y"), "localization": "false"},
    )
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
    """Write the report and a detailed CSV of transfers excluded from it."""
    output_path = Path(__file__).with_name(f"vvv_staking_report_{STEUERJAHR}.csv")
    review_path = Path(__file__).with_name(f"vvv_staking_review_{STEUERJAHR}.csv")
    price_cache = {}
    rows = []
    review_rows = []
    total_income = Decimal("0.00")
    headers = [
        "Wallet-Adresse",
        "Datum & Uhrzeit",
        "Asset",
        "Erhaltene Menge",
        "Wechselkurs (EUR)",
        "Wert in EUR (Zufluss)",
    ]
    review_headers = headers + [
        "Absender",
        "Empfänger",
        "Transaktionshash",
        "Methode",
        "Einordnung",
        "Notiz",
    ]

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
            row = [
                VVV_WALLET_ADDRESS,
                transfer["time"].strftime("%d.%m.%Y %H:%M:%S %Z"),
                "VVV",
                format_decimal(transfer["quantity"]),
                format_decimal(eur_price, 8),
                format_decimal(income_eur, 2),
            ]
            sender = transfer.get("from_address", "")
            staking_candidate = (
                not VVV_FROM_ADDRESS
                or sender.lower() == VVV_FROM_ADDRESS.lower()
            )
            if staking_candidate:
                rows.append(row)
                total_income += income_eur
            else:
                review_rows.append(
                    row
                    + [
                        sender,
                        transfer.get("to_address", ""),
                        transfer.get("transaction_hash", ""),
                        transfer.get("method", ""),
                        "Anderer Absender (Übertrag prüfen)",
                        "",
                    ]
                )

    for csv_path, csv_headers, csv_rows in (
        (output_path, headers, rows),
        (review_path, review_headers, review_rows),
    ):
        with csv_path.open("w", newline="", encoding="utf-8-sig") as csv_file:
            writer = csv.writer(csv_file, delimiter=";")
            writer.writerow(csv_headers)
            writer.writerows(csv_rows)

    print(f"CSV erstellt: {output_path}")
    print(f"Prüfliste erstellt: {review_path}")
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
