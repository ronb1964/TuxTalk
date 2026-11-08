"""Open apps by voice (TuxTalk Labs): which dictations are an open command."""
from tuxtalk.app_commands import parse, OpenRequest


def test_open_launch_and_start_name_an_app():
    for said in ("Open Firefox.", "launch firefox", "Start Firefox please"):
        request = parse(said)
        assert (request.name, request.new, request.install) == ("firefox", False, "")


def test_new_and_another_start_a_second_one():
    assert parse("open new Firefox") == OpenRequest("firefox", new=True)
    assert parse("open another firefox") == OpenRequest("firefox", new=True)


def test_install_word_picks_that_copy():
    assert parse("open Firefox Flatpak") == OpenRequest("firefox", install="flatpak")
    assert parse("open firefox app image") == OpenRequest("firefox", install="appimage")


def test_split_names_keep_their_words():
    assert parse("open fire fox") == OpenRequest("fire fox")
    assert parse("open the calculator") == OpenRequest("the calculator")


def test_sentences_are_not_commands():
    for said in ("I opened Firefox yesterday", "open the door and come in now please",
                 "open", "please open", "literal open Firefox", "can you open Firefox"):
        assert parse(said) is None


def test_a_bare_install_word_or_new_is_not_an_app_name():
    for said in ("open flatpak", "open new", "open another snap"):
        assert parse(said) is None


def test_spoken_quotation_is_never_a_command():
    """"open quote" is the curly quote's command (spoken_quotes.py)."""
    for said in ("open quote hello close quote", "open quote", "start quote yes end quote",
                 "launch unquote"):
        assert parse(said) is None
