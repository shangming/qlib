# Collect China Mainboard A-share Data From Baostock

This collector downloads China mainboard A-share daily OHLCV data from Baostock and converts it
into the CSV format expected by Qlib's `dump_bin.py`.

The default stock universe keeps only Shanghai mainboard `SH60*` and Shenzhen mainboard `SZ00*`.
It intentionally excludes STAR Market `SH68*` and ChiNext `SZ30*`.

Baostock access can be slow or unstable. Use a small `--limit_nums` first to verify the network
and output format before collecting the full mainboard universe.

## Requirements

```bash
pip install -r requirements.txt
```

## Daily Data

Run commands from this directory unless paths are absolute.

### 1. Download raw data

```bash
python collector.py download_data \
  --source_dir ~/.qlib/stock_data/source/baostock_cn_1d \
  --region CN \
  --interval 1d \
  --start 2024-01-01 \
  --end 2024-03-31 \
  --adjust qfq \
  --delay 0.5 \
  --limit_nums 10
```

Useful options:

- `adjust`: `qfq` by default. It queries raw prices and qfq close, then computes Qlib `factor`.
  Use `none` to store unadjusted prices with `factor=1`.
- `symbols_file`: optional text file with one symbol per line. Supported formats include
  `sh.600000`, `SH600000`, and `600000`.
- `symbol_regex`: optional regex matched against both Baostock symbols and Qlib symbols.
- `include_delisted`: `True` by default when Baostock returns delisted symbols from
  `query_stock_basic`.

If you previously generated data that included STAR Market or ChiNext stocks, use a new
`--qlib_dir` or clear the old source, normalize, and qlib output directories before rerunning.

### 2. Normalize data

```bash
python collector.py normalize_data \
  --source_dir ~/.qlib/stock_data/source/baostock_cn_1d \
  --normalize_dir ~/.qlib/stock_data/source/baostock_cn_1d_nor \
  --region CN \
  --interval 1d
```

Normalization creates adjusted Qlib fields:

- `open`, `high`, `low`, `close`: adjusted and normalized so the first valid close is `1`.
- `volume`: adjusted consistently with Qlib's `factor`.
- `factor`: `normalized_adjusted_price / raw_price`, so `$close / $factor` recovers raw close.
- `paused`: `1` for suspended or missing trading days, otherwise `0`.

### 3. Dump into Qlib format

From the repository root:

```bash
python scripts/dump_bin.py dump_all \
  --data_path ~/.qlib/stock_data/source/baostock_cn_1d_nor \
  --qlib_dir ~/.qlib/qlib_data/cn_baostock \
  --freq day \
  --include_fields open,close,high,low,volume,amount,turn,change,factor,paused,isST \
  --date_field_name date \
  --symbol_field_name symbol \
  --file_suffix .csv
```

### 4. Add index instruments

```bash
python scripts/data_collector/cn_index/collector.py \
  --index_name CSI300 \
  --qlib_dir ~/.qlib/qlib_data/cn_baostock \
  --method parse_instruments

python scripts/data_collector/cn_index/collector.py \
  --index_name CSI500 \
  --qlib_dir ~/.qlib/qlib_data/cn_baostock \
  --method parse_instruments
```

### 5. Use data

```python
import qlib
from qlib.data import D

qlib.init(provider_uri="~/.qlib/qlib_data/cn_baostock", region="cn")
df = D.features(["SH600000"], ["$close", "$factor", "$paused"], freq="day")
```

## Health Check

```bash
python scripts/check_data_health.py check_data \
  --qlib_dir ~/.qlib/qlib_data/cn_baostock \
  --freq day
```
