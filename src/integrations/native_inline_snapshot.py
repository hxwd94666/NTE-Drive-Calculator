# 复用正式分页附带的原始记录，按既有快照契约校验后建立共享基线。
from src.integrations.native_raw_snapshot import read_native_raw_domain
from src.integrations.nte_core_protocol import NteCoreProtocolError


INLINE_RAW_CAPABILITY = "native_snapshot_inline_raw_v1"
_PROJECTION_FIELDS = frozenset({
    "items", "profiles", "characters", "referencedItemUids", "projectionMissing",
    "projectionComplete", "statProvenance", "rawRecords",
})


class InlineSnapshotPages:
    """Request-local pages only; publication still requires live revision validation."""

    def __init__(self, call, domain):
        self._call, self._domain = call, domain
        self._pages = {}
        self._header = None

    def call(self, method, params):
        page = self._call(method, params)
        if method == "native.snapshot.refresh":
            self._header = page
        if method == f"native.{self._domain}.page":
            if not isinstance(page, dict) or not isinstance(page.get("rawRecords"), list):
                raise NteCoreProtocolError("原生组件已声明随页证据能力，但没有返回原始记录。")
            offset = params["offset"]
            if offset in self._pages:
                raise NteCoreProtocolError("原生随页证据游标重复。")
            self._pages[offset] = {key: value for key, value in page.items() if key not in _PROJECTION_FIELDS}
            self._pages[offset]["records"] = page["rawRecords"]
        # The formal reader bounds the complete response, including rawRecords.
        # It returns only its explicitly selected business fields.
        return page

    def read(self, projection, check):
        header = self._header
        if not isinstance(header, dict) or header.get("snapshotId") != projection["snapshotId"]:
            raise NteCoreProtocolError("原生随页证据缺少同次刷新元数据。")

        def local_page(method, params):
            if (method != "native.snapshot.page" or params["domain"] != self._domain
                    or params["snapshotId"] != header["snapshotId"]):
                raise NteCoreProtocolError("原生随页证据请求身份不一致。")
            try:
                return self._pages.pop(params["offset"])
            except KeyError as error:
                raise NteCoreProtocolError("原生随页证据缺少分页。") from error

        raw = read_native_raw_domain(local_page, check, self._domain, header=header)
        if self._pages:
            raise NteCoreProtocolError("原生随页证据存在未消费的分页。")
        return raw
