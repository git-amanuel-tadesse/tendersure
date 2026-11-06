"""Spec 21: the category list shaped by the team's comments - the list, the checks, the outputs.
The model is a fake that answers from a script; the cases are the ones the Amref prototype run met."""

import openpyxl
import pytest

from tendersure import catlist as cl
from tendersure.refine import Draft

DRAFTS = [Draft("1-1", "Supply of general stationeries and tonners", "GENERAL"),
          Draft("1-2", "Supply of household cleaning materials and sundries", "GENERAL"),
          Draft("1-3", "Provision of event management services", "GENERAL"),
          Draft("2-1", "Project/Programme research evaluations", "Consultancy", ["Market Survey"]),
          Draft("2-2", "Research design", "Consultancy"),
          Draft("2-3", "Rapporteur and Facilitation", "Consultancy"),
          Draft("2-4", "Provision of air ticketing services (IATA Registered Only)", "Consultancy")]
PSI = cl.Reference("PSI", [("GENERAL", "Supply of Office Stationery", []),
                           ("GENERAL", "Supply of Toners and Cartridges", []),
                           ("GENERAL", "Supply of Cleaning Materials (powder soap, liquid soap)", []),
                           ("COMMUNICATION", "Provision of Event Management Services (Conferences, Seminars)", []),
                           ("CONSULTANCY & PROFESSIONAL SERVICES", "Provision of Project Based Consultancy Services", [])])


class FakeLLM:
    def __init__(self, *answers):
        self.answers, self.prompts = list(answers), []

    def complete_json(self, system, prompt, schema, name, client=None, max_tokens=0, **_):
        self.prompts.append(prompt)
        return self.answers.pop(0)


def rows():
    return cl.from_drafts(DRAFTS)


def test_the_list_starts_as_the_draft_was_written():
    r = rows()
    assert [(x.key, x.description, x.from_draft) for x in r][:2] == [
        ("1-1", "Supply of general stationeries and tonners", ["1-1"]),
        ("1-2", "Supply of household cleaning materials and sundries", ["1-2"])]
    assert r[3].subs == ["Market Survey"]


def test_every_action_applies():
    C = cl.Change
    out = cl.apply(rows(), [
        C("split", key="1-1", texts=["Supply of Office Stationery", "Supply of Toners and Cartridges"]),
        C("merge", keys=["2-1", "2-2"], text="Provision of Project Based Consultancy Services",
          group="CONSULTANCY & PROFESSIONAL SERVICES"),
        C("fold", key="2-3", target="1-3"),
        C("reword", key="1-2", text="Supply of household cleaning materials"),
        C("add", text="Provision of Secretarial Services", group="GENERAL"),
        C("remove", key="2-4"),
        C("group", key="1-3", group="COMMUNICATION"),
        C("subs", key="1-2", subs=["Soaps", "Mops"]),
        C("order", groups=["CONSULTANCY & PROFESSIONAL SERVICES", "GENERAL"]),
        C("reword", key="gone", text="skipped")])
    by = {r.key: r for r in out}
    assert [r.description for r in out if r.group == "GENERAL"] == [
        "Supply of Office Stationery", "Supply of Toners and Cartridges", "Supply of household cleaning materials",
        "Provision of Secretarial Services"]
    assert out[0].key == "2-1" and by["2-1"].from_draft == ["2-1", "2-2"] and by["2-1"].subs == ["Market Survey"]
    assert by["1-3"].subs == ["Rapporteur and Facilitation"] and by["1-3"].from_draft == ["1-3", "2-3"]
    assert by["1-1.2"].from_draft == ["1-1"] and by["N1"].from_draft == []
    assert by["1-2"].subs == ["Soaps", "Mops"] and "2-4" not in by and out[-1].group == "COMMUNICATION"


def test_changes_naming_rows_the_list_does_not_have_are_reported_not_shown():
    ok, problems = cl.check({"changes": [
        {"kind": "reword", "instruction": 0, "key": "9-9", "text": "x"},
        {"kind": "fold", "instruction": 0, "key": "2-3", "target": "2-3"},
        {"kind": "merge", "instruction": 0, "keys": ["2-1"], "text": "x"},
        {"kind": "merge", "instruction": 0, "keys": ["2-1", "2-2"], "text": ""},
        {"kind": "split", "instruction": 0, "key": "1-1", "texts": ["one", " "]}]}, rows(), DRAFTS, [PSI])
    assert [c.kind for c in ok] == ["merge"] and ok[0].text == "Project/Programme research evaluations"
    assert "keeps the first row's" in ok[0].why and not ok[0].risky
    assert len(problems) == 4


@pytest.mark.parametrize("change,flag", [
    # a swap to a category that only sounds alike (Amref run 2: cleaning, water treatment, events...)
    (dict(kind="reword", key="1-2", text="Supply of Cleaning Materials (powder soap, liquid soap)", source="PSI"),
     "changes what the category covers"),
    # wording found in no source
    (dict(kind="reword", key="1-3", text="Provision of Event Management and Decor Services", source="PSI"),
     "not in the draft or any approved list"),
    # a merged line reworded to one of its own draft rows
    (dict(kind="reword", key="M", text="Research design", source="the client's draft"), "undoes a merge"),
    # the same category in PSI's words: no flag
    (dict(kind="reword", key="1-3", text="Provision of Event Management Services (Conferences, Seminars)",
          source="PSI"), ""),
    # a reword that drops part of the category (toners) is flagged, even with PSI's wording
    (dict(kind="reword", key="1-1", text="Supply of Office Stationery", source="PSI"), "changes what the category"),
    # back to the client's own wording, whatever the source is called: no flag
    (dict(kind="reword", key="1-2", text="Supply of household cleaning materials and sundries",
          source="the client's draft"), ""),
    # a removal asked for: no flag
    (dict(kind="remove", key="2-4"), ""),
])
def test_flags_fire_on_real_risks_only(change, flag):
    r = rows() + [cl.Row("M", "Consultancy", "Research and Evaluation", [], ["2-1", "2-2"])]
    ok, _ = cl.check({"changes": [dict(change, instruction=0)]}, r, DRAFTS, [PSI], "Use PSI's wording")
    assert (flag in ok[0].risky) if flag else ok[0].risky == ""


def test_wording_the_comment_names_is_not_flagged():
    comment = 'Rename it to "Provision of Project Based Consultancy Services"'
    ok, _ = cl.check({"changes": [{"kind": "reword", "instruction": 0, "key": "2-1", "source": "PSI",
                                   "text": "Provision of Project Based Consultancy Services (Staff Training)"}]},
                     rows(), DRAFTS, [PSI], comment)
    assert ok[0].risky == ""


def answer(instructions, changes, note=""):
    return {"instructions": instructions, "changes": changes, "note": note}


def test_done_without_a_change_is_asked_again_then_shown_not_understood():
    llm = FakeLLM(answer([{"text": "Put rapporteur under events", "status": "done", "targets": ["2-3"]}], []),
                  answer([{"text": "Put rapporteur under events", "status": "done"}], []))
    got = cl.read("Put rapporteur under events", rows(), DRAFTS, [PSI], "AAA", llm=llm)
    assert len(llm.prompts) == 2 and "Rows still to change: 2-3" in llm.prompts[1]
    assert got.instructions[0].status == "unclear" and "no change came back" in got.instructions[0].note
    assert set(got.raw) == {"first", "again"}


def test_an_answer_asked_again_is_kept():
    first = answer([{"text": "Split stationery", "status": "done", "targets": ["1-1"]},
                    {"text": "Put rapporteur under events", "status": "done", "targets": ["2-3"]}],
                   [{"kind": "split", "instruction": 0, "key": "1-1", "texts": ["A", "B"]}])
    again = answer([{"text": "Put rapporteur under events", "status": "done"}],
                   [{"kind": "fold", "instruction": 0, "key": "2-3", "target": "1-3"}])
    got = cl.read("...", rows(), DRAFTS, [PSI], "AAA", llm=FakeLLM(first, again))
    assert [(c.kind, c.instruction, c.id) for c in got.changes] == [("split", 0, "c1"), ("fold", 1, "c2")]
    assert [i.status for i in got.instructions] == ["done", "done"]


def test_done_for_some_of_its_targets_is_partly_done():
    six = answer([{"text": "Use PSI's wording for stationery, cleaning and events", "status": "done",
                   "targets": ["1-1", "1-2", "1-3"]}],
                 [{"kind": "reword", "instruction": 0, "key": "1-1", "text": "Supply of Office Stationery"}])
    got = cl.read("...", rows(), DRAFTS, [PSI], "AAA", llm=FakeLLM(six, answer([], [])))
    ins = got.instructions[0]
    assert ins.status == "partly_done" and "household cleaning" in ins.note and "event management" in ins.note


def test_nothing_to_change_and_unclear_are_not_asked_again():
    llm = FakeLLM(answer([{"text": "a", "status": "nothing_to_change"}, {"text": "b", "status": "unclear",
                                                                          "note": "which ones?"}], []))
    got = cl.read("a. b.", rows(), DRAFTS, [PSI], "AAA", brief="Ethiopian suppliers only", llm=llm)
    assert len(llm.prompts) == 1 and [i.status for i in got.instructions] == ["nothing_to_change", "unclear"]
    assert "Ethiopian suppliers only" in llm.prompts[0] and "APPROVED LIST FOR PSI" in llm.prompts[0]


def test_closeness_to_a_team_list():
    r = cl.apply(rows(), [cl.Change("split", key="1-1", texts=["Supply of Office Stationery",
                                                               "Supply of Toners and Cartridges"])])
    got = cl.compare(r, [("GENERAL", "Supply of Office Stationery", []), ("GENERAL", "Supply of Toners", []),
                         ("TRAVEL", "Provision of air ticketing services (IATA Registered Only)", []),
                         ("GENERAL", "Supply of Fuel", [])])
    assert (got["same_wording"], got["close_wording"], got["same_group"]) == (2, 1, 2)
    assert got["missing"] == ["Supply of Fuel"] and len(got["extra"]) == 5


def test_outputs_the_client_layout_and_the_categories_table(tmp_path):
    r = cl.apply(rows(), [cl.Change("fold", key="2-3", target="1-3")])
    path = tmp_path / "list.xlsx"
    cl.write_client_workbook(str(path), r)
    ws = openpyxl.load_workbook(path).active
    assert [c.value for c in ws[1]] == ["#", "Category Description", "Sub-Category"]
    assert ws["B2"].value == "GENERAL" and ws["B3"].value == "Supply of general stationeries and tonners"
    cats = {c.description: c for c in cl.to_categories(r, DRAFTS, "AAA", "v1")}
    assert cats["Provision of air ticketing services (IATA Registered Only)"].eligibility == [
        "IATA Registration Certificate"]
    assert cats["Provision of event management services"].sub_categories == ["Rapporteur and Facilitation"]
    assert [c.number for c in cats.values()] == list(range(1, len(r) + 1))


def test_instructions_left_out_of_a_long_answer_are_asked_one_at_a_time():
    """Amref: 13 instructions read with their rows, one change returned; then the rest one by one."""
    first = answer([{"text": "Merge 2-1 and 2-2", "status": "done", "targets": ["2-1", "2-2"]},
                    {"text": "Put 2-3 under 1-3", "status": "done", "targets": ["2-3", "1-3"]},
                    {"text": "Remove 2-4", "status": "done", "targets": ["2-4"]}],
                   [{"kind": "merge", "instruction": 0, "keys": ["2-1", "2-2"], "text": "Research"}])
    llm = FakeLLM(first, answer([{"text": "x", "status": "done"}],
                                [{"kind": "fold", "instruction": 0, "key": "2-3", "target": "1-3"}]),
                  answer([{"text": "x", "status": "done"}], [{"kind": "remove", "instruction": 0, "key": "2-4"}]))
    got = cl.read("...", rows(), DRAFTS, [PSI], "AAA", llm=llm)
    assert len(llm.prompts) == 3                                       # the first, then one per missing instruction
    assert all(p.count("COMMENT:") == 1 for p in llm.prompts[1:])
    assert {p.split("COMMENT: ")[1].split("\n")[0][:9] for p in llm.prompts[1:]} == {"Put 2-3 u", "Remove 2-"}
    assert sorted((c.kind, c.instruction) for c in got.changes) == [("fold", 1), ("merge", 0), ("remove", 2)]
    assert [i.status for i in got.instructions] == ["done", "done", "done"]


def test_a_wordless_merge_takes_the_name_its_instruction_quotes():
    llm = FakeLLM(answer([{"text": 'Merge 2-1 and 2-2 into "Provision of Project Based Consultancy Services"',
                           "status": "done", "targets": ["2-1", "2-2"]}],
                         [{"kind": "merge", "instruction": 0, "keys": ["2-1", "2-2"]}]))
    c = cl.read("...", rows(), DRAFTS, [PSI], "AAA", llm=llm).changes[0]
    assert c.text == "Provision of Project Based Consultancy Services" and "wording from the comment" in c.why


def test_the_list_drops_a_list_number_left_in_a_numbered_row():
    r = cl.from_drafts([Draft("1-37", "3 Provision of Strategic Communications", "G"),
                        Draft("r9", "3 Phase generators", "G")])
    assert [x.description for x in r] == ["Provision of Strategic Communications", "3 Phase generators"]


@pytest.mark.parametrize("instruction,name", [
    # Amref v7: the name after "into ... one category:" is the result, not the first quote
    ('Merge "Animations and Illustrations" and "Sound animators and Graphic Animators" into one category: '
     '"Provision of Animation Illustration, Sound Animation and Graphic Animation Services".',
     "Provision of Animation Illustration, Sound Animation and Graphic Animation Services"),
    # the quoted line is merged in; the result is unnamed: keep the first row's wording
    ('Merge "Provision of IT Services, Software Developers" into software solutions', ""),
    # a short quote that starts exactly one source line: that whole line
    ("Merge cameras with audio-visual equipment, using PSI's \"Supply of Office Stationery\" wording",
     "Supply of Office Stationery"),
    ("Merge them, using PSI's \"Supply of Cleaning Materials\" wording",
     "Supply of Cleaning Materials (powder soap, liquid soap)"),
])
def test_a_merge_takes_the_name_the_instruction_gives_the_result(instruction, name):
    sources = [d for _, d, _ in PSI.rows]
    assert cl._quoted_name(instruction, sources) == name


def test_two_changes_to_one_line_in_a_comment_flag_the_later():
    """Amref v4: "use PSI's wording" then "brackets to round" both reworded Relief NFI; the later undid it."""
    llm = FakeLLM(answer([{"text": "Use PSI's wording", "status": "done", "targets": ["1-2"]},
                          {"text": "Brackets to round", "status": "done", "targets": ["1-2"]}],
                         [{"kind": "reword", "instruction": 0, "key": "1-2", "source": "PSI",
                           "text": "Supply of Cleaning Materials (powder soap, liquid soap)"},
                          {"kind": "reword", "instruction": 1, "key": "1-2",
                           "text": "Supply of household cleaning materials and sundries"}]))
    first, later = cl.read("...", rows(), DRAFTS, [PSI], "AAA", llm=llm).changes
    assert "another change in this comment also rewords this line" in later.risky
    assert "another change" not in first.risky
