# 管理角色优先级平级批次的符号转换和旧配置兼容。
"""Helpers for role-priority batch links and persisted priority groups."""

from __future__ import annotations

from typing import Iterable


STRICT_LINK = ">"
GROUP_BOUNDARY_LINK = ">>"
EQUAL_LINK = "="
VALID_LINKS = {STRICT_LINK, GROUP_BOUNDARY_LINK, EQUAL_LINK}


def normalize_priority_links(selected: list[str], links: Iterable[str] | None) -> list[str]:
    """Return a link list matching selected roles, defaulting to strict order."""

    expected = max(0, len(selected) - 1)
    clean = [str(link) if str(link) in VALID_LINKS else STRICT_LINK for link in list(links or [])]
    if len(clean) < expected:
        clean.extend([STRICT_LINK] * (expected - len(clean)))
    return clean[:expected]


def links_to_priority_groups(selected: list[str], links: Iterable[str] | None) -> list[list[str]]:
    """Convert UI links into persisted role batches."""

    clean_links = normalize_priority_links(selected, links)
    groups: list[list[str]] = []
    current: list[str] = []
    for index, role in enumerate(selected):
        current.append(role)
        if index >= len(clean_links) or clean_links[index] != EQUAL_LINK:
            groups.append(current)
            current = []
    return [group for group in groups if group]


def priority_groups_to_links(selected: list[str], groups: Iterable[Iterable[str]] | None) -> list[str]:
    """Convert persisted role batches into UI links."""

    role_to_group: dict[str, int] = {}
    for group_index, group in enumerate(groups or []):
        for role in group or []:
            role_to_group[str(role)] = group_index

    links: list[str] = []
    for left, right in zip(selected, selected[1:]):
        if role_to_group.get(left) == role_to_group.get(right) and left in role_to_group:
            links.append(EQUAL_LINK)
        else:
            links.append(GROUP_BOUNDARY_LINK)
    return normalize_priority_links(selected, links)


def load_priority_selection(data: dict, all_roles: dict) -> tuple[list[str], list[str]]:
    """Load new priority_groups first, then fall back to old priority_list."""

    selected: list[str] = []
    raw_groups = data.get("priority_groups")
    if isinstance(raw_groups, list):
        for group in raw_groups:
            if not isinstance(group, list):
                continue
            for role in group:
                role = str(role)
                if role in all_roles and role not in selected:
                    selected.append(role)
        if selected:
            raw_links = data.get("priority_links")
            if isinstance(raw_links, list):
                return selected, normalize_priority_links(selected, raw_links)
            return selected, priority_groups_to_links(selected, raw_groups)

    selected = [
        role
        for role in data.get("priority_list", [])
        if role in all_roles and role not in selected
    ]
    return selected, normalize_priority_links(selected, None)


def _previous_boundary_index(links: list[str], index: int) -> int:
    for pos in range(index - 1, -1, -1):
        if links[pos] == GROUP_BOUNDARY_LINK:
            return pos
    return -1


def promote_priority_boundary(links: list[str], index: int) -> None:
    """Turn an equal link into a tier boundary and equalize the preceding tier."""

    if index < 0 or index >= len(links):
        return
    previous_boundary = _previous_boundary_index(links, index)
    for pos in range(previous_boundary + 1, index):
        links[pos] = EQUAL_LINK
    links[index] = GROUP_BOUNDARY_LINK


def merge_priority_boundary(links: list[str], index: int) -> None:
    """Turn a tier boundary into strict order and strictify the preceding tier."""

    if index < 0 or index >= len(links):
        return
    previous_boundary = _previous_boundary_index(links, index)
    for pos in range(previous_boundary + 1, index):
        links[pos] = STRICT_LINK
    links[index] = STRICT_LINK


def strictify_priority_region(links: list[str], index: int) -> None:
    """Make only this pair equal priority."""

    if index < 0 or index >= len(links):
        return
    links[index] = EQUAL_LINK


def cycle_priority_link(links: list[str], index: int) -> None:
    """Cycle one UI link through strict, equal, boundary, and strict states."""

    if index < 0 or index >= len(links):
        return
    current = links[index]
    if current == GROUP_BOUNDARY_LINK:
        merge_priority_boundary(links, index)
    elif current == EQUAL_LINK:
        promote_priority_boundary(links, index)
    else:
        strictify_priority_region(links, index)
