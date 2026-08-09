"""Derive location/name-anchored projects from agenda items.

A *project* is a distinct real-world effort at the action/application grain: one
development order, one rezoning ordinance, one contract, one named initiative.
Items are grouped primarily by their **application identifier** (DOS/DCI/LDO/…,
an Ordinance/Resolution number, or an EC/RFB/RFP/STA contract number), which is
the most reliable signal the minutes carry — every reading and follow-up of the
same application shares the id, so they land in one project across meetings.
Because each building/action files its own id, this keeps distinct efforts at
the same site separate ("Estero Crossing – Oak & Stone Restaurant" is its own
project, apart from "Estero Crossing Residential" and from "Corkscrew Road
Landscaping").

Items with no id fall back to a cleaned development NAME, then to
location + subject.  Items with no real-world anchor — agenda approvals,
minutes, monthly financial reports, appointments, code-enforcement cases — are
intentionally NOT projects.  The title is used only to produce a readable name.

`build_projects` is the single entry point used by both the full pipeline
(`build.py`) and the silver->project regeneration script, so the grouping is
identical no matter how the tables are produced.
"""
from __future__ import annotations

import collections
import re
from dataclasses import dataclass

# ---- title cleaning -------------------------------------------------------
_BOILERPLATE = re.compile(r"\ban?\s+(ordinance|resolution)\s+of\s+the\s+village\b.*", re.I)
_ORDNUM = re.compile(r"\b(ordinance|resolution)\s+(no\.?\s*)?\d{4}-?\d*\b", re.I)
_APPID_ANY = re.compile(r"\b(DOS|DCI|LDO|ADD|CPA|REZ|DRI|EC|CN|RFQ|RFB)\s*#?\s*\d[\d\-E]*\b", re.I)
_READING = re.compile(
    r"\b(first|second|third|final)\s+reading\b|\bpublic\s+hearing\b"
    r"|\bquasi[\s-]?judicial\b|\bchange\s+order\s*#?\s*\d*\b",
    re.I,
)
_STOPPHRASES = re.compile(
    r"\b(zoning\s+amendment|development\s+order|dev\s+order|rezoning"
    r"|planned\s+development|comprehensive\s+plan\s+amendment|conditional\s+use"
    r"|variance|site\s+plan|special\s+exception|task\s+authorization|supplemental)\b",
    re.I,
)
# Same land-use phrases, but only stripped as a trailing suffix so a mid-name
# occurrence ("Firestone Rezoning Case") is preserved.
_STOPPHRASE_SUFFIX = re.compile(
    r"\s*[-–—:]?\s*(?:zoning\s+amendment|development\s+order|dev\s+order|rezoning"
    r"|planned\s+development|conditional\s+use|variance|site\s+plan"
    r"|special\s+exception|task\s+authorization|supplemental)\s*$",
    re.I,
)
_PHASE = re.compile(r"\bphase\s+[ivx0-9]+\b", re.I)

# Development-type application IDs anchor a project even without a clean name.
_DEV_APPID = re.compile(r"\b(DOS|DCI|LDO|ADD|CPA|REZ|DRI)\b", re.I)

# Canonical application-identifier extraction. Order matters: development codes
# first, then legislative numbers, then procurement/engineering numbers.
_ID_DEV = re.compile(r"\b(DOS|DCI|LDO|ADD|CPA|REZ|DRI)\s*#?\s*(\d{2,4}\s*-?\s*E?\s*\d{1,4})", re.I)
_ID_ORDRES = re.compile(r"\b(ordinance|resolution)\s+(?:no\.?\s*)?(\d{4}\s*-\s*\d{1,3})", re.I)
_ID_CONTRACT = re.compile(r"\b(EC|CN|RFQ|RFB|RFP|STA|CMAR|PO)\s*#?\s*-?\s*(\d{2,4}\s*-?\s*\d{1,4})", re.I)


def canonical_application_id(application_id: str, title: str) -> str | None:
    """Normalize an application id to a stable grouping token, searching the
    explicit field first and then the title. Returns e.g. 'DOS2022-E007',
    'ORDINANCE-2020-09', 'EC-2024-06', or None."""
    for source in (application_id or "", title or ""):
        for regex, prefix in (
            (_ID_DEV, None), (_ID_ORDRES, None), (_ID_CONTRACT, None),
        ):
            m = regex.search(source)
            if m:
                kind = m.group(1).upper()
                num = re.sub(r"\s+", "", m.group(2)).upper().strip("-")
                if kind in {"ORDINANCE", "RESOLUTION"}:
                    return f"{kind}-{num}"
                if kind in {"EC", "CN", "RFQ", "RFB", "RFP", "STA", "CMAR", "PO"}:
                    return f"{kind}-{num}"
                return f"{kind}{num}"     # DOS2022-E007, DCI2019-E003
    return None


# Leading procedural / section noise to strip before reading the project name.
_LEADING_JUNK = re.compile(
    r"^\s*(?:public\s+hearings?|new\s+business|old\s+business|unfinished\s+business"
    r"|consent\s+agenda|regular\s+agenda|action\s+items?|presentations?|workshops?"
    r"|resolution|ordinance|discussion|the\s+public\s+will\s+have.*?agenda\s+item\.?"
    r"|planning\s+zoning.*?page\s+\d+\s+of\s+\d+"
    r"|first\s+reading|second\s+reading|final\s+reading|quasi[\s-]?judicial)\b[\s:.\-]*",
    re.I,
)
_ITEM_MARKER = re.compile(r"^\s*(?:\(?[a-z]\)|\d{1,2}\.)\s+", re.I)


def clean_leading_name(title: str | None) -> str:
    """Pull a readable project name from the front of a (often paragraph-long)
    agenda title: strip section/procedural prefixes, then cut at the first
    street address, parenthetical, or 'located' clause."""
    t = title or ""
    t = re.sub(r"\bof(the|Estero)\b", r"of \1", t, flags=re.I)  # OCR glue "ofthe"/"ofEstero"
    t = re.sub(r"\bof(?=[A-Z])", "of ", t)       # OCR glue: "ofEstero" -> "of Estero"
    for _ in range(4):
        stripped = _ITEM_MARKER.sub("", _LEADING_JUNK.sub("", t))
        if stripped == t:
            break
        t = stripped
    t = _MOTION_PREFIX.sub("", t)                 # "Authorize…to execute the contract for X" -> "X"
    t = _LEADING_ACTION.sub("", t)                # "Terminate as of …", "Approve award of …"
    t = _BOILERPLATE.sub(" ", t)                 # "An Ordinance of the Village…"
    t = re.split(r"\bof\s+the\s+village\s+council\b", t, flags=re.I)[0]
    t = re.split(r"\s\d{3,}\b", t)[0]            # cut at a street number
    t = re.split(r"\(", t)[0]                     # cut at "(District 4)" etc.
    t = re.split(r"\blocated\b", t, flags=re.I)[0]
    t = _ID_DEV.sub(" ", t)
    t = _ORDNUM.sub(" ", t)
    t = _READING.sub(" ", t)
    t = _PHASE.sub(" ", t)
    t = re.sub(r"\bwith\s+Gas\b", " ", t, flags=re.I)
    # Strip land-use phrases only as a trailing suffix (keep them mid-name).
    for _ in range(2):
        t = _STOPPHRASE_SUFFIX.sub("", t)
    # Drop bare legislative labels and dangling prepositions left after cuts.
    t = re.sub(r"\b(ordinance|resolution)s?\s+(no\.?)?\s*$", " ", t, flags=re.I)
    t = re.sub(r"^\s*(and\s+for|and|for|of|a|an|the)\b[\s:.-]*", " ", t, flags=re.I)
    for _ in range(2):
        t = re.sub(r"\s+(of|for|to|and|with|under|between|case\s+of)\s*$", "", t, flags=re.I)
    t = re.sub(r"[–—]", "-", t)
    t = re.sub(r"[\-–—&]{2,}", "-", t)
    t = re.sub(r"\s+", " ", t).strip(" -:/&.,")
    return t

# Titles that are administrative/legal business, never a project even when a
# location is attached (litigation, service contracts, governance, minutes).
_ADMIN_HARD = re.compile(
    r"\b(approval of (the )?agenda|agenda additions|approve.*minutes"
    r"|financial report|monthly financial|fiscal year|budget amendment"
    r"|lien mitigation|code enforcement|settlement agreement"
    r"|offer of judgment|v\.?\s+village of estero|litigation|bert harris"
    r"|legal services agreement|election services|interlocal agreement"
    r"|service[s]? agreement between|agreement between the village"
    r"|election of\b|selection of (?:the\s+)?(?:vice[\s-]?mayor"
    r"|vice[\s-]?chair(?:man|person)?|mayor|chair(?:man|person)?"
    r"|canvassing\s+board)|confirm(?:ation)? (?:selection )?of"
    r"|canvassing\s+board|section \d+ to read|\bwhereas\b|revised to read"
    r"|board (member )?(interview|appointment)|reappoint"
    r"|adjournment|roll call|public comment|proclamation"
    r"|council communications|board communications|manager.?s report"
    r"|attorney.?s report|clerk.?s report)\b",
    re.I,
)

# Motion-style titles that are a project only when a real site/application
# anchors them (a contract execution for a road is; a generic authorization
# with no place is not).
_ADMIN_SOFT = re.compile(
    r"\bauthoriz(?:e|ed)\s+the\s+village\s+manager\b"
    r"|\bgrant\s+the\s+village\s+manager\b",
    re.I,
)

# Strip a leading motion phrase so the object of the contract becomes the name:
# "Authorize the Village Manager to execute the contract for X" -> "X".
_MOTION_PREFIX = re.compile(
    r"^\s*(?:approve\s+ranking\s+of\s+consultants\s+and\s+)?"
    r"authoriz(?:e|ed)\s+(?:staff\s+to\s+negotiate\s+a\s+contract\s+with.*?\bfor\b\s+"
    r"|the\s+"
    r"(?:village\s+manager\s+to\s+(?:execute|enter\s+into|negotiate|sign|award)"
    r"|contract\s+negotiations?\s+for|execution\s+of))"
    r"(?:\s+and\s+\w+)*"
    r"(?:\s*(?:the\s+)?(?:contract|agreement|task\s+authorization|amendment"
    r"|change\s+order|purchase\s+order|negotiations?|documents?|no\.?\s*\d+"
    r"|cn\s*[\d-]+))*"
    r"[\s,]*(?:for|with|to|on|of|relating\s+to)?\s+",
    re.I,
)
# Leading contract-lifecycle verbs that are not part of a project name.
_LEADING_ACTION = re.compile(
    r"^\s*(?:terminate\s+as\s+of\s+[\w\s,]+?\d{4}\s*|approval\s+of\s+"
    r"|approve(?:d)?\s+(?:award\s+of\s+)?|award\s+of\s+)",
    re.I,
)

# A cleaned name still this generic is not a usable project name.
_JUNK_NAME = re.compile(
    r"^(?:(?:ordinance|resolution|zoning|the|a|an|and|of|for"
    r"|authorized?|approved?|contract|agreement|amendment|update|section"
    r"|village of estero|authorize the|authorized the|approve the)\s*)*$",
    re.I,
)

_MEETINGS_CATEGORY = "Meetings, Records & Public Input"
_SUBJECT_CATEGORIES = {
    "Residential Development",
    "Commercial & Mixed-Use Development",
    "Industry, Mining & Agriculture",
    "Transportation & Mobility",
    "Utilities, Stormwater & Environment",
    "Public Facilities & Services",
}
_FACET = {
    "Transportation & Mobility": "roadway",
    "Utilities, Stormwater & Environment": "utility",
    "Residential Development": "development",
    "Commercial & Mixed-Use Development": "development",
    "Industry, Mining & Agriculture": "development",
    "Public Facilities & Services": "facility",
}

_GENERIC = {
    "the", "a", "an", "of", "for", "and", "on", "at", "to", "estero", "village",
    "council", "board", "florida", "lee", "county", "district", "no", "phase",
    "project", "second", "first", "third", "final", "reading", "located",
    "approval", "update", "presentation", "staff", "report", "request", "review",
    "discussion", "consideration", "acceptance", "authorization",
}


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", (text or "").lower())).strip()


def clean_display_name(title: str | None) -> str:
    """Readable project name: strip IDs, ordinance numbers, reading markers,
    parentheticals, and land-use phrase suffixes, keeping original casing."""
    t = title or ""
    t = _BOILERPLATE.sub(" ", t)
    t = _ORDNUM.sub(" ", t)
    t = _APPID_ANY.sub(" ", t)
    t = _READING.sub(" ", t)
    t = _PHASE.sub(" ", t)
    t = re.sub(r"\(.*?\)", " ", t)          # (District 4), (DCI2021-E005)
    t = _STOPPHRASES.sub(" ", t)
    t = re.sub(r"\bwith\s+Gas\b", " ", t, flags=re.I)
    t = re.sub(r"[–—]", "-", t)
    t = re.sub(r"\s+", " ", t).strip(" -:/&.,")
    return t


def _grouping_key(name: str) -> str:
    """Aggressive normalized key so variants of one effort collapse together
    (drops street numbers and connective/generic words)."""
    n = _norm(name)
    n = re.sub(r"\b\d[\d-]*\b", " ", n)      # street numbers / addresses
    toks = [t for t in n.split() if t not in _GENERIC and len(t) > 1]
    return " ".join(toks[:5])


def _is_substantive(name: str) -> bool:
    words = [w for w in _norm(name).split() if w not in _GENERIC]
    return len(name) >= 6 and any(len(w) >= 3 for w in words)


def location_hint(location_name: str | None) -> str:
    """Distill a short area/cross-street hint from a resolved location name,
    e.g. '8800 Corkscrew Road, Estero, FL' -> 'Corkscrew Road',
    'CORKSCREW RD & PUENTE LN' -> 'Corkscrew Rd & Puente Ln'."""
    s = location_name or ""
    s = re.sub(r",\s*estero.*$", "", s, flags=re.I)      # ", Estero, FL"
    s = re.sub(r"\bfrom\b.*$", "", s, flags=re.I)          # "from X to Y" ranges
    s = re.sub(r"^\s*\d{3,}\s+", "", s)                    # leading house number
    s = re.sub(r"\bsubdivision\b", "", s, flags=re.I)
    s = re.sub(r"\s+", " ", s).strip(" ,-")
    if s.isupper():
        s = s.title()
    return s[:40]


def _combine_name(name: str, hint: str) -> str:
    """Append the area hint unless it is redundant with the name."""
    if not hint:
        return name
    if not name:
        return hint
    if hint.lower() in name.lower() or name.lower() in hint.lower():
        return name
    return f"{name} - {hint}"


@dataclass
class ItemView:
    """Minimal item projection the grouping needs."""
    item_id: str
    title: str
    application_id: str = ""
    category_name: str = ""
    action_type: str = ""
    location_name: str = ""


# A land-use action (rezoning, development order, CPD/zoning ordinance, comp
# plan amendment) marks an item as a real project even when its category is
# mislabeled or its title cleans to a bare label.
_DEV_SIGNAL = re.compile(
    r"development\s+order|\brezon|planned\s+development|\bMPD\b|\bCPD\b"
    r"|zoning\s+ordinance|zoning\s+amendment|conditional\s+use|\bvariance\b"
    r"|comprehensive\s+plan\s+amendment|\bCPA\s*\d|special\s+exception"
    r"|\bDCI\s*\d|\bDOS\s*\d|\bLDO\b",
    re.I,
)

# Recover a development name that trails the ordinance/case number, e.g.
# "Zoning Ordinance No. 2024-14, Coconut Trace CPD Amendment" -> "Coconut Trace",
# "Ordinance No. 2022-10 Estero Town Center (Wawa) Zoning Amendment" -> "Estero
# Town Center Wawa". Stops before the legal-boilerplate tail.
_NAME_AFTER_ORD = re.compile(
    r"ordinance\s+(?:no\.?\s*)?[\d.\-]+\s*[,:]?\s*(.+?)\s*"
    r"(?:\ban?\s+(?:zoning\s+)?ordinance\b|zoning\s+amendment|zoning\s+ordinance"
    r"|development\s+order|\bCPD\b|\bMPD\b|\bPD\b|rezoning|$)",
    re.I,
)
_NAME_BOILERPLATE = re.compile(
    r"^(?:an?\s+)?(?:zoning\s+)?(?:ordinance|resolution)$|of\s+the\s+village|the\s+village",
    re.I,
)


def _recover_dev_name(title: str) -> str:
    m = _NAME_AFTER_ORD.search(title or "")
    if not m:
        return ""
    cand = re.sub(r"\bof(the|Estero)\b", r"of \1", m.group(1), flags=re.I)
    cand = re.sub(r"[()]", " ", cand)
    cand = re.sub(r"\s+", " ", cand).strip(" ,.-")
    if cand and not _NAME_BOILERPLATE.match(cand) and _is_substantive(cand):
        return cand
    return ""


def _anchor(view: ItemView) -> tuple[str | None, str]:
    """Return (grouping_key, display_name). key is None when the item is not a
    project (pure administrative business with no real-world anchor)."""
    title = view.title or ""
    name = clean_leading_name(title)
    app_id = canonical_application_id(view.application_id, title)
    loc = (view.location_name or "").strip()
    dev_id = bool(_DEV_APPID.search(view.application_id or ""))
    is_ordres = app_id is not None and app_id.startswith(("ORDINANCE-", "RESOLUTION-"))
    dev_item = dev_id or bool(_DEV_SIGNAL.search(title))

    # A legislative id (ordinance/resolution) only anchors a project when it is
    # a land-use action or carries a location — otherwise it is policy business.
    # Development and procurement ids always anchor.
    group_id = app_id if (app_id and (not is_ordres or dev_item or loc)) else None

    # Scope gate. Hard-admin / legal business is never a project. Soft-admin
    # motions and Meetings/Records items qualify only with a real anchor, but a
    # land-use action (dev_item) always qualifies regardless of category.
    if _ADMIN_HARD.search(title):
        return None, name
    if _ADMIN_SOFT.search(title) and not (loc or group_id):
        return None, name
    if view.category_name == _MEETINGS_CATEGORY and not (loc or group_id or dev_item):
        return None, name
    # A bare-label name is only fatal when there is nothing else to anchor on.
    if _JUNK_NAME.match(name) and not (loc or group_id):
        return None, name

    # Recover a real name for dev items whose title cleaned down to junk.
    if _JUNK_NAME.match(name) or not _is_substantive(name):
        name = _recover_dev_name(title) or name

    # 1. Application identifier — one project per application/action, grouping
    #    every reading and follow-up that shares the id across meetings.
    if group_id:
        return f"id:{group_id}", name
    # 2. Cleaned development NAME — recurring named items with no id
    #    (e.g. "Corkscrew Road Landscaping" updates across meetings).
    key_from_name = _grouping_key(name)
    if key_from_name and _is_substantive(name):
        return f"nm:{key_from_name}", name
    # 3. Location + subject facet — a mappable item with neither id nor name.
    if loc:
        facet = _FACET.get(view.category_name, "other")
        return f"@ {_norm(loc)} :: {facet}", (name or loc)
    return None, name


def _fallback_name(key: str) -> str:
    """Human-ish name when a group's titles yield nothing usable — surface the
    application id from the key (e.g. 'id:DOS2022-E007' -> 'DOS2022-E007')."""
    if key.startswith("id:"):
        return key[3:].replace("ORDINANCE-", "Ordinance ").replace("RESOLUTION-", "Resolution ")
    return "Untitled Project"


_BOILERPLATE_NAME = re.compile(
    r"^(?:an?\s+)?(?:zoning\s+)?(?:ordinance|resolution)s?\b.*(?:village|council|florida)"
    r"|of\s+the\s+village|an?\s+ordinance\s+of|an?\s+resolution\s+of",
    re.I,
)


def _choose_name(displays: list[str], key: str) -> str:
    """Canonical display for a group: most common substantive cleaned name,
    tie-broken by the shortest. Legal-boilerplate strings are never chosen —
    the application id is a better label than "An Ordinance of the Village…"."""
    counts = collections.Counter(
        d for d in displays
        if d and _is_substantive(d) and not _BOILERPLATE_NAME.search(d)
    )
    if not counts:
        return _fallback_name(key)
    return max(counts, key=lambda d: (counts[d], -len(d)))


def build_projects(
    views: list[ItemView],
) -> tuple[list[dict], list[dict], dict[str, str]]:
    """Group item views into projects.

    Returns:
      projects: [{project_id, project_name, description, start_year, status}]
      links:    [{item_id, project_id}]
      names:    {item_id: project_name}  (for the item's project_matches field)
    """
    key_to_items: dict[str, list[ItemView]] = collections.defaultdict(list)
    key_to_displays: dict[str, list[str]] = collections.defaultdict(list)
    key_to_hints: dict[str, list[str]] = collections.defaultdict(list)
    key_order: list[str] = []

    for view in views:
        key, display = _anchor(view)
        if not key:
            continue
        if key not in key_to_items:
            key_order.append(key)
        key_to_items[key].append(view)
        key_to_displays[key].append(display)
        key_to_hints[key].append(location_hint(view.location_name))

    projects: list[dict] = []
    links: list[dict] = []
    names: dict[str, str] = {}
    for project_id, key in enumerate(key_order, start=1):
        base = _choose_name(key_to_displays[key], key)
        hint_counts = collections.Counter(h for h in key_to_hints[key] if h)
        hint = max(hint_counts, key=lambda h: (hint_counts[h], -len(h))) if hint_counts else ""
        # If the title cleaned down to a bare label, let the area hint carry the
        # name ("Authorize the" + Williams Road -> "Williams Road").
        if _JUNK_NAME.match(base) and hint:
            base = hint
            hint = ""
        name = _combine_name(base, hint)
        projects.append({
            "project_id": project_id,
            "project_name": name,
            "description": None,
            "start_year": None,
            "status": "Active",
        })
        for view in key_to_items[key]:
            links.append({"item_id": view.item_id, "project_id": project_id})
            names[view.item_id] = name
    return projects, links, names
