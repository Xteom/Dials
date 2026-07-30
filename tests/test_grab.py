import pytest
from Xlib import X

from dials.grab import Debouncer, GrabManager, tolerated_masks
from tests.conftest import FakeClock

NUM_LOCK_KC = 77
SCROLL_LOCK_KC = 78
CAPS_LOCK_KC = 66


def mapping(mod2=(NUM_LOCK_KC,), mod3=()):
    """8 modifier rows as X reports them: shift, lock, control, mod1..mod5."""
    rows = [(), (CAPS_LOCK_KC,), (), (), tuple(mod2), tuple(mod3), (), ()]
    return [list(r) + [0] * (4 - len(r)) for r in rows]


def keysyms(kc):
    return {NUM_LOCK_KC: "Num_Lock", SCROLL_LOCK_KC: "Scroll_Lock",
            CAPS_LOCK_KC: "Caps_Lock"}.get(kc, "")


def test_capslock_only_yields_two_masks():
    # THE bug probe 07 found: mask 0 alone dies whenever CapsLock is on.
    assert sorted(tolerated_masks(mapping(), keysyms)) == [0, X.LockMask]


def test_numlock_modifier_is_never_tolerated():
    """Including Mod2Mask would destroy the entire premise of the project."""
    for m in tolerated_masks(mapping(), keysyms):
        assert not m & X.Mod2Mask


def test_a_mapped_scrolllock_grows_the_cross_product_to_four():
    masks = tolerated_masks(mapping(mod3=(SCROLL_LOCK_KC,)), keysyms)
    assert sorted(masks) == sorted([
        0, X.LockMask, X.Mod3Mask, X.LockMask | X.Mod3Mask,
    ])
    for m in masks:
        assert not m & X.Mod2Mask


def test_numlock_on_a_different_modifier_is_still_excluded():
    # Do not hardcode Mod2: find whichever modifier actually holds Num_Lock.
    masks = tolerated_masks(mapping(mod2=(), mod3=(NUM_LOCK_KC,)), keysyms)
    for m in masks:
        assert not m & X.Mod3Mask


def test_masks_always_include_zero():
    assert 0 in tolerated_masks(mapping(), keysyms)


def test_debouncer_allows_the_first_press():
    assert Debouncer(clock=FakeClock()).allow(81) is True


def test_debouncer_swallows_autorepeat():
    """Holding a Dial key must not toggle it show/hide continuously."""
    c = FakeClock()
    deb = Debouncer(window_s=0.18, clock=c)
    assert deb.allow(81) is True
    c.advance(0.02)
    assert deb.allow(81) is False
    c.advance(0.02)
    assert deb.allow(81) is False


def test_debouncer_allows_a_deliberate_second_press():
    c = FakeClock()
    deb = Debouncer(window_s=0.18, clock=c)
    deb.allow(81)
    c.advance(0.5)
    assert deb.allow(81) is True


def test_debouncer_is_per_keycode():
    c = FakeClock()
    deb = Debouncer(window_s=0.18, clock=c)
    assert deb.allow(81) is True
    assert deb.allow(85) is True     # a different Dial is not blocked


class FakeRoot:
    def __init__(self, fail=()):
        self.fail = set(fail)
        self.grabs = []
        self.ungrabs = []

    def grab_key(self, kc, mask, owner_events, ptr, kbd, onerror=None):
        if (kc, mask) in self.fail:
            onerror._error = True
        else:
            self.grabs.append((kc, mask))

    def ungrab_key(self, kc, mask):
        self.ungrabs.append((kc, mask))


class FakeCatch:
    def __init__(self, *a, **kw):
        self._error = False

    def get_error(self):
        return self._error


class FakeDisplay:
    def sync(self):
        pass

    def get_modifier_mapping(self):
        return mapping()

    def keycode_to_keysym(self, kc, index):
        return kc

    def flush(self):
        pass


def test_install_grabs_every_keycode_over_every_mask(monkeypatch):
    monkeypatch.setattr("dials.grab.error.CatchError", FakeCatch)
    root = FakeRoot()
    gm = GrabManager(FakeDisplay(), root, masks=[0, X.LockMask])
    failures = gm.install([81, 85])
    assert sorted(root.grabs) == sorted([
        (81, 0), (81, X.LockMask), (85, 0), (85, X.LockMask),
    ])
    assert failures == {}
    assert gm.active is True


def test_install_records_failures_but_keeps_the_other_grabs(monkeypatch):
    monkeypatch.setattr("dials.grab.error.CatchError", FakeCatch)
    root = FakeRoot(fail=[(36, 0)])
    gm = GrabManager(FakeDisplay(), root, masks=[0, X.LockMask])
    failures = gm.install([36, 81])
    assert failures == {36: [0]}
    assert (81, 0) in root.grabs and (36, X.LockMask) in root.grabs


def test_remove_all_releases_everything_and_clears_active(monkeypatch):
    monkeypatch.setattr("dials.grab.error.CatchError", FakeCatch)
    root = FakeRoot()
    gm = GrabManager(FakeDisplay(), root, masks=[0, X.LockMask])
    gm.install([81])
    gm.remove_all()
    assert sorted(root.ungrabs) == sorted([(81, 0), (81, X.LockMask)])
    assert gm.active is False


REAL_KEYSYMS = {77: 0xFF7F, 66: 0xFFE5, 78: 0xFF14, 79: 0xFFE6}


class KeysymDisplay:
    """Returns real X keysym constants, unlike FakeDisplay's raw-keycode stub."""

    def __init__(self, mapping):
        self.mapping = mapping

    def keycode_to_keysym(self, keycode, index):
        return self.mapping.get(keycode, 0)


@pytest.mark.parametrize("keycode,expected", [
    (77, "Num_Lock"),
    (66, "Caps_Lock"),
    (78, "Scroll_Lock"),   # the one keysym_to_string mis-reports as '\x14'
    (79, "Shift_Lock"),
])
def test_keysym_name_resolves_every_lock_key(keycode, expected):
    from dials.grab import _keysym_name
    assert _keysym_name(KeysymDisplay(REAL_KEYSYMS), keycode) == expected


def test_a_real_mapped_scrolllock_is_tolerated_end_to_end():
    """Drives the REAL _keysym_name, not a stub - this is what caught the bug."""
    from dials.grab import _keysym_name
    d = KeysymDisplay(REAL_KEYSYMS)
    masks = tolerated_masks(mapping(mod3=(78,)), lambda kc: _keysym_name(d, kc))
    assert sorted(masks) == sorted([0, X.LockMask, X.Mod3Mask,
                                    X.LockMask | X.Mod3Mask])
    for m in masks:
        assert not m & X.Mod2Mask


def test_keysym_name_degrades_on_an_unknown_keysym():
    from dials.grab import _keysym_name
    assert _keysym_name(KeysymDisplay({}), 999) == ""
