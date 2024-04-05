import concurrent.futures
from typing import Optional

from podgenai.config import MAX_CONCURRENT_WORKERS, PROMPTS
from podgenai.util.openai import get_cached_content
from podgenai.work import get_topic_work_path
from podgenai.util.sys import print_error


def is_subtopics_list_valid(subtopics: list[str]) -> bool:
    """Return true if the subtopics are structurally valid, otherwise false.

    A validation error is printed if a subtopic is invalid.
    """
    seen = set()
    for num, subtopic in enumerate(subtopics, start=1):
        expected_num_prefix = f"{num}. "
        if subtopic != subtopic.strip():
            print_error(f"Subtopic {num} is invalid because it has leading or trailing whitespace: {subtopic!r}")
            return False
        if not subtopic.startswith(expected_num_prefix):
            print_error(f"Subtopic {num} is invalid because it is not numbered correctly: {subtopic}")
            return False
        subtopic_name = subtopic.removeprefix(expected_num_prefix).strip()
        if not subtopic_name:
            print_error(f"Subtopic {num} is invalid because it has no value: {subtopic}")
            return False
        if subtopic_name in seen:
            print_error(f"Subtopic {num} is invalid because it is a duplicate: {subtopic}")
            return False
    return True


def list_subtopics(topic: str) -> Optional[list[str]]:
    """Get the list of subtopics for the given topic."""
    prompt_name = "list_subtopics"
    prompt = PROMPTS[prompt_name].format(topic=topic)
    subtopics = get_cached_content(prompt, cache_key_prefix=f"0. {prompt_name}", cache_path=get_topic_work_path(topic))
    assert subtopics, subtopics
    none_subtopics = ("none", "none.")
    if subtopics.lower() in ("none", "none."):
        print_error(f"No subtopics exist for topic: {topic}")
        return
    invalid_subtopics = ("", *none_subtopics)
    subtopics = [s.strip() for s in subtopics.splitlines() if s.strip().lower() not in invalid_subtopics]  # Note: A terminal "None" line has been observed with valid subtopics before it.
    if not is_subtopics_list_valid(subtopics):
        print_error(f"Invalid subtopic exists for topic: {topic}")
        return
    return subtopics


def get_subtopic(*, topic: str, subtopics: list[str], subtopic: str, strategy: str = "oneshot") -> str:
    """Get the full text for a given subtopic within the context of the given topic and list of subtopics."""
    assert subtopic[0].isdigit()  # Is numbered.
    common_kwargs = {"strategy": strategy, "cache_key_prefix": subtopic, "cache_path": get_topic_work_path(topic)}
    match strategy:
        case "oneshot":
            prompt = PROMPTS["generate_subtopic"].format(optional_continuation="", topic=topic, subtopics="\n".join(subtopics), numbered_subtopic=subtopic)
            subtopic = get_cached_content(prompt, **common_kwargs)
        case "multishot":  # Observed to never really benefit or produce longer content relative to oneshot.
            prompt = PROMPTS["generate_subtopic"].format(optional_continuation="\n\n" + PROMPTS["continuation_first"], topic=topic, subtopics="\n".join(subtopics), numbered_subtopic=subtopic)
            subtopic = get_cached_content(prompt, **common_kwargs, max_completions=5, update_prompt=False)
        case _:
            assert False, strategy
    return subtopic.rstrip()


def get_subtopics_texts(*, topic: str, subtopics: Optional[list[str]] = None) -> Optional[dict[str, str]]:
    """Return the ordered full text for all subtopics within the context of the given topic and optional ordered list of subtopics.

    If the list of subtopics is not provided, it is read.
    """
    if not subtopics:
        subtopics = list_subtopics(topic)
    if not subtopics:
        print_error(f"No subtopics exist for topic: {topic}")
        return

    if MAX_CONCURRENT_WORKERS == 1:
        subtopics_texts = {s: get_subtopic(topic=topic, subtopics=subtopics, subtopic=s) for s in subtopics}
    else:
        assert MAX_CONCURRENT_WORKERS > 1
        with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_CONCURRENT_WORKERS) as executor:
            fn_get_subtopic = lambda subtopic: get_subtopic(topic=topic, subtopics=subtopics, subtopic=subtopic)
            subtopics_texts = {s: text for s, text in zip(subtopics, executor.map(fn_get_subtopic, subtopics))}
    return subtopics_texts


def get_subtopics_speech_texts(*, topic: str, subtopics: Optional[list[str]] = None) -> Optional[dict[str, str]]:
    """Return the ordered speech text for all subtopics within the context of the given topic and optional ordered list of subtopics.

    If the list of subtopics is not provided, it is read.
    """
    if not subtopics:
        subtopics = list_subtopics(topic)
    if not subtopics:
        print_error(f"No subtopics exist for topic: {topic}")
        return

    subtopics_texts = get_subtopics_texts(topic=topic, subtopics=subtopics)
    subtopics_speech_texts = {subtopic_name: f'Section {subtopic_name.replace('.', ':', 1)}:\n\n{subtopic_text} {{pause}}' for subtopic_name, subtopic_text in subtopics_texts.items()}
    # Note: A pause at the beginning is skipped by the TTS generator, but it is not skipped if at the end, and so it is kept at the end.
    subtopic1 = subtopics[0]
    subtopics_speech_texts[subtopic1] = f'"{topic}"\n\n{{pause}}\n{PROMPTS['tts_disclaimer']} {{pause}}\n\n{subtopics_speech_texts[subtopic1]}'
    # Note: TTS disclaimer about AI generated audio is required by OpenAI as per https://platform.openai.com/docs/guides/text-to-speech/do-i-own-the-outputted-audio-files
    # Note: It has proven more reliable for the pause to be structured in this way for section 1, rather than be in the leading topic line.
    return subtopics_speech_texts
