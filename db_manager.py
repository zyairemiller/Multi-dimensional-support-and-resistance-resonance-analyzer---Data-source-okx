"""
Local Database Management Module - Cache candlestick data using SQLite

Avoids full API fetch on every run, significantly reducing request count and startup time
"""

import sqlite3
import logging
import pandas as pd
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Default database path
DEFAULT_DB_PATH = Path(__file__).parent / "data" / "trading.db"


class DBManager:
    """Local database manager, responsible for candlestick data cache read/write"""

    def __init__(self, db_path: Path = None):
        """
        Initialize database manager

        Args:
            db_path: Database file path, defaults to ./data/trading.db
        """
        self.db_path = Path(db_path) if db_path else DEFAULT_DB_PATH

        # Create data directory if it doesn't exist
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        # Initialize database connection (check_same_thread=False allows cross-thread access, for pywebview background analysis thread)
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")

        # Initialize tables
        self._init_tables()

        logger.info(f"Database connected: {self.db_path}")

    def _init_tables(self):
        """Create candlestick table if it doesn't exist"""
        create_sql = """
        CREATE TABLE IF NOT EXISTS candles (
            inst_id TEXT NOT NULL,
            bar TEXT NOT NULL,
            ts INTEGER NOT NULL,
            open REAL NOT NULL,
            high REAL NOT NULL,
            low REAL NOT NULL,
            close REAL NOT NULL,
            vol REAL,
            volCcy REAL,
            PRIMARY KEY (inst_id, bar, ts)
        )
        """
        cursor = self._conn.cursor()
        cursor.execute(create_sql)

        # Create indexes to speed up queries
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_candles_inst_bar
            ON candles(inst_id, bar)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_candles_ts
            ON candles(inst_id, bar, ts)
        """)
        self._conn.commit()

    def get_latest_ts(self, inst_id: str, bar: str) -> Optional[int]:
        """
        Get the latest timestamp (milliseconds) for an instrument and timeframe in the local database

        Args:
            inst_id: Product ID, e.g., BTC-USDT-SWAP
            bar: Candlestick timeframe, e.g., 1H, 4H, 1D

        Returns:
            Latest timestamp (milliseconds), or None if no data
        """
        try:
            cursor = self._conn.cursor()
            cursor.execute(
                "SELECT MAX(ts) FROM candles WHERE inst_id=? AND bar=?",
                (inst_id, bar)
            )
            result = cursor.fetchone()[0]
            return result
        except Exception as e:
            logger.warning(f"Failed to query latest timestamp: {e}")
            return None

    def get_earliest_ts(self, inst_id: str, bar: str) -> Optional[int]:
        """
        Get the earliest timestamp (milliseconds) for an instrument and timeframe in the local database

        Args:
            inst_id: Product ID, e.g., BTC-USDT-SWAP
            bar: Candlestick timeframe, e.g., 1H, 4H, 1D

        Returns:
            Earliest timestamp (milliseconds), or None if no data
        """
        try:
            cursor = self._conn.cursor()
            cursor.execute(
                "SELECT MIN(ts) FROM candles WHERE inst_id=? AND bar=?",
                (inst_id, bar)
            )
            result = cursor.fetchone()[0]
            return result
        except Exception as e:
            logger.warning(f"Failed to query earliest timestamp: {e}")
            return None

    def get_candles(
        self,
        inst_id: str,
        bar: str,
        start_ts: int = None,
        end_ts: int = None,
        limit: int = None
    ) -> pd.DataFrame:
        """
        Read candlestick data from local database

        Args:
            inst_id: Product ID
            bar: Candlestick timeframe
            start_ts: Start timestamp (milliseconds), optional
            end_ts: End timestamp (milliseconds), optional
            limit: Maximum number of records to return, optional

        Returns:
            DataFrame, columns: ts, open, high, low, close, vol, volCcy
            ts column is datetime type (consistent with fetch_candles)
        """
        try:
            conditions = ["inst_id=?", "bar=?"]
            params = [inst_id, bar]

            if start_ts is not None:
                conditions.append("ts>=?")
                params.append(start_ts)
            if end_ts is not None:
                conditions.append("ts<=?")
                params.append(end_ts)

            where_clause = " AND ".join(conditions)
            sql = f"SELECT ts, open, high, low, close, vol, volCcy FROM candles WHERE {where_clause} ORDER BY ts ASC"

            if limit is not None:
                sql += f" LIMIT {limit}"

            df = pd.read_sql_query(sql, self._conn, params=params)

            if df.empty:
                return df

            # Convert ts milliseconds to datetime (consistent with fetch_candles return format)
            df["ts"] = pd.to_datetime(df["ts"], unit="ms")

            return df
        except Exception as e:
            logger.warning(f"Failed to read local candlestick data: {e}")
            return pd.DataFrame(columns=["ts", "open", "high", "low", "close", "vol", "volCcy"])

    def save_candles(self, inst_id: str, bar: str, df: pd.DataFrame):
        """
        Write candlestick data to local database

        Uses INSERT OR REPLACE (based on inst_id+bar+ts unique key) to avoid duplicates
        Batch write (executemany), 1000 records per batch

        Args:
            inst_id: Product ID
            bar: Candlestick timeframe
            df: Candlestick data DataFrame, must contain ts, open, high, low, close, vol, volCcy
        """
        if df is None or df.empty:
            return

        try:
            # Prepare data: ensure ts is milliseconds integer
            rows = []
            for _, row in df.iterrows():
                ts_val = row["ts"]

                # If ts is datetime, convert to millisecond timestamp
                if hasattr(ts_val, "timestamp"):
                    ts_ms = int(ts_val.value // 1_000_000)
                elif isinstance(ts_val, (int, float)):
                    # Determine if seconds or milliseconds (millisecond timestamp usually > 1e12)
                    ts_ms = int(ts_val) if ts_val > 1e12 else int(ts_val * 1000)
                else:
                    # Try to parse
                    try:
                        ts_ms = int(float(ts_val))
                        if ts_ms < 1e12:
                            ts_ms = ts_ms * 1000
                    except (ValueError, TypeError):
                        continue

                rows.append((
                    inst_id,
                    bar,
                    ts_ms,
                    float(row["open"]) if pd.notna(row["open"]) else 0.0,
                    float(row["high"]) if pd.notna(row["high"]) else 0.0,
                    float(row["low"]) if pd.notna(row["low"]) else 0.0,
                    float(row["close"]) if pd.notna(row["close"]) else 0.0,
                    float(row["vol"]) if pd.notna(row.get("vol", 0)) else 0.0,
                    float(row["volCcy"]) if pd.notna(row.get("volCcy", 0)) else 0.0,
                ))

            if not rows:
                return

            # Batch write, 1000 records per batch
            insert_sql = """
            INSERT OR REPLACE INTO candles (inst_id, bar, ts, open, high, low, close, vol, volCcy)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """
            cursor = self._conn.cursor()
            batch_size = 1000
            for i in range(0, len(rows), batch_size):
                batch = rows[i:i + batch_size]
                cursor.executemany(insert_sql, batch)
                self._conn.commit()

            logger.info(f"Written to database: {inst_id} {bar} total {len(rows)} records")
        except Exception as e:
            logger.error(f"Failed to write to database: {e}")
            try:
                self._conn.rollback()
            except Exception:
                pass

    def get_candle_count(self, inst_id: str, bar: str) -> int:
        """
        Get the number of data records for an instrument and timeframe in the local database

        Args:
            inst_id: Product ID
            bar: Candlestick timeframe

        Returns:
            Number of data records
        """
        try:
            cursor = self._conn.cursor()
            cursor.execute(
                "SELECT COUNT(*) FROM candles WHERE inst_id=? AND bar=?",
                (inst_id, bar)
            )
            result = cursor.fetchone()[0]
            return result
        except Exception as e:
            logger.warning(f"Failed to query data count: {e}")
            return 0

    def checkpoint(self):
        """Execute WAL checkpoint, merge WAL data back to main database file to prevent WAL from growing indefinitely"""
        try:
            if self._conn:
                self._conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
        except Exception as e:
            logger.warning(f"WAL checkpoint failed: {e}")

    def close(self):
        """Close database connection, ensure all data is committed"""
        try:
            if self._conn:
                self._conn.commit()
                self._conn.close()
                logger.info("Database connection closed")
        except Exception as e:
            logger.warning(f"Error closing database connection: {e}")
