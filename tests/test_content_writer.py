import json

from autonomous_intelligence.agents.content_writer import ContentWriterAgent
from autonomous_intelligence.content_models import TrendItem


def _demo_json() -> str:
    return json.dumps(
        {
            "social_posts": [{"platform": "x", "text": "hello", "hashtags": []}],
            "short_script": {"hook": "h", "beats": ["b"], "cta": "c", "est_duration_seconds": 30},
            "story": {"slides": []},
            "podcast_script": {
                "title": "t",
                "intro": "i",
                "segments": [],
                "outro": "o",
                "est_duration_minutes": 1,
            },
            "video_script": {"title": "t", "shots": []},
        }
    )


class RecordingLLM:
    def __init__(self):
        self.calls: list[tuple[str, str]] = []
        self.openrouter_calls: list[tuple[str, str, str]] = []

    def text(self, instructions: str, prompt: str) -> str:
        self.calls.append((instructions, prompt))
        return _demo_json()

    def text_via_openrouter(self, instructions: str, prompt: str, *, model: str) -> str:
        self.openrouter_calls.append((instructions, prompt, model))
        return _demo_json()

    @staticmethod
    def extract_json(text: str):
        return json.loads(text)


def _trend() -> TrendItem:
    return TrendItem(
        rank=1, title="Test trend", summary="Summary", category="tech", search_query="test"
    )


def test_default_voice_used_when_no_brand_voice_given():
    llm = RecordingLLM()
    ContentWriterAgent(llm).run(_trend())

    instructions, prompt = llm.calls[0]
    assert "Apple's marketing voice" in instructions
    assert "Apple marketing copy" in prompt


def test_brand_voice_overrides_default_apple_voice():
    llm = RecordingLLM()
    ContentWriterAgent(llm).run(_trend(), brand_voice="Warm, plain-spoken, like a helpful friend.")

    instructions, prompt = llm.calls[0]
    assert "Warm, plain-spoken, like a helpful friend." in instructions
    assert "Warm, plain-spoken, like a helpful friend." in prompt
    assert "Apple's marketing voice" not in instructions


def test_blank_brand_voice_falls_back_to_default():
    llm = RecordingLLM()
    ContentWriterAgent(llm).run(_trend(), brand_voice="   ")

    instructions, _ = llm.calls[0]
    assert "Apple's marketing voice" in instructions


def test_content_model_routes_through_openrouter():
    llm = RecordingLLM()
    ContentWriterAgent(llm).run(_trend(), content_model="anthropic/claude-3.5-sonnet")

    assert llm.calls == []
    assert len(llm.openrouter_calls) == 1
    _, _, model = llm.openrouter_calls[0]
    assert model == "anthropic/claude-3.5-sonnet"


def test_no_content_model_uses_default_text_call():
    llm = RecordingLLM()
    ContentWriterAgent(llm).run(_trend())

    assert llm.openrouter_calls == []
    assert len(llm.calls) == 1


def test_language_instructs_output_language():
    llm = RecordingLLM()
    ContentWriterAgent(llm).run(_trend(), language="German")

    instructions, prompt = llm.calls[0]
    assert "German" in instructions
    assert "German" in prompt
    assert "image_prompt stays in English" in prompt


def test_no_language_omits_language_instruction():
    llm = RecordingLLM()
    ContentWriterAgent(llm).run(_trend())

    instructions, prompt = llm.calls[0]
    assert "Output language" not in prompt
    assert "image_prompt stays in English" not in instructions


def test_target_duration_instructs_script_length():
    llm = RecordingLLM()
    ContentWriterAgent(llm).run(_trend(), target_duration_seconds=30)

    _, prompt = llm.calls[0]
    assert "approximately 30 seconds" in prompt
    assert "75 words" in prompt  # 30s * 2.5 words/sec


def test_no_target_duration_omits_duration_instruction():
    llm = RecordingLLM()
    ContentWriterAgent(llm).run(_trend())

    _, prompt = llm.calls[0]
    assert "Target narration length" not in prompt


def test_prompt_requests_ab_variants_for_every_platform():
    llm = RecordingLLM()
    ContentWriterAgent(llm).run(_trend())

    _, prompt = llm.calls[0]
    assert "exactly 20 entries" in prompt
    assert '"variant":"A"' in prompt
    assert '"variant":"B"' in prompt


def test_social_post_variant_is_none_when_response_omits_it():
    llm = RecordingLLM()
    bundle = ContentWriterAgent(llm).run(_trend())

    assert bundle.social_posts[0].variant is None
