"""All prompts in one place. The [[task:...]] markers are used by the offline demo provider."""

BASE = ("You are a rigorous study coach for a busy learner (student or professional) who is revising course "
        "material. Work ONLY from the SOURCE excerpts you are given. Never invent facts, doses, diagnostic criteria, "
        "legal rules, numbers or citations. If the sources do not cover something, say so instead of guessing. "
        "Be precise, practical and concise. Any person, client or case material you write must be fictional.")


def out_lang(lang):
    return {"de": "Write the output in German (correct terminology of the field, as used in German-speaking practice).",
            "en": "Write the output in English.",
            "fr": "Write the output in French (correct terminology of the field, as used in French-speaking practice)."}.get(lang, "Write the output in the language of the source material.")


def goal_line(goal):
    return f"The learner's goal for this topic: {goal}\n" if goal else ""


def _m(system, user):
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


# ------------------------------------------------------------------ inventory
def p_tags(filename, excerpt, known):
    sys = (BASE + " [[task:tags]]\nClassify a study document. Reply with ONE JSON object: "
           '{"category": short label (reuse an existing one when it fits), "tags": up to 5 short lowercase tags, '
           '"priority": integer 1-5 (1 = most relevant for exams and daily practice, 5 = background), '
           '"one_line": one sentence on what the document is}.')
    return _m(sys, f"Existing categories: {', '.join(known) or '-'}\nFile name: {filename}\n\nExcerpt:\n{excerpt}")


# ------------------------------------------------------------------ summaries
HEADINGS = ["Essence (2-3 sentences)", "Key points", "Practical takeaways (what to do differently in daily practice, "
            "with patients, in the team)", "To memorise (definitions, criteria, numbers, lists, steps)",
            "Pitfalls and common mistakes", "Questions to think about"]


def p_notes(part, language, words=250):
    sys = (BASE + " [[task:notes]]\nCondense the excerpt into study notes. FIRST LINE: a short descriptive title of this "
           f"section (plain text, no markdown). Then Markdown bullets, about {words} words in total. Keep EVERY concrete fact: "
           "numbers, criteria, names, steps, rules, definitions, examples. Drop filler and repetition. Keep source locators in "
           "brackets like (Page 12) or [00:12:30] where they matter. " + out_lang(language))
    return _m(sys, part)


def p_summary(material, language, title, goal, multi=False, focus="", digest=False):
    how = ("Merge and de-duplicate the material from several documents, organise by theme and mention the document "
           "name where it matters." if multi else
           "The material is a digest of the section notes of one LONG document. Write the summary of the WHOLE document: its "
           "storyline, the most important points across all sections, and what matters in practice." if digest else
           "Summarise the material.")
    sys = (BASE + " [[task:summary]]\n" + how + " Use exactly these Markdown sections (translate headings to the output "
           "language):\n" + "\n".join(f"## {h}" for h in HEADINGS) +
           "\nPrefer short bullets. Put source locators in brackets where useful, e.g. (Slide 4). "
           "Emphasise what is practical and testable. " + out_lang(language))
    if focus:
        sys += f"\nExtra focus requested by the learner: {focus}"
    return _m(sys, f"{goal_line(goal)}Title: {title}\n\nMATERIAL:\n{material}")


# ------------------------------------------------------------------ exercises
SCHEMAS = {
    "mcq": ('single-best-answer multiple-choice questions. Exactly 4 options, one clearly correct, plausible distractors '
            'built from typical misconceptions, similar option length, no "all/none of the above". '
            '{"question","options":[4 strings],"answer_index":0-3,"explanation":"why right, why the others are wrong","source"}'),
    "case": ('short fictional scenarios (practical, organisational or regulatory, whichever fits the sources): a realistic '
             'vignette of 4-8 sentences without real personal data, followed by 2-4 concrete tasks (assessment, next steps, '
             'what to document, what to say). '
             '{"vignette","tasks":[strings],"model_answer","key_points":[strings],"source"}'),
    "open": ('short-answer questions that test recall and understanding. '
             '{"question","key_points":[what a complete answer contains],"model_answer","source"}'),
    "audit": ('audit-style questions, the way an external auditor or inspection body would ask them: ask for the process, '
              'responsibilities, evidence and documentation, and test whether the learner could prove compliance every day. '
              '{"question","expected_evidence":[documents/records/procedures to show],"key_points":[strings],'
              '"model_answer","pitfalls":[common findings],"source"}'),
    "jury": ('oral-exam questions as an examination committee would ask them (open question, then escalating follow-ups). '
             '{"question","follow_ups":[2-3 escalating follow-up questions],"key_points":[strings],"model_answer","source"}'),
    "elab": ('elaboration questions that force deeper processing: "why", "how" and "what if" questions, plus links to prior '
             'knowledge. {"kind":"why|how|what-if|connect","question","key_points":[strings],"model_answer","source"}'),
}


def p_items(typ, k, ctx, difficulty, language, goal, existing):
    sys = (BASE + f" [[task:items:{typ}]] [[n:{k}]]\nWrite exactly {k} {SCHEMAS[typ]}\n"
           f"Difficulty: {difficulty}. 'source' = file name and locator taken from the SOURCE header the item is based on. "
           'Reply with ONE JSON object: {"items":[...]} and nothing else. ' + out_lang(language))
    avoid = ("\nDo not repeat these existing questions:\n- " + "\n- ".join(existing)) if existing else ""
    return _m(sys, f"{goal_line(goal)}{avoid}\n\nSOURCES:\n{ctx}")


def p_eval(item, answer, language):
    d = item["data"]
    sys = (BASE + " [[task:evaluate]]\nCompare the learner's answer with the reference. Be fair and specific. Reply with ONE "
           'JSON object: {"score":0-100,"grade":0-3 (0 again, 1 hard, 2 good, 3 easy),"covered":[key points the learner got],'
           '"missing":[key points missing],"wrong":[statements that are incorrect],"feedback":"2-4 sentences, how to improve"}. '
           + out_lang(language))
    ref = (f"QUESTION:\n{item['question']}\n"
           + (f"TASKS: {d.get('tasks')}\n" if d.get("tasks") else "")
           + f"KEY POINTS: {d.get('key_points')}\nMODEL ANSWER: {d.get('model_answer')}\n")
    return _m(sys, f"{ref}\nLEARNER'S ANSWER:\n{answer}")


# ------------------------------------------------------------------ chat
def p_chat(sources, history, question, allow_general, language_hint=""):
    ctx = "\n\n".join(f"[{i}] {s['filename']} · {s['locator'] or '-'}\n{s['text']}" for i, s in enumerate(sources, 1))
    rule = ("If the sources do not contain the answer, say 'Not in your sources.' Then you may add a clearly separated "
            "section 'General knowledge (not from your files):' and keep it short and cautious."
            if allow_general else
            "If the sources do not contain the answer, say 'Not in your sources.' and suggest where to look or what to upload. "
            "Do not answer from general knowledge.")
    sys = (BASE + " [[task:chat]]\nAnswer the learner's question using ONLY the numbered sources below and cite them like [1] "
           "or [2][3]. " + rule + " Answer in the language of the question. Be concise; use bullets for lists.\n\nSOURCES:\n" + ctx)
    msgs = [{"role": "system", "content": sys}]
    msgs += [{"role": m["role"], "content": m["content"]} for m in history[-6:] if m["role"] in ("user", "assistant")]
    msgs.append({"role": "user", "content": question})
    return msgs


# ------------------------------------------------------------------ recall / teach / jury / mnemonics
def p_points(ctx, language, goal):
    sys = (BASE + " [[task:points]]\nList the 8-12 most important things a learner should be able to recall about this "
           'material, as short statements. Reply with ONE JSON object {"key_points":[strings]}. ' + out_lang(language))
    return _m(sys, f"{goal_line(goal)}SOURCES:\n{ctx}")


def p_recall_compare(points, text, language):
    sys = (BASE + " [[task:recall_compare]]\nThe learner wrote down from memory what they remember. Compare with the key "
           'points. Reply with ONE JSON object: {"recalled":[points present],"missing":[points absent],"incorrect":'
           '[statements that are wrong, with the correction],"coverage_pct":0-100,"next_steps":"1-3 sentences"}. ' + out_lang(language))
    return _m(sys, "KEY POINTS:\n- " + "\n- ".join(points) + f"\n\nLEARNER'S TEXT:\n{text}")


def p_teach(ctx, audience, text, language):
    sys = (BASE + f" [[task:teach]]\nThe learner is practising teaching this material to: {audience}. Judge the explanation "
           "against the sources for accuracy, completeness and clarity for that audience. Reply with ONE JSON object: "
           '{"score":0-100,"strengths":[...],"errors":[factual mistakes with correction],"missing":[important omissions],'
           '"clarity_tips":[...],"followup_questions":[2-3 questions this audience would really ask],"improved_outline":[5-7 steps]}. '
           + out_lang(language))
    return _m(sys, f"SOURCES:\n{ctx}\n\nLEARNER'S EXPLANATION (may be a speech transcript):\n{text}")


def p_jury_turn(item, transcript, answer, follow_used, max_follow, language):
    d = item["data"]
    sys = (BASE + " [[task:jury_turn]]\nYou are a strict but fair examiner in an oral exam. Decide: ask ONE short follow-up "
           f"(you have used {follow_used} of {max_follow}) if the answer is incomplete, vague or wrong, probing the weakest "
           "spot; otherwise move on. Reply with ONE JSON object: "
           '{"action":"follow_up" or "next","say":"what you say to the candidate, max 2 sentences, no model answer","note":'
           '"private one-line assessment"}. ' + out_lang(language))
    ref = (f"QUESTION: {item['question']}\nEXPECTED KEY POINTS: {d.get('key_points')}\n"
           f"SUGGESTED FOLLOW-UPS: {d.get('follow_ups')}\nMODEL ANSWER: {d.get('model_answer')}\n")
    return _m(sys, ref + "\nCONVERSATION SO FAR:\n" + transcript + f"\n\nCANDIDATE'S LATEST ANSWER:\n{answer}")


def p_jury_final(transcript, language):
    sys = (BASE + " [[task:jury_final]]\nGive the candidate feedback on the whole oral exam. Reply with ONE JSON object: "
           '{"score":0-100,"strengths":[...],"weaknesses":[...],"advice":"concrete next steps"}. ' + out_lang(language))
    return _m(sys, transcript)


def p_mnemonic(items, language, style):
    sys = (BASE + " [[task:mnemonic]]\nCreate memory aids for the ordered list below. Build an acronym from the first letters "
           "(keep the order; you may suggest a pronounceable word) and 2-3 memorable phrases or sentences whose words start "
           f"with those letters, in the style: {style}. Reply with ONE JSON object: "
           '{"acronym":"...","phrases":[...],"tip":"one sentence on how to rehearse it"}. ' + out_lang(language))
    return _m(sys, "LIST:\n" + "\n".join(f"{i}. {x}" for i, x in enumerate(items, 1)))


# ------------------------------------------------------------------ recommend / present
def p_lit_queries(context, langs, goal, keywords=""):
    names = {"de": "German", "en": "English", "fr": "French"}
    want = ", ".join(f'"{l}" ({names[l]})' for l in langs)
    anchor = (f"MANDATORY ANCHOR: every query must contain the core concept of these keywords (translated into the query's "
              f"language): {keywords}. " if keywords else "")
    sys = (BASE + " [[task:lit_queries]]\nWrite literature-search queries for a database search (PubMed / OpenAlex / library "
           "catalogues) that find papers, reviews and books on the SPECIFIC subject matter below. " + anchor +
           "Rules: (1) SPECIFIC, never broad: combine the core condition or method with a concrete aspect found in the material, "
           "e.g. 'spaced repetition adult learners randomized trial', 'Lernen Erwachsene Wiederholung Studie'. Never "
           "use only the topic name or generic words like 'treatment' or 'management' alone. (2) 4-6 queries per language, each "
           "covering a DIFFERENT aspect of the material, 2-6 words each, key terms only, no sentences. (3) Each query is written in "
           f"its own language with the vocabulary experts use there. Languages: {want}. "
           'Reply with ONE JSON object like {"en":["..."],"de":["..."]} and nothing else.')
    return _m(sys, f"{goal_line(goal)}SUBJECT MATTER:\n{context}")


def p_deck(material, audience, n_slides, minutes, language, goal, extra):
    sys = (BASE + " [[task:deck]]\nCreate a presentation from the material. Audience: " + audience +
           f". Length: about {minutes} minutes = {n_slides} content slides. Structure: learning objectives, a logical body, at "
           "least one practical example or fictional case, key take-aways, and 2-3 questions for the audience. Slides must be "
           "short: max 6 bullets of max 14 words; put the explanation into speaker notes (2-5 sentences). Adapt the vocabulary to "
           "the audience. Reply with ONE JSON object: "
           '{"title":"","subtitle":"","slides":[{"title":"","bullets":["..."],"notes":"..."}]}. ' + out_lang(language))
    if extra:
        sys += f"\nExtra instructions from the learner: {extra}"
    return _m(sys, f"{goal_line(goal)}MATERIAL:\n{material}")


def p_outline(material, audience, n_slides, minutes, language, goal, extra):
    sys = (BASE + " [[task:outline]]\nPlan a presentation from the material. Audience: " + audience +
           f". Length: about {minutes} minutes = exactly {n_slides} content slides. Cover the material's most important and most "
           "practical content, in a teachable order: learning objectives first, then the body, at least one practical example "
           "or fictional case, take-aways, and questions for the audience at the end. For each slide give a title, a one-sentence "
           "focus (what the audience should understand) and 3-6 search words (key terms from the material) that find the "
           'passages needed. Reply with ONE JSON object: {"title":"","subtitle":"","slides":[{"title":"","focus":"","query":""}]}. '
           + out_lang(language))
    if extra:
        sys += f"\nExtra instructions from the learner: {extra}"
    return _m(sys, f"{goal_line(goal)}MATERIAL (overview):\n{material}")


def p_slide(title, focus, passages, audience, language):
    sys = (BASE + " [[task:slide]]\nWrite ONE slide from the passages. Audience: " + audience + ". 'bullets': 3-6 bullets, max 14 words "
           "each, concrete (numbers, criteria, steps, names from the passages), no filler. 'notes': speaker notes of 120-200 words "
           "that explain the slide in full sentences with the details from the passages and name the source locator in "
           'brackets, e.g. (Page 45). Reply with ONE JSON object: {"bullets":["..."],"notes":"..."}. ' + out_lang(language))
    return _m(sys, f"SLIDE TITLE: {title}\nSLIDE FOCUS: {focus}\n\nPASSAGES:\n{passages}")
