"""
本地数据库管理模块 - 使用SQLite缓存K线数据

避免每次运行都全量从API拉取，大幅减少请求次数和启动时间
"""

import sqlite3
import logging
import pandas as pd
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# 默认数据库路径
DEFAULT_DB_PATH = Path(__file__).parent / "data" / "trading.db"


class DBManager:
    """本地数据库管理器，负责K线数据的缓存读写"""

    def __init__(self, db_path: Path = None):
        """
        初始化数据库管理器

        Args:
            db_path: 数据库文件路径，默认为 ./data/trading.db
        """
        self.db_path = Path(db_path) if db_path else DEFAULT_DB_PATH

        # 如果data目录不存在则创建
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        # 初始化数据库连接（check_same_thread=False 允许跨线程访问，配合pywebview后台分析线程）
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")

        # 初始化表
        self._init_tables()

        logger.info(f"数据库已连接: {self.db_path}")

    def _init_tables(self):
        """创建K线表（如果不存在）"""
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

        # 创建索引加速查询
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
        获取本地数据库中某个品种某个周期的最新时间戳（毫秒）

        Args:
            inst_id: 产品ID，如 BTC-USDT-SWAP
            bar: K线周期，如 1H, 4H, 1D

        Returns:
            最新时间戳（毫秒），没有数据则返回None
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
            logger.warning(f"查询最新时间戳失败: {e}")
            return None

    def get_earliest_ts(self, inst_id: str, bar: str) -> Optional[int]:
        """
        获取本地数据库中某个品种某个周期的最早时间戳（毫秒）

        Args:
            inst_id: 产品ID，如 BTC-USDT-SWAP
            bar: K线周期，如 1H, 4H, 1D

        Returns:
            最早时间戳（毫秒），没有数据则返回None
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
            logger.warning(f"查询最早时间戳失败: {e}")
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
        从本地数据库读取K线数据

        Args:
            inst_id: 产品ID
            bar: K线周期
            start_ts: 起始时间戳（毫秒），可选
            end_ts: 结束时间戳（毫秒），可选
            limit: 最多返回条数，可选

        Returns:
            DataFrame，列: ts, open, high, low, close, vol, volCcy
            ts列为datetime类型（与fetch_candles一致）
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

            # 将ts毫秒转为datetime（与fetch_candles返回格式一致）
            df["ts"] = pd.to_datetime(df["ts"], unit="ms")

            return df
        except Exception as e:
            logger.warning(f"读取本地K线数据失败: {e}")
            return pd.DataFrame(columns=["ts", "open", "high", "low", "close", "vol", "volCcy"])

    def save_candles(self, inst_id: str, bar: str, df: pd.DataFrame):
        """
        将K线数据写入本地数据库

        使用INSERT OR REPLACE（基于inst_id+bar+ts唯一键），避免重复
        批量写入（executemany），每1000条一批

        Args:
            inst_id: 产品ID
            bar: K线周期
            df: K线数据DataFrame，需包含 ts, open, high, low, close, vol, volCcy
        """
        if df is None or df.empty:
            return

        try:
            # 准备数据：确保ts为毫秒整数
            rows = []
            for _, row in df.iterrows():
                ts_val = row["ts"]

                # 如果ts是datetime，转为毫秒时间戳
                if hasattr(ts_val, "timestamp"):
                    ts_ms = int(ts_val.value // 1_000_000)
                elif isinstance(ts_val, (int, float)):
                    # 判断是秒还是毫秒（毫秒时间戳通常 > 1e12）
                    ts_ms = int(ts_val) if ts_val > 1e12 else int(ts_val * 1000)
                else:
                    # 尝试解析
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

            # 批量写入，每1000条一批
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

            logger.info(f"写入数据库: {inst_id} {bar} 共 {len(rows)} 条")
        except Exception as e:
            logger.error(f"写入数据库失败: {e}")
            try:
                self._conn.rollback()
            except Exception:
                pass

    def get_candle_count(self, inst_id: str, bar: str) -> int:
        """
        获取本地数据库中某个品种某个周期的数据条数

        Args:
            inst_id: 产品ID
            bar: K线周期

        Returns:
            数据条数
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
            logger.warning(f"查询数据条数失败: {e}")
            return 0

    def checkpoint(self):
        """执行WAL checkpoint，将WAL数据合并回主数据库文件，防止WAL无限膨胀"""
        try:
            if self._conn:
                self._conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
        except Exception as e:
            logger.warning(f"WAL checkpoint 失败: {e}")

    def close(self):
        """关闭数据库连接，确保所有数据已提交"""
        try:
            if self._conn:
                self._conn.commit()
                self._conn.close()
                logger.info("数据库连接已关闭")
        except Exception as e:
            logger.warning(f"关闭数据库连接时出错: {e}")
