"""Read prompt files from config/prompts/."""
import os

PROMPTS = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                       "config", "prompts")


def read_prompt(name):
    with open(os.path.join(PROMPTS, name), encoding="utf-8") as handle:
        return handle.read()
