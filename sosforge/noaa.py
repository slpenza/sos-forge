"""Submitting published datasets to NOAA's SOS Data Catalog, and keeping each one's correspondence with it.

The pure half of `sos admin` (design/v0/v0.5.0.md): the email a submission starts from, the message SES sends, filing
the mail kept for the sending address under the dataset it concerns, and the one public fact the site shows, a
dataset's catalog link in site.toml. Nothing here reaches AWS or the network; sosforge/admin.py does, through ports.
"""
from __future__ import annotations

import email
import email.policy
import email.utils
import html
import re
from email.message import EmailMessage

NOAA_ADDRESS = "sos.support@noaa.gov"
DATASET_HEADER = "X-SOS-Forge-Dataset"
SUGGESTION_HEADER = "X-SOS-Forge-Suggestion"   # set by the site's Suggest a dataset form (design/v0/v0.6.0.md)
TEST_HEADER = "X-SOS-Forge-Test"               # a test send from sos admin, never a submission (design/v0/v0.7.3.md)
SUGGESTIONS = "@suggestions"                    # file_mail's key for them; "@" can't start a slug
CATALOG_PREFIX = "https://sos.noaa.gov/"
FILL = re.compile(r"\[\[[^\]]*\]\]")
# Each source's terms of use, matched by a phrase its credit line always carries, in the words docs/data-sources.md
# checked them in. A credit that matches none gets a [[...]] line, so a new source's terms are written before a send.
TERMS = (
    ("Global Imagery Browse Services", "NASA background imagery, via GIBS", "not copyrighted; NASA asks to be credited"),
    ("Scientific Visualization Studio", "NASA Scientific Visualization Studio imagery", "not copyrighted; NASA asks to be credited"),
    ("OpenStreetMap", "OpenStreetMap data",
     "ODbL 1.0; the image is a Produced Work and keeps the credit \"© OpenStreetMap contributors\""),
    ("Natural Earth", "Natural Earth country shapes", "public domain"),
    ("Our World in Data", "Our World in Data", "CC BY 4.0; the data it republishes keeps its original source's terms, "
                                          "and both are cited"),
    ("European Commission Joint Research Centre", "GHSL data, European Commission Joint Research Centre",
     "CC BY 4.0, © European Union; cite the dataset and its DOI"),
    ("U.S. Geological Survey", "USGS earthquake feed", "a US Government work, in the public domain; credit the USGS"),
    ("NOAA Space Weather Prediction Center", "NOAA SWPC OVATION forecast",
     "a US Government work, in the public domain; credit NOAA SWPC"),
    ("FIRMS", "NASA FIRMS active fires", "NASA data, free and open; acknowledge FIRMS and NASA's ESDIS, with a link"),
)
TERMS_DOC = "https://github.com/slpenza/sos-forge/blob/main/docs/data-sources.md"


def length_text(seconds: float) -> str:
    """Return a length as presenters say it, "40 s", "2 min" or "1 min 30 s", as the gallery cards do. Tested by test_noaa.py::test_draft_follows_noaa_checklist."""
    s = round(seconds)
    m, r = divmod(s, 60)
    return f"{m} min {r} s" if m and r else f"{m} min" if m else f"{r} s"


def description_of(d: dict) -> str:
    """Return a dataset's own description from its manifest entry, without the credits the playlist appends to it. Tested by test_noaa.py::test_draft_follows_noaa_checklist."""
    return re.sub(r"\s*Credits:.*$", "", d.get("playlist", {}).get("description", ""), flags=re.S).strip()


def features(d: dict) -> list[str]:
    """Return the notable features a dataset's manifest entry can state for itself (size, length, versions), followed by one marked line asking for what presenters should point out. Tested by test_noaa.py::test_draft_follows_noaa_checklist."""
    size = f"{d.get('width', 4096)} × {d.get('height', 2048)}"
    if d.get("kind") != "movie":
        out = [f"A still image, {size}."]
    else:
        out = [f"A {length_text(d.get('duration_s', 0))} movie at {round(d.get('source_fps', 30))} fps, {size}, in MPEG-4 "
               "Part 2 as NOAA recommends, with an H.264 copy."]
        extra = [x for x in d.get("lengths", []) if not x.get("default")]
        if extra:
            out.append("Also rendered at " + ", ".join(length_text(x["seconds"]) for x in extra)
                       + ", each frame drawn fresh rather than slowed down.")
        if any(x.get("labels") for x in d.get("lengths", [])):
            out.append("Every length also comes with its text as SOS labels (labels.txt) instead of painted on the globe.")
    out.append("[[What presenters should point out: write two or three lines here]]")
    return out


def terms(credits: list[str]) -> list[str]:
    """Return one line per source in a dataset's credits giving its terms of use, each source once, with a [[...]] line for each credit no known source matches, or one when there are no credits. Tested by test_noaa.py::test_draft_states_each_sources_terms and test_every_bundled_source_has_terms."""
    out: list[str] = []
    for c in credits:
        line = next((f"{name}: {t}." for key, name, t in TERMS if key in c), f"[[Terms of use for: {c}]]")
        if line not in out:
            out.append(line)
    return out or ["[[Terms of use for this dataset's data]]"]


def files_of(d: dict) -> list[str]:
    """Return one line per published zip of a dataset, standard length first, each naming the file and its link. Tested by test_noaa.py::test_draft_follows_noaa_checklist."""
    seen, lines = set(), []
    groups = [d.get("downloads", [])] + [x.get("downloads", []) + [x["labels"]] if x.get("labels") else x.get("downloads", [])
                                         for x in d.get("lengths", [])]
    for f in (f for g in groups for f in g):
        if f and f["url"] not in seen:
            seen.add(f["url"])
            lines.append(f"- {f['label']}: {f['url']}")
    return lines


def draft(d: dict, site_url: str, sender: dict) -> dict:
    """Return the first email for submitting a published dataset, {to, subject, body}, laid out as NOAA's catalog page asks: description, notable features, credits, each source's licence and the files, with a [[...]] line left to fill in. Tested by test_noaa.py::test_draft_follows_noaa_checklist and test_draft_states_each_sources_terms."""
    site = site_url.rstrip("/")
    sources = "; ".join(f"{s['label']} ({s['url']})" for s in d.get("sources", [])) or "; ".join(d.get("credits", []))
    body = "\n".join([
        "Hello SOS team,",
        "",
        f"I'd like to offer \"{d['name']}\" for the SOS Data Catalog, and would welcome your review first. It was made "
        "with sos-forge, an open-source tool that builds SOS datasets from open data, and every file below is public.",
        "",
        "Description",
        description_of(d),
        "",
        "Notable features",
        *(f"- {x}" for x in features(d)),
        "",
        "Credits",
        f"Dataset Source: {sources}",
        f"Dataset Developer: {sender['name']}",
        f"Dataset Visualization Developer: {sender['name']}, with sos-forge (https://github.com/slpenza/sos-forge)",
        f"Contact: {sender['address']}",
        *(["Full credits, as the dataset's playlist and README carry them:", *(f"- {c}" for c in d["credits"])]
          if d.get("credits") else []),
        "",
        "Licences",
        *(f"- {x}" for x in terms(d.get("credits", []))),
        f"Every source sos-forge uses, with its terms: {TERMS_DOC}",
        "",
        "Files (each zip holds the dataset and its playlist.sos)",
        *files_of(d),
        "",
        f"Preview it on a sphere in a browser: {site}/simulator.html?d={d['slug']}",
        "",
        "Thank you,",
        sender["name"],
        site,
    ])
    return {"to": NOAA_ADDRESS, "subject": f"Dataset for the SOS Data Catalog: {d['name']}", "body": body}


def unfilled(text: str) -> list[str]:
    """Return every [[...]] line a draft still holds, so a submission can't go out with its placeholders. Tested by test_noaa.py::test_draft_follows_noaa_checklist."""
    return FILL.findall(text)


def message(sender: dict, to: str, subject: str, body: str, slug: str, reply_to: dict | None = None,
            test: bool = False) -> EmailMessage:
    """Return the email to send for a dataset: from the sending address, marked with the dataset's slug so the copy kept for that address files itself, threaded under `reply_to` (a filed message) when it answers one, and marked as a test when `test` is set. Tested by test_noaa.py::test_message_is_marked_and_threaded."""
    m = EmailMessage()
    m["From"] = email.utils.formataddr((sender["name"], sender["address"]))
    m["To"] = to
    m["Subject"] = subject
    m["Date"] = email.utils.formatdate(localtime=False)
    m["Message-ID"] = email.utils.make_msgid(domain=sender["address"].split("@")[1])
    m[DATASET_HEADER] = slug
    if test:
        m[TEST_HEADER] = "1"
    if reply_to and reply_to.get("message_id"):
        m["In-Reply-To"] = reply_to["message_id"]
        # RFC 5322: the parent's References, or its In-Reply-To when it has none, then the parent itself.
        chain = reply_to.get("references") or ([reply_to["in_reply_to"]] if reply_to.get("in_reply_to") else [])
        m["References"] = " ".join([*chain, reply_to["message_id"]])
    m.set_content(body)
    return m


def text_of(msg: email.message.EmailMessage) -> str:
    """Return a received message's readable text: its plain part, or its HTML part with the tags taken out. Tested by test_noaa.py::test_mail_files_under_its_dataset."""
    part = msg.get_body(preferencelist=("plain", "html"))
    if part is None:
        return ""
    text = part.get_content()
    if part.get_content_type() == "text/html":
        text = re.sub(r"<(br|/p|/div)[^>]*>", "\n", text, flags=re.I)
        text = html.unescape(re.sub(r"<[^>]+>", "", text))
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def parse_mail(raw: bytes, key: str = "") -> dict:
    """Return the parts of a stored email the admin page files and shows: ids, threading, sender, recipients, date, subject, dataset, suggestion and test marks, and text. Tested by test_noaa.py::test_mail_files_under_its_dataset."""
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    when = email.utils.parsedate_to_datetime(msg["Date"]) if msg["Date"] else None
    return {
        "key": key,
        "message_id": (msg["Message-ID"] or "").strip(),
        "in_reply_to": (msg["In-Reply-To"] or "").strip(),
        "references": (msg["References"] or "").split(),
        "from": str(msg["From"] or ""),
        "to": str(msg["To"] or ""),
        "date": when.isoformat() if when else "",
        "subject": str(msg["Subject"] or ""),
        "dataset": (msg[DATASET_HEADER] or "").strip(),
        "suggestion": bool((msg[SUGGESTION_HEADER] or "").strip()),
        "test": bool((msg[TEST_HEADER] or "").strip()),
        "text": text_of(msg),
    }


def file_mail(mails: list[dict], datasets: list[dict], sender_address: str) -> dict:
    """Return {slug: [mail, ...], SUGGESTIONS: [...], "": [unfiled]} in date order: a site suggestion by its mark first, a test send as unfiled so it never counts as a submission, then a sent copy by its dataset mark, a reply by the message it answers, else by a dataset's name in the subject; each mail gains `ours`. Tested by test_noaa.py::test_mail_files_under_its_dataset."""
    names = sorted(((d["name"].lower(), d["slug"]) for d in datasets), key=lambda x: -len(x[0]))
    slugs = {d["slug"] for d in datasets}
    thread_of: dict[str, str] = {}
    out: dict[str, list] = {d["slug"]: [] for d in datasets}
    out[""] = []
    out[SUGGESTIONS] = []
    for m in sorted(mails, key=lambda m: m["date"]):
        m = {**m, "ours": sender_address.lower() in m["from"].lower()}
        if m.get("suggestion"):   # before any name match: a suggestion may name a dataset without being NOAA's mail
            out[SUGGESTIONS].append(m)
            continue
        if m.get("test"):   # a test carries its dataset's mark, but filing it there would make the dataset look sent
            out[""].append(m)
            continue
        slug = m["dataset"] if m["dataset"] in slugs else ""
        if not slug:
            slug = next((thread_of[i] for i in [m["in_reply_to"], *reversed(m["references"])] if i in thread_of), "")
        if not slug:
            subject = m["subject"].lower()
            slug = next((s for name, s in names if name in subject), "")
        if slug and m["message_id"]:
            thread_of[m["message_id"]] = slug
        out[slug].append(m)
    return out


def status(thread: list[dict], catalog: str | None) -> str:
    """Return a dataset's submission state in words: in the catalog, NOAA replied, sent, or not submitted. Tested by test_noaa.py::test_mail_files_under_its_dataset."""
    if catalog:
        return "In NOAA's SOS Data Catalog"
    theirs = [m for m in thread if not m["ours"]]
    if theirs:
        return f"NOAA replied {theirs[-1]['date'][:10]}"
    if thread:
        return f"Sent {thread[-1]['date'][:10]}"
    return "Not submitted"


def set_catalog(text: str, slug: str, url: str) -> str:
    """Return site.toml's text with `noaa_catalog = "<url>"` set in the slug's [[datasets]] block, replacing an earlier one and keeping every comment. Tested by test_noaa.py::test_set_catalog_edits_one_block."""
    if not url.startswith(CATALOG_PREFIX) or any(c in url for c in '"\\\n '):
        raise ValueError(f"a catalog link starts with {CATALOG_PREFIX} and has no quotes or spaces")
    blocks = re.split(r"(?m)^(?=\[\[datasets\]\]\s*$)", text)
    for i, block in enumerate(blocks):
        if re.search(rf'(?m)^slug\s*=\s*"{re.escape(slug)}"\s*(#.*)?$', block):
            line = f'noaa_catalog = "{url}"'
            if re.search(r"(?m)^noaa_catalog\s*=", block):
                blocks[i] = re.sub(r"(?m)^noaa_catalog\s*=.*$", line, block)
            else:
                blocks[i] = re.sub(rf'(?m)^(slug\s*=\s*"{re.escape(slug)}"[^\n]*)$', rf"\1\n{line}", block, count=1)
            return "".join(blocks)
    raise ValueError(f"site.toml has no dataset {slug}")
