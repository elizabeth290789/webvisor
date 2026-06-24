"""Client for Yandex Metrica Logs API.

The client creates log requests for visits and hits, waits until Yandex prepares
parts, downloads TSV data, and converts it to pandas DataFrames.
"""
from __future__ import annotations

import io
import os
import time
from dataclasses import dataclass
from typing import Iterable

import pandas as pd
import requests

API_BASE = "https://api-metrika.yandex.net/management/v1/counter"


class MetrikaAPIError(RuntimeError):
    """Raised when the Logs API returns an error or a request times out."""


VISIT_FIELDS = [
    "ym:s:visitID", "ym:s:clientID", "ym:s:dateTime", "ym:s:startURL",
    "ym:s:endURL", "ym:s:pageViews", "ym:s:visitDuration", "ym:s:bounce",
    "ym:s:goalsID", "ym:s:lastTrafficSource", "ym:s:UTMCampaign",
    "ym:s:UTMSource", "ym:s:UTMTerm", "ym:s:deviceCategory",
    "ym:s:browser", "ym:s:regionCountry", "ym:s:regionCity",
    "ym:s:screenWidth", "ym:s:screenHeight",
]

HIT_FIELDS = [
    "ym:pv:visitID", "ym:pv:URL", "ym:pv:dateTime", "ym:pv:title",
    "ym:pv:goalsID", "ym:pv:referer", "ym:pv:artificial", "ym:pv:params",
]


def get_metrika_token() -> str | None:
    """Read the Yandex Metrica token from Streamlit secrets or env vars."""
    token = os.getenv("YANDEX_METRIKA_TOKEN")
    if token:
        return token
    try:
        import streamlit as st

        return st.secrets.get("YANDEX_METRIKA_TOKEN")
    except Exception:
        return None


@dataclass(frozen=True)
class LogRequestResult:
    request_id: int
    dataframe: pd.DataFrame


class MetrikaLogsClient:
    def __init__(self, token: str | None = None, timeout: int = 60) -> None:
        self.token = token or get_metrika_token()
        if not self.token:
            raise MetrikaAPIError("Не задан YANDEX_METRIKA_TOKEN: включен демо-режим")
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"Authorization": f"OAuth {self.token}", "Accept-Encoding": "gzip"})

    def fetch_visits_and_hits(self, counter_id: int, date_from: str, date_to: str) -> tuple[pd.DataFrame, pd.DataFrame]:
        visits = self.fetch_log(counter_id, "visits", date_from, date_to, VISIT_FIELDS).dataframe
        hits = self.fetch_log(counter_id, "hits", date_from, date_to, HIT_FIELDS).dataframe
        return visits, hits

    def fetch_log(self, counter_id: int, source: str, date_from: str, date_to: str, fields: Iterable[str]) -> LogRequestResult:
        request_id = self._create_request(counter_id, source, date_from, date_to, fields)
        info = self._wait_processed(counter_id, request_id)
        frames = [self._download_part(counter_id, request_id, p["part_number"]) for p in info.get("parts", [])]
        df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        self._clean_request(counter_id, request_id)
        return LogRequestResult(request_id, df)

    def _url(self, counter_id: int, suffix: str) -> str:
        return f"{API_BASE}/{counter_id}/logrequests{suffix}"

    def _request(self, method: str, url: str, **kwargs) -> requests.Response:
        response = self.session.request(method, url, timeout=self.timeout, **kwargs)
        if response.status_code >= 400:
            raise MetrikaAPIError(f"Ошибка Logs API {response.status_code}: {response.text[:1000]}")
        return response

    def _create_request(self, counter_id: int, source: str, date_from: str, date_to: str, fields: Iterable[str]) -> int:
        params = {"source": source, "date1": date_from, "date2": date_to, "fields": ",".join(fields)}
        data = self._request("POST", self._url(counter_id, ""), params=params).json()
        return int(data["log_request"]["request_id"])

    def _wait_processed(self, counter_id: int, request_id: int, poll_seconds: int = 10, max_wait_seconds: int = 900) -> dict:
        deadline = time.time() + max_wait_seconds
        while time.time() < deadline:
            data = self._request("GET", self._url(counter_id, f"/{request_id}")).json()["log_request"]
            status = data.get("status")
            if status == "processed":
                return data
            if status in {"canceled", "failed"}:
                raise MetrikaAPIError(f"Log request {request_id} завершился со статусом {status}")
            time.sleep(poll_seconds)
        raise MetrikaAPIError(f"Log request {request_id} не подготовлен за {max_wait_seconds} секунд")

    def _download_part(self, counter_id: int, request_id: int, part_number: int) -> pd.DataFrame:
        response = self._request("GET", self._url(counter_id, f"/{request_id}/part/{part_number}/download"))
        return pd.read_csv(io.StringIO(response.text), sep="\t")

    def _clean_request(self, counter_id: int, request_id: int) -> None:
        try:
            self._request("POST", self._url(counter_id, f"/{request_id}/clean"))
        except MetrikaAPIError:
            pass
