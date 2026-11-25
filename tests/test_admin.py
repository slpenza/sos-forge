"""Tests for `sos admin`, the local page that submits datasets to NOAA and keeps their replies (sosforge/admin.py)."""
from __future__ import annotations

import email
import email.policy
import http.client
import json
import threading
import urllib.request

import pytest

from sosforge import noaa
from sosforge.admin import Admin, Config, Ports, catalogs, load_config, rule_stores, serve
from sosforge.recipe import RecipeError
from test_noaa import MOVIE, SITE, STILL, raw


class Fake:
    """Stand-in ports: a manifest, an in-memory mail store, a switch for whether mail is stored, and a record of what was sent."""

    def __init__(self):
        self.mail: dict[str, bytes] = {}
        self.ready = False
        self.sent: list[tuple] = []

    def ports(self) -> Ports:
        def send(data, sender, to, bcc):
            self.sent.append((data, sender, to, bcc))
            return f"ses-{len(self.sent)}"
        return Ports(manifest=lambda: {"datasets": [MOVIE, STILL]}, list_mail=lambda: sorted(self.mail),
                     get_mail=lambda k: self.mail[k], send=send, inbound_ready=lambda address: self.ready)


@pytest.fixture
def setup(tmp_path):
    """Return an Admin over a site.toml copy and fake ports, with the fake. Helper fixture; not itself tested."""
    (tmp_path / "site.toml").write_text(SITE)
    cfg = Config(address="sos@example.org", name="Pat Maker", bucket="b", prefix="mail/sos/", domain="sos.example.org",
                 site_toml=tmp_path / "site.toml")
    fake = Fake()
    return Admin(cfg, fake.ports()), fake


def test_config_names_missing_settings(tmp_path):
    """Checks sos admin refuses to start without its four settings, naming the missing ones, reads the site's domain from site.toml with the mail prefix ending in a slash, and reads the optional test address, refusing one that is a list or the sending address. Covers load_config; .env.local loading is tested by test_cli.py::test_env_local_loaded_without_override."""
    (tmp_path / "site.toml").write_text(SITE)
    with pytest.raises(RecipeError, match="SOSFORGE_MAIL_BUCKET, SOSFORGE_MAIL_PREFIX"):
        load_config(tmp_path / "site.toml", {"SOSFORGE_ADMIN_FROM": "sos@example.org", "SOSFORGE_ADMIN_NAME": "Pat"})
    cfg = load_config(tmp_path / "site.toml", {"SOSFORGE_ADMIN_FROM": "sos@example.org", "SOSFORGE_ADMIN_NAME": "Pat",
                                                "SOSFORGE_MAIL_BUCKET": "b", "SOSFORGE_MAIL_PREFIX": "mail/sos"})
    assert (cfg.domain, cfg.prefix, cfg.address, cfg.test_to) == ("sos.example.org", "mail/sos/", "sos@example.org", "")
    env = {"SOSFORGE_ADMIN_FROM": "sos@example.org", "SOSFORGE_ADMIN_NAME": "Pat", "SOSFORGE_MAIL_BUCKET": "b",
           "SOSFORGE_MAIL_PREFIX": "mail/sos/"}
    assert load_config(tmp_path / "site.toml", {**env, "SOSFORGE_TEST_TO": " tester@example.net "}).test_to == "tester@example.net"
    for bad in ("tester@example.net, other@example.net", "Pat <tester@example.net>", "not-an-address"):
        with pytest.raises(RecipeError, match="one email address"):
            load_config(tmp_path / "site.toml", {**env, "SOSFORGE_TEST_TO": bad})
    with pytest.raises(RecipeError, match="other than SOSFORGE_ADMIN_FROM"):
        load_config(tmp_path / "site.toml", {**env, "SOSFORGE_TEST_TO": "SOS@example.org"})


def test_rule_stores_reads_the_rule_set():
    """Checks the readiness test: an enabled rule must take the address (by address, by its domain, or by listing none) and store to the configured bucket under exactly the prefix; a rule for other addresses, a disabled one, another bucket or a broader prefix doesn't count. Covers rule_stores against the shape describe-active-receipt-rule-set returns; the live rule set is checked by sos admin."""
    s3 = {"S3Action": {"BucketName": "b", "ObjectKeyPrefix": "mail/sos/"}}
    other = {"Name": "site", "Enabled": True, "Recipients": ["hello@example.org"], "Actions": [s3]}
    assert not rule_stores({"Rules": [other]}, "sos@example.org", "b", "mail/sos/")
    ours = {"Name": "sos", "Enabled": True, "Recipients": ["sos@example.org"], "Actions": [{"LambdaAction": {}}, s3]}
    assert rule_stores({"Rules": [other, ours]}, "sos@example.org", "b", "mail/sos/")
    assert not rule_stores({"Rules": [{**ours, "Enabled": False}]}, "sos@example.org", "b", "mail/sos/")
    assert not rule_stores({"Rules": [ours]}, "sos@example.org", "other-bucket", "mail/sos/")
    broad = {**ours, "Actions": [{"S3Action": {"BucketName": "b", "ObjectKeyPrefix": "mail/"}}]}
    assert not rule_stores({"Rules": [broad]}, "sos@example.org", "b", "mail/sos/")
    assert rule_stores({"Rules": [{**ours, "Recipients": ["example.org"]}]}, "sos@example.org", "b", "mail/sos/")
    assert rule_stores({"Rules": [{**ours, "Recipients": []}]}, "sos@example.org", "b", "mail/sos/")
    assert not rule_stores({}, "sos@example.org", "b", "mail/sos/")


def test_admin_files_drafts_sends_and_records(setup):
    """Checks the admin's actions end to end over fake ports: state lists each dataset as not submitted, or in the catalog when site.toml says so; the draft is the submission; sending refuses a draft with its [[...]] line and refuses while replies wouldn't be stored, sending nothing; once ready it sends only to NOAA with a copy to the sender, marked with the slug; with the copy stored the next draft answers it and threads; a recorded catalog link lands in site.toml and the status; and the site's suggestions list apart, newest first, without joining a dataset's thread. Covers Admin.state, filed, draft, send, record_catalog and catalogs; the aws CLI ports are exercised by sos admin."""
    admin, fake = setup
    st = admin.state()
    assert st["ready"] is False and [d["status"] for d in st["datasets"]] == ["Not submitted", "In NOAA's SOS Data Catalog"]
    d = admin.draft("gr")
    assert d["reply"] is None and d["subject"].endswith("Getting Richer") and "[[" in d["body"]
    with pytest.raises(ValueError, match="fill in or remove"):
        admin.send("gr", d["subject"], d["body"], None)
    body = noaa.FILL.sub("The 1820s start nearly level.", d["body"])
    with pytest.raises(PermissionError, match="nothing was sent"):
        admin.send("gr", d["subject"], body, None)
    assert fake.sent == []
    fake.ready = True
    with pytest.raises(KeyError):
        admin.send("nope", d["subject"], body, None)
    assert admin.send("gr", d["subject"], body, None) == "ses-1"
    data, sender, to, bcc = fake.sent[0]
    sent = email.message_from_bytes(data, policy=email.policy.default)
    assert (sender, to, bcc) == ("sos@example.org", ["sos.support@noaa.gov"], ["sos@example.org"])
    assert sent[noaa.DATASET_HEADER] == "gr" and sent["To"] == "sos.support@noaa.gov" and "nearly level" in sent.get_content()
    from datetime import datetime, timezone
    fake.mail["mail/sos/1"] = data
    fake.mail["mail/sos/2"] = raw("SOS Support <sos.support@noaa.gov>", "RE: " + d["subject"],
                                  datetime(2030, 1, 2, tzinfo=timezone.utc), "<r1@noaa.gov>", reply=sent["Message-ID"],
                                  body="Looks good.")
    st = admin.state()
    gr = st["datasets"][0]
    assert [m["key"] for m in gr["thread"]] == ["mail/sos/1", "mail/sos/2"] and gr["status"] == "NOAA replied 2030-01-02"
    nxt = admin.draft("gr")
    assert nxt["reply"] == "mail/sos/2" and nxt["subject"] == "RE: " + d["subject"] and "> Looks good." in nxt["body"]
    admin.send("gr", nxt["subject"], "Thank you.\n" + nxt["body"], nxt["reply"])
    answer = email.message_from_bytes(fake.sent[1][0], policy=email.policy.default)
    assert answer["In-Reply-To"] == "<r1@noaa.gov>" and sent["Message-ID"] in answer["References"]
    admin.record_catalog("gr", " https://sos.noaa.gov/catalog/datasets/getting-richer/ ")
    assert catalogs(admin.cfg.site_toml)["gr"] == "https://sos.noaa.gov/catalog/datasets/getting-richer/"
    assert admin.state()["datasets"][0]["status"] == "In NOAA's SOS Data Catalog"
    # A suggestion from the site's form lists apart, newest first, even when its subject names a dataset.
    for n, day in ((1, 3), (2, 4)):
        fake.mail[f"mail/sos/s{n}"] = raw("SOS Forge suggestions <sos-suggest@example.org>", f"Dataset suggestion: Getting Richer {n}",
                                          datetime(2030, 1, day, tzinfo=timezone.utc), f"<s{n}@example.org>", suggestion=True)
    st = admin.state()
    assert [m["key"] for m in st["suggestions"]] == ["mail/sos/s2", "mail/sos/s1"]
    assert [m["key"] for m in st["datasets"][0]["thread"]] == ["mail/sos/1", "mail/sos/2"] and st["unfiled"] == []


def test_send_a_test(setup):
    """Checks a test send: refused with the setting's name while no test address is set, and refused with [[...]] lines like a real send; once set it goes even while replies wouldn't be stored, only to the test address with no copy, as the same subject and text marked as a test; and a stored copy of it would still leave the dataset unsent, with the next draft the submission. Covers Admin.send's test path and state's test_to; the email arriving is checked by hand with sos admin."""
    admin, fake = setup
    d = admin.draft("gr")
    body = noaa.FILL.sub("The 1820s start nearly level.", d["body"])
    assert admin.state()["test_to"] is None
    with pytest.raises(ValueError, match="SOSFORGE_TEST_TO"):
        admin.send("gr", d["subject"], body, None, test=True)
    admin.cfg.test_to = "tester@example.net"
    with pytest.raises(ValueError, match="fill in or remove"):
        admin.send("gr", d["subject"], d["body"], None, test=True)
    assert fake.sent == [] and fake.ready is False and admin.state()["test_to"] == "tester@example.net"
    assert admin.send("gr", d["subject"], body, None, test=True) == "ses-1"
    data, sender, to, bcc = fake.sent[0]
    sent = email.message_from_bytes(data, policy=email.policy.default)
    assert (sender, to, bcc) == ("sos@example.org", ["tester@example.net"], [])
    assert sent["To"] == "tester@example.net" and sent["Subject"] == d["subject"] and sent.get_content().strip() == body.strip()
    assert sent[noaa.TEST_HEADER] == "1" and sent[noaa.DATASET_HEADER] == "gr"
    fake.mail["mail/sos/t"] = data
    st = admin.state()
    assert st["datasets"][0]["status"] == "Not submitted" and [m["key"] for m in st["unfiled"]] == ["mail/sos/t"]
    assert admin.draft("gr")["reply"] is None


def test_suggestion_switch(tmp_path):
    """Checks the switch for where the site's suggestions go: state shows the current one with both deployed addresses, "test" is refused while no test address is deployed and any value but mailbox or test is refused, a switch writes the setting and returns the new state, and a failure to read it shows as unavailable rather than a guess. Covers Admin.suggest_state and set_destination with fake ports; the aws CLI ports are exercised by sos admin, and the Lambda's reading by infra/test/suggest.test.mjs."""
    (tmp_path / "site.toml").write_text(SITE)
    cfg = Config(address="sos@example.org", name="Pat", bucket="b", prefix="mail/sos/", domain="sos.example.org",
                 site_toml=tmp_path / "site.toml")
    box = {"destination": "mailbox", "targets": {"mailbox": "sos@example.org", "test": None}}
    fake = Fake()
    ports = fake.ports()
    ports.suggest_targets = lambda: dict(box["targets"])
    ports.destination = lambda: box["destination"]
    ports.set_destination = lambda value: box.update(destination=value)
    admin = Admin(cfg, ports)
    assert admin.state()["suggest"] == {"destination": "mailbox", "mailbox": "sos@example.org", "test": None}
    with pytest.raises(ValueError, match="SOS_SUGGEST_TEST"):
        admin.set_destination("test")
    with pytest.raises(ValueError, match="mailbox, test"):
        admin.set_destination("anywhere")
    assert box["destination"] == "mailbox"
    box["targets"]["test"] = "tester@example.net"
    assert admin.set_destination("test") == {"destination": "test", "mailbox": "sos@example.org", "test": "tester@example.net"}
    assert admin.set_destination("mailbox")["destination"] == "mailbox"

    def broken():
        raise RecipeError("no deployed Suggest Lambda found")
    ports.suggest_targets = broken
    assert admin.state()["suggest"] == {"unavailable": "no deployed Suggest Lambda found"}


def call(port, method, path, *, token=None, host=None, origin=None, body=None):
    """Make one request to the admin server with chosen Host, Origin and token headers, returning (status, json-or-text). Helper for test_server_refuses_other_origins_and_tokens; not itself tested."""
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    headers = {"Host": host or f"127.0.0.1:{port}"}
    if token:
        headers["X-Admin-Token"] = token
    if origin:
        headers["Origin"] = origin
    data = json.dumps(body).encode() if body is not None else None
    if data is not None:
        headers["Content-Type"] = "application/json"
    c.request(method, path, body=data, headers=headers)
    r = c.getresponse()
    text = r.read().decode()
    c.close()
    try:
        return r.status, json.loads(text)
    except ValueError:
        return r.status, text


def test_server_refuses_other_origins_and_tokens(setup):
    """Checks the server answers only local requests: the page carries this run's token; the API refuses a missing or wrong token, another site's Origin, and a foreign Host (DNS rebinding); a send while mail isn't stored is a 409 that sends nothing, while a test goes once a test address is set and not before, and a test flag that isn't true or false is refused rather than sent; the suggestion switch needs the token and refuses a test address that isn't deployed; the favicon request gets an empty answer; unknown paths are 404. Covers serve and handler; a real browser's behaviour is not tested."""
    admin, fake = setup
    srv = serve(admin, 0)
    port, token = srv.server_address[1], srv.token
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        status, page = call(port, "GET", "/")
        assert status == 200 and f"const TOKEN = '{token}';" in page
        assert call(port, "GET", "/", host="evil.example")[0] == 403
        assert call(port, "GET", "/favicon.ico")[0] == 204
        assert call(port, "GET", "/api/state")[0] == 403
        assert call(port, "GET", "/api/state", token="wrong")[0] == 403
        status, st = call(port, "GET", "/api/state", token=token)
        assert status == 200 and [d["slug"] for d in st["datasets"]] == ["gr", "aurora"]
        assert call(port, "POST", "/api/draft", token=token, body={"slug": "gr"}, origin="https://evil.example")[0] == 403
        assert call(port, "POST", "/api/draft", token=token, body={"slug": "gr"}, host=f"evil.example:{port}")[0] == 403
        status, d = call(port, "POST", "/api/draft", token=token, body={"slug": "gr"}, origin=f"http://127.0.0.1:{port}")
        assert status == 200 and d["to"] == "sos.support@noaa.gov"
        status, err = call(port, "POST", "/api/send", token=token,
                           body={"slug": "gr", "subject": "S", "body": "filled in"})
        assert status == 409 and "nothing was sent" in err["error"] and fake.sent == []
        test = {"slug": "gr", "subject": "S", "body": "filled in", "test": True}
        assert call(port, "POST", "/api/send", token=token, body=test)[0] == 400 and fake.sent == []
        admin.cfg.test_to = "tester@example.net"
        assert call(port, "POST", "/api/send", body=test)[0] == 403 and fake.sent == []
        status, ok = call(port, "POST", "/api/send", token=token, body=test)
        assert status == 200 and ok["message_id"] == "ses-1" and fake.sent[0][2:] == (["tester@example.net"], [])
        assert call(port, "POST", "/api/send", token=token, body={**test, "test": "yes"})[0] == 400 and len(fake.sent) == 1
        assert call(port, "POST", "/api/catalog", token=token, body={"slug": "gr", "url": "https://x.example/"})[0] == 400
        assert call(port, "POST", "/api/nope", token=token, body={})[0] == 404
        assert call(port, "POST", "/api/suggest-to", token=token, body={"destination": "test"})[0] == 400
        status, switched = call(port, "POST", "/api/suggest-to", token=token, body={"destination": "mailbox"})
        assert status == 200 and switched["destination"] == "mailbox"
        assert call(port, "POST", "/api/suggest-to", body={"destination": "mailbox"})[0] == 403
        assert urllib.request.urlopen(f"http://127.0.0.1:{port}/").status == 200
    finally:
        srv.shutdown()
