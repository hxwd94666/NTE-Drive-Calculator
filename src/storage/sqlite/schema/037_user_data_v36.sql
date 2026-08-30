-- 用户数据库 v36：逐场冻结 nte-core 构建来源。

ALTER TABLE battle_record ADD COLUMN nte_core_version TEXT;
ALTER TABLE battle_record ADD COLUMN nte_core_protocol_version INTEGER
    CHECK (
        nte_core_protocol_version IS NULL
        OR nte_core_protocol_version >= 1
    );
ALTER TABLE battle_record ADD COLUMN nte_core_data_version TEXT;
ALTER TABLE battle_record ADD COLUMN nte_core_executable_sha256 TEXT
    CHECK (
        nte_core_executable_sha256 IS NULL
        OR length(nte_core_executable_sha256) = 64
    );
