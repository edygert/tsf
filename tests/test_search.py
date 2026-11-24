import re

import pytest

from tsf import tshark as tshark_mod
from tsf.data import Database
from tsf.search import FieldSearcher, implies, match_fields, match_protocols, parse


def f(name, desc="", ftype="FT_UINT8", blurb=""):
    return {"name": name, "description": desc, "type": ftype, "base": "BASE_DEC", "bitmask": "0x0", "blurb": blurb}


DOC = {
    "tshark_version": "TShark test",
    "generated_utc": "2026-11-21T00:00:00Z",
    "protocols": {
        "dns": {
            "name": "Domain Name System",
            "short_name": "DNS",
            "fields": [
                f("dns.a", "Address", "FT_IPv4"),
                f("dns.qry.name", "Name", "FT_STRING"),
                f("dns.qry.type", "Type", "FT_UINT16", blurb="query record type"),
            ],
        },
        "http": {
            "name": "Hypertext Transfer Protocol",
            "short_name": "HTTP",
            "fields": [
                f("http.request", "Request", "FT_BOOLEAN"),
                f("http.user_agent", "User-Agent", "FT_STRING"),
                f("http.x", "Carries the agent header", "FT_STRING"),
            ],
        },
        "empty": {"name": "No Fields", "short_name": "EMPTY", "fields": []},
        "tpkt": {
            "name": "TPKT",
            "short_name": "TPKT",
            "fields": [],
            "also_registered_as": [{"name": "TPKT Heuristic (for RDP)", "short_name": "TPKT Heuristic"}],
        },
    },
}


@pytest.fixture(scope="module")
def db():
    return Database(DOC)


def names(db, idx):
    return [db.f_name[i] for i in idx]


def test_load_layout(db):
    assert db.field_total == 6
    dns = db.proto_by_key["dns"]
    assert (dns.start, dns.end, dns.field_count) == (0, 3, 3)
    assert db.proto_by_key["empty"].field_count == 0


def test_empty_query_returns_all_in_order(db):
    assert names(db, match_fields(db, parse(""))) == db.f_name


def test_words_are_anded_and_ranked(db):
    # name match (http.user_agent) ranks above description match (http.x)
    assert names(db, match_fields(db, parse("agent"))) == ["http.user_agent", "http.x"]
    assert names(db, match_fields(db, parse("http agent"))) == ["http.user_agent", "http.x"]
    assert names(db, match_fields(db, parse("dns agent"))) == []


def test_exact_and_component_prefix_rank(db):
    assert names(db, match_fields(db, parse("dns.a")))[0] == "dns.a"
    # "qry" is a dotted-component prefix of both dns.qry.* names
    assert names(db, match_fields(db, parse("qry"))) == ["dns.qry.name", "dns.qry.type"]


def test_blurb_match(db):
    assert names(db, match_fields(db, parse("record"))) == ["dns.qry.type"]


def test_prefix_filters(db):
    assert names(db, match_fields(db, parse("p:DNS"))) == ["dns.a", "dns.qry.name", "dns.qry.type"]
    assert names(db, match_fields(db, parse("p:dns p:http t:string"))) == [
        "dns.qry.name",
        "http.user_agent",
        "http.x",
    ]
    assert names(db, match_fields(db, parse("t:ipv4"))) == ["dns.a"]
    assert names(db, match_fields(db, parse("n:agent"))) == ["http.user_agent"]
    assert names(db, match_fields(db, parse("p:nosuch"))) == []
    assert names(db, match_fields(db, parse("p:"))) == db.f_name  # incomplete token ignored


def test_regex(db):
    assert names(db, match_fields(db, parse("/^dns\\.qry/"))) == ["dns.qry.name", "dns.qry.type"]
    assert names(db, match_fields(db, parse("/USER"))) == ["http.user_agent"]  # unclosed, case-insensitive
    with pytest.raises(re.error):
        parse("/foo(")


def test_implies():
    assert implies(parse("agent x"), parse("agent"))
    assert implies(parse("agents"), parse("agent"))
    assert implies(parse("n:agent"), parse("agent"))
    assert not implies(parse("n:qry"), parse("n"))  # "n" -> "n:qry" is not narrowing
    assert not implies(parse("p:dns"), parse("p"))
    assert not implies(parse("p:dnsx"), parse("p:dns"))
    assert implies(parse("p:dns q"), parse("p:dns"))
    assert not implies(parse("/ab/"), parse("/a/"))
    assert implies(parse("/a/ x"), parse("/a/"))


def test_searcher_cache_matches_full_scan(db):
    s = FieldSearcher(db)
    for text in ["a", "ag", "age", "agent", "agent x", "agen", "n", "n:", "n:q", "p", "p:", "p:d", "p:dns", "p:dns q"]:
        q = parse(text)
        assert s.search(q) == match_fields(db, q), text


def test_protocols(db):
    keys = lambda idx: [db.protocols[i].key for i in idx]  # noqa: E731
    assert keys(match_protocols(db, parse(""))) == ["dns", "http", "empty", "tpkt"]
    assert keys(match_protocols(db, parse("http")))[0] == "http"
    assert keys(match_protocols(db, parse("domain"))) == ["dns"]
    assert keys(match_protocols(db, parse("rdp"))) == ["tpkt"]  # also_registered_as


def test_build_document_from_tshark_output(monkeypatch):
    outputs = {
        ("-v",): "TShark (Wireshark) 9.9.9.\n",
        ("-G", "protocols"): "Domain Name System\tDNS\tdns\nTPKT\tTPKT\ttpkt\nTPKT H\tTPKT H\ttpkt\n",
        ("-G", "fields"): (
            "P\tDomain Name System\tdns\n"
            "F\tName\tdns.qry.name\tFT_STRING\tdns\tBASE_NONE\t0x0\t\n"
            "F\tAddress\tdns.a\tFT_IPv4\tdns\tBASE_NONE\t0x0\tblurb text\n"
            "F\tShort\tfoo.x\tFT_UINT8\tfoo\n"
        ),
    }
    monkeypatch.setattr(tshark_mod, "_run", lambda tshark, *args: outputs[args])
    doc = tshark_mod.build_document("tshark")
    assert doc["tshark_version"] == "TShark (Wireshark) 9.9.9."
    assert doc["field_count"] == 3
    assert [x["name"] for x in doc["protocols"]["dns"]["fields"]] == ["dns.a", "dns.qry.name"]
    assert doc["protocols"]["tpkt"]["also_registered_as"] == [{"name": "TPKT H", "short_name": "TPKT H"}]
    assert doc["protocols"]["foo"]["fields"][0]["blurb"] == ""
    db = Database(doc, source="tshark (live)")
    assert db.field_total == 3 and db.source == "tshark (live)"


def test_name_prefix_ranks_above_dotted_part_prefix():
    # Seen with TShark 4.6.10: "tcp.flags.sy" listed stt.tcp.flags.syn first,
    # because both names were class 1 and stt sorts before tcp.
    doc = {
        "protocols": {
            "stt": {"name": "STT", "short_name": "STT", "fields": [f("stt.tcp.flags.syn")]},
            "tcp": {"name": "TCP", "short_name": "TCP", "fields": [f("tcp.flags.syn"), f("tcp.flags.synack")]},
        }
    }
    db = Database(doc)
    assert names(db, match_fields(db, parse("tcp.flags.sy"))) == ["tcp.flags.syn", "tcp.flags.synack", "stt.tcp.flags.syn"]
    assert names(db, match_fields(db, parse("flags.syn"))) == ["stt.tcp.flags.syn", "tcp.flags.syn", "tcp.flags.synack"]
