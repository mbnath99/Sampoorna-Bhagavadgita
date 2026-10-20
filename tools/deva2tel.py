"""Devanagari -> Telugu script (letter for letter; same Unicode layout, offset 0x300)."""
import re

def deva_to_telugu(s):
    out = []
    for ch in s:
        cp = ord(ch)
        if ch == "ॐ": out.append("ఓం")
        elif 0x0900 <= cp <= 0x0963 and ch not in "।॥": out.append(chr(cp + 0x300))
        elif 0x0966 <= cp <= 0x096F: out.append(str(cp - 0x0966))
        else: out.append(ch)
    return "".join(out)

_VARGA = {"ఙ": "కఖగఘ", "ఞ": "చఛజఝ", "ణ": "టఠడఢ", "న": "తథదధ", "మ": "పఫబభ"}

def to_anusvara(t):
    """Write a class nasal before a consonant of its own class as ం (సఞ్జయ -> సంజయ), as Telugu books do."""
    for nasal, cons in _VARGA.items():
        t = re.sub(nasal + "్(?=[" + cons + "])", "ం", t)
    return t

def split_verse(deva_text):
    """'धृतराष्ट्र उवाच\n\nधर्मक्षेत्रे ...।\n\nमामकाः ...।।1.1।।' -> (speaker, [line1, line2, ...]) in Telugu script."""
    t = deva_to_telugu(deva_text)
    t = re.sub(r"[।॥|]*\s*\d+\.\d+\s*[।॥|]*", " ॥", t)           # ।।1.1।। / ॥18.78॥ -> ॥
    t = t.replace("।।", "॥")
    t = re.sub(r"॥(\s*॥)+", "॥", t)
    t = to_anusvara(t)
    t = re.sub(r"్([ా-ౌ])([క-హ])", r"్\2\1", t)      # source typo: vowel sign before the conjunct's 2nd letter
    t = t.replace("ృ఼", "ౄ").replace("఼", "")
    speaker = None
    m = re.match(r"\s*((?:శ్రీ\s*)?[^\s।॥]+\s*ఉవాచ)\s*", t)
    if m:
        speaker = re.sub(r"\s+", " ", m.group(1)).strip()
        speaker = speaker.replace("శ్రీ భగవానువాచ", "శ్రీభగవానువాచ")
        t = t[m.end():]
    if not speaker:
        m = re.match(r"\s*(శ్రీ\s*భగవానువాచ)\s*", t)
        if m: speaker = "శ్రీభగవానువాచ"; t = t[m.end():]
    # one line per half-verse: break after each danda
    parts = [p.strip() for p in re.split(r"(?<=[।॥])", t) if p.strip()]
    lines = []
    for p in parts:
        if p in ("।", "॥") and lines: lines[-1] += " " + p
        else: lines.append(re.sub(r"\s+", " ", p.replace("।", " ।").replace("॥", " ॥")).replace("  ", " ").strip())
    return speaker, lines
