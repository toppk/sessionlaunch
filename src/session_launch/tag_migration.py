"""One-time conversion of saved comma tags into explicit organization."""

import copy
import json
import uuid

from .session_groups import annotation_error
from .paths import private_write_text


def apply_migration(groups, tags, plan, identity_for, tags_path):
    """Apply explicit choices; leave tags intact until group storage succeeds."""
    names = {tag for values in tags.values() for tag in values}
    if names != set(plan):
        raise ValueError("Choose a destination for every legacy tag")
    for tag, (kind, target) in plan.items():
        if kind not in ("domain", "new_domain", "group", "new_group", "note", "drop"):
            raise ValueError(f"No destination chosen for {tag}")
        if kind == "domain" and not groups.get_domain(target):
            raise ValueError(f"Domain no longer exists for {tag}")
        if kind == "group" and not groups.get(target):
            raise ValueError(f"Group no longer exists for {tag}")
        if kind in ("new_domain", "new_group") and (
                not target or len(target) > 80 or "\n" in target or "\r" in target):
            raise ValueError(f"Enter a one-line name for {tag}")
    for legacy_key, values in tags.items():
        domains = {(kind, target.casefold() if kind == "new_domain" else target)
                   for tag in values for kind, target in [plan[tag]]
                   if kind in ("domain", "new_domain")}
        if len(domains) > 1:
            raise ValueError(f"{legacy_key} has tags mapped to different domains")
        note = groups.annotation_for([identity_for(legacy_key)])
        for tag in sorted(values):
            if plan[tag][0] == "note" and f" {tag} " not in f" {note} ":
                note = (note + " " + tag).strip()
        if error := annotation_error(note):
            raise ValueError(f"{legacy_key}: {error}")
    if not tags_path.exists():
        raise OSError("Legacy tags file is missing; reload before migrating")
    backup = tags_path.with_name(tags_path.name + ".pre-phase4.bak")
    if not backup.exists():
        private_write_text(backup, tags_path.read_text())
    snapshot = copy.deepcopy((groups.domains, groups.groups, groups.memberships,
                              groups.annotations, groups.session_domains))
    try:
        targets = {}
        for tag, (kind, target) in plan.items():
            if kind == "new_domain":
                existing = next((row for row in groups.domains
                                 if row["name"].casefold() == target.casefold()), None)
                if existing:
                    target = existing["id"]
                else:
                    domain_id = f"d-{uuid.uuid4().hex}"
                    groups.domains.append({"id": domain_id, "name": target,
                                           "collapsed": False})
                    target = domain_id
            elif kind == "new_group":
                existing = next((row for row in groups.groups
                                 if row["name"].casefold() == target.casefold()), None)
                if existing:
                    target = existing["id"]
                else:
                    group_id = uuid.uuid4().hex
                    groups.groups.append({"id": group_id, "name": target,
                                          "domain": None, "goal": "", "collapsed": False})
                    target = group_id
            targets[tag] = (kind.removeprefix("new_"), target)
        for legacy_key, values in tags.items():
            key = identity_for(legacy_key)
            for tag in sorted(values):
                kind, target = targets[tag]
                if kind == "domain":
                    groups.session_domains[key] = target
                elif kind == "group":
                    memberships = groups.memberships.setdefault(key, [])
                    if not any(entry["group"] == target for entry in memberships):
                        memberships.append({"group": target, "section": ""})
                elif kind == "note":
                    old = groups.annotations.get(key, "")
                    if f" {tag} " not in f" {old} ":
                        groups.annotations[key] = (old + " " + tag).strip()
        groups._sync_legacy_views()
        groups.save()
    except (OSError, ValueError):
        (groups.domains, groups.groups, groups.memberships,
         groups.annotations, groups.session_domains) = snapshot
        groups._sync_legacy_views()
        raise
    private_write_text(tags_path, json.dumps({}) + "\n")
