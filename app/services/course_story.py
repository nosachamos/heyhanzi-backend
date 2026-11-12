"""Bounded story responses. Completion records attempted understanding, not mastery."""
from app.services import course_story_media
from app.services.learning import learning_error


def is_story(item):
    return item["definition"]["id"] in {"ACT-STORY", "ACT-STORY-COMP"}


def checks(item):
    return [step for step in item["definition"]["steps"] if step["kind"] == "check"]


def validate_definition(item):
    definition = item["definition"]
    steps = definition["steps"]
    activity = definition.get("storyActivity", {})
    paragraphs = activity.get("sample", {}).get("paragraphs", [])
    expected_mode = "listen" if definition["id"] == "ACT-STORY" else "read"
    if (activity.get("mode") != expected_mode or not 1 <= len(paragraphs) <= 8
            or len({step["id"] for step in steps}) != len(steps)
            or len(checks(item)) != sum(bool(paragraph.get("question")) for paragraph in paragraphs)
            or not checks(item)):
        raise ValueError("Invalid story definition")
    expected = []
    for index, paragraph in enumerate(paragraphs):
        if expected_mode == "listen" or index == 0:
            expected.append("reading")
        if paragraph.get("question"):
            expected.append("check")
    if expected_mode == "read":
        expected = ["reading"] + ["check"] * len(checks(item))
    if [step["kind"] for step in steps] != expected:
        raise ValueError("Invalid story step sequence")
    question_ids = []
    for step, paragraph in zip(checks(item), [paragraph for paragraph in paragraphs if paragraph.get("question")], strict=True):
        if len(step["questions"]) != 1:
            raise ValueError("Invalid story question count")
        question = step["questions"][0]
        choices = question.get("choices", [])
        ids = [choice["id"] for choice in choices]
        if (question.get("kind") != "choice" or not 2 <= len(choices) <= 6
                or len(ids) != len(set(ids)) or sum(choice.get("correct") is True for choice in choices) != 1
                or question["id"] != paragraph["question"]["id"]
                or [(choice["id"], choice["correct"]) for choice in choices]
                   != [(choice["id"], choice["correct"]) for choice in paragraph["question"]["choices"]]):
            raise ValueError("Invalid story choices")
        question_ids.append(question["id"])
    if len(question_ids) != len(set(question_ids)):
        raise ValueError("Duplicate story question")
    course_story_media.validate(activity)


def question_attempts(run, question_id):
    return [attempt for attempt in run["attempts"] if attempt["answer"][0] == question_id]


def settled(run, question_id):
    attempts = question_attempts(run, question_id)
    return any(attempt["correct"] is True for attempt in attempts) or len(attempts) == 2


def status(item, run):
    if all(settled(run, step["questions"][0]["id"]) for step in checks(item)):
        return "completed"
    current = next(step for step in checks(item) if not settled(run, step["questions"][0]["id"]))
    return "retry" if question_attempts(run, current["questions"][0]["id"]) else "draft"


def validate_draft(item, draft, run):
    if not draft:
        return
    steps = item["definition"]["steps"]
    index = next((index for index, step in enumerate(steps) if step["id"] == draft[0]), None)
    if index is None or len(draft) > 2:
        raise learning_error(422, "course_invalid_story_cursor")
    step = steps[index]
    if len(draft) == 2 and (step["kind"] != "check" or draft[1] not in {
            choice["id"] for choice in step["questions"][0]["choices"]}):
        raise learning_error(422, "course_invalid_story_choice")
    if any(previous["kind"] == "check" and not settled(run, previous["questions"][0]["id"])
           for previous in steps[:index]):
        raise learning_error(409, "course_story_question_required")


def grade(item, answer, run):
    pending = next((step for step in checks(item) if not settled(run, step["questions"][0]["id"])), None)
    if pending is None:
        raise learning_error(409, "course_run_finished")
    question = pending["questions"][0]
    if len(answer) != 2 or answer[0] != question["id"]:
        raise learning_error(422, "course_invalid_story_question")
    choice = next((choice for choice in question["choices"] if choice["id"] == answer[1]), None)
    if choice is None:
        raise learning_error(422, "course_invalid_story_choice")
    return choice["correct"], choice["feedback"], [pending["id"], choice["id"]]
