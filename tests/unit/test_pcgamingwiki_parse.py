"""Offline tests for the PCGamingWiki wikitext save-location parser.

The parser is the highest-risk piece: the ``{{Game data/saves}}`` path field
contains nested ``{{p|token}}`` templates whose pipes must NOT split the outer
template. These tests pin the brace-matcher + top-level-pipe splitter + token
translation without any network.
"""
from unifideck.metadata import pcgamingwiki as pcgw


def test_translate_basic_tokens():
    assert pcgw._translate_path("{{p|appdata}}\\Foo\\Saves") == "<winAppData>/Foo/Saves"
    assert pcgw._translate_path("{{p|localappdata}}\\Bar") == "<winLocalAppData>/Bar"
    assert pcgw._translate_path("{{p|userprofile}}\\Saved Games\\X") == "<home>/Saved Games/X"


def test_translate_compound_token():
    # {{p|userprofile\Documents}} → <home>/Documents
    assert pcgw._translate_path(
        "{{p|userprofile\\Documents}}\\My Games\\Terraria",
    ) == "<home>/Documents/My Games/Terraria"


def test_translate_uid_and_game():
    assert pcgw._translate_path("{{p|game}}\\save") == "<base>/save"
    assert pcgw._translate_path(
        "{{p|appdata}}\\VS_EGS\\{{p|uid}}",
    ) == "<winAppData>/VS_EGS/<storeUserId>"


def test_translate_unknown_token_returns_none():
    assert pcgw._translate_path("{{p|osxhome}}/Library/foo") is None


def test_parse_saves_per_store_rows_and_nested_pipes():
    wikitext = (
        "intro\n"
        "{{Game data/saves|Windows|{{p|appdata}}\\MyGame\\saves}}\n"
        "{{Game data/saves|Steam|{{p|steam}}\\userdata\\{{p|uid}}\\123\\remote}}\n"
        "{{Game data/saves|Epic Games Launcher|{{p|appdata}}\\MyGame_EGS\\{{p|uid}}}}\n"
        "{{Game data/saves|OS X|{{p|osxhome}}/Library/MyGame}}\n"
        "outro\n"
    )
    rows = pcgw._parse_saves(wikitext)
    by_store = {tuple(r["stores"]): r["path"] for r in rows}
    # Windows = generic (no store scope)
    assert by_store[()] == "<winAppData>/MyGame/saves"
    # Steam row tagged for steam (resolver will skip it for gog/epic)
    assert by_store[("steam",)] == "<root>/userdata/<storeUserId>/123/remote"
    # Epic row tagged for epic
    assert by_store[("epic",)] == "<winAppData>/MyGame_EGS/<storeUserId>"
    # OS X row dropped entirely
    assert not any(r["path"].startswith("<osx") for r in rows)
    # every row tagged as a save
    assert all("save" in r["tags"] for r in rows)


def test_parse_saves_multiple_alternatives_in_one_field():
    # A single field can list several {{p|..}} paths separated by top-level |.
    wikitext = (
        "{{Game data/saves|Windows|"
        "{{p|game}}\\save\\a.dat|{{p|game}}\\save\\b.json}}\n"
    )
    rows = pcgw._parse_saves(wikitext)
    paths = sorted(r["path"] for r in rows)
    assert paths == ["<base>/save/a.dat", "<base>/save/b.json"]
