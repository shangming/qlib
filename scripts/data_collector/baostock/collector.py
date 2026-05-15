# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import re
import sys
from pathlib import Path
from typing import Iterable, List, Optional

import baostock as bs
import fire
import numpy as np
import pandas as pd
from loguru import logger

CUR_DIR = Path(__file__).resolve().parent
sys.path.append(str(CUR_DIR.parent.parent))

from data_collector.base import BaseCollector, BaseNormalize, BaseRun


HISTORY_FIELDS = (
    "date,code,open,high,low,close,preclose,volume,amount,adjustflag,"
    "turn,tradestatus,pctChg,isST"
)
_BAOSTOCK_LOGGED_IN = False


def _login_baostock():
    global _BAOSTOCK_LOGGED_IN
    if _BAOSTOCK_LOGGED_IN:
        return
    lg = bs.login()
    if getattr(lg, "error_code", "0") != "0":
        raise RuntimeError(f"baostock login failed: {lg.error_code} {lg.error_msg}")
    _BAOSTOCK_LOGGED_IN = True


def _to_baostock_symbol(symbol: str) -> str:
    symbol = str(symbol).strip()
    if not symbol:
        raise ValueError("symbol is empty")

    if "." in symbol:
        code, exchange = symbol.split(".", maxsplit=1)
        exchange = exchange.lower()
        exchange = "sh" if exchange in {"sh", "ss"} else exchange
        return f"{exchange}.{code.zfill(6)}"

    symbol = symbol.upper()
    if symbol.startswith(("SH", "SZ")):
        return f"{symbol[:2].lower()}.{symbol[2:].zfill(6)}"

    code = symbol.zfill(6)
    exchange = "sh" if code.startswith(("6", "9")) else "sz"
    return f"{exchange}.{code}"


def _to_qlib_symbol(symbol: str) -> str:
    symbol = _to_baostock_symbol(symbol)
    exchange, code = symbol.split(".", maxsplit=1)
    return f"{exchange.upper()}{code}"


def _is_cn_mainboard_stock(symbol: str) -> bool:
    try:
        symbol = _to_baostock_symbol(symbol)
    except ValueError:
        return False
    exchange, code = symbol.split(".", maxsplit=1)
    if exchange == "sh":
        return code.startswith("60")
    if exchange == "sz":
        return code.startswith("00")
    return False


def _result_to_dataframe(result) -> pd.DataFrame:
    rows = []
    while result.error_code == "0" and result.next():
        rows.append(result.get_row_data())
    return pd.DataFrame(rows, columns=result.fields)


def _read_symbols_file(symbols_file: [str, Path]) -> List[str]:
    symbols = []
    with Path(symbols_file).expanduser().open("r", encoding="utf-8") as fp:
        for line in fp:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            symbols.append(_to_baostock_symbol(line.split()[0]))
    return symbols


class BaostockCollectorCN1d(BaseCollector):
    """Collect China mainboard A-share daily data from Baostock."""

    def __init__(
        self,
        save_dir: [str, Path],
        start=None,
        end=None,
        interval="1d",
        max_workers=1,
        max_collector_count=2,
        delay=0,
        check_data_length: int = None,
        limit_nums: int = None,
        adjust: str = "qfq",
        symbols_file: Optional[str] = None,
        symbol_regex: Optional[str] = None,
        include_delisted: bool = True,
    ):
        self.adjustflag = self._normalize_adjust(adjust)
        self.symbols_file = symbols_file
        self.symbol_regex = re.compile(symbol_regex) if symbol_regex else None
        self.include_delisted = include_delisted
        _login_baostock()
        super().__init__(
            save_dir=save_dir,
            start=start,
            end=end,
            interval=interval,
            max_workers=max_workers,
            max_collector_count=max_collector_count,
            delay=delay,
            check_data_length=check_data_length,
            limit_nums=limit_nums,
        )

    @staticmethod
    def _normalize_adjust(adjust: str) -> str:
        adjust = str(adjust or "qfq").lower()
        adjust_map = {
            "hfq": "1",
            "backward": "1",
            "1": "1",
            "qfq": "2",
            "forward": "2",
            "2": "2",
            "none": "3",
            "raw": "3",
            "3": "3",
        }
        if adjust not in adjust_map:
            raise ValueError("adjust must be one of: qfq, hfq, none")
        return adjust_map[adjust]

    @staticmethod
    def _query_stock_basic(include_delisted: bool) -> List[str]:
        _login_baostock()
        rs = bs.query_stock_basic()
        if rs.error_code != "0":
            raise RuntimeError(f"query_stock_basic failed: {rs.error_code} {rs.error_msg}")
        df = _result_to_dataframe(rs)
        if df.empty:
            return []
        if "type" in df.columns:
            df = df[df["type"] == "1"]
        if not include_delisted and "status" in df.columns:
            df = df[df["status"] == "1"]
        return df["code"].dropna().map(_to_baostock_symbol).tolist()

    def _query_all_stock(self) -> List[str]:
        _login_baostock()
        day = min(pd.Timestamp.today().normalize(), self.end_datetime - pd.Timedelta(days=1))
        rs = bs.query_all_stock(day=day.strftime("%Y-%m-%d"))
        if rs.error_code != "0":
            raise RuntimeError(f"query_all_stock failed: {rs.error_code} {rs.error_msg}")
        df = _result_to_dataframe(rs)
        if df.empty:
            return []
        return df["code"].dropna().map(_to_baostock_symbol).tolist()

    def get_instrument_list(self) -> List[str]:
        logger.info("get CN mainboard A-share stock symbols from Baostock......")
        if self.symbols_file:
            symbols = _read_symbols_file(self.symbols_file)
        else:
            symbols = self._query_stock_basic(self.include_delisted)
            if not symbols:
                symbols = self._query_all_stock()

        symbols = [s for s in symbols if _is_cn_mainboard_stock(s)]
        if self.symbol_regex is not None:
            symbols = [
                s
                for s in symbols
                if self.symbol_regex.search(s) or self.symbol_regex.search(_to_qlib_symbol(s))
            ]
        symbols = sorted(set(symbols))
        logger.info(f"get {len(symbols)} symbols.")
        return symbols

    def normalize_symbol(self, symbol: str):
        return _to_qlib_symbol(symbol)

    @staticmethod
    def _query_history(
        symbol: str,
        fields: str,
        start_datetime: pd.Timestamp,
        end_datetime: pd.Timestamp,
        adjustflag: str,
    ) -> pd.DataFrame:
        _login_baostock()
        rs = bs.query_history_k_data_plus(
            symbol,
            fields,
            start_date=start_datetime.strftime("%Y-%m-%d"),
            end_date=end_datetime.strftime("%Y-%m-%d"),
            frequency="d",
            adjustflag=adjustflag,
        )
        if rs.error_code != "0":
            raise RuntimeError(f"query_history_k_data_plus failed: {symbol} {rs.error_code} {rs.error_msg}")
        return _result_to_dataframe(rs)

    def get_data(
        self, symbol: str, interval: str, start_datetime: pd.Timestamp, end_datetime: pd.Timestamp
    ) -> pd.DataFrame:
        if interval != self.INTERVAL_1d:
            raise ValueError(f"cannot support interval={interval}")
        symbol = _to_baostock_symbol(symbol)
        raw_df = self._query_history(symbol, HISTORY_FIELDS, start_datetime, end_datetime, adjustflag="3")
        if raw_df.empty:
            return raw_df

        if self.adjustflag == "3":
            raw_df["adjclose"] = raw_df["close"]
        else:
            adj_df = self._query_history(symbol, "date,code,close", start_datetime, end_datetime, self.adjustflag)
            if adj_df.empty:
                raw_df["adjclose"] = raw_df["close"]
            else:
                adj_df = adj_df.rename(columns={"close": "adjclose"})
                raw_df = raw_df.merge(adj_df[["date", "code", "adjclose"]], on=["date", "code"], how="left")

        raw_df = raw_df.rename(columns={"code": "symbol"})
        raw_df["symbol"] = raw_df["symbol"].map(_to_qlib_symbol)
        return raw_df


class BaostockNormalizeCN1d(BaseNormalize):
    COLUMNS = ["open", "close", "high", "low", "volume"]
    PRICE_COLUMNS = ["open", "close", "high", "low", "preclose"]
    NUMERIC_COLUMNS = [
        "open",
        "high",
        "low",
        "close",
        "preclose",
        "volume",
        "amount",
        "turn",
        "tradestatus",
        "pctChg",
        "isST",
        "adjustflag",
        "adjclose",
    ]

    @staticmethod
    def _get_baostock_calendar() -> Iterable[pd.Timestamp]:
        _login_baostock()
        end = pd.Timestamp.today().strftime("%Y-%m-%d")
        rs = bs.query_trade_dates(start_date="1990-01-01", end_date=end)
        if rs.error_code != "0":
            raise RuntimeError(f"query_trade_dates failed: {rs.error_code} {rs.error_msg}")
        df = _result_to_dataframe(rs)
        if df.empty:
            return []
        return sorted(map(pd.Timestamp, df[df["is_trading_day"] == "1"]["calendar_date"].tolist()))

    def _get_calendar_list(self):
        return self._get_baostock_calendar()

    @staticmethod
    def calc_change(close: pd.Series) -> pd.Series:
        close = close.ffill()
        return close / close.shift(1) - 1

    @staticmethod
    def normalize_baostock(
        df: pd.DataFrame,
        calendar_list: list = None,
        date_field_name: str = "date",
        symbol_field_name: str = "symbol",
    ):
        if df.empty:
            return df

        symbol = _to_qlib_symbol(df.loc[df[symbol_field_name].first_valid_index(), symbol_field_name])
        if not _is_cn_mainboard_stock(symbol):
            return pd.DataFrame()

        df = df.copy()
        df[date_field_name] = pd.to_datetime(df[date_field_name])
        df = df.drop_duplicates(date_field_name, keep="last").sort_values(date_field_name)
        df[symbol_field_name] = symbol

        for col in BaostockNormalizeCN1d.NUMERIC_COLUMNS:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        df = df.set_index(date_field_name)
        if calendar_list is not None:
            calendar_index = pd.DatetimeIndex(calendar_list)
            calendar_index = calendar_index[
                (calendar_index >= df.index.min().normalize()) & (calendar_index <= df.index.max().normalize())
            ]
            df = df.reindex(calendar_index)

        df.sort_index(inplace=True)
        df[symbol_field_name] = symbol

        if "adjclose" not in df.columns:
            df["adjclose"] = df["close"]
        source_factor = df["adjclose"] / df["close"]
        source_factor = source_factor.replace([np.inf, -np.inf], np.nan).ffill()
        if source_factor.notna().any():
            source_factor = source_factor.fillna(source_factor.dropna().iloc[0])
        else:
            source_factor = pd.Series(1.0, index=df.index)

        tradestatus = (
            df["tradestatus"]
            if "tradestatus" in df.columns
            else pd.Series(1.0, index=df.index, dtype="float64")
        )
        paused = (
            tradestatus.ne(1)
            | df["close"].isna()
            | df["volume"].isna()
            | df["volume"].le(0)
        )

        df["factor"] = source_factor
        for col in BaostockNormalizeCN1d.PRICE_COLUMNS:
            if col in df.columns:
                df[col] = df[col] * source_factor
        if "volume" in df.columns:
            df["volume"] = df["volume"] / source_factor

        first_close_index = df["close"].first_valid_index()
        if first_close_index is None:
            return pd.DataFrame()
        first_close = df.loc[first_close_index, "close"]
        if not np.isfinite(first_close) or first_close == 0:
            return pd.DataFrame()

        for col in BaostockNormalizeCN1d.PRICE_COLUMNS:
            if col in df.columns:
                df[col] = df[col] / first_close
        if "volume" in df.columns:
            df["volume"] = df["volume"] * first_close
        df["factor"] = df["factor"] / first_close

        df["change"] = BaostockNormalizeCN1d.calc_change(df["close"])
        df["paused"] = paused.astype("float32")

        nan_cols = ["open", "high", "low", "close", "preclose", "volume", "amount", "turn", "change"]
        df.loc[paused, [col for col in nan_cols if col in df.columns]] = np.nan

        df.index.names = [date_field_name]
        return df.reset_index()

    def normalize(self, df: pd.DataFrame) -> pd.DataFrame:
        return self.normalize_baostock(df, self._calendar_list, self._date_field_name, self._symbol_field_name)


class Run(BaseRun):
    def __init__(self, source_dir=None, normalize_dir=None, max_workers=1, interval="1d", region="CN"):
        super().__init__(source_dir, normalize_dir, max_workers, interval)
        self.region = region

    @property
    def collector_class_name(self):
        return f"BaostockCollector{self.region.upper()}{self.interval}"

    @property
    def normalize_class_name(self):
        return f"BaostockNormalize{self.region.upper()}{self.interval}"

    @property
    def default_base_dir(self) -> [Path, str]:
        return CUR_DIR

    def download_data(
        self,
        max_collector_count=2,
        delay=0.5,
        start=None,
        end=None,
        check_data_length=None,
        limit_nums=None,
        adjust="qfq",
        symbols_file=None,
        symbol_regex=None,
        include_delisted=True,
    ):
        """Download China mainboard A-share daily data from Baostock."""
        if self.interval != "1d":
            raise ValueError("Baostock CN collector currently supports --interval 1d only")
        super().download_data(
            max_collector_count=max_collector_count,
            delay=delay,
            start=start,
            end=end,
            check_data_length=check_data_length,
            limit_nums=limit_nums,
            adjust=adjust,
            symbols_file=symbols_file,
            symbol_regex=symbol_regex,
            include_delisted=include_delisted,
        )


if __name__ == "__main__":
    try:
        fire.Fire(Run)
    finally:
        try:
            bs.logout()
        except Exception:
            pass
