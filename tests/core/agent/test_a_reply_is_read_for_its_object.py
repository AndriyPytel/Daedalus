"""
The turns of `logs/sessions/123a8f44549a46b5a2ff1f78ab661dba.jsonl` that the
agent could not read: 5 of that run's 27 turns, every one of them in Execute,
every one of them carrying the object the model meant.
"""
import os

import pytest

from core.agent import agent
from core.loop import prompt


REPLIES = os.path.join(os.path.dirname(__file__), "replies")


def reply(name):
    with open(os.path.join(REPLIES, name + ".txt")) as handle:
        return handle.read()


@pytest.mark.parametrize("name", [
    "prose_then_object",
    "object_then_debris_then_object",
    "object_then_debris_then_object_2",
    "object_then_debris_then_object_3",
    "truncated_then_debris_then_object",
])
def test_the_object_is_read_out_of_what_surrounds_it(name):
    assert "code" in agent._parsed(reply(name))


@pytest.mark.parametrize("name", [
    "prose_then_object",
    "object_then_debris_then_object",
    "truncated_then_debris_then_object",
])
def test_both_paths_read_a_reply_the_same_way(name):
    assert agent._parsed(reply(name)) == prompt._object_of(reply(name))


def test_a_reply_with_no_object_is_still_malformed():
    assert agent._parsed("I had a look and there is nothing to do here.") == {
        "_malformed": "I had a look and there is nothing to do here."}


def test_a_reply_of_nothing_is_still_empty():
    assert agent._parsed("   ") == {"_empty": True}


def test_a_reply_that_is_json_but_not_an_object_says_so():
    assert agent._parsed("[1, 2]") == {"_not_object": [1, 2]}
