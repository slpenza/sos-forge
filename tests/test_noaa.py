"""Tests for drafting NOAA catalog submissions and filing the mail that follows (sosforge/noaa.py)."""
from __future__ import annotations

import email.utils

import pytest

from sosforge import noaa

SENDER = {"address": "sos@example.org", "name": "Pat Maker"}
REL = "https://github.com/o/r/releases/download/data-1"
f = lambda n: {"label": n, "url": f"{REL}/{n}"}  # noqa: E731
MOVIE = {
    "slug": "gr", "name": "Getting Richer", "kind": "movie", "width": 4096, "height": 2048, "duration_s": 40.4,
    "source_fps": 30.0,
    "playlist": {"description": "Average income per person. Credits: NASA Blue Marble; Maddison Project."},
    "credits": ["NASA Blue Marble", "Maddison Project Database 2023 (CC BY 4.0)"],
    "sources": [{"label": "Maddison Project Database 2023", "url": "https://example.com/maddison"}],
    "downloads": [f("gr_4k.zip"), f("gr_4k_h264.zip")],
    "lengths": [
        {"seconds": 20, "default": False, "downloads": [f("gr_20s_4k.zip")], "labels": f("gr_20s_labels_4k.zip")},
        {"seconds": 40, "default": True, "downloads": [f("gr_4k.zip"), f("gr_4k_h264.zip")], "labels": f("gr_labels_4k.zip")},
        {"seconds": 101, "default": False, "downloads": [f("gr_101s_4k.zip")], "labels": f("gr_101s_labels_4k.zip")},
    ],
}
STILL = {"slug": "aurora", "name": "Aurora Forecast", "kind": "texture", "width": 4096, "height": 2048,
         "playlist": {"description": "NOAA's forecast."}, "credits": ["NOAA SWPC"], "sources": [],
         "downloads": [f("aurora_4k.zip")], "lengths": []}


def test_draft_follows_noaa_checklist():
    """Checks a submission draft goes to NOAA's catalog address with the description (without the appended credits), notable features stated from the manifest, the credits NOAA asks for, every zip once and a simulator link, and that its one [[...]] line is caught until it is filled. Covers draft, description_of, features, files_of, length_text and unfilled; whether NOAA likes the wording is not tested."""
    d = noaa.draft(MOVIE, "https://sos.example.org/", SENDER)
    assert d["to"] == "sos.support@noaa.gov" and d["subject"] == "Dataset for the SOS Data Catalog: Getting Richer"
    body = d["body"]
    for heading in ("Description", "Notable features", "Credits", "Files (each zip holds the dataset and its playlist.sos)"):
        assert f"\n{heading}\n" in body
    assert "Average income per person." in body and "Credits: NASA" not in body
    assert "- A 40 s movie at 30 fps, 4096 × 2048, in MPEG-4 Part 2" in body
    assert "- Also rendered at 20 s, 1 min 41 s, each frame drawn fresh" in body
    assert "SOS labels (labels.txt)" in body
    assert "Dataset Source: Maddison Project Database 2023 (https://example.com/maddison)" in body
    assert "Dataset Developer: Pat Maker" in body and "Contact: sos@example.org" in body
    assert "carry them:\n- NASA Blue Marble\n- Maddison Project Database 2023 (CC BY 4.0)\n" in body
    zips = [line for line in body.splitlines() if line.startswith("- gr_")]
    assert len(zips) == len(set(zips)) == 7 and zips[0] == f"- gr_4k.zip: {REL}/gr_4k.zip"
    assert "https://sos.example.org/simulator.html?d=gr" in body
    assert noaa.unfilled(body) == ["[[What presenters should point out: write two or three lines here]]"]
    assert noaa.unfilled(body.replace(noaa.unfilled(body)[0], "The 1820s start nearly level.")) == []
    still = noaa.draft(STILL, "https://sos.example.org", SENDER)["body"]
    assert "- A still image, 4096 × 2048." in still and "movie" not in still.split("Credits")[0].split("Notable")[1]
    assert "Dataset Source: NOAA SWPC" in still
    assert [noaa.length_text(s) for s in (40.4, 60, 101, 0)] == ["40 s", "1 min", "1 min 41 s", "0 s"]


def test_message_is_marked_and_threaded():
    """Checks a sent email is from the sending address under its name, to NOAA, carries the dataset's slug, has a Message-ID on the sender's domain, and answers a filed message with In-Reply-To and References built from its References, or its In-Reply-To when it has none. Covers message; how SES rewrites the Message-ID is not tested."""
    m = noaa.message(SENDER, noaa.NOAA_ADDRESS, "Hi", "Body\n", "gr")
    assert m["From"] == "Pat Maker <sos@example.org>" and m["To"] == "sos.support@noaa.gov"
    assert m[noaa.DATASET_HEADER] == "gr" and m["Message-ID"].endswith("@example.org>") and m["In-Reply-To"] is None
    r = noaa.message(SENDER, noaa.NOAA_ADDRESS, "Re: Hi", "Thanks\n", "gr",
                     {"message_id": "<b@noaa.gov>", "references": ["<a@ses>"]})
    assert r["In-Reply-To"] == "<b@noaa.gov>" and r["References"] == "<a@ses> <b@noaa.gov>"
    bare = noaa.message(SENDER, noaa.NOAA_ADDRESS, "Re: Hi", "Thanks\n", "gr",
                        {"message_id": "<b@noaa.gov>", "references": [], "in_reply_to": "<a@ses>"})
    assert bare["References"] == "<a@ses> <b@noaa.gov>"


def raw(frm, subject, when, mid, *, dataset=None, reply=None, refs=None, html=False, body="Hello"):
    """Return a stored email's bytes as the receipt rule would keep it. Helper for test_mail_files_under_its_dataset; not itself tested."""
    lines = [f"From: {frm}", "To: sos@example.org", f"Subject: {subject}", f"Date: {email.utils.format_datetime(when)}",
             f"Message-ID: {mid}"]
    lines += [f"{noaa.DATASET_HEADER}: {dataset}"] if dataset else []
    lines += [f"In-Reply-To: {reply}"] if reply else []
    lines += [f"References: {refs}"] if refs else []
    lines += ["MIME-Version: 1.0", f"Content-Type: text/{'html' if html else 'plain'}; charset=utf-8", "", body]
    return "\r\n".join(lines).encode()


def test_mail_files_under_its_dataset():
    """Checks stored mail files under its dataset: the sent copy by its mark, NOAA's reply by In-Reply-To, a reply to that reply by References, a mail naming a dataset in its subject by name, and anything else as unfiled; HTML text is readable and each dataset's status follows its thread. Covers parse_mail, text_of, file_mail and status; real NOAA mail formats are not tested."""
    from datetime import datetime, timedelta, timezone
    t0 = datetime(2026, 11, 25, 15, tzinfo=timezone.utc)
    mails = [
        noaa.parse_mail(raw("Pat Maker <sos@example.org>", "Dataset for the SOS Data Catalog: Getting Richer", t0,
                            "<sent@email.amazonses.com>", dataset="gr"), "k1"),
        noaa.parse_mail(raw("SOS Support <sos.support@noaa.gov>", "RE: Dataset for the SOS Data Catalog: Getting Richer",
                            t0 + timedelta(days=2), "<r1@noaa.gov>", reply="<sent@email.amazonses.com>",
                            html=True, body="<p>Thanks &amp; welcome.</p><p>We'll review it.</p>"), "k2"),
        noaa.parse_mail(raw("Other Person <op@noaa.gov>", "Fwd: your dataset", t0 + timedelta(days=3), "<r2@noaa.gov>",
                            refs="<sent@email.amazonses.com> <r1@noaa.gov>"), "k3"),
        noaa.parse_mail(raw("Someone <a@b.c>", "About Aurora Forecast", t0 + timedelta(days=1), "<x@b.c>"), "k4"),
        noaa.parse_mail(raw("Spam <s@s.s>", "Win a prize", t0, "<s@s.s>"), "k5"),
    ]
    assert mails[1]["text"] == "Thanks & welcome.\nWe'll review it." and mails[0]["dataset"] == "gr"
    filed = noaa.file_mail(mails, [MOVIE, STILL], "sos@example.org")
    assert [m["key"] for m in filed["gr"]] == ["k1", "k2", "k3"] and [m["ours"] for m in filed["gr"]] == [True, False, False]
    assert [m["key"] for m in filed["aurora"]] == ["k4"] and [m["key"] for m in filed[""]] == ["k5"]
    assert noaa.status(filed["gr"], None) == "NOAA replied 2026-11-28"
    assert noaa.status(filed["gr"][:1], None) == "Sent 2026-11-25"
    assert noaa.status([], None) == "Not submitted"
    assert noaa.status([], "https://sos.noaa.gov/catalog/datasets/x/") == "In NOAA's SOS Data Catalog"


SITE = '''[site]
domain = "sos.example.org"

[[datasets]]
slug = "gr"    # Getting Richer
release = "data-1"
# keep this comment

[[datasets]]
slug = "aurora"
noaa_catalog = "https://sos.noaa.gov/catalog/datasets/old/"
release = "data-1"
'''


def test_set_catalog_edits_one_block():
    """Checks recording a catalog link adds the line under the dataset's slug or replaces its earlier one, leaves the other block and every comment alone, and refuses a link outside sos.noaa.gov, one with a quote, or an unknown dataset. Covers set_catalog; that site.toml then loads and the card links it are tested by test_site.py::test_lengths_and_download_checks and test_browser.py::test_gallery_cards_set_downloads."""
    t = noaa.set_catalog(SITE, "gr", "https://sos.noaa.gov/catalog/datasets/getting-richer/")
    assert 'slug = "gr"    # Getting Richer\nnoaa_catalog = "https://sos.noaa.gov/catalog/datasets/getting-richer/"\n' in t
    assert "# keep this comment" in t and 'noaa_catalog = "https://sos.noaa.gov/catalog/datasets/old/"' in t
    t2 = noaa.set_catalog(t, "aurora", "https://sos.noaa.gov/catalog/datasets/aurora/")
    assert t2.count("noaa_catalog") == 2 and "/old/" not in t2
    for url in ("https://example.com/x", 'https://sos.noaa.gov/a"b'):
        with pytest.raises(ValueError, match="starts with"):
            noaa.set_catalog(SITE, "gr", url)
    with pytest.raises(ValueError, match="no dataset nope"):
        noaa.set_catalog(SITE, "nope", "https://sos.noaa.gov/catalog/")
