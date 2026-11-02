"""Locate quotations without treating semantic similarity as source verification."""
import re
import unicodedata


def normalize(text):
    text=unicodedata.normalize('NFKC',text).casefold().replace('\u00ad','')
    text=text.translate(str.maketrans({'“':'"','”':'"','«':'"','»':'"','‘':"'",'’':"'",'‐':'-','‑':'-','–':'-','—':'-'}))
    return re.sub(r'\s+',' ',text).strip()


def locate_quote(quote,source):
    """Return a verified quote and method; expanded omissions retain intervening words."""
    q,src=normalize(quote),normalize(source)
    if not q: return quote,'not_found'
    if q in src: return quote,'normalized'
    # Expand explicitly marked omissions, never accept paraphrases or reordered text.
    fragments=[normalize(p) for p in re.split(r'\[\s*(?:\.\.\.|…)\s*\]|\.\.\.|…',quote) if normalize(p)]
    if len(fragments)<2 or any(len(p)<12 for p in fragments) or sum(map(len,fragments))<40:
        return quote,'not_found'
    cursor=0;start=None
    for fragment in fragments:
        pos=src.find(fragment,cursor)
        if pos<0:return quote,'not_found'
        if start is None:start=pos
        cursor=pos+len(fragment)
    if cursor-start>4000:return quote,'not_found'
    # Include omitted clauses (including negations), rather than silently dropping them.
    return src[start:cursor],'expanded_omission'
