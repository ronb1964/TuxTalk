"""Which keyboards an older hotkey helper leaves out, judged from TuxTalk's
side, as the user, who can't open the device nodes.

A helper before 1.12 opened keyboards read-write only, and skipped without
a word one it may only read; 1.12 reads it (listen-only). One before 1.13
skipped a keyboard that's a mouse too, as a Logitech receiver makes its
keyboards and mice. So TuxTalk asks at start to update such a helper on a
computer that has such a keyboard (secure_input.missed_keyboard_reason), and
stays quiet elsewhere.

The helper runs as a throwaway user (systemd's DynamicUser) with no
capabilities, whose one group a device node can name is "input": what it
may do with a node is what the node's permissions give a stranger in the
input group. That is worked out as the kernel does: from the node's POSIX
ACL when it has one (its mode's group bits are then the ACL's mask), else
from the mode bits, the group's when the node belongs to "input" and the
others' when it doesn't. Only keyboards on seat0 count, as the helper reads
no other seat's.

The same sysfs reading tells the service at start whether a keyboard is
plugged in at all (keyboard_plugged_in), so it says it can't read one only
when there is one to read.
"""
import errno
import grp
import os
import struct

from .hotkey_helper import SYS_CHAR_DIR, UDEV_DATA_DIR, made_through_uinput, udev_seat
from .keyboard_devices import has_letter_keys, is_pointer
from .logger import setup_logger

logger = setup_logger(__name__)

READ, WRITE = 4, 2

ACL_XATTR = "system.posix_acl_access"
# Entry tags (linux/posix_acl_xattr.h); the owner and named users never
# match the helper's throwaway user.
ACL_GROUP_OBJ, ACL_GROUP, ACL_MASK, ACL_OTHER = 0x04, 0x08, 0x10, 0x20


def parse_acl(data):
    """[(tag, permissions, id)] from a system.posix_acl_access value: a
    version (2), then an 8-byte entry each, little-endian. ValueError when
    it isn't one."""
    if len(data) < 4 or (len(data) - 4) % 8 or struct.unpack_from("<I", data)[0] != 2:
        raise ValueError("not a POSIX ACL")
    return [struct.unpack_from("<HHI", data, offset) for offset in range(4, len(data), 8)]


def helper_may(mode, file_gid, acl, input_gid, wanted):
    """Whether the helper (in group *input_gid* only) may have the access
    *wanted* (READ, or READ | WRITE for its read-write open) to a node with
    *mode*, group *file_gid* and the ACL entries *acl* (None: no ACL). As
    POSIX has it, a group entry that matches decides, the other entry only
    when none matches, and the mask limits every group entry."""
    if acl is None:
        bits = (mode >> 3) & 7 if file_gid == input_gid else mode & 7
        return bits & wanted == wanted
    mask = next((perms for tag, perms, _ in acl if tag == ACL_MASK), 7)
    groups = [perms & mask for tag, perms, ident in acl
              if (tag == ACL_GROUP_OBJ and file_gid == input_gid)
              or (tag == ACL_GROUP and ident == input_gid)]
    if groups:
        return any(perms & wanted == wanted for perms in groups)
    return any(perms & wanted == wanted for tag, perms, _ in acl if tag == ACL_OTHER)


# The answers that mean a node has no ACL, or its file system keeps none.
_NO_ACL = {errno.ENODATA, errno.ENOTSUP, errno.EOPNOTSUPP}


class UnknownAccess(Exception):
    """A node's ACL couldn't be read: what the helper may do with it isn't known."""


def acl_of(node):
    """The ACL entries of *node*, or None when it has none (or the file
    system keeps none): its mode bits then say it all. Raises
    UnknownAccess when the ACL is there but can't be read: the mode's
    group bits would then be the ACL's mask, not the group's own rights."""
    try:
        return parse_acl(os.getxattr(node, ACL_XATTR))
    except AttributeError:
        return None                     # a Python without getxattr: the mode bits only
    except OSError as e:
        if e.errno in _NO_ACL:
            return None
        why = errno.errorcode.get(e.errno, e.errno)
        raise UnknownAccess(f"{node}'s ACL couldn't be read ({why})") from e
    except ValueError as e:
        raise UnknownAccess(f"{node} has an ACL that couldn't be understood: {e}") from e


def _sysfs_bits(path):
    """The bit numbers set in a sysfs capability file (*path*): hex words,
    each a C long, the highest first. None when it can't be read."""
    try:
        with open(path) as f:
            words = f.read().split()
    except OSError:
        return None
    width = struct.calcsize("l") * 8
    return {i * width + bit for i, word in enumerate(reversed(words))
            for bit in range(width) if int(word, 16) >> bit & 1}


def _is_keyboard(sys_dir, name):
    """Whether event node *name* has letter keys (has_letter_keys, as the
    helper decides), from sysfs."""
    return has_letter_keys(
        _sysfs_bits(os.path.join(sys_dir, name, "device", "capabilities", "key")) or ())


def _is_mouse(sys_dir, name):
    """Whether event node *name* is a mouse too (is_pointer, as the helper
    decides), from sysfs."""
    caps = os.path.join(sys_dir, name, "device", "capabilities")
    return is_pointer(_sysfs_bits(os.path.join(caps, "key")) or (),
                      _sysfs_bits(os.path.join(caps, "rel")) or ())


def _event_nodes(input_dir):
    """(path, stat) of each event node in *input_dir*; none when it can't be listed."""
    try:
        names = sorted(n for n in os.listdir(input_dir) if n.startswith("event"))
    except OSError:
        return []
    found = []
    for name in names:
        node = os.path.join(input_dir, name)
        try:
            found.append((node, os.stat(node)))
        except OSError:
            continue                    # unplugged meanwhile
    return found


def _helper_readable(input_dir, group):
    """(path, stat, writable) of each event node in *input_dir* the helper
    may read, *writable* when it may write it too. A node whose access
    can't be told is left out: a question that may not be needed every
    start is worse than a quiet one, and System check's update stays
    offered."""
    try:
        gid = grp.getgrnam(group).gr_gid
    except KeyError:
        return []
    found = []
    for node, st in _event_nodes(input_dir):
        try:
            acl = acl_of(node)
        except UnknownAccess as e:
            logger.info(f"Keyboard check skipped one node: {e}")
            continue
        if helper_may(st.st_mode, st.st_gid, acl, gid, READ):
            found.append((node, st, helper_may(st.st_mode, st.st_gid, acl, gid, READ | WRITE)))
    return found


def keyboards_also_mice(input_dir="/dev/input", sys_dir="/sys/class/input", group="input",
                        udev_dir=UDEV_DATA_DIR, char_dir=SYS_CHAR_DIR):
    """The event nodes of seat0's keyboards that are mice too, no
    program's (helpers before 1.13 read a remapper's already), that the
    helper may read: updating it can't make one it may not open work.
    Logitech mice count: nothing tells them from a Logitech keyboard. The
    paths can be moved for tests."""
    return [node for node, st, _writable in _helper_readable(input_dir, group)
            if _is_keyboard(sys_dir, os.path.basename(node))
            and _is_mouse(sys_dir, os.path.basename(node))
            and not made_through_uinput(st.st_rdev, char_dir)
            and udev_seat(st.st_rdev, udev_dir) == "seat0"]


def read_only_keyboards(input_dir="/dev/input", sys_dir="/sys/class/input", group="input",
                        udev_dir=UDEV_DATA_DIR):
    """The event nodes of seat0's keyboards that the helper may read but
    not write, mice too aside: a helper before 1.12 leaves those out for
    being mice as well (before 1.13), whatever their node allows, and a
    read-only Logitech mouse is no reason to say the keyboard can only be
    read (keyboards_also_mice speaks for them). The paths can be moved
    for tests."""
    return [node for node, st, writable in _helper_readable(input_dir, group)
            if not writable and _is_keyboard(sys_dir, os.path.basename(node))
            and not _is_mouse(sys_dir, os.path.basename(node))
            and udev_seat(st.st_rdev, udev_dir) == "seat0"]


def keyboard_plugged_in(input_dir="/dev/input", sys_dir="/sys/class/input",
                        char_dir=SYS_CHAR_DIR):
    """Whether a keyboard is plugged in, whoever may open it; one a program
    made through uinput (TuxTalk's own, a remapper's) doesn't count. The
    paths can be moved for tests."""
    return any(_is_keyboard(sys_dir, os.path.basename(node))
               and not made_through_uinput(st.st_rdev, char_dir)
               for node, st in _event_nodes(input_dir))
