"""Which keyboards an older hotkey helper leaves out (helper_keyboard_access.py):
one it may only read (before 1.12) and one that's a mouse too (before 1.13).

Such a helper skips that keyboard without a word, so TuxTalk asks to update
it where one is (secure_input.missed_keyboard_reason), and nowhere else.
Judged as the kernel would for the helper's throwaway user, whose only
group a node can name is "input".
"""
import grp
import os
import struct

import pytest

from tuxtalk import helper_keyboard_access as access
from tuxtalk.helper_keyboard_access import READ, WRITE

INPUT = 997                     # the input group's id, for the permission rules
LETTERS = list(range(16, 26)) + [30, 31, 32]
R, RW = READ, READ | WRITE


def _acl(*entries):
    """A system.posix_acl_access value: (tag, permissions, id) entries."""
    return struct.pack("<I", 2) + b"".join(struct.pack("<HHI", *e) for e in entries)


USER_OBJ, USER = 0x01, 0x02


@pytest.mark.parametrize("mode, gid, acl, may", [
    (0o640, INPUT, None, R),                    # crw-r----- root:input: read only
    (0o660, INPUT, None, RW),                   # the usual crw-rw---- root:input
    (0o600, INPUT, None, 0),
    (0o644, 0, None, R),                        # root:root, read by the others' bits
    (0o660, 0, None, 0),                        # root:root, nothing for the others
    # crw-rw----+ root:input with group::r-- and a user's rw entry: the mode's
    # group bits are the mask, and the input group may only read.
    (0o660, INPUT, [(USER_OBJ, 6, 0), (USER, 6, 1000), (access.ACL_GROUP_OBJ, 4, 0),
                    (access.ACL_MASK, 6, 0), (access.ACL_OTHER, 0, 0)], R),
    # Another group's node with a named entry for input.
    (0o660, 0, [(USER_OBJ, 6, 0), (access.ACL_GROUP_OBJ, 6, 0), (access.ACL_GROUP, 6, INPUT),
                (access.ACL_MASK, 6, 0), (access.ACL_OTHER, 0, 0)], RW),
    # The mask takes the write away from the named entry.
    (0o640, 0, [(USER_OBJ, 6, 0), (access.ACL_GROUP_OBJ, 4, 0), (access.ACL_GROUP, 6, INPUT),
                (access.ACL_MASK, 4, 0), (access.ACL_OTHER, 0, 0)], R),
    # A matching group entry decides: the others' rw doesn't add to it.
    (0o646, INPUT, [(USER_OBJ, 6, 0), (access.ACL_GROUP_OBJ, 4, 0),
                    (access.ACL_MASK, 6, 0), (access.ACL_OTHER, 6, 0)], R),
])
def test_what_the_helper_may_do_with_a_node(mode, gid, acl, may):
    if acl is not None:
        acl = access.parse_acl(_acl(*acl))
    for wanted in (R, RW):
        assert access.helper_may(mode, gid, acl, INPUT, wanted) is (may & wanted == wanted)


def test_something_else_in_the_acl_xattr_is_refused():
    with pytest.raises(ValueError):
        access.parse_acl(b"\x01\x00\x00\x00" + b"\x00" * 8)


def _capabilities(sys_dir, name, keys, rel=()):
    """sysfs's capability files for event node *name*: hex longs, highest first."""
    caps = sys_dir / name / "device" / "capabilities"
    caps.mkdir(parents=True)
    width = struct.calcsize("l") * 8
    for kind, codes in (("key", keys), ("rel", rel)):
        value = sum(1 << code for code in codes)
        words = [(value >> (i * width)) & ((1 << width) - 1)
                 for i in range(max(codes, default=0) // width + 1)]
        (caps / kind).write_text(" ".join(f"{w:x}" for w in reversed(words)) + "\n")


@pytest.fixture
def nodes(tmp_path):
    """Make event nodes (plain files) with their sysfs capabilities; returns
    a scan of them for a group and the folders."""
    dev, sys_dir, udev = tmp_path / "input", tmp_path / "sys", tmp_path / "udev"
    dev.mkdir()
    udev.mkdir()

    def make(name, mode, keys=LETTERS, rel=()):
        (dev / name).write_text("")
        (dev / name).chmod(mode)
        _capabilities(sys_dir, name, keys, rel)
        return str(dev / name)

    def scan(group=None):
        group = group or grp.getgrgid(os.getgid()).gr_name      # the files' own group
        return access.read_only_keyboards(str(dev), str(sys_dir), group, str(udev))
    return make, scan, udev


def test_keyboards_the_helper_may_only_read_are_found(nodes):
    make, scan, _udev = nodes
    read_only = make("event3", 0o640)
    make("event4", 0o660)                                   # writable: 1.11 reads it
    make("event5", 0o640, keys=[116])                       # a power button
    make("event6", 0o640, keys=LETTERS + [0x110], rel=[0])  # a Logitech one: a mouse too
    make("event7", 0o600)                                   # unreadable: 1.12 can't either
    assert scan() == [read_only]
    assert scan("no-such-group") == []


def test_a_node_of_another_group_is_judged_by_the_others_bits(nodes):
    """crw-r--r-- root:root: the helper reads it as one of the others, and
    can't write it."""
    make, scan, _udev = nodes
    others_read = make("event3", 0o644)
    make("event4", 0o640)
    other_group = next(g.gr_name for g in grp.getgrall() if g.gr_gid != os.getgid())
    assert scan(other_group) == [others_read]


def test_another_seats_keyboard_is_left_out(nodes):
    """The helper reads seat0's keyboards only: updating it can't make a
    seat1 keyboard work, so it's no reason to ask."""
    make, scan, udev = nodes
    make("event3", 0o640)
    st = os.stat(udev.parent / "input" / "event3")
    (udev / f"c{os.major(st.st_rdev)}:{os.minor(st.st_rdev)}").write_text(
        "E:ID_INPUT=1\nE:ID_SEAT=seat1\n")
    assert scan() == []


def test_an_acl_that_cant_be_read_isnt_taken_for_no_acl(nodes, monkeypatch):
    """A refused ACL read counted as "no ACL", so the mode's group bits (an
    ACL's mask) said the helper could write a keyboard it could only read.
    What can't be known isn't asked about; a node with no ACL still is."""
    import errno
    make, scan, _udev = nodes
    refused = make("event3", 0o640)
    plain = make("event4", 0o640)

    def getxattr(node, name):
        code = errno.EACCES if node == refused else errno.ENODATA
        raise OSError(code, os.strerror(code))
    monkeypatch.setattr(access.os, "getxattr", getxattr)
    assert scan() == [plain]


def test_a_python_without_getxattr_judges_by_the_mode_bits(nodes, monkeypatch):
    make, scan, _udev = nodes
    read_only = make("event3", 0o640)
    monkeypatch.delattr(access.os, "getxattr")
    assert scan() == [read_only]


def _also_mice(tmp_path, udev):
    group = grp.getgrgid(os.getgid()).gr_name      # the files' own group
    return access.keyboards_also_mice(str(tmp_path / "input"), str(tmp_path / "sys"), group,
                                      str(udev), str(tmp_path / "char"))


def test_keyboards_that_are_mice_too_are_found(nodes, tmp_path):
    """A helper before 1.13 leaves out an MX Keys on a Unifying receiver,
    whose node has a mouse's motion and left button too (issue #26)."""
    make, _scan, udev = nodes
    make("event3", 0o660)                                       # a plain keyboard
    make("event4", 0o660, keys=[0x110, 0x111], rel=[0, 1])      # a plain mouse
    mx_keys = make("event5", 0o660, keys=LETTERS + [0x110], rel=[0, 1, 6, 8])
    make("event6", 0o600, keys=LETTERS + [0x110], rel=[0, 1])     # 1.13 can't open it either
    assert _also_mice(tmp_path, udev) == [mx_keys]


def test_a_programs_keyboard_and_mouse_is_no_reason_to_ask(nodes, tmp_path):
    """Every helper since issue #23 reads a remapper's (kanata's)."""
    make, _scan, udev = nodes
    make("event5", 0o660, keys=LETTERS + [0x110], rel=[0])
    rdev = os.stat(tmp_path / "input" / "event5").st_rdev
    (tmp_path / "char").mkdir()
    (tmp_path / "char" / f"{os.major(rdev)}:{os.minor(rdev)}").symlink_to(
        "/sys/devices/virtual/input/input40/event20")
    assert _also_mice(tmp_path, udev) == []


def test_another_seats_keyboard_and_mouse_is_left_out(nodes, tmp_path):
    make, _scan, udev = nodes
    make("event5", 0o660, keys=LETTERS + [0x110], rel=[0])
    rdev = os.stat(tmp_path / "input" / "event5").st_rdev
    (udev / f"c{os.major(rdev)}:{os.minor(rdev)}").write_text("E:ID_SEAT=seat1\n")
    assert _also_mice(tmp_path, udev) == []


def _plugged_in(tmp_path):
    return access.keyboard_plugged_in(str(tmp_path / "input"), str(tmp_path / "sys"),
                                      str(tmp_path / "char"))


def test_a_keyboard_counts_as_plugged_in_even_when_it_cant_be_opened(nodes, tmp_path):
    """A keyboard TuxTalk has no permission for is still there, and that's
    what the notice at start is about; a power button is no keyboard."""
    make, _scan, _udev = nodes
    make("event3", 0o660, keys=[116])
    assert not _plugged_in(tmp_path)
    make("event4", 0o600)
    assert _plugged_in(tmp_path)


def test_a_keyboard_a_program_made_isnt_one_plugged_in(nodes, tmp_path):
    """TuxTalk's own virtual keyboard, or a remapper's, is there from the
    start: with only that, no keyboard is plugged in yet."""
    make, _scan, _udev = nodes
    make("event5", 0o600)
    rdev = os.stat(tmp_path / "input" / "event5").st_rdev
    (tmp_path / "char").mkdir()
    (tmp_path / "char" / f"{os.major(rdev)}:{os.minor(rdev)}").symlink_to(
        "/sys/devices/virtual/input/input40/event20")
    assert not _plugged_in(tmp_path)


@pytest.mark.parametrize("logi, says", [
    (True, "both at once"),
    (False, "can only be read"),
])
def test_a_read_only_logitech_mouse_isnt_called_a_read_only_keyboard(nodes, tmp_path,
                                                                    monkeypatch, logi, says):
    """Beside helper 1.11, a Logitech mouse the input group may only read
    (it reports a keyboard's keys too) made TuxTalk say "Your keyboard can
    only be read", though the keyboard was fine. Helpers before 1.13 skip
    it for being a mouse too: that's the reason given."""
    from tuxtalk import secure_input
    make, scan, udev = nodes
    make("event3", 0o660)                                       # the keyboard, read-write
    if logi:
        make("event5", 0o640, keys=LETTERS + [0x110], rel=[0, 1])
    else:
        make("event5", 0o640)
    read_only, also_mice = scan(), _also_mice(tmp_path, udev)
    monkeypatch.setattr(access, "read_only_keyboards", lambda: read_only)
    monkeypatch.setattr(access, "keyboards_also_mice", lambda: also_mice)
    assert says in secure_input.missed_keyboard_reason("1.11")
