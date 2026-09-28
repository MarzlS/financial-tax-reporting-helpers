import csv
from datetime import datetime
from decimal import Decimal

import requests

from vvv_staking_report import generate_report as report


WALLET = "0x" + "1" * 40
STAKING_CONTRACT = "0x" + "2" * 40
OTHER_ADDRESS = "0x" + "3" * 40


class FakeResponse:
    def __init__(self, payload, status_code=200, text="", headers=None):
        self.payload = payload
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, payloads):
        self.payloads = iter(payloads)
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, dict(params or {}), timeout))
        payload = next(self.payloads)
        if isinstance(payload, FakeResponse):
            return payload
        return FakeResponse(payload)


def make_transfer(
    *,
    timestamp="2026-09-20T09:00:00Z",
    sender=STAKING_CONTRACT,
    recipient=WALLET,
    token_address=None,
    method="claim",
    value="1000000000000000000",
):
    return {
        "token": {
            "address_hash": token_address or report.VVV_TOKEN_ADDRESS,
            "decimals": "18",
            "symbol": "VVV",
        },
        "from": {"hash": sender},
        "to": {"hash": recipient},
        "timestamp": timestamp,
        "method": method,
        "hash": "0x" + "a" * 64,
        "value": value,
    }


def test_prompt_tax_year_defaults_to_current_year(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _: "")

    assert report.prompt_tax_year() == datetime.now().year


def test_prompt_tax_year_retries_invalid_input(monkeypatch, capsys):
    answers = iter(["not-a-year", "2024"])
    monkeypatch.setattr("builtins.input", lambda _: next(answers))

    assert report.prompt_tax_year() == 2024
    assert "Bitte ein gültiges Jahr eingeben." in capsys.readouterr().out


def test_fetch_vvv_inflows_filters_and_paginates(monkeypatch):
    monkeypatch.setattr(report, "VVV_WALLET_ADDRESS", WALLET)
    monkeypatch.setattr(report, "VVV_FROM_ADDRESS", STAKING_CONTRACT)
    monkeypatch.setattr(report, "STEUERJAHR", 2026)
    session = FakeSession(
        [
            {
                "items": [
                    make_transfer(),
                    make_transfer(sender=OTHER_ADDRESS),
                    make_transfer(token_address=OTHER_ADDRESS),
                ],
                "next_page_params": {"block_number": 123, "index": 1},
            },
            {
                "items": [
                    make_transfer(timestamp="2026-09-20T08:00:00Z", value="2000000000000000000"),
                    make_transfer(sender=OTHER_ADDRESS),
                    make_transfer(method="claimReward"),
                    make_transfer(recipient=OTHER_ADDRESS),
                    make_transfer(timestamp="2025-06-01T09:00:00Z"),
                ],
            },
        ]
    )

    transfers = report.fetch_vvv_inflows(session)

    assert [transfer["quantity"] for transfer in transfers] == [
        Decimal("2"),
        Decimal("1"),
        Decimal("1"),
        Decimal("1"),
        Decimal("1"),
    ]
    assert [transfer["token_symbol"] for transfer in transfers] == ["VVV"] * 5
    assert transfers[0]["from_address"] == STAKING_CONTRACT
    assert transfers[0]["to_address"] == WALLET
    assert transfers[0]["transaction_hash"] == "0x" + "a" * 64
    assert transfers[0]["method"] == "claim"
    assert OTHER_ADDRESS in [transfer["from_address"] for transfer in transfers]
    assert len(session.calls) == 2
    assert session.calls[0][0].endswith(f"/addresses/{WALLET}/token-transfers")
    assert session.calls[1][1]["block_number"] == 123


def test_fetch_vvv_inflows_retries_server_error_and_prints_response_body(
    monkeypatch, capsys
):
    monkeypatch.setattr(report, "VVV_WALLET_ADDRESS", WALLET)
    monkeypatch.setattr(report, "VVV_FROM_ADDRESS", "")
    monkeypatch.setattr(report, "STEUERJAHR", 2026)
    monkeypatch.setattr(report, "BLOCKSCOUT_MAX_RETRIES", 2)
    monkeypatch.setattr(report, "BLOCKSCOUT_RETRY_DELAY_SECONDS", 0)
    sleep_delays = []
    monkeypatch.setattr(report.time, "sleep", sleep_delays.append)
    session = FakeSession(
        [
            FakeResponse({}, status_code=500, text="temporary upstream failure"),
            {"items": [make_transfer()]},
        ]
    )

    transfers = report.fetch_vvv_inflows(session)

    assert len(transfers) == 1
    assert len(session.calls) == 2
    assert sleep_delays == [0]
    output = capsys.readouterr().out
    assert "HTTP 500" in output
    assert "temporary upstream failure" in output
def test_fetch_eur_price_uses_historical_date():
    session = FakeSession([{"market_data": {"current_price": {"eur": 27.42}}}])

    price = report.fetch_eur_price(session, datetime(2026, 9, 20).date())

    assert price == Decimal("27.42")
    assert session.calls[0][1] == {"date": "20-09-2026", "localization": "false"}


def test_fetch_eur_price_retries_429_and_honors_retry_after(monkeypatch, capsys):
    monkeypatch.setattr(report, "COINGECKO_MAX_RETRIES", 2)
    monkeypatch.setattr(report, "COINGECKO_RETRY_DELAY_SECONDS", 1)
    sleep_delays = []
    monkeypatch.setattr(report.time, "sleep", sleep_delays.append)
    session = FakeSession(
        [
            FakeResponse(
                {},
                status_code=429,
                text="rate limit exceeded",
                headers={"Retry-After": "7"},
            ),
            {"market_data": {"current_price": {"eur": 27.42}}},
        ]
    )

    price = report.fetch_eur_price(session, datetime(2026, 9, 20).date())

    assert price == Decimal("27.42")
    assert len(session.calls) == 2
    assert sleep_delays == [7.0]
    output = capsys.readouterr().out
    assert "HTTP 429" in output
    assert "rate limit exceeded" in output


def test_create_report_writes_german_csv_and_caches_daily_price(
    monkeypatch, tmp_path, capsys
):
    monkeypatch.setattr(report, "STEUERJAHR", 2026)
    monkeypatch.setattr(report, "VVV_FROM_ADDRESS", STAKING_CONTRACT)
    monkeypatch.setattr(report, "VVV_WALLET_ADDRESS", WALLET)
    monkeypatch.setattr(report, "__file__", str(tmp_path / "generate_report.py"))
    output_path = tmp_path / "vvv_staking_report_2026.csv"
    requested_dates = []

    def fake_fetch_eur_price(session, event_date):
        requested_dates.append(event_date)
        return Decimal("27.42")

    monkeypatch.setattr(report, "fetch_eur_price", fake_fetch_eur_price)
    transfers = [
        {
            "time": datetime(2026, 9, 20, 11, tzinfo=report.LOCAL_TIMEZONE),
            "quantity": Decimal("1.25"),
            "from_address": STAKING_CONTRACT,
            "to_address": WALLET,
            "transaction_hash": "0x" + "a" * 64,
            "method": "claim",
        },
        {
            "time": datetime(2026, 9, 20, 12, tzinfo=report.LOCAL_TIMEZONE),
            "quantity": Decimal("2"),
            "from_address": STAKING_CONTRACT,
        },
        {
            "time": datetime(2026, 9, 20, 13, tzinfo=report.LOCAL_TIMEZONE),
            "quantity": Decimal("10"),
            "from_address": OTHER_ADDRESS,
        },
    ]

    total = report.create_report(transfers)

    with output_path.open(encoding="utf-8-sig", newline="") as csv_file:
        rows = list(csv.reader(csv_file, delimiter=";"))
    review_path = tmp_path / "vvv_staking_review_2026.csv"
    with review_path.open(encoding="utf-8-sig", newline="") as csv_file:
        review_rows = list(csv.reader(csv_file, delimiter=";"))

    assert total == Decimal("89.12")
    assert len(requested_dates) == 1
    assert rows[0] == [
        "Wallet-Adresse",
        "Datum & Uhrzeit",
        "Asset",
        "Erhaltene Menge",
        "Wechselkurs (EUR)",
        "Wert in EUR (Zufluss)",
    ]
    assert rows[1] == [
        WALLET,
        "20.09.2026 11:00:00 CEST",
        "VVV",
        "1,25",
        "27,42000000",
        "34,28",
    ]
    assert len(rows) == 3
    assert rows[2][0] == WALLET
    assert rows[2][5] == "54,84"
    assert len(review_rows) == 2
    assert review_rows[0][0] == "Wallet-Adresse"
    assert review_rows[1][0] == WALLET
    assert "Art der Einkünfte" not in review_rows[0]
    assert review_rows[0][-6:] == [
        "Absender",
        "Empfänger",
        "Transaktionshash",
        "Methode",
        "Einordnung",
        "Notiz",
    ]
    assert review_rows[1][-6:] == [
        OTHER_ADDRESS,
        "",
        "",
        "",
        "Anderer Absender (Übertrag prüfen)",
        "",
    ]
    assert "Gesamte Einkünfte 2026: 89,12 EUR" in capsys.readouterr().out
