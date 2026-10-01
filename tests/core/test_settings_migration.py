"""
The first launch after the rename finds the old group and copies it over.

Only an empty new group is filled, so a user who already set Daedalus up is
never overwritten, and the old group is left in place.
"""
from core.settings import migrate_legacy_preferences


class Group:
    def __init__(self):
        self.entries = {}
        self.groups = {}

    def GetContents(self):
        return [(kind, name, value) for name, (kind, value) in self.entries.items()]

    def GetGroups(self):
        return list(self.groups)

    def GetGroup(self, name):
        return self.groups.setdefault(name, Group())

    def SetString(self, name, value):
        self.entries[name] = ("String", value)

    def SetBool(self, name, value):
        self.entries[name] = ("Boolean", value)

    def SetInt(self, name, value):
        self.entries[name] = ("Integer", value)

    def SetUnsigned(self, name, value):
        self.entries[name] = ("Unsigned Long", value)

    def SetFloat(self, name, value):
        self.entries[name] = ("Float", value)


def old_group():
    old = Group()
    old.SetString("ApiKeys", "[1]")
    old.SetBool("AutoApprovePlan", True)
    old.SetInt("N", 3)
    old.SetUnsigned("U", 4)
    old.SetFloat("F", 0.5)
    old.GetGroup("Nested").SetString("k", "v")
    return old


def test_an_empty_new_group_receives_every_entry_of_the_old_one():
    old, new = old_group(), Group()

    migrate_legacy_preferences(old, new)

    assert new.entries == old.entries
    assert new.groups["Nested"].entries == {"k": ("String", "v")}


def test_a_new_group_with_content_is_left_alone():
    old, new = old_group(), Group()
    new.SetString("ApiKeys", "[2]")

    migrate_legacy_preferences(old, new)

    assert new.entries == {"ApiKeys": ("String", "[2]")}


def test_the_old_group_is_not_emptied():
    old = old_group()

    migrate_legacy_preferences(old, Group())

    assert len(old.entries) == 5
