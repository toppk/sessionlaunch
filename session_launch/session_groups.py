"""Persistent manual folders keyed by stable session identities."""

import json
import os
import re
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .sessionlaunch_paths import private_write_text


MARKER = re.compile(r"(?<!\S)([rto]):([^\s]+)")
SCHEMA_VERSION = 2


def annotation_markers(line):
    """Read known one-line markers; keep the annotation itself untouched."""
    result = {"r": [], "t": [], "o": []}
    for match in MARKER.finditer(line):
        result[match.group(1)].append(match.group(2))
    return result


def annotation_note_text(line):
    """Return the prose and unknown tokens without the recognized role markers."""
    return " ".join(MARKER.sub("", line).split())


def annotation_error(line):
    """Validate known markers without restricting the rest of a session note."""
    if "\n" in line or "\r" in line:
        return "Keep the annotation on one line."
    for token in line.split():
        if token[:2] not in ("r:", "t:", "o:"):
            continue
        kind, value = token[:1], token[2:]
        if not value:
            return f"{kind}: needs a value."
        if kind in ("t", "o") and not value.startswith("/"):
            return f"{kind}: paths begin with / (for example, {kind}:/auth)."
    return ""


def annotation_hierarchy_key(line):
    """Place broad overseers before work in their area; leave unscoped rows last."""
    markers = annotation_markers(line)
    for kind, rank in (("o", 0), ("t", 1)):
        paths = [tuple(part.casefold() for part in value.strip("/").split("/") if part)
                 for value in markers[kind] if value.startswith("/")]
        if paths:
            return (0, min(paths, key=lambda path: (len(path), path)), rank)
    return (1, (), 0)


class SessionGroups:
    def __init__(self, path, load=True, migrate=True):
        self.path = Path(path)
        self.groups = []
        self.domains = []
        self.memberships = {}
        self.members = {}
        self.sections = {}
        self.member_sections = {}
        self.member_order = {}
        self.annotations = {}
        self.session_domains = {}
        self.standing = set()
        self.lifecycle = {}
        self.retired = {}
        if not load or not self.path.exists():
            return
        data = json.loads(self.path.read_text())
        if not isinstance(data, dict) or not isinstance(data.get("groups"), list):
            raise ValueError(f"Invalid group data: {self.path}")
        version = data.get("schema_version", 0)
        if version not in (0, 1, SCHEMA_VERSION):
            raise ValueError(f"Unsupported group data version: {self.path}")
        if version < 2 and not isinstance(data.get("members"), dict):
            raise ValueError(f"Invalid group data: {self.path}")
        if version == 2 and not isinstance(data.get("memberships"), dict):
            raise ValueError(f"Invalid group data: {self.path}")
        for group in data["groups"]:
            if (not isinstance(group, dict) or not isinstance(group.get("id"), str)
                    or not isinstance(group.get("name"), str)
                    or not isinstance(group.get("collapsed", False), bool)):
                raise ValueError(f"Invalid group data: {self.path}")
            self.groups.append({"id": group["id"], "name": group["name"],
                                "domain": group.get("domain"),
                                "goal": group.get("goal", ""),
                                "collapsed": group.get("collapsed", False)})
        valid_ids = {group["id"] for group in self.groups}
        raw_sections = (data.get("sections", {}) if version < 2 else
                        {group["id"]: group.get("sections", []) for group in data["groups"]})
        for group_id, rows in raw_sections.items():
            if group_id not in valid_ids or not isinstance(rows, list):
                continue
            self.sections[group_id] = [row for row in rows if isinstance(row, dict)
                                       and isinstance(row.get("id"), str)
                                       and isinstance(row.get("name"), str)
                                       and isinstance(row.get("collapsed", False), bool)]
        valid_sections = {row["id"] for rows in self.sections.values() for row in rows}
        if version < 2:
            old_sections = data.get("member_sections", {})
            for key, group_id in data["members"].items():
                if not isinstance(key, str):
                    continue
                if group_id is None:
                    self.memberships[key] = []
                elif group_id in valid_ids:
                    section_id = old_sections.get(key, "")
                    valid = section_id in valid_sections and any(
                        item["id"] == section_id for item in self.sections.get(group_id, []))
                    self.memberships[key] = [{"group": group_id,
                                              "section": section_id if valid else ""}]
        else:
            for key, entries in data["memberships"].items():
                if not isinstance(key, str) or not isinstance(entries, list):
                    raise ValueError(f"Invalid memberships: {self.path}")
                seen_groups = set()
                cleaned = []
                for entry in entries:
                    if not isinstance(entry, dict) or entry.get("group") not in valid_ids:
                        raise ValueError(f"Invalid membership: {self.path}")
                    group_id = entry["group"]
                    if group_id in seen_groups:
                        raise ValueError(f"Duplicate membership: {self.path}")
                    seen_groups.add(group_id)
                    section_id = entry.get("section", "")
                    if section_id and not any(row["id"] == section_id for row in
                                              self.sections.get(group_id, [])):
                        raise ValueError(f"Invalid membership section: {self.path}")
                    cleaned.append({"group": group_id, "section": section_id})
                self.memberships[key] = cleaned
        self.member_order = {scope: list(dict.fromkeys(keys))
                             for scope, keys in data.get("member_order", {}).items()
                             if (scope in valid_ids or scope in valid_sections)
                             and isinstance(keys, list) and all(isinstance(key, str) for key in keys)}
        self.annotations = {key: value for key, value in data.get("annotations", {}).items()
                            if isinstance(key, str) and isinstance(value, str)
                            and "\n" not in value and "\r" not in value}
        stored_domains = data.get("session_domains", {})
        if not isinstance(stored_domains, dict):
            raise ValueError(f"Invalid domain data: {self.path}")
        self.domains = [dict(row) for row in data.get("domains", []) if
                        isinstance(row, dict) and isinstance(row.get("id"), str) and
                        isinstance(row.get("name"), str)]
        domain_ids = {row["id"] for row in self.domains}
        if version < 2:
            for name in dict.fromkeys(stored_domains.values()):
                if isinstance(name, str) and name:
                    domain_id = f"d-{uuid.uuid4().hex}"
                    self.domains.append({"id": domain_id, "name": name, "collapsed": False})
            name_ids = {row["name"]: row["id"] for row in self.domains}
        else:
            name_ids = {}
        domain_ids = {row["id"] for row in self.domains}
        for group in self.groups:
            if group["domain"] not in domain_ids:
                group["domain"] = None
        self.session_domains = {key: name_ids.get(value, value) for key, value in
                                stored_domains.items() if isinstance(key, str) and
                                isinstance(value, str) and name_ids.get(value, value) in domain_ids}
        self.standing = {key for key in data.get("standing", []) if isinstance(key, str)}
        self.lifecycle = {key: {"at_boot": value.get("at_boot", "ask"),
                                "on_stop": value.get("on_stop", "nothing")}
                          for key, value in data.get("lifecycle", {}).items()
                          if isinstance(key, str) and isinstance(value, dict) and
                          value.get("at_boot", "ask") in ("never", "ask", "always") and
                          value.get("on_stop", "nothing") in ("nothing", "notify")}
        self.retired = {key: value for key, value in data.get("retired", {}).items()
                        if isinstance(key, str) and isinstance(value, str)}
        self._sync_legacy_views()
        if version < 2 and migrate:
            backup = self.path.with_name(self.path.name + ".pre-v2.bak")
            if not backup.exists():
                private_write_text(backup, self.path.read_text())
            self.save()

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({"schema_version": SCHEMA_VERSION,
                              "domains": self.domains,
                              "groups": [{**group, "sections": self.sections.get(group["id"], [])}
                                         for group in self.groups],
                              "memberships": self.memberships,
                              "member_order": self.member_order,
                              "annotations": self.annotations,
                              "session_domains": self.session_domains,
                              "standing": sorted(self.standing),
                              "lifecycle": self.lifecycle,
                              "retired": self.retired}, indent=2) + "\n"
        temporary = None
        try:
            with tempfile.NamedTemporaryFile("w", dir=self.path.parent,
                                             prefix=".groups-", delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(payload)
            os.replace(temporary, self.path)
        finally:
            if temporary and temporary.exists():
                temporary.unlink()

    def get(self, group_id):
        return next((group for group in self.groups if group["id"] == group_id), None)

    def _sync_legacy_views(self):
        """Retain first-membership adapters while callers move to plural APIs."""
        self.members = {key: entries[0]["group"] if entries else None
                        for key, entries in self.memberships.items()}
        self.member_sections = {key: entries[0]["section"] for key, entries in
                                self.memberships.items() if entries and entries[0]["section"]}

    def memberships_for(self, keys):
        for key in keys:
            if key in self.memberships:
                return list(self.memberships[key])
        return []

    def groups_for(self, keys):
        return [entry["group"] for entry in self.memberships_for(keys)]

    def group_for(self, keys):
        return next(iter(self.groups_for(keys)), None)

    def domain_for(self, keys):
        return next((self.session_domains[key] for key in keys
                     if key in self.session_domains), None)

    def set_domain(self, keys, domain):
        """Set one durable domain for a proven session key; None leaves it unassigned."""
        self.set_domains([keys], domain)

    def set_domains(self, key_sets, domain):
        """Assign one direct domain to each session, preserving its group memberships."""
        if not key_sets or any(not keys for keys in key_sets):
            raise ValueError("No session identity is available")
        if domain is not None and not self.get_domain(domain):
            raise ValueError(f"Unknown domain: {domain}")
        for keys in key_sets:
            for key in keys:
                self.session_domains.pop(key, None)
            if domain is not None:
                self.session_domains[keys[0]] = domain
        self.save()

    def get_domain(self, domain_id):
        return next((row for row in self.domains if row["id"] == domain_id), None)

    def create_domain(self, name):
        name = self.valid_domain_name(name)
        if any(row["name"].casefold() == name.casefold() for row in self.domains):
            raise ValueError("A domain with that name already exists")
        domain_id = f"d-{uuid.uuid4().hex}"
        self.domains.append({"id": domain_id, "name": name, "collapsed": False})
        self.save()
        return domain_id

    def rename_domain(self, domain_id, name):
        domain = self.get_domain(domain_id)
        if not domain:
            raise ValueError("Domain no longer exists")
        name = self.valid_domain_name(name)
        if any(row["id"] != domain_id and row["name"].casefold() == name.casefold()
               for row in self.domains):
            raise ValueError("A domain with that name already exists")
        domain["name"] = name
        self.save()

    @staticmethod
    def valid_domain_name(name):
        name = name.strip()
        if not name or len(name) > 80 or "\n" in name or "\r" in name:
            raise ValueError("Domain name must be 1–80 characters on one line")
        return name

    def move_domain(self, domain_id, direction):
        domain = self.get_domain(domain_id)
        if not domain:
            return
        index = self.domains.index(domain)
        target = index + direction
        if 0 <= target < len(self.domains):
            self.domains[index], self.domains[target] = self.domains[target], self.domains[index]
            self.save()

    def toggle_domain(self, domain_id):
        domain = self.get_domain(domain_id)
        if domain:
            domain["collapsed"] = not domain["collapsed"]
            self.save()

    def domains_for(self, keys):
        domains = []
        direct = self.domain_for(keys)
        if direct:
            domains.append(direct)
        for group_id in self.groups_for(keys):
            group = self.get(group_id)
            if group and group["domain"] and group["domain"] not in domains:
                domains.append(group["domain"])
        return domains

    def set_group_domain(self, group_id, domain_id):
        group = self.get(group_id)
        if not group or domain_id is not None and not self.get_domain(domain_id):
            raise ValueError("Group or domain no longer exists")
        group["domain"] = domain_id
        self.save()

    def set_group_goal(self, group_id, goal):
        group = self.get(group_id)
        if not group or not isinstance(goal, str) or "\n" in goal or "\r" in goal:
            raise ValueError("Group goal must be one line")
        group["goal"] = goal.strip()
        self.save()

    def is_standing(self, keys):
        return any(key in self.standing for key in keys)

    def set_standing(self, keys, enabled):
        if not keys:
            raise ValueError("No session identity is available")
        self.standing.difference_update(keys)
        if enabled:
            self.standing.add(keys[0])
        self.save()

    def lifecycle_for(self, keys):
        return next((self.lifecycle[key] for key in keys if key in self.lifecycle),
                    {"at_boot": "ask", "on_stop": "nothing"})

    def set_lifecycle(self, keys, at_boot=None, on_stop=None):
        if not keys:
            raise ValueError("No session identity is available")
        current = dict(self.lifecycle_for(keys))
        if at_boot is not None:
            current["at_boot"] = at_boot
        if on_stop is not None:
            current["on_stop"] = on_stop
        if current["at_boot"] not in ("never", "ask", "always") or \
                current["on_stop"] not in ("nothing", "notify"):
            raise ValueError("Invalid lifecycle setting")
        for key in keys:
            self.lifecycle.pop(key, None)
        if current != {"at_boot": "ask", "on_stop": "nothing"}:
            self.lifecycle[keys[0]] = current
        self.save()

    def is_retired(self, keys):
        return any(key in self.retired for key in keys)

    def set_retired(self, keys, enabled):
        self.set_retired_many([keys], enabled)

    def set_retired_many(self, identity_groups, enabled):
        """Change several sessions in one save; each group contains one identity's aliases."""
        if not identity_groups or any(not keys for keys in identity_groups):
            raise ValueError("No session identity is available")
        timestamp = datetime.now(timezone.utc).isoformat() if enabled else ""
        for keys in identity_groups:
            old = next((self.retired[key] for key in keys if key in self.retired), None)
            for key in keys:
                self.retired.pop(key, None)
            if enabled:
                self.retired[keys[0]] = old or timestamp
        self.save()
        return len(identity_groups)

    def migrate_aliases(self, aliases):
        """Move legacy PTY membership to a proven agent or frozen session key."""
        changed = 0
        for alias, canonical in aliases.items():
            if alias == canonical:
                continue
            if alias in self.memberships:
                old = self.memberships.pop(alias)
                if canonical not in self.memberships:
                    self.memberships[canonical] = old
                changed += 1
            if alias in self.annotations:
                self.annotations.setdefault(canonical, self.annotations.pop(alias))
                changed += 1
            if alias in self.session_domains:
                domain = self.session_domains.pop(alias)
                self.session_domains.setdefault(canonical, domain)
                changed += 1
            if alias in self.standing:
                self.standing.discard(alias)
                self.standing.add(canonical)
                changed += 1
            if alias in self.lifecycle:
                self.lifecycle.setdefault(canonical, self.lifecycle.pop(alias))
                changed += 1
            if alias in self.retired:
                self.retired.setdefault(canonical, self.retired.pop(alias))
                changed += 1
            for scope, order in self.member_order.items():
                if alias not in order:
                    continue
                section = self.section(scope)
                scope_group = section[0] if section else scope
                index = order.index(alias)
                order.remove(alias)
                if scope_group in self.groups_for([canonical]) and canonical not in order:
                    order.insert(index, canonical)
                changed += 1
        if changed:
            self._sync_legacy_views()
            self.save()
        return changed

    def has_metadata(self, key):
        return (key in self.memberships or key in self.annotations or
                key in self.session_domains or key in self.standing or
                key in self.lifecycle or key in self.retired or
                any(key in order for order in self.member_order.values()))

    def link_terminal(self, alias, canonical):
        """Move a temporary terminal's organization onto a confirmed agent identity."""
        moved = self.has_metadata(alias)
        if moved and self.has_metadata(canonical):
            raise ValueError("Both terminal and agent already have organization. "
                             "Resolve their group or annotation before linking.")
        if moved:
            self.migrate_aliases({alias: canonical})
        return moved

    def unlink_terminal(self, alias, canonical, moved):
        """Return moved organization to the temporary identity after a mistaken link."""
        if moved:
            self.migrate_aliases({canonical: alias})

    def create(self, name):
        name = self.valid_name(name)
        group_id = uuid.uuid4().hex
        self.groups.append({"id": group_id, "name": name, "domain": None,
                            "goal": "", "collapsed": False})
        self.save()
        return group_id

    def valid_name(self, name, exclude_id=None):
        name = name.strip()
        if not name or len(name) > 80 or any(char in name for char in "\r\n"):
            raise ValueError("Group name must be 1–80 characters on one line")
        if any(group["name"].casefold() == name.casefold() and group["id"] != exclude_id
               for group in self.groups):
            raise ValueError("A group with that name already exists")
        return name

    def rename(self, group_id, name):
        group = self.get(group_id)
        if not group:
            raise ValueError("Group no longer exists")
        name = self.valid_name(name, group_id)
        group["name"] = name
        self.save()

    def remove(self, group_id):
        if not self.get(group_id):
            return
        section_ids = {row["id"] for row in self.sections.pop(group_id, [])}
        self.groups = [group for group in self.groups if group["id"] != group_id]
        self.memberships = {key: [entry for entry in entries if entry["group"] != group_id]
                            for key, entries in self.memberships.items()}
        for scope in section_ids | {group_id}:
            self.member_order.pop(scope, None)
        self._sync_legacy_views()
        self.save()

    def assign(self, keys, group_id):
        if group_id is not None and not self.get(group_id):
            raise ValueError("Group no longer exists")
        keys = list(keys)
        for key in keys:
            old = self.memberships.get(key, [])
            if old != ([{"group": group_id, "section": ""}] if group_id else []):
                for order in self.member_order.values():
                    if key in order:
                        order.remove(key)
            if group_id is None:
                if key.startswith(("agent:", "saved:")):
                    self.memberships[key] = []
                else:
                    self.memberships.pop(key, None)
            else:
                self.memberships[key] = [{"group": group_id, "section": ""}]
        self._sync_legacy_views()
        self.save()

    def add_membership(self, keys, group_id, section_id=""):
        if not self.get(group_id):
            raise ValueError("Group no longer exists")
        if section_id and not any(row["id"] == section_id for row in self.sections_for(group_id)):
            raise ValueError("Section no longer exists")
        for key in keys:
            entries = self.memberships.setdefault(key, [])
            found = next((entry for entry in entries if entry["group"] == group_id), None)
            if found:
                if found["section"] != section_id:
                    self._remove_order_for_group(key, group_id)
                found["section"] = section_id
            else:
                entries.append({"group": group_id, "section": section_id})
        self._sync_legacy_views()
        self.save()

    def remove_membership(self, keys, group_id):
        for key in keys:
            entries = self.memberships.get(key, [])
            self.memberships[key] = [entry for entry in entries if entry["group"] != group_id]
            if not self.memberships[key] and not key.startswith(("agent:", "saved:")):
                self.memberships.pop(key, None)
            self._remove_order_for_group(key, group_id)
        self._sync_legacy_views()
        self.save()

    def _remove_order_for_group(self, key, group_id):
        for scope in [group_id, *(row["id"] for row in self.sections_for(group_id))]:
            if key in self.member_order.get(scope, []):
                self.member_order[scope].remove(key)

    def sections_for(self, group_id):
        return self.sections.get(group_id, [])

    def section(self, section_id):
        return next(((group_id, row) for group_id, rows in self.sections.items()
                     for row in rows if row["id"] == section_id), None)

    def section_for(self, keys, group_id):
        return next((entry["section"] for key in keys
                     for entry in self.memberships.get(key, [])
                     if entry["group"] == group_id and entry["section"]), None)

    def create_section(self, group_id, name):
        if not self.get(group_id):
            raise ValueError("Group no longer exists")
        name = self.valid_section_name(group_id, name)
        section_id = uuid.uuid4().hex
        self.sections.setdefault(group_id, []).append(
            {"id": section_id, "name": name, "collapsed": False})
        self.save()
        return section_id

    def valid_section_name(self, group_id, name, exclude_id=None):
        name = name.strip()
        if not name or len(name) > 80 or any(char in name for char in "\r\n"):
            raise ValueError("Section name must be 1–80 characters on one line")
        if name.casefold() == "unsectioned":
            raise ValueError("'Unsectioned' is reserved for members outside a section")
        if any(row["name"].casefold() == name.casefold() and row["id"] != exclude_id
               for row in self.sections_for(group_id)):
            raise ValueError("A section with that name already exists")
        return name

    def rename_section(self, section_id, name):
        found = self.section(section_id)
        if not found:
            raise ValueError("Section no longer exists")
        group_id, row = found
        row["name"] = self.valid_section_name(group_id, name, section_id)
        self.save()

    def remove_section(self, section_id):
        found = self.section(section_id)
        if not found:
            return
        group_id, _ = found
        self.sections[group_id] = [row for row in self.sections[group_id]
                                   if row["id"] != section_id]
        for entries in self.memberships.values():
            for entry in entries:
                if entry["section"] == section_id:
                    entry["section"] = ""
        self.member_order.pop(section_id, None)
        self._sync_legacy_views()
        self.save()

    def toggle_section(self, section_id):
        found = self.section(section_id)
        if found:
            found[1]["collapsed"] = not found[1]["collapsed"]
            self.save()

    def move_section(self, section_id, direction):
        found = self.section(section_id)
        if not found:
            return
        rows = self.sections[found[0]]
        index = rows.index(found[1])
        target = index + direction
        if 0 <= target < len(rows):
            rows[index], rows[target] = rows[target], rows[index]
            self.save()

    def place_section(self, section_id, target_id, before):
        found = self.section(section_id)
        target = self.section(target_id)
        if not found or not target or found[0] != target[0] or section_id == target_id:
            return
        rows = self.sections[found[0]]
        rows.remove(found[1])
        rows.insert(rows.index(target[1]) + (not before), found[1])
        self.save()

    def assign_section(self, keys, group_id, section_id):
        found = self.section(section_id) if section_id else None
        if not self.get(group_id) or section_id and (not found or found[0] != group_id):
            raise ValueError("Section or group no longer exists")
        for key in keys:
            entries = self.memberships.setdefault(key, [])
            entry = next((item for item in entries if item["group"] == group_id), None)
            if entry is None:
                entry = {"group": group_id, "section": ""}
                entries.append(entry)
            previous = entry["section"]
            if previous != section_id:
                self._remove_order_for_group(key, group_id)
            entry["section"] = section_id or ""
        self._sync_legacy_views()
        self.save()

    def annotation_for(self, keys):
        return next((self.annotations[key] for key in keys if key in self.annotations), "")

    def set_annotation(self, keys, line):
        if not keys:
            raise ValueError("No session identity is available")
        if error := annotation_error(line):
            raise ValueError(error)
        for key in keys:
            self.annotations.pop(key, None)
        if line.strip():
            self.annotations[keys[0]] = line
        self.save()

    def place_member(self, scope, key, target, before, visible_keys):
        """Move one member relative to another, preserving hidden members' order."""
        if scope not in self.member_order:
            self.member_order[scope] = []
        order = self.member_order[scope]
        for visible in visible_keys:
            if visible not in order:
                order.append(visible)
        if key not in order or target not in order or key == target:
            return
        order.remove(key)
        index = order.index(target) + (not before)
        order.insert(index, key)
        self.save()

    def toggle(self, group_id):
        group = self.get(group_id)
        if not group:
            return
        group["collapsed"] = not group["collapsed"]
        self.save()

    def move(self, group_id, direction):
        index = next((index for index, group in enumerate(self.groups)
                      if group["id"] == group_id), -1)
        target = index + direction
        if index < 0 or target < 0 or target >= len(self.groups):
            return
        self.groups[index], self.groups[target] = self.groups[target], self.groups[index]
        self.save()

    def place_group(self, group_id, target_id, before):
        group = self.get(group_id)
        target = self.get(target_id)
        if not group or not target or group_id == target_id:
            return
        self.groups.remove(group)
        self.groups.insert(self.groups.index(target) + (not before), group)
        self.save()
