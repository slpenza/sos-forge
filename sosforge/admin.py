"""`sos admin`: the maintainer's local page for submitting published datasets to NOAA and reading what came back.

It runs on 127.0.0.1 with the maintainer's own AWS login, through the aws CLI, so the public site has no button,
no login and nothing anyone else can press (design/v0/v0.5.0.md). Every POST needs the page's per-run token, and
mail can only go to NOAA's catalog address, or as a test to the maintainer's own SOSFORGE_TEST_TO
(design/v0/v0.7.3.md). A submission is sent from the address in SOSFORGE_ADMIN_FROM with a copy to itself, so the copy
and every reply land in the mail store that address's receipt rule writes to, where this page files them under their
dataset. A test goes only to the test address, so nothing is filed. The pure parts are sosforge/noaa.py.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
import secrets
import shutil
import subprocess
import tempfile
import tomllib
import urllib.request
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

from . import noaa
from .fetch import cache_root
from .recipe import RecipeError

LOG = logging.getLogger(__name__)
PAGE = Path(__file__).with_name("admin.html")
ENV = {"address": "SOSFORGE_ADMIN_FROM", "name": "SOSFORGE_ADMIN_NAME", "bucket": "SOSFORGE_MAIL_BUCKET",
       "prefix": "SOSFORGE_MAIL_PREFIX"}
TEST_TO = "SOSFORGE_TEST_TO"   # optional: where "Send a test to me" goes instead of NOAA (design/v0/v0.7.3.md)
ADDRESS = re.compile(r"[^@\s,;<>\"]+@[^@\s,;<>\"]+\.[^@\s,;<>\"]+")


@dataclass
class Config:
    """What `sos admin` needs to know: who sends, where that address's mail is stored, which site it serves, and where a test goes."""
    address: str
    name: str
    bucket: str
    prefix: str
    domain: str
    site_toml: Path
    test_to: str = ""


@dataclass
class Ports:
    """Everything `sos admin` reaches outside itself, so tests can hand it fakes: the live manifest, the mail store, sending, and whether the sending address's mail is being stored."""
    manifest: Callable[[], dict]
    list_mail: Callable[[], list[str]]
    get_mail: Callable[[str], bytes]
    send: Callable[[bytes, str, list[str], list[str]], str]
    inbound_ready: Callable[[str], bool]
    # Where the site's suggestions go (design/v0/v0.7.2.md): the addresses the deployed Lambda may use, and the switch.
    suggest_targets: Callable[[], dict] = lambda: {"mailbox": None, "test": None}
    destination: Callable[[], str] = lambda: "mailbox"
    set_destination: Callable[[str], None] = lambda value: None


def load_config(site_toml: Path, env: dict | None = None) -> Config:
    """Read the sending address, its name, its mail store and the optional test address from the environment (.env.local is loaded first) and the site's domain from site.toml, refusing with the names of any missing settings and a test address that isn't one address or is the sending one. Tested by test_admin.py::test_config_names_missing_settings."""
    env = os.environ if env is None else env
    missing = [v for v in ENV.values() if not env.get(v, "").strip()]
    if missing:
        raise RecipeError(f"sos admin needs {', '.join(missing)} (put them in .env.local)")
    domain = tomllib.loads(Path(site_toml).read_text())["site"]["domain"]
    prefix = env[ENV["prefix"]].strip()
    address, test_to = env[ENV["address"]].strip(), env.get(TEST_TO, "").strip()
    if test_to and not ADDRESS.fullmatch(test_to):
        raise RecipeError(f"{TEST_TO} must be one email address")
    if test_to.lower() == address.lower():
        # Its copy would be stored with the NOAA mail; the test is for seeing the email as NOAA would, elsewhere.
        raise RecipeError(f"{TEST_TO} must be an address other than {ENV['address']}")
    return Config(address=address, name=env[ENV["name"]].strip(), bucket=env[ENV["bucket"]].strip(),
                  prefix=prefix if prefix.endswith("/") else prefix + "/", domain=domain, site_toml=Path(site_toml),
                  test_to=test_to)


def aws(*args: str) -> str:
    """Run one aws CLI command with the maintainer's login and return its output, raising its error text if it fails. Exercised by sos admin against AWS; not unit tested."""
    exe = shutil.which("aws")
    if not exe:
        raise RecipeError("sos admin uses the aws CLI, which isn't on your PATH")
    res = subprocess.run([exe, *args], capture_output=True, text=True, timeout=120)
    if res.returncode:
        raise RecipeError(f"aws {args[0]} {args[1]}: {res.stderr.strip() or res.stdout.strip()}")
    return res.stdout


def rule_stores(rule_set: dict, address: str, bucket: str, prefix: str) -> bool:
    """Return whether the active receipt rule set has an enabled rule that takes mail for `address` and stores it in `bucket` under exactly `prefix`, where the page reads it, as `aws ses describe-active-receipt-rule-set` describes it. Tested by test_admin.py::test_rule_stores_reads_the_rule_set."""
    domain = address.split("@")[1].lower()
    for rule in rule_set.get("Rules", []):
        if not rule.get("Enabled"):
            continue
        recipients = [r.lower() for r in rule.get("Recipients", [])]
        takes = not recipients or address.lower() in recipients or domain in recipients
        # Exactly the prefix: a rule storing under a broader one ("mail/") puts messages where the page never lists.
        stores = any(a.get("S3Action", {}).get("BucketName") == bucket
                     and a["S3Action"].get("ObjectKeyPrefix", "") == prefix for a in rule.get("Actions", []))
        if takes and stores:
            return True
    return False


def aws_ports(cfg: Config) -> Ports:
    """Return the ports that reach the live site, S3, SES and the receipt rules through the aws CLI, caching each stored email under the sosforge cache. Exercised by sos admin against AWS; not unit tested."""
    cache = cache_root() / "mail" / cfg.bucket

    def manifest() -> dict:
        with urllib.request.urlopen(f"https://{cfg.domain}/datasets.json", timeout=60) as r:
            return json.load(r)

    def list_mail() -> list[str]:
        out = aws("s3api", "list-objects-v2", "--bucket", cfg.bucket, "--prefix", cfg.prefix,
                  "--query", "Contents[].Key", "--output", "json")
        return [k for k in (json.loads(out) or []) if not k.endswith("AMAZON_SES_SETUP_NOTIFICATION")]

    def get_mail(key: str) -> bytes:
        path = cache / key
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            aws("s3", "cp", f"s3://{cfg.bucket}/{key}", str(path), "--only-show-errors")
        return path.read_bytes()

    def send(raw: bytes, sender: str, to: list[str], bcc: list[str]) -> str:
        body = {"FromEmailAddress": sender, "Destination": {"ToAddresses": to, "BccAddresses": bcc},
                "Content": {"Raw": {"Data": base64.b64encode(raw).decode()}}}
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(body, f)
        try:
            return json.loads(aws("sesv2", "send-email", "--cli-input-json", f"file://{f.name}", "--output", "json"))["MessageId"]
        finally:
            os.unlink(f.name)

    def inbound_ready(address: str) -> bool:
        return rule_stores(json.loads(aws("ses", "describe-active-receipt-rule-set", "--output", "json") or "{}"),
                           address, cfg.bucket, cfg.prefix)

    def suggest_targets() -> dict:
        found = json.loads(aws("lambda", "list-functions", "--query",
                               "Functions[?starts_with(FunctionName, 'SosForgeSite-Suggest')].Environment.Variables",
                               "--output", "json") or "[]")
        env = next((e for e in found if e and e.get("TO_ADDRESS")), None)
        if env is None:
            raise RecipeError("no deployed Suggest Lambda found (SosForgeSite-Suggest…)")
        return {"mailbox": env["TO_ADDRESS"], "test": env.get("TEST_ADDRESS") or None}

    def destination() -> str:
        try:
            return aws("ssm", "get-parameter", "--name", DESTINATION_PARAM, "--query", "Parameter.Value", "--output", "text").strip()
        except RecipeError as exc:
            if "ParameterNotFound" in str(exc):
                return "mailbox"
            raise

    def set_destination(value: str) -> None:
        aws("ssm", "put-parameter", "--name", DESTINATION_PARAM, "--type", "String", "--value", value, "--overwrite")

    return Ports(manifest, list_mail, get_mail, send, inbound_ready, suggest_targets, destination, set_destination)


DESTINATION_PARAM = "/sosforge/suggest/destination"   # read by infra/lambda/suggest at send time (design/v0/v0.7.2.md)
DESTINATIONS = ("mailbox", "test")


def catalogs(site_toml: Path) -> dict:
    """Return each dataset's recorded NOAA catalog link from site.toml. Tested by test_admin.py::test_admin_files_drafts_sends_and_records."""
    return {d["slug"]: d.get("noaa_catalog") for d in tomllib.loads(Path(site_toml).read_text()).get("datasets", [])}


class Admin:
    """The admin page's actions over its config and ports: the state it shows, drafts, sending and recording a catalog link."""

    def __init__(self, cfg: Config, ports: Ports):
        """Keep the config and ports. Exercised by every test_admin.py test."""
        self.cfg, self.ports = cfg, ports

    @property
    def sender(self) -> dict:
        """Return the sending address and name as noaa.draft and noaa.message take them. Exercised by every test_admin.py test."""
        return {"address": self.cfg.address, "name": self.cfg.name}

    def filed(self) -> tuple[list[dict], dict]:
        """Return the live datasets and the stored mail filed under them. Tested by test_admin.py::test_admin_files_drafts_sends_and_records."""
        datasets = self.ports.manifest()["datasets"]
        mails = [noaa.parse_mail(self.ports.get_mail(k), k) for k in self.ports.list_mail()]
        return datasets, noaa.file_mail(mails, datasets, self.cfg.address)

    def state(self) -> dict:
        """Return what the page shows: whether replies would be stored, where a test goes, each dataset with its status, catalog link and thread, the site's suggestions newest first, then mail that names no dataset. Tested by test_admin.py::test_admin_files_drafts_sends_and_records."""
        datasets, filed = self.filed()
        cats = catalogs(self.cfg.site_toml)
        return {"sender": self.cfg.address, "noaa": noaa.NOAA_ADDRESS, "ready": self.ports.inbound_ready(self.cfg.address),
                "test_to": self.cfg.test_to or None,
                "datasets": [{"slug": d["slug"], "name": d["name"], "catalog": cats.get(d["slug"]),
                              "status": noaa.status(filed[d["slug"]], cats.get(d["slug"])), "thread": filed[d["slug"]]}
                             for d in datasets],
                "suggestions": filed[noaa.SUGGESTIONS][::-1], "unfiled": filed[""], "suggest": self.suggest_state()}

    def suggest_state(self) -> dict:
        """Return where the site's suggestions go now, {destination, mailbox, test}, or {unavailable} naming why it couldn't be read. Tested by test_admin.py::test_suggestion_switch."""
        try:
            return {"destination": self.ports.destination(), **self.ports.suggest_targets()}
        except RecipeError as exc:
            return {"unavailable": str(exc)}

    def set_destination(self, value: str) -> dict:
        """Switch where the site's suggestions go, "mailbox" or "test", refusing a test address that isn't deployed, and return the new state. Tested by test_admin.py::test_suggestion_switch."""
        if value not in DESTINATIONS:
            raise ValueError(f"suggestions go to one of {', '.join(DESTINATIONS)}")
        if value == "test" and not self.ports.suggest_targets().get("test"):
            raise ValueError("no test address is deployed: set the production variable SOS_SUGGEST_TEST, then release")
        self.ports.set_destination(value)
        return self.suggest_state()

    def draft(self, slug: str) -> dict:
        """Return the email to write next for a dataset: the submission when nothing has been sent, otherwise a reply to the latest message in its thread. Tested by test_admin.py::test_admin_files_drafts_sends_and_records."""
        datasets, filed = self.filed()
        d = next((x for x in datasets if x["slug"] == slug), None)
        if d is None:
            raise KeyError(slug)
        thread = filed[slug]
        if not thread:
            return {**noaa.draft(d, f"https://{self.cfg.domain}", self.sender), "reply": None}
        last = thread[-1]
        quoted = "\n".join(f"> {line}" for line in last["text"].splitlines())
        subject = last["subject"] if last["subject"].lower().startswith("re:") else f"Re: {last['subject']}"
        return {"to": noaa.NOAA_ADDRESS, "subject": subject, "reply": last["key"],
                "body": f"\n\n{self.cfg.name}\n\nOn {last['date'][:10]}, {last['from']} wrote:\n{quoted}"}

    def send(self, slug: str, subject: str, body: str, reply: str | None, test: bool = False) -> str:
        """Send one email about a dataset to NOAA, copied to the sending address, refusing while replies wouldn't be stored, the text still holds [[...]] lines, or `test` isn't true or false; as a test, send the same email only to the test address, marked and uncopied so nothing is filed. Tested by test_admin.py::test_admin_files_drafts_sends_and_records and test_send_a_test."""
        if not isinstance(test, bool):   # a malformed flag must never fall through to a real send
            raise ValueError("test is true or false")
        if not subject.strip() or not body.strip():
            raise ValueError("write a subject and a message")
        left = noaa.unfilled(subject + "\n" + body)
        if left:
            raise ValueError(f"fill in or remove: {', '.join(left)}")
        if test and not self.cfg.test_to:
            raise ValueError(f"no test address: set {TEST_TO} in .env.local and restart sos admin")
        # A test expects no reply, so it needn't wait for replies to be stored.
        if not test and not self.ports.inbound_ready(self.cfg.address):
            raise PermissionError(f"mail to {self.cfg.address} isn't being stored in s3://{self.cfg.bucket}/{self.cfg.prefix} "
                                  "yet, so NOAA's reply would be lost; nothing was sent")
        datasets, filed = self.filed()
        if slug not in {d["slug"] for d in datasets}:
            raise KeyError(slug)
        answering = next((m for m in filed[slug] if m["key"] == reply), None) if reply else None
        to = self.cfg.test_to if test else noaa.NOAA_ADDRESS
        msg = noaa.message(self.sender, to, subject.strip(), body, slug, answering, test=test)
        return self.ports.send(msg.as_bytes(), self.cfg.address, [to], [] if test else [self.cfg.address])

    def record_catalog(self, slug: str, url: str) -> None:
        """Write a dataset's NOAA catalog link into site.toml, for the maintainer to commit with the next version. Tested by test_admin.py::test_admin_files_drafts_sends_and_records."""
        path = self.cfg.site_toml
        path.write_text(noaa.set_catalog(path.read_text(), slug, url.strip()))


def handler(admin: Admin, token: str, port_of: Callable[[], int]) -> type:
    """Return the request handler for the admin page: the page itself, then JSON actions that each need the token, a local Host and, when the browser sends one, a local Origin. Tested by test_admin.py::test_server_refuses_other_origins_and_tokens."""
    page = PAGE.read_text().replace("__TOKEN__", token)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            LOG.debug(fmt, *args)

        def local(self) -> bool:
            hosts = {f"127.0.0.1:{port_of()}", f"localhost:{port_of()}"}
            origin = self.headers.get("Origin")
            return self.headers.get("Host") in hosts and (origin is None or urlsplit(origin).netloc in hosts)

        def reply(self, code: int, body, kind="application/json"):
            data = body.encode() if isinstance(body, str) else json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", f"{kind}; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
                                                        "frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if not self.local():
                return self.reply(403, {"error": "not a local request"})
            if self.path in ("/", "/index.html"):
                return self.reply(200, page, "text/html")
            if self.path == "/favicon.ico":   # the browser asks for one; an empty answer keeps its console clean
                self.send_response(204)
                self.end_headers()
                return None
            if self.path == "/api/state":
                if not secrets.compare_digest(self.headers.get("X-Admin-Token", ""), token):
                    return self.reply(403, {"error": "missing or wrong token"})
                return self.act(admin.state)
            return self.reply(404, {"error": "not found"})

        def do_POST(self):
            if not self.local() or not secrets.compare_digest(self.headers.get("X-Admin-Token", ""), token):
                return self.reply(403, {"error": "not a local request with this page's token"})
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0) or 0)) or b"{}")
            except ValueError:
                return self.reply(400, {"error": "send JSON"})
            actions = {
                "/api/draft": lambda: admin.draft(body.get("slug", "")),
                "/api/send": lambda: {"message_id": admin.send(body.get("slug", ""), body.get("subject", ""),
                                                               body.get("body", ""), body.get("reply"),
                                                               test=body.get("test", False))},
                "/api/catalog": lambda: admin.record_catalog(body.get("slug", ""), body.get("url", "")) or {"ok": True},
                "/api/suggest-to": lambda: admin.set_destination(body.get("destination", "")),
            }
            if self.path not in actions:
                return self.reply(404, {"error": "not found"})
            return self.act(actions[self.path])

        def act(self, fn):
            try:
                return self.reply(200, fn())
            except PermissionError as exc:
                return self.reply(409, {"error": str(exc)})
            except (ValueError, KeyError) as exc:
                return self.reply(400, {"error": str(exc)})
            except RecipeError as exc:
                return self.reply(502, {"error": str(exc)})

    return Handler


def serve(admin: Admin, port: int = 8030) -> ThreadingHTTPServer:
    """Return the admin page's server bound to 127.0.0.1 with a fresh token, not yet serving; port 0 picks a free one. Tested by test_admin.py::test_server_refuses_other_origins_and_tokens."""
    token = secrets.token_urlsafe(24)
    box: dict = {}
    srv = ThreadingHTTPServer(("127.0.0.1", port), handler(admin, token, lambda: box["port"]))
    box["port"] = srv.server_address[1]
    srv.token = token
    return srv
