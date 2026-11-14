# 复用当前原生连接读取环境域，完整分页并复核修订后才交付家具观测。
from src.integrations.native_core.snapshots.native_raw_snapshot import read_native_raw_domain
from src.services.native_session.inventory import require_current_sync_revision


def read_furniture_snapshot(owner, client, check):
    current = lambda: owner._check_projection(client, check)
    with owner._snapshot_scope(current):
        current()
        baseline = owner._baseline

        def call(method, params):
            if method == 'native.snapshot.refresh':
                return owner._refresh_snapshot(client, params, current)
            return client.call(method, params, check_cancelled=current)

        snapshot = read_native_raw_domain(call, current, 'environment')
        current()
        require_current_sync_revision(snapshot, call('native.snapshot.status', {}))
        current()
        baseline.put("environment", snapshot)
        return snapshot
