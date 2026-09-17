"""Pure parsers; do not execute JavaScript or follow page actions."""

from __future__ import annotations

import hashlib
import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from urllib.parse import parse_qs, urljoin, urlsplit
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from .errors import ParseError

TZ = ZoneInfo("Europe/Prague")


def text(node):
    return node.get_text(" ", strip=True) if node else ""


def soup(html):
    return BeautifulSoup(html, "html.parser")


def query(url):
    return {k: v[-1] for k, v in parse_qs(urlsplit(url).query.replace(";", "&")).items()}


def links(node, base):
    result = {}
    for a in node.select("a[href]"):
        href = a.get("href", "")
        if not href or href.startswith(("#", "javascript:")):
            continue
        url = urljoin(base, href)
        if urlsplit(url).scheme not in ("http", "https"):
            continue
        result[url] = {"title": text(a), "url": url}
    return list(result.values())


def clean_text(node):
    copy = soup(str(node))
    for e in copy.select("script, style, form, .show-for-sr, .io-info-mode-all"):
        e.decompose()
    return "\n".join(x.strip() for x in copy.get_text("\n").splitlines() if x.strip())


def czech_datetime(value):
    m = re.search(
        r"(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{4})\s+(\d{1,2}):(\d{2})", value.replace("\xa0", " ")
    )
    if not m:
        return None
    d, mo, y, h, mi = map(int, m.groups())
    return datetime(y, mo, d, h, mi, tzinfo=TZ).isoformat()


def course_index(html):
    doc = soup(html)
    cards = doc.select(".predmet_conteiner")
    if not cards:
        raise ParseError(
            "Course-list structure changed or no courses are exposed; refusing a silent empty result."
        )
    context = {
        e["name"]: e.get("value", "")
        for e in doc.select("#vyhledavani input[name]")
        if e["name"] in ("fakulta", "obdobi", "studium")
    }
    calls = {}
    for script in doc.select("script"):
        m = re.search(r"is\.Design\.init\(\s*", script.get_text())
        if not m:
            continue
        try:
            config, _ = json.JSONDecoder().raw_decode(script.get_text()[m.end() :])
        except ValueError:
            continue
        for entry in config.get("js_init", []):
            p = entry.get("params", [])
            if (
                len(p) > 2
                and isinstance(p[0], str)
                and p[0].startswith("#predmet-button-")
                and isinstance(p[2], dict)
            ):
                params = p[2].get("url", {})
                if params.get("option") == "predmet-option-info":
                    calls[p[0][1:]] = params
    result = []
    for card in cards:
        title = card.select_one(".predmet_header_name")
        code = text(title.select_one("strong"))
        button = card.select_one('[id^="predmet-button-"]')
        course_id = int(button["id"].split("-")[-1])
        result.append(
            {
                "code": code,
                "title": text(title).removeprefix(code).strip(),
                "course_id": course_id,
                "detail_params": calls.get(button["id"]),
            }
        )
    return context, result


def course_detail(html, base):
    doc = soup(html)
    found = links(doc, base)

    def matching(part):
        return [a for a in found if part in a["url"]]

    catalog = next(iter(matching("/predmet/")), None)
    result = {"source_url": base, "text": clean_text(doc)}
    if catalog:
        pieces = urlsplit(catalog["url"]).path.strip("/").split("/")
        result.update(faculty=pieces[-3], semester=pieces[-2], catalog_url=catalog["url"])
    for key, title in [
        ("credits", "Počet kreditů"),
        ("completion", "Ukončení"),
        ("grade", "Získané hodnocení"),
    ]:
        e = doc.select_one(f'[title="{title}"]')
        val = text(e).removeprefix(title + ":").strip()
        result[key] = (
            int(re.search(r"\d+", val).group())
            if key == "credits" and re.search(r"\d+", val)
            else val or None
        )
    result["teachers"] = [text(x) for x in doc.select(".vyucujici a")]
    seminar = doc.select_one('a[href*="seminare/student"]')
    result["seminar"] = text(seminar) or None
    result["forums"] = [
        dict(a, forum_id=query(a["url"]).get("guz"))
        for a in matching("/diskuse/diskusni_forum_predmet")
    ]
    result["ropot_url"] = next((a["url"] for a in matching("test_pruchod_el_student")), None)
    result["syllabus_entry_url"] = next(
        (
            a["url"]
            for a in found
            if "/elearning/io/" in a["url"] or urlsplit(a["url"]).path.endswith("/index.qwarp")
        ),
        None,
    )
    result["reservation_urls"] = [a["url"] for a in matching("/student/prihl_na_zkousky")]
    result["materials_url"] = next((a["url"] for a in found if "/auth/el/" in a["url"]), None)
    # Retain only read sources; omit destructive enrolment and submit-action links.
    return result


def file_tree(xml):
    if "<!DOCTYPE" in xml.upper() or "<!ENTITY" in xml.upper():
        raise ParseError("Unexpected XML declaration.")
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise ParseError("File API did not return valid XML.") from exc
    if root.tag != "fmgr":
        raise ParseError("Unexpected file API response root.")
    result = {}
    for e in list(root.iter("uzel")) + list(root.iter("poduzel")):
        fields = {c.tag: c.text or "" for c in e if not list(c)}
        node_id = fields.get("uzel_id")
        if not node_id:
            continue
        item = result.setdefault(node_id, {"node_id": node_id, "variants": []})
        item.update(
            title=text(soup(fields.get("nazev", ""))),
            path=fields.get("cesta"),
            parent_id=fields.get("rodic_id") or None,
            uploaded_by=fields.get("vlozil_uco") or item.get("uploaded_by"),
            description=text(soup(fields.get("popis", ""))) or item.get("description", ""),
            modified=fields.get("zmeneno"),
            child_count=int(fields.get("pocet_poduzlu") or 0),
            object_count=int(fields.get("pocet_objektu") or 0),
        )
        for o in e.findall("./objekty/objekt"):
            v = {c.tag: c.text or "" for c in o}
            obj = {
                "object_id": v.get("objekt_id") or None,
                "name": v.get("jmeno_souboru"),
                "path": v.get("cesta"),
                "mime_type": v.get("mime_type"),
                "size": int(v["velikost"]) if v.get("velikost") else None,
                "uploaded": v.get("vlozeno"),
            }
            if obj not in item["variants"]:
                item["variants"].append(obj)
        item["state"] = (
            "objects_listed"
            if item["variants"]
            else ("children_listed" if fields.get("url_metadata") else "metadata_only")
        )
    return list(result.values())


def syllabus(html, base):
    doc = soup(html)
    main = doc.select_one("#io-kontejner")
    if not main:
        raise ParseError("Syllabus content is absent or access is unavailable.")
    sections = []
    for body in main.select(".prvek-obsah-podosnova"):
        heading = body.find_previous_sibling(class_="io-nazev-kapitoly")
        body_text = clean_text(body)
        sections.append(
            {
                "title": text(heading.select_one("h1")) if heading else "Overview",
                "id": heading.get("id") if heading else None,
                "schedule_text": text(heading.select_one(".nedurazne")) if heading else "",
                "state": "locked" if "Obsah není zveřejněný" in body_text else "available",
                "text": body_text,
            }
        )
    all_links = links(main, base)
    return {
        "title": text(doc.title),
        "sections": sections,
        "text": clean_text(main),
        "links": all_links,
        "source_url": base,
        "content_hash": hashlib.sha256(clean_text(main).encode()).hexdigest(),
    }


def ropots(html, base):
    doc = soup(html)
    main = doc.select_one("#vyber_odpovedniku")
    if not main:
        raise ParseError("ROPOT list structure changed or access is unavailable.")
    items = []
    for row in main.select("tbody tr"):
        a = row.select_one("td.uzel a[href]")
        if not a:
            continue
        url = urljoin(base, a["href"])
        windows = []
        for block in row.select("td.poznamka li"):
            dates = block.select(".odp_date_time")
            windows.append(
                {
                    "opens": czech_datetime(text(dates[0])) if dates else None,
                    "closes": czech_datetime(text(dates[1])) if len(dates) > 1 else None,
                    "text": text(block),
                }
            )
        icon = row.select_one("td.budik [title]")
        items.append(
            {
                "title": text(a),
                "qref_path": query(url).get("testurl"),
                "url": url,
                "availability": windows,
                "status_text": icon.get("title", "") if icon else "",
                "grading_text": text(row.select_one("td.hodn-pb")),
                "attempt_started_by_client": False,
            }
        )
    suppressed = "velkému počtu" in text(main)
    return {
        "items": items,
        "count": len(items),
        "state": "suppressed" if suppressed else ("listed" if items else "empty"),
        "source_url": base,
    }


def forum_posts(html, base):
    doc = soup(html)
    main = doc.select_one("#discussion")
    if not main:
        raise ParseError("Forum structure changed or access is unavailable.")
    posts = []
    for post in main.select(".prispevek[data-id]"):
        body = post.select_one(".text")
        author = post.select_one(".df_autor_jmeno")
        date = post.select_one(".datum_wrap")
        permalink = post.select_one("a.prisp_link")
        thread = post.find_parent(class_="vlakno")
        body_text = clean_text(body) if body else ""
        posts.append(
            {
                "id": post["data-id"],
                "title": text(thread.select_one(".nazev")) if thread else "",
                "author": text(author),
                "date_text": date.get("title", text(date)) if date else "",
                "text": body_text,
                "links": links(body, base) if body else [],
                "url": urljoin(base, permalink["href"]) if permalink else base,
                "content_hash": hashlib.sha256(body_text.encode()).hexdigest(),
            }
        )
    pages = [a for a in links(main, base) if "/prispevky/" in a["url"] and a["url"] != base]
    return {"posts": posts, "count": len(posts), "pagination_links": pages, "source_url": base}


def reservations(html, base):
    main = soup(html).select_one("#app_content")
    if not main:
        raise ParseError("Reservation page structure changed.")
    items = []
    for row in main.select("tr"):
        cells = row.find_all("td", recursive=False)
        if len(cells) < 3:
            continue
        detail = next(
            (
                a
                for a in row.select("a[href]")
                if query(a["href"]).get("zkt") and not query(a["href"]).get("prihlasit")
            ),
            None,
        )
        if not detail:
            continue
        value = text(cells[2])
        heading = text(cells[2].select_one("b"))
        occupied = re.search(r"přihlášeno\s+(\d+)", value)
        capacity = re.search(r"max\.\s*(\d+)", value)
        opened = re.search(r"přihlašuje se od (.*?),", value)
        closing = re.search(r"přihlásit do\s+\w+\s+(\d+)\.\s*(\d+)\.\s*(\d{4})", value)
        closedate = None
        if closing:
            d, m, y = map(int, closing.groups())
            closedate = f"{y:04d}-{m:02d}-{d:02d}"
        status = text(cells[0])
        items.append(
            {
                "id": query(detail["href"])["zkt"],
                "event": heading,
                "starts_at": czech_datetime(heading),
                "registered": False
                if "nejste" in status
                else (True if "přihlášen" in status else None),
                "registration_status_text": status,
                "occupied": int(occupied.group(1)) if occupied else None,
                "capacity": int(capacity.group(1)) if capacity else None,
                "registration_opens": czech_datetime(opened.group(1)) if opened else None,
                "registration_closes_date": closedate,
                "closing_date_inclusive": "vč." in value,
                "text": value,
            }
        )
    return {"source_url": base, "items": items, "text": clean_text(main)}
