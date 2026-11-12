"""Read existing notebook evidence; optional practice never introduces its bank."""
import re
import unicodedata
from typing import NamedTuple

from sqlalchemy import exists, select

from app.models.learning import (
    BlockCharacter,
    LearningStage,
    Vocabulary,
    VocabularyCharacter,
    VocabularyExposure,
    WordBlock,
    WordCompletion,
)
from app.services import course_content as content
from app.services import guest_learning, learning


def reading(value):
    return re.sub(r"[\s'’]+", "", unicodedata.normalize("NFC", value).lower())


def matches(requirement, row):
    # A slash means explicitly displayed alternatives, e.g. shéi/shuí. Tone
    # distinctions and lexical source IDs remain significant.
    return (row.text == requirement["text"]
            and reading(requirement["pinyin"]) in {
                reading(part) for part in row.pronunciation.get("value", "").split("/")})


class Availability(NamedTuple):
    eligible: set
    grammar_reviews: dict

    @property
    def reached(self):
        return self.eligible | self.grammar_reviews.keys()


async def assess(db, *, enrollment=None, notebook=None):
    policy = content.catalog()[0].get("learner_policy", {})
    if not policy.get("requires_introduced_vocabulary"):
        return Availability(set(), {})  # Missing proof cannot grant introduction.
    if enrollment is None and notebook is None:
        return Availability(set(), {})
    completed_query = select(WordBlock).join(LearningStage).where(
        LearningStage.track_id == content.TRACK_ID, WordBlock.kind == "word")
    if notebook is not None:
        if notebook.trackId != content.TRACK_ID:
            raise learning.learning_error(422, "learning_invalid_guest_progress")
        guest = await guest_learning.validate_progress(db, notebook)
        completed_query = completed_query.where(WordBlock.id.in_(guest.completed))
    else:
        completed_query = completed_query.join(WordCompletion).where(
            WordCompletion.enrollment_id == enrollment.id)
    blocks = {block.id: block for block in await db.scalars(completed_query)}
    history_blocks = set(blocks)
    if notebook is not None:
        exposures = [(block_id, word_id) for block_id, ids in notebook.exposures.items() for word_id in set(ids)]
        if any(block_id not in blocks for block_id, _ in exposures):
            # The notebook permits previewing its actual next card before the
            # visit is recorded. Retain those displays as pending history, but
            # do not count them as introduced vocabulary for course practice.
            next_word = await learning.next_word_for(db, guest)
            if next_word is not None:
                history_blocks.add(next_word.id)
            # The exact previous-order next card may have had visible pending
            # words before the reorder. It validates retained history only;
            # `blocks` still contains actual completed current cards alone.
            history_blocks.update(guest.historical_pending)
        if any(block_id not in history_blocks for block_id, _ in exposures):
            raise learning.learning_error(422, "learning_invalid_vocabulary")
    else:
        exposures = (await db.execute(select(VocabularyExposure.block_id, VocabularyExposure.vocabulary_id)
            .where(VocabularyExposure.enrollment_id == enrollment.id))).all()
    exposure_ids = {word_id for _, word_id in exposures}
    words = {word.id: word for word in await db.scalars(select(Vocabulary).where(
        Vocabulary.id.in_(exposure_ids), Vocabulary.language_tag == "cmn-Hans"))} if exposure_ids else {}
    if notebook is not None and exposure_ids:
        known = select(BlockCharacter.character).where(BlockCharacter.block_id.in_(history_blocks))
        unknown = exists(select(VocabularyCharacter.vocabulary_id).where(
            VocabularyCharacter.vocabulary_id == Vocabulary.id, VocabularyCharacter.character.not_in(known)))
        invalid = await db.scalar(select(Vocabulary.id).where(Vocabulary.id.in_(exposure_ids), unknown).limit(1))
        if set(words) != exposure_ids or invalid is not None:
            raise learning.learning_error(422, "learning_invalid_vocabulary")
    first_exposure = {}
    for block_id, word_id in exposures:
        block = blocks.get(block_id)
        if block is not None and block.sequence is not None:
            word = words.get(word_id)
            if word and not learning.vocabulary_display_current(word, block.sequence):
                continue
            first_exposure[word_id] = min(first_exposure.get(word_id, block.sequence), block.sequence)
    result = set()
    reviews = {}
    from app.services import notebook_grammar
    grammar = await notebook_grammar.displayed(db, enrollment=enrollment,
        guest=guest if notebook is not None else None)
    course_items = content.catalog()[1]
    grammar_items = {item['id']: item for item in notebook_grammar.catalog()['items']}
    sources = {id: (words[id], sequence) for id, sequence in first_exposure.items() if id in words}
    from app.services import notebook_lexical_progress
    sources = await notebook_lexical_progress.close(db, sources, blocks,
        enrollment=enrollment, guest=guest if notebook is not None else None)
    first_exposure = {id: source[1] for id, source in sources.items()}
    requirement_index = policy.get("lexical_requirements", {})
    for instance_id in requirement_index:
        placement = policy.get("trail_placements", {}).get(instance_id, {})
        preword = course_items[instance_id].get('introductoryAnchor')
        if preword:
            requirements = requirement_index[instance_id]
            visits = guest.pinyin_completed if notebook is not None else enrollment.pinyin_completed
            if (preword == 'INTRO-PINYIN-SOUNDS' and placement.get('afterBlockId') == preword
                    and placement.get('afterSequence') == 0 and not requirements and 'sounds' in visits):
                result.add(instance_id)
            continue
        anchor = blocks.get(placement.get("afterBlockId"))
        if anchor is None or anchor.sequence != placement.get("afterSequence"):
            continue
        requirements = requirement_index[instance_id]
        if (not requirements
                and course_items[instance_id]['definition'].get('setActivity', {}).get('schema') != 'course-practice-v1'):
            continue

        def introduced(requirement):
            # The compiler has reviewed exact primary-card sense equivalence;
            # neither shared Hanzi nor a new sense of the same reading suffices.
            for block_id in requirement.get("primary_block_ids", []):
                block = blocks.get(block_id)
                if (block is not None and block.sequence is not None and block.sequence <= anchor.sequence
                        and matches(requirement, block)):
                    return True
            source_id = requirement.get("source_id")
            word = words.get(source_id)
            return (word is not None and source_id in first_exposure
                    and first_exposure[source_id] <= anchor.sequence and matches(requirement, word))

        grammar_required = notebook_grammar.story_requirements(course_items[instance_id])
        if not all(id in grammar_items and grammar_items[id]['block_sequence'] <= anchor.sequence for id in grammar_required):
            continue
        missing_words = [requirement for requirement in requirements if not introduced(requirement)]
        if missing_words:
            item = course_items[instance_id]
            if not content.is_optional(item):
                # Required reading remains a main-path stop even when the
                # learner skipped a common-word section. Return to that exact
                # earlier introduction; never teach its bank inside the story.
                word = min(missing_words, key=lambda requirement: requirement.get('introduction_block_sequence', float('inf')))
                block_id = word.get('introduction_block_id')
                source = blocks.get(block_id)
                if source and source.sequence == word.get('introduction_block_sequence') and source.sequence <= anchor.sequence:
                    reviews[instance_id] = {'wordId': word['id'], 'vocabularyId': word['source_id'], 'blockId': block_id,
                        'title': {'en': word.get('meaning', word['text'])}, 'text': source.text}
            continue
        if grammar_required <= grammar:
            result.add(instance_id)
            continue
        # Keep the existing story node reachable as a notebook recovery action.
        # The explanation itself remains unassessed until explicitly displayed.
        needed = set(grammar_required)
        for id in reversed(grammar_items):
            if id in needed:
                needed.update(grammar_items[id]['requires_grammar'])
        note = next(item for id, item in grammar_items.items() if id in needed and id not in grammar)
        block_id = note['block_id']
        word_review = {}
        for word in note['lexical_requirements']:
            if not notebook_grammar.lexically_ready({**note, 'lexical_requirements': [word]}, (blocks, sources)):
                block_id = word.get('introduction_block_id', block_id)
                word_review = {'wordId': word['id'], 'vocabularyId': word.get('source_id')}
                break
        if block_id in blocks:
            reviews[instance_id] = {'grammarId': note['id'], 'blockId': block_id,
                'title': note['title'], 'text': blocks[block_id].text, **word_review}
    if reviews:
        stages = {stage.id: stage.level or 'warmup' for stage in await db.scalars(select(LearningStage).where(
            LearningStage.id.in_({blocks[review['blockId']].stage_id for review in reviews.values()})))}
        for review in reviews.values():
            review['levelId'] = stages[blocks[review['blockId']].stage_id]
    return Availability(result, reviews)


async def eligible(db, *, enrollment=None, notebook=None):
    return (await assess(db, enrollment=enrollment, notebook=notebook)).eligible


def require_eligible(instance_id, eligible_ids):
    if instance_id not in eligible_ids:
        raise learning.learning_error(409, "course_vocabulary_not_introduced")
