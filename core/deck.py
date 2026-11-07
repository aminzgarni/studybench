"""Build a .pptx from a deck (title, subtitle, slides[{title, bullets, notes}])."""
import io

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.util import Emu, Inches, Pt

ACCENT = RGBColor(0x0F, 0x8B, 0x8D)
INK = RGBColor(0x1B, 0x24, 0x30)
MUTE = RGBColor(0x66, 0x73, 0x85)


def _style(tf, size, color, bold=False):
    for p in tf.paragraphs:
        for r in p.runs:
            r.font.size, r.font.bold = Pt(size), bold
            r.font.color.rgb = color


def build_pptx(deck):
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    W = prs.slide_width

    s = prs.slides.add_slide(prs.slide_layouts[0])
    s.shapes.title.text = deck.get("title") or "Presentation"
    for sh, (l, t, w, h) in ((s.shapes.title, (0.9, 2.3, 11.5, 1.6)), (s.placeholders[1], (0.9, 4.1, 11.5, 1.2))):
        sh.left, sh.top, sh.width, sh.height = Inches(l), Inches(t), Inches(w), Inches(h)
    s.placeholders[1].text = deck.get("subtitle") or ""
    for p in s.shapes.title.text_frame.paragraphs:
        p.alignment = 1
    for p in s.placeholders[1].text_frame.paragraphs:
        p.alignment = 1
    _style(s.shapes.title.text_frame, 40, ACCENT, True)
    _style(s.placeholders[1].text_frame, 20, MUTE)
    bar = s.shapes.add_shape(1, Inches(0.9), Inches(4.0), Inches(2.2), Emu(45720))
    bar.fill.solid(), bar.line.fill.background()
    bar.fill.fore_color.rgb = ACCENT

    for sl in deck.get("slides", []):
        s = prs.slides.add_slide(prs.slide_layouts[1])
        s.shapes.title.text = sl.get("title", "")
        t, b = s.shapes.title, s.placeholders[1]
        t.left, t.top, t.width, t.height = Inches(0.6), Inches(0.35), Inches(12.1), Inches(1.1)
        b.left, b.top, b.width, b.height = Inches(0.6), Inches(1.75), Inches(12.1), Inches(5.0)
        for p in t.text_frame.paragraphs:
            p.alignment = 1
        tf = b.text_frame
        tf.word_wrap = True
        bullets = [x for x in sl.get("bullets", []) if str(x).strip()] or [""]
        tf.text = str(bullets[0])
        for x in bullets[1:]:
            tf.add_paragraph().text = str(x)
        _style(t.text_frame, 30, ACCENT, True)
        _style(tf, 22 if len(bullets) <= 5 else 19, INK)
        line = s.shapes.add_shape(1, Inches(0.6), Inches(1.5), Inches(1.4), Emu(38100))
        line.fill.solid(), line.line.fill.background()
        line.fill.fore_color.rgb = ACCENT
        if sl.get("notes"):
            s.notes_slide.notes_text_frame.text = sl["notes"]

    if deck.get("sources"):
        s = prs.slides.add_slide(prs.slide_layouts[1])
        s.shapes.title.text = "Sources"
        t, b = s.shapes.title, s.placeholders[1]
        t.left, t.top, t.width, t.height = Inches(0.6), Inches(0.35), Inches(12.1), Inches(1.1)
        b.left, b.top, b.width, b.height = Inches(0.6), Inches(1.75), Inches(12.1), Inches(5.0)
        b.text_frame.text = deck["sources"][0]
        for x in deck["sources"][1:]:
            b.text_frame.add_paragraph().text = x
        _style(t.text_frame, 30, ACCENT, True)
        _style(b.text_frame, 18, MUTE)
    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()


def to_markdown(deck):
    out = [f"# {deck.get('title', '')}", deck.get("subtitle", ""), ""]
    for i, sl in enumerate(deck.get("slides", []), 1):
        out.append(f"## {i}. {sl.get('title', '')}")
        out += [f"- {b}" for b in sl.get("bullets", [])]
        if sl.get("notes"):
            out.append(f"\n*Speaker notes:* {sl['notes']}")
        out.append("")
    return "\n".join(out)
