"""Bookly support agent package.

Thesis: the model decides, the code enforces. The LLM is used for the two
things only language can do — understanding what the customer wants and
phrasing the reply — and every rule that must hold lives in code it cannot
talk its way around.

Rule (Kramer, *Voice AI & Voice Agents*): a cascaded pipeline — browser
speech-to-text, then this app's LLM turn, then browser text-to-speech. No
speech-to-speech model appears anywhere in this package.
Rule (Horthy, *12-Factor Agents*): every layer here is ours — prompts,
control flow, context window, tools.
"""
