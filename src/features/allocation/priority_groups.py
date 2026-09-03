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
    group_sizes: dict[int, int] = {}
    for group_index, group in enumerate(groups or []):
        clean_group = [str(role) for role in group or []]
        group_sizes[group_index] = len(clean_group)
        for role in clean_group:
            role_to_group[role] = group_index

    links: list[str] = []
    for left, right in zip(selected, selected[1:]):
        left_group = role_to_group.get(left)
        right_group = role_to_group.get(right)
        if left_group == right_group and left in role_to_group:
            links.append(EQUAL_LINK)
        elif (
            left_group is not None
            and right_group is not None
            and group_sizes.get(left_group, 0) == 1
            and group_sizes.get(right_group, 0) == 1
        ):
            links.append(STRICT_LINK)
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


def shift_crossed_priority_boundaries(
    links: list[str],
    index: int,
    new_index: int,
) -> None:
    """Move each crossed batch boundary one slot opposite to a role drag."""

    if new_index > index:
        for pos in range(index, min(new_index, len(links))):
            if links[pos] == GROUP_BOUNDARY_LINK and pos > 0:
                links[pos - 1], links[pos] = links[pos], links[pos - 1]
    elif new_index < index:
        for pos in range(index - 1, max(new_index - 1, -1), -1):
            if links[pos] == GROUP_BOUNDARY_LINK and pos + 1 < len(links):
                links[pos], links[pos + 1] = links[pos + 1], links[pos]
                promote_priority_boundary(links, pos + 1)


def _link_after_removing_role(left: str, right: str) -> str:
    """Keep the surrounding batch relationship when one role is removed."""

    if left == EQUAL_LINK and right == EQUAL_LINK:
        return EQUAL_LINK
    if left == EQUAL_LINK:
        return right
    if right == EQUAL_LINK:
        return left
    if GROUP_BOUNDARY_LINK in {left, right}:
        return GROUP_BOUNDARY_LINK
    return STRICT_LINK


def _forward_drop_link(links: list[str], target_index: int) -> str:
    """Return the link for a front-to-back drop on the target role.

    A target joins a same-priority batch only when following its contiguous
    equal links reaches a batch boundary. A strict link or the end of the
    selection ends that reachability.
    """

    cursor = target_index
    while cursor < len(links) and links[cursor] == EQUAL_LINK:
        cursor += 1
    if cursor < len(links) and links[cursor] == GROUP_BOUNDARY_LINK:
        return EQUAL_LINK
    return STRICT_LINK


def relink_forward_drop(
    selected: list[str],
    links: Iterable[str] | None,
    source_index: int,
    target_index: int,
) -> tuple[list[str], list[str]]:
    """Move a role before a later target and derive its new priority link."""

    roles = list(selected)
    clean_links = normalize_priority_links(roles, links)
    if (
        source_index < 0
        or target_index < 0
        or source_index >= len(roles)
        or target_index >= len(roles)
        or source_index >= target_index
    ):
        return roles, clean_links

    target_role = roles[target_index]
    moved_role = roles.pop(source_index)
    if source_index == 0:
        clean_links.pop(0)
    elif source_index == len(clean_links):
        clean_links.pop(source_index - 1)
    else:
        clean_links[source_index - 1] = _link_after_removing_role(
            clean_links[source_index - 1], clean_links[source_index]
        )
        clean_links.pop(source_index)

    insert_index = roles.index(target_role)
    inserted_link = _forward_drop_link(clean_links, insert_index)
    roles.insert(insert_index, moved_role)
    clean_links.insert(insert_index, inserted_link)
    return roles, normalize_priority_links(roles, clean_links)
