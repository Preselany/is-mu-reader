"""Pure adapters for student services. Source content is data, never executable code."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from email import policy
from email.parser import BytesParser
from urllib.parse import unquote, urljoin, urlsplit

from . import parsers as p
from .errors import ParseError


def embedded_json(html, call):
    """Decode a literal JSON argument, without executing the surrounding JavaScript."""
    for script in p.soup(html).select("script:not([src])"):
        for match in re.finditer(re.escape(call) + r"\s*\(\s*", script.text):
            try:
                value, _ = json.JSONDecoder().raw_decode(script.text[match.end() :])
                if isinstance(value, dict):
                    return value
            except ValueError:
                continue
    raise ParseError(f"Expected {call} data is missing or is not literal JSON.")


def main_content(html):
    main = p.soup(html).select_one("#app_content")
    if main is None:
        raise ParseError("Expected IS application content is missing.")
    return main


def local_iso(value):
    if not value:
        return None
    try:
        if len(value) == 10:
            return datetime.strptime(value, "%Y-%m-%d").date().isoformat()
        dt = datetime.fromisoformat(value)
        return (dt if dt.tzinfo else dt.replace(tzinfo=p.TZ)).isoformat()
    except (TypeError, ValueError):
        raise ParseError("Unexpected calendar date format.") from None


CALENDAR_KINDS = {
    "5_1": "notice",
    "6_1": "exam_registration",
    "6_8": "exam",
    "6_3": "ropot",
    "6_4": "semester",
    "6_5": "holiday",
    "8_1": "timetable",
    "8_2": "timetable",
    "8_4": "online_teaching",
}


def calendar(html, base):
    config = embedded_json(html, "is.Design.init")
    init = next(
        (
            x
            for x in config.get("js_init", [])
            if x.get("module") == "RozvrhKalendar" and x.get("method") == "init"
        ),
        None,
    )
    if not init or not isinstance(init.get("params"), list) or len(init["params"]) < 2:
        raise ParseError("Calendar event data is missing.")
    events, hidden = init["params"][:2]
    if not isinstance(events, list) or not isinstance(hidden, list):
        raise ParseError("Calendar event data changed shape.")
    items = []
    for e in events:
        if not isinstance(e, dict) or not e.get("start") or "title" not in e:
            raise ParseError("Calendar contains an unsupported event record.")
        typ = str(e.get("typ", ""))
        start, end = local_iso(e["start"]), local_iso(e.get("end"))
        provider_id = str(e.get("__iskup", ""))
        identity = json.dumps([provider_id, typ, start, end, e["title"]], ensure_ascii=False)
        items.append(
            {
                "id": hashlib.sha256(identity.encode()).hexdigest(),
                "provider_id": provider_id or None,
                "type_code": typ,
                "kind": CALENDAR_KINDS.get(typ, "unknown"),
                "title": e["title"],
                "start": start,
                "end": end,
                "all_day": bool(e.get("allDay")),
                "location": e.get("__location"),
                "description": e.get("__desc"),
                "url": e.get("url"),
                "hidden_in_ui": typ in hidden,
                "boundary_code": e.get("od_vs_do"),
            }
        )
    legend = []
    for layer in main_content(html).select(".legenda .toggle-switch"):
        codes = [x[3:] for x in layer.get("class", []) if x.startswith("sw_")]
        legend.append({"title": p.text(layer), "type_codes": codes})
    return {
        "source_url": base,
        "timezone": "Europe/Prague",
        "items": items,
        "count": len(items),
        "upstream_event_count": len(items),
        "layers": legend,
        "hidden_type_codes": hidden,
        "scope": "events_exposed_by_IS",
        "coverage_start": min((x["start"][:10] for x in items), default=None),
        "coverage_end": max(((x["end"] or x["start"])[:10] for x in items), default=None),
        "range_completeness": "not_guaranteed",
    }


def timetable(html, base):
    main = main_content(html)
    if not main.select_one(".predmet_kod") and not re.search(
        r"(žádn|nemáte|nejsou|není).{0,90}(rozvrh|výuk|předmět)", p.text(main), re.I
    ):
        raise ParseError("Timetable entries or a recognized empty state are missing.")
    items = []
    for cell in main.select("td[aria-label]"):
        code = cell.select_one(".predmet_kod")
        if not code:
            continue
        label = cell.get("aria-label", "")
        match = re.search(r"^(Po|Út|St|Čt|Pá|So|Ne)\s+(\d\d:\d\d)[–-](\d\d:\d\d)", label)
        link = code.select_one("a[href]")
        note = cell.select_one(".nepravidelnost_poznamka")
        items.append(
            {
                "course_label": p.text(code),
                "title": p.text(cell.select_one(".predmet_nazev")),
                "weekday": ["Po", "Út", "St", "Čt", "Pá", "So", "Ne"].index(match[1]) + 1
                if match
                else None,
                "start_time": match[2] if match else None,
                "end_time": match[3] if match else None,
                "kind": "lecture" if "prednaska" in cell.get("class", []) else "other",
                "location": p.text(cell.select_one(".mistnost_oznaceni")) or None,
                "schedule_text": label,
                "exceptions_text": note.get("aria-label", p.text(note)) if note else None,
                "detail_text": p.clean_text(p.soup(cell.get("title", ""))),
                "url": urljoin(base, link["href"]) if link else None,
            }
        )
    return {
        "source_url": base,
        "timezone": "Europe/Prague",
        "items": items,
        "count": len(items),
        "scope": "weekly_pattern",
        "text": p.clean_text(main),
    }


def open_submissions(html, base):
    main = main_content(html)
    heading = next((x for x in main.select("h2") if "Vám otevřené odevzdávárny" in p.text(x)), None)
    if heading is None:
        raise ParseError("Open submission-box section is missing.")
    section = []
    for node in heading.next_siblings:
        if getattr(node, "name", None) == "h2":
            break
        section.append(str(node))
    section = p.soup("".join(section))
    items = {}
    for a in section.select("a[href]"):
        url = urljoin(base, a["href"])
        path = p.query(url).get("furl") or unquote(urlsplit(url).path.removeprefix("/auth"))
        match = re.fullmatch(r"/el/([^/]+)/([^/]+)/([^/]+)/ode/(.*)", path)
        if not match:
            continue
        items[path] = {
            "title": p.text(a),
            "path": path,
            "faculty": match[1],
            "semester": match[2],
            "course": match[3],
            "open_for_submission": True,
            "source_url": url,
            "context_text": p.text(a.parent),
        }
    if not items and "Žádné otevřené odevzdávárny" not in p.text(section):
        raise ParseError("Unrecognized submission-box listing; refusing a silent empty result.")
    return {
        "source_url": base,
        "items": list(items.values()),
        "count": len(items),
        "state": "listed" if items else "empty",
        "scope": "open_to_current_user",
        "text": p.clean_text(section),
    }


def _plain_tree(value):
    """Retain native permission names/values while removing HTML in their descriptions."""
    if isinstance(value, dict):
        return {k: _plain_tree(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_plain_tree(v) for v in value]
    if isinstance(value, str):
        return p.text(p.soup(value))
    return value


def submission_folder(html, base, expected_path):
    data = embedded_json(html, "is.fmgr.set")
    nodes = data.get("uzly")
    if not isinstance(nodes, dict) or expected_path not in nodes:
        raise ParseError("Submission folder is not exposed in the file-manager response.")
    items = []
    for path, n in nodes.items():
        if not path.startswith(expected_path) or not isinstance(n, dict):
            continue
        items.append(
            {
                "path": path,
                "title": n.get("nazev") or n.get("nazev_sort") or path.rsplit("/", 1)[-1],
                "description": p.clean_text(p.soup(n.get("popis", ""))),
                "is_folder": isinstance(n.get("slozka"), dict),
                "submission_box_code": str(n["je_to_odevzdavarna"])
                if "je_to_odevzdavarna" in n
                else None,
                "can_read": bool(n.get("r")),
                "can_insert": bool(n.get("w")),
                "permissions": _plain_tree(n.get("prava", {})),
                "attributes": _plain_tree(n.get("atributy", {})),
                "inherited_attributes": _plain_tree(n.get("atributy_zdedene", {})),
                "modified_raw": n.get("timestamp"),
                "source_url": urljoin(base, "/auth" + path),
            }
        )
    root = next(x for x in items if x["path"] == expected_path)
    return {
        "source_url": base,
        "path": expected_path,
        "folder": root,
        "children": [x for x in items if x["path"] != expected_path],
        "visibility": "readable"
        if root["can_read"]
        else ("insert_only" if root["can_insert"] else "metadata_only"),
        "submission_status": "unknown",
        "scope": "visible_children",
        "text": p.clean_text(main_content(html)),
    }


def exam_index(html, base):
    main = main_content(html)
    source_text = p.clean_text(main)
    if not any(x in source_text for x in ("zkušební", "zkoušk", "přihlašování k výuce")):
        raise ParseError("Exam registration page is not recognized.")
    series = {}
    for a in main.select("a[href]"):
        url = urljoin(base, a["href"])
        q = p.query(url)
        if urlsplit(url).path != "/auth/student/prihl_na_zkousky" or not q.get("predmet"):
            continue
        if set(q) - {"fakulta", "obdobi", "studium", "predmet", "serie", "lang"}:
            continue
        key = (q["predmet"], q.get("serie"))
        series[key] = {
            "course_id": q["predmet"],
            "series_id": q.get("serie"),
            "title": p.text(a),
            "kind": "teaching_reservation"
            if "přihlašování k výuce" in p.text(a)
            else "exam_or_other",
            "source_url": url,
        }
    return {
        "source_url": base,
        "series": list(series.values()),
        "registered_items": p.reservations(html, base)["items"],
        "text": source_text,
    }


def mail_listing(html, base):
    main = main_content(html)
    folders_node = main.select_one("#folders")
    table = main.select_one("#mailListTable")
    if folders_node is None or table is None:
        raise ParseError("IS mailbox listing is missing or unsupported.")
    fields = {e.get("name"): e.get("value", "") for e in folders_node.select("input[name]")}
    folders = []
    for key, name in fields.items():
        if not key.endswith("_nazev"):
            continue
        fid = key.removesuffix("_nazev")
        counts = fields.get(fid + "_pocty", "").split("/")
        folders.append(
            {
                "id": fid,
                "name": name,
                "type": fields.get(fid + "_typ"),
                "unread_count": int(counts[0]) if counts[0].isdigit() else None,
                "total_count": int(counts[1]) if len(counts) == 2 and counts[1].isdigit() else None,
            }
        )
    items = []
    for row in table.select('tr[id^="mail_"]'):
        mid = row["id"][5:]
        a = row.select_one('a[id^="read_link_"]')
        cells = row.find_all("td", recursive=False)
        if not mid.isdigit() or a is None or len(cells) < 7:
            raise ParseError("Mailbox message row changed shape.")
        flags = p.text(row.select_one('[id^="allFlags_"]'))
        sender = row.select_one("[data-from_adr]")
        items.append(
            {
                "id": mid,
                "subject": p.text(a),
                "sender_name": p.text(cells[3]),
                "sender_address": sender.get("data-from_adr") if sender else None,
                "date_text": p.text(cells[5]),
                "size_text": p.text(cells[6]),
                "flags": flags,
                "unread": any("_new" in cls for cls in row.get("class", [])),
                "url": urljoin(base, a["href"]),
                "folder_id": p.query(urljoin(base, a["href"])).get("folder_id"),
            }
        )
    current = main.select_one("#currentFolder")
    count = main.select_one("#mailCount")
    pages = [
        a["url"]
        for a in p.links(main, base)
        if urlsplit(a["url"]).path == "/auth/mail/" and p.query(a["url"]).get("start")
    ]
    return {
        "source_url": base,
        "folders": folders,
        "items": items,
        "count": len(items),
        "folder_id": current.get("value") if current else None,
        "total_count": int(count["value"]) if count and count.get("value", "").isdigit() else None,
        "forwarding_enabled": "Pošta je přesměrována" in p.text(main),
        "pagination_urls": pages,
    }


def mail_message(raw, message_id, base):
    if raw.startswith(b"From "):
        raw = raw.split(b"\n", 1)[1]
    message = BytesParser(policy=policy.default).parsebytes(raw)
    if not message.get("From") or not message.get("Date"):
        raise ParseError("Mail export did not contain an RFC822 message.")
    bodies, attachments, parts = [], [], {}

    def visit(part, path):
        # Attached .eml is an attachment even though email treats it as multipart.
        if part.get_content_disposition() == "attachment" or part.get_filename():
            data = part.get_payload(decode=True)
            if data is None and part.get_content_type() == "message/rfc822":
                payload = part.get_payload()
                data = b"\n".join(x.as_bytes(policy=policy.default) for x in payload)
            if data is None:
                raise ParseError("Unsupported MIME attachment encoding.")
            parts[path] = data
            attachments.append(
                {
                    "part_id": path,
                    "filename": part.get_filename(),
                    "mime_type": part.get_content_type(),
                    "bytes": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                }
            )
        elif part.is_multipart():
            for i, child in enumerate(part.iter_parts(), 1):
                visit(child, path + "." + str(i))
        elif part.get_content_type() in ("text/plain", "text/html"):
            try:
                content = part.get_content()
            except (LookupError, UnicodeError):
                content = (part.get_payload(decode=True) or b"").decode("utf-8", errors="replace")
            bodies.append({"part_id": path, "mime_type": part.get_content_type(), "text": content})
        else:
            data = part.get_payload(decode=True) or b""
            parts[path] = data
            attachments.append(
                {
                    "part_id": path,
                    "filename": part.get_filename(),
                    "mime_type": part.get_content_type(),
                    "bytes": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                }
            )

    visit(message, "1")
    return (
        {
            "id": str(message_id),
            "source_url": base,
            "headers": {
                name.lower(): [str(v) for v in message.get_all(name, [])]
                for name in ("From", "To", "Cc", "Date", "Subject", "Message-ID", "Reply-To")
            },
            "bodies": bodies,
            "attachments": attachments,
            "read_state_may_change": True,
        },
        parts,
    )


def notices(html, base):
    main = main_content(html).select_one("#noticeboard")
    if main is None:
        raise ParseError("Noticeboard listing is missing.")
    items = {}
    for card in main.select(".dlazdice"):
        a = card.select_one(".nazev a[data-zprava_id]")
        if not a:
            raise ParseError("Noticeboard card changed shape.")
        d = card.select_one(".kdy")
        cl = card.get("class", [])
        items[a["data-zprava_id"]] = {
            "id": a["data-zprava_id"],
            "title": p.text(a),
            "url": urljoin(base, a["href"]),
            "author": p.text(card.select_one(".kdo")),
            "date_text": p.text(d),
            "date_detail": d.get("title", "") if d else "",
            "unread": "nova" in cl,
            "priority": "important"
            if "cervena" in cl
            else ("targeted" if "modra" in cl else "normal"),
            "preview": p.text(card.select_one(".anotace, .perex, .text")) or None,
        }
    pagination = list(
        dict.fromkeys(urljoin(base, a["href"]) for a in main.select(".pagination a[href]"))
    )
    if not items and not re.search(
        r"(žádné zprávy|neobsahuje.{0,40}zpráv|nenalezen.{0,40}zpráv)", p.text(main), re.I
    ):
        raise ParseError("Unrecognized empty noticeboard; refusing a silent empty result.")
    boards = [
        a
        for a in p.links(main, base)
        if a["url"].endswith("/zpravy/") or a["url"].endswith("/sledovane/")
    ]
    return {
        "source_url": base,
        "items": list(items.values()),
        "count": len(items),
        "pagination_urls": pagination,
        "board_links": boards,
        "scope": "overview" if "rozcestnik" in main.get("class", []) else "board",
        "state": "listed" if items else "empty",
    }


def notice_detail(html, base, notice_id):
    main = main_content(html).select_one(".noticeboard_zprava")
    if main is None or main.select_one(f'[data-id="{notice_id}"]') is None:
        raise ParseError("Requested notice body is missing.")
    body = main.select_one(".obsah")
    if body is None:
        raise ParseError("Notice body structure changed.")
    date = main.select_one(".vlozeno")
    content = p.clean_text(body)
    return {
        "id": str(notice_id),
        "source_url": base,
        "title": p.text(main.select_one("h2")),
        "author": p.text(main.select_one(".vlozeno_autor .jmeno")),
        "date_text": p.text(date),
        "text": content,
        "links": p.links(body, base),
        "content_hash": hashlib.sha256(content.encode()).hexdigest(),
        "read_state_may_change": True,
    }
