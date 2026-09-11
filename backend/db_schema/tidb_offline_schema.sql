CREATE DATABASE IF NOT EXISTS `daytrade` DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

USE `daytrade`;

CREATE TABLE IF NOT EXISTS `vwap_pattern_scan` (
    scan_date DATE NOT NULL,
    stock_id VARCHAR(16) NOT NULL,
    stock_name VARCHAR(80) NOT NULL DEFAULT '',
    pattern_type VARCHAR(64) NOT NULL,
    pattern_name VARCHAR(80) NOT NULL DEFAULT '',
    timeframe VARCHAR(16) NOT NULL DEFAULT 'day',
    score DOUBLE NULL,
    event_date DATE NULL,
    payload_json LONGTEXT NULL,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (scan_date, stock_id, pattern_type, timeframe),
    KEY idx_scan_score (scan_date, score),
    KEY idx_stock_date (stock_id, scan_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `vwap_activity` (
    scan_date DATE NOT NULL,
    stock_id VARCHAR(16) NOT NULL,
    day_atr DOUBLE NULL,
    open5_rng DOUBLE NULL,
    vol5_pr DOUBLE NULL,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (scan_date, stock_id),
    KEY idx_activity_stock (stock_id, scan_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `vwap_signals` (
    scan_date DATE NOT NULL,
    kind VARCHAR(24) NOT NULL,
    stock_id VARCHAR(16) NOT NULL DEFAULT '',
    event_time VARCHAR(16) NOT NULL DEFAULT '',
    value DOUBLE NULL,
    payload_json LONGTEXT NULL,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (scan_date, kind, stock_id, event_time),
    KEY idx_signal_stock (stock_id, scan_date),
    KEY idx_signal_kind_date (kind, scan_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `vwap_signal_bundles` (
    scan_date DATE NOT NULL,
    payload_gzip LONGBLOB NOT NULL,
    source_row_count INT NOT NULL,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (scan_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `vwap_chart_day` (
    stock_id VARCHAR(16) NOT NULL,
    bar_date DATE NOT NULL,
    open DOUBLE NOT NULL,
    high DOUBLE NOT NULL,
    low DOUBLE NOT NULL,
    close DOUBLE NOT NULL,
    volume BIGINT NOT NULL,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (stock_id, bar_date),
    KEY idx_chart_day_date (bar_date, stock_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `vwap_chart_m1` (
    stock_id VARCHAR(16) NOT NULL,
    bar_time DATETIME NOT NULL,
    bar_date DATE NOT NULL,
    open DOUBLE NOT NULL,
    high DOUBLE NOT NULL,
    low DOUBLE NOT NULL,
    close DOUBLE NOT NULL,
    volume BIGINT NOT NULL,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (stock_id, bar_time),
    KEY idx_chart_m1_date_stock (bar_date, stock_id, bar_time),
    KEY idx_chart_m1_time (bar_time)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `vwap_offline_shard_syncs` (
    source_path VARCHAR(255) NOT NULL,
    dataset VARCHAR(64) NOT NULL,
    month_key VARCHAR(7) NOT NULL,
    date_count INT NOT NULL,
    row_count INT NOT NULL,
    sha256 CHAR(64) NOT NULL,
    synced_at DATETIME NOT NULL,
    PRIMARY KEY (source_path),
    KEY idx_dataset_month (dataset, month_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
