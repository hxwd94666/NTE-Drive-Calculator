-- 战报目录的主静态只读投影；与主库一起构建和晋升，分析程序不内嵌游戏目录。
CREATE TABLE battle_analysis_catalog (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    format_version INTEGER NOT NULL CHECK (format_version = 1),
    codec TEXT NOT NULL CHECK (codec = 'xz-json'),
    decoded_bytes INTEGER NOT NULL CHECK (decoded_bytes > 0 AND decoded_bytes <= 67108864),
    decoded_sha256 TEXT NOT NULL CHECK (length(decoded_sha256) = 64),
    payload BLOB NOT NULL
);
