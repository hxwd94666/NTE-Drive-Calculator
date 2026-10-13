# 从维护文档和本地组件证据生成可离线阅读的跨项目逻辑地图，不执行编译或游戏操作。
"""Build a local HTML snapshot; never fetch, merge, deploy or embed private code."""
from __future__ import annotations

import argparse
import ast
import base64
import hashlib
import html
import json
from pathlib import Path
import re
import subprocess
from datetime import datetime


ROOT = Path(__file__).resolve().parents[2]
SOURCES: dict[str, dict] = {}
REPOS: dict[str, Path] = {}
IDENTITIES: dict[str, dict] = {}
NAV_PANELS = {
    'home': 'menu-1', 'execute': 'menu-3', 'equipment': 'menu-4',
    'my_role': 'menu-2', 'warehouse': 'menu-5', 'identify': 'menu-6',
    'battle_report': 'menu-7', 'blueprint': 'role-blueprints',
    'config': 'role-weights', 'toolbox': 'menu-8', 'plugins': 'menu-9',
    'static_catalog': 'catalog', 'settings': 'menu-10',
}


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(
        ['git', '-C', str(root), *args], encoding='utf-8', errors='strict',
    ).rstrip('\n')


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path: Path) -> str:
    return path.read_text(encoding='utf-8-sig')


def source(path: Path, fragment: str = '') -> str:
    path = path.resolve()
    owner = next((key for key, root in REPOS.items() if path.is_relative_to(root)), None)
    if owner is None or not path.is_file():
        raise ValueError(f'Missing or out-of-scope source: {path}')
    key = str(len(SOURCES))
    for existing, item in SOURCES.items():
        if item['path'] == str(path) and item['fragment'] == fragment:
            return existing
    relative = path.relative_to(REPOS[owner]).as_posix()
    SOURCES[key] = {
        'repo': owner, 'relative': relative, 'path': str(path),
        'sha256': digest(path), 'fragment': fragment,
        'head': IDENTITIES[owner]['head'],
        'dirty': bool(git(REPOS[owner], 'status', '--porcelain=v1', '--', relative)),
    }
    return key


def inline(text: str, origin: Path) -> str:
    tokens = []

    def keep(value: str) -> str:
        tokens.append(value)
        return f'\x00{len(tokens) - 1}\x00'

    def link(match: re.Match) -> str:
        label, target = match.groups()
        if target.startswith('https://'):
            return keep(f'<a href="{html.escape(target, quote=True)}" target="_blank" rel="noreferrer">{html.escape(label)}</a>')
        location, _, fragment = target.partition('#')
        key = source(origin.parent / location if location else origin, fragment)
        return keep(f'<button class="source-link" data-source="{key}" type="button">{html.escape(label)} ↗</button>')

    text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', link, text)
    text = re.sub(r'`([^`]+)`', lambda m: keep('<code>' + html.escape(m[1]) + '</code>'), text)
    text = html.escape(text)
    text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
    return re.sub(r'\x00(\d+)\x00', lambda m: tokens[int(m[1])], text)


def render(text: str, origin: Path) -> str:
    """只渲染当前文档约定的有限 Markdown；本地链接须先登记来源身份。"""
    lines = text.splitlines()
    output = []
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        if not line:
            index += 1
            continue
        if line.startswith('证据：'):
            output.append('<details class="evidence"><summary>查看依据与实现位置（可选）</summary><p>'
                          + inline(line[3:], origin) + '</p></details>')
            index += 1
            continue
        if line.startswith('```'):
            end = index + 1
            while end < len(lines) and not lines[end].startswith('```'):
                end += 1
            if end == len(lines):
                raise ValueError(f'Unclosed code block in {origin}')
            output.append('<pre>' + html.escape('\n'.join(lines[index + 1:end])) + '</pre>')
            index = end + 1
            continue
        if line.startswith('|'):
            rows = []
            while index < len(lines) and lines[index].strip().startswith('|'):
                cells = [cell.strip() for cell in lines[index].strip().strip('|').split('|')]
                if not all(re.fullmatch(r':?-+:?', cell) for cell in cells):
                    rows.append(cells)
                index += 1
            if any(len(row) != len(rows[0]) for row in rows):
                raise ValueError(f'Uneven table in {origin}')
            header = ''.join('<th>' + inline(cell, origin) + '</th>' for cell in rows[0])
            body = ''.join('<tr>' + ''.join('<td>' + inline(cell, origin) + '</td>' for cell in row) + '</tr>' for row in rows[1:])
            output.append('<div class="table-wrap"><table><thead><tr>' + header + '</tr></thead><tbody>' + body + '</tbody></table></div>')
            continue
        heading = re.match(r'^(#{1,4}) (.+)$', line)
        if heading:
            level = max(2, len(heading[1]))
            output.append(f'<h{level}>' + inline(heading[2], origin) + f'</h{level}>')
            index += 1
            continue
        if line.startswith('- '):
            items = []
            while index < len(lines) and lines[index].strip().startswith('- '):
                items.append('<li>' + inline(lines[index].strip()[2:], origin) + '</li>')
                index += 1
            output.append('<ul>' + ''.join(items) + '</ul>')
            continue
        output.append('<p>' + inline(line, origin) + '</p>')
        index += 1
    return '\n'.join(output)


def identity(root: Path) -> dict:
    return {
        'head': git(root, 'rev-parse', 'HEAD'),
        'branch': git(root, 'branch', '--show-current'),
        'dirty': bool(git(root, 'status', '--porcelain=v1')),
        'refs': git(root, 'for-each-ref', '--format=%(refname:short) %(objectname)', 'refs/remotes'),
    }


def components() -> list[dict]:
    bundle_path = ROOT / 'third_party/native-capture/component-bundle.json'
    bundle = json.loads(read(bundle_path))
    analysis_path = ROOT / 'third_party/analysis-core/component.json'
    analysis = json.loads(read(analysis_path))
    rows = []
    inputs = [
        ('采集 Core', bundle['roles']['core'], None),
        ('计算／分析 Core', 'third_party/analysis-core/bin/nte-analysis-core.exe', analysis.get('sha256')),
        ('游戏采集 DLL', bundle['roles']['capture_plugin'], None),
        ('D3D 插件宿主' if bundle.get('layout') in {'native-plugins-v2', 'native-plugins-v3'} else '最小 D3D 代理',
         bundle['roles']['host'], None),
    ]
    for role, label in (('user_plugin', '账号服务插件'), ('user_signature', '账号插件签名'),
                        ('capture_signature', '采集插件签名'), ('hud_plugin', 'HUD 展示插件'),
                        ('hud_signature', 'HUD 插件签名'), ('performance_plugin', '性能诊断插件'),
                        ('performance_signature', '性能插件签名'), ('loader', '当前 Loader')):
        if role in bundle['roles']:
            inputs.append((label, bundle['roles'][role], None))
    for label, relative, declared in inputs:
        path = ROOT / relative
        expected = declared or bundle['files'].get(relative)
        actual = digest(path) if path.is_file() else None
        rows.append({'name': label, 'path': relative, 'sha256': actual,
                     'declared': expected, 'matches': bool(actual and actual == expected)})
    core_copy = ROOT / 'third_party/nte-core/bin/nte-core.exe'
    rows[0]['copy_matches'] = core_copy.is_file() and digest(core_copy) == rows[0]['sha256']
    source(bundle_path)
    source(analysis_path)
    return rows


def protection() -> dict:
    root = REPOS['UETools']
    path = root / 'build/vmprotect_functions.json'
    policy = json.loads(read(path))
    upstream = json.loads(git(root, 'show', 'origin/main:build/vmprotect_functions.json'))
    rows = []
    for target, functions in policy['targets'].items():
        for entry in functions:
            rows.append({'target': target, 'function': entry['scope'] + '::' + entry['name'], 'mode': entry['mode']})
    extra = []
    for target, functions in upstream['targets'].items():
        for entry in functions:
            if entry not in policy['targets'].get(target, []):
                extra.append({'target': target, 'function': entry['scope'] + '::' + entry['name'], 'mode': entry['mode']})
    source(path)
    source(root / 'build/vmprotect.targets')
    source(root / 'tools/protect_release.py')
    manifest_path = ROOT / 'third_party/native-capture/capture/capture-component.json'
    manifest = json.loads(read(manifest_path))
    # Only extract protection metadata; do not embed source archives or local receipts.
    def find(value, trail=''):
        found = []
        if isinstance(value, dict):
            for key, item in value.items():
                location = f'{trail}.{key}' if trail else key
                if 'protect' in key.lower() or 'vmp' in key.lower():
                    found.append({'field': location, 'value': item})
                else:
                    found.extend(find(item, location))
        return found
    return {'current': rows, 'upstream_extra': extra,
            'policy_sha256': digest(path), 'upstream_commit': git(root, 'rev-parse', 'origin/main'),
            'manifest_protection': find(manifest)}


def operation_panels(document: Path) -> list[dict]:
    """按稳定 key 与 parent 校验文档导航；AST 读取应用声明，不导入 Qt 或应用。"""
    chunks = re.split(r'^## (.+)$', read(document), flags=re.M)
    panels = []
    heading = re.compile(
        r'^### (.+?) <!-- page: ([a-z0-9-]+); type: ([^;<>]+)'
        r'(?:; parent: ([a-z0-9-]+))? -->$', re.M,
    )
    for index in range(1, len(chunks), 2):
        root_id = f'menu-{index // 2}'
        body = chunks[index + 1]
        children = list(heading.finditer(body))
        panels.append({'id': root_id, 'title': chunks[index], 'kind': 'current',
                       'parent': None, 'entry_type': '菜单概览',
                       'body': render(body[:children[0].start()] if children else body, document)})
        for position, match in enumerate(children):
            end = children[position + 1].start() if position + 1 < len(children) else len(body)
            panels.append({'id': match[2], 'title': match[1], 'kind': 'current',
                           'parent': match[4] or root_id, 'entry_type': match[3],
                           'body': render(body[match.end():end], document)})
    by_id = {panel['id']: panel for panel in panels}
    if len(by_id) != len(panels):
        raise ValueError('Duplicate operation page identity')
    for panel in panels:
        ancestors = {panel['id']}
        parent = panel['parent']
        while parent:
            if parent not in by_id or parent in ancestors:
                raise ValueError(f'Missing parent or navigation cycle: {panel["id"]}')
            ancestors.add(parent)
            parent = by_id[parent]['parent']
    # Read app navigation metadata without importing the application or Qt.
    navigation = ROOT / 'src/ui/navigation.py'
    tree = ast.parse(read(navigation))
    assignment = next(node for node in tree.body if isinstance(node, ast.Assign)
                      and any(isinstance(target, ast.Name) and target.id == 'NAV_ITEMS' for target in node.targets))
    actual_keys = set()
    for order, call in enumerate(assignment.value.elts, 1):
        key = ast.literal_eval(call.args[0])
        actual_keys.add(key)
        if key not in NAV_PANELS or NAV_PANELS[key] not in by_id:
            raise ValueError(f'Application navigation is not documented: {key}')
        panel = by_id[NAV_PANELS[key]]
        panel['app_nav_key'] = key
        panel['order'] = order
        parent_key = next((ast.literal_eval(item.value) for item in call.keywords if item.arg == 'parent_key'), None)
        if parent_key and panel['parent'] != NAV_PANELS[parent_key]:
            raise ValueError(f'Application parent differs from documentation: {key}')
    if actual_keys != set(NAV_PANELS):
        raise ValueError('Stale application navigation mapping')
    source(navigation)
    return panels


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--core-root', type=Path, default=ROOT.parent / 'nte-dps-toolkit')
    parser.add_argument('--uetools-root', type=Path, default=ROOT.parent / 'UETools-NTE')
    parser.add_argument('--audit', type=Path, required=True, help='Local Markdown review; does not fetch remotes')
    parser.add_argument('--output', type=Path, default=ROOT / 'output/architecture-map/index.html')
    parser.add_argument('--ui-references', type=Path, help='Optional manifest from render_ui_reference.py')
    args = parser.parse_args()
    REPOS.update({'Calc': ROOT, 'Core 私库': args.core_root.resolve(), 'UETools': args.uetools_root.resolve()})
    IDENTITIES.update({name: identity(path) for name, path in REPOS.items()})
    document = ROOT / 'docs/architecture-map.md'
    panels = operation_panels(document)
    roadmap = ROOT / 'docs/roadmap/sync-lifecycle.md'
    panels.append({'id': 'target', 'title': '目标设计与实施顺序', 'kind': 'target', 'body': render(read(roadmap), roadmap)})
    panels.append({'id': 'audit', 'title': '上游核对与开发验收', 'kind': 'audit', 'body': render(read(args.audit), args.audit)})
    declaration = ROOT / 'RESPONSIBLE_USE.md'
    panels.append({'id': 'responsible-use', 'title': '项目用途、反滥用与协作声明',
                   'kind': 'policy', 'body': render(read(declaration), declaration)})
    data = {'generated': datetime.now().astimezone().isoformat(timespec='seconds'),
            'panels': panels, 'repos': IDENTITIES, 'components': components(), 'protection': protection()}
    if args.ui_references:
        manifest_path = args.ui_references.resolve()
        if not manifest_path.is_relative_to(ROOT / 'output'):
            raise ValueError('UI references must remain in local output')
        references = json.loads(read(manifest_path))
        for relative, expected in references['dependencies'].items():
            path = (ROOT / relative).resolve()
            if not path.is_relative_to(ROOT) or not path.is_file() or digest(path) != expected:
                raise ValueError(f'Stale UI reference; render again: {relative}')
        for entry in references['images']:
            path = (manifest_path.parent / entry['file']).resolve()
            if not path.is_relative_to(manifest_path.parent) or digest(path) != entry['sha256']:
                raise ValueError('UI reference image mismatch')
            panel = next(panel for panel in panels if panel['id'] == entry['panel'])
            for hotspot in entry['hotspots']:
                if f'<td>{html.escape(hotspot["row"])}</td>' not in panel['body']:
                    raise ValueError(f'Missing operation explanation: {hotspot["row"]}')
            entry['uri'] = 'data:image/png;base64,' + base64.b64encode(path.read_bytes()).decode('ascii')
        data['ui_references'] = references
        source(manifest_path)
    source(document)
    source(roadmap)
    source(args.audit)
    source(declaration)
    data['sources'] = SOURCES
    template = read(Path(__file__).with_name('architecture_map_template.html'))
    serialized = json.dumps(data, ensure_ascii=False).replace('<', '\\u003c')
    result = template.replace('__MAP_DATA__', serialized)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(result, encoding='utf-8')
    args.output.with_suffix('.evidence.json').write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'HTML: {args.output.resolve()}')
    print(f'Panels: {len(panels)}; sources: {len(SOURCES)}; component hashes match: {all(row["matches"] for row in data["components"])}')


if __name__ == '__main__':
    main()
