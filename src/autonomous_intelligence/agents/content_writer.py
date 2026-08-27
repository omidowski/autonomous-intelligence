from ..content_models import (
    ContentBundle,
    PodcastScript,
    ShortScript,
    SocialPost,
    StoryContent,
    TrendItem,
    VideoScript,
)
from ..llm import LLMProvider

DEFAULT_COPY_STYLE = (
    'Copy style for every text field (social posts, captions, scripts): write like Apple '
    "marketing copy - short confident sentences, plain concrete language, benefit-first, "
    "minimal punctuation, no hype words or emoji spam."
)

DEFAULT_SYSTEM_VOICE = (
    "You are a multi-format social/video/podcast content strategist who writes in Apple's "
    "marketing voice and art-directs every image in Apple's clean, minimalist "
    "product-photography style. You write with the precision and restraint of a senior editor - "
    "every line is deliberate, platform-specific, and free of generic AI filler."
)


class ContentWriterAgent:
    def __init__(self, llm: LLMProvider):
        self.llm = llm

    def run(
        self,
        trend: TrendItem,
        *,
        brand_voice: str | None = None,
        content_model: str | None = None,
        language: str | None = None,
        target_duration_seconds: int | None = None,
    ) -> ContentBundle:
        brand_voice = (brand_voice or "").strip()
        language = (language or "").strip()

        if language:
            language_instruction = (
                f"Write every human-readable text field in {language} - social post text, hooks, "
                f"captions, video/podcast scripts, story slide text, titles. Do this even though "
                f"the trend/topic given to you below may be in a different language. Exception: "
                f"each image_prompt stays in English regardless (fed directly to an AI image "
                f"generator, which works most reliably in English) - only the human-facing text "
                f"fields need translating. Hashtags may mix {language} and globally-used English "
                f"hashtags where that's genuinely the norm on that platform (e.g. #fyi, #ai are "
                f"used across languages) - don't force-translate a hashtag that would never "
                f"actually be used in {language}."
            )
        else:
            language_instruction = ""

        if target_duration_seconds:
            target_words = round(target_duration_seconds * 2.5)  # ~150 wpm natural speech pace
            duration_instruction = (
                f"\nTarget narration length: write short_script (hook + beats + cta combined) so "
                f"that speaking it aloud takes approximately {target_duration_seconds} seconds - "
                f"roughly {target_words} words total at a natural speaking pace. Set "
                f"short_script.est_duration_seconds to {target_duration_seconds}. Size video_script's "
                f"shots so their duration_seconds values sum to approximately "
                f"{target_duration_seconds} seconds too (add/remove shots as needed, not just "
                "longer/shorter individual shots).\n"
            )
        else:
            duration_instruction = ""

        if brand_voice:
            copy_style_instruction = (
                "Copy style for every text field (social posts, captions, scripts): write in "
                f'this brand\'s own voice and tone, exactly as described: "{brand_voice}". Stay '
                "factual and platform-appropriate regardless of voice - the brand voice shapes "
                "tone and word choice, not the facts."
            )
            system_voice = (
                "You are a multi-format social/video/podcast content strategist writing on "
                f'behalf of a specific brand. Brand voice: "{brand_voice}". Every sentence you '
                "write must sound like this brand, not a generic AI assistant. You still "
                "art-direct every image in a clean, minimalist product-photography style, and "
                "write with the precision of a senior editor - platform-specific, free of "
                "generic AI filler."
            )
        else:
            copy_style_instruction = DEFAULT_COPY_STYLE
            system_voice = DEFAULT_SYSTEM_VOICE

        language_block = f"\nOutput language: {language} - {language_instruction}\n" if language_instruction else ""

        if language_instruction:
            system_voice = f"{system_voice} {language_instruction}"

        prompt = f"""
News trend: {trend.title}
Summary: {trend.summary}
Category: {trend.category}
{language_block}{duration_instruction}
Generate a complete multi-format content bundle for this trend. Return ONLY JSON with this exact shape:
{{
  "social_posts": [
    {{"platform":"x","variant":"A","text":"...","hashtags":["..."]}},
    {{"platform":"x","variant":"B","text":"...","hashtags":["..."]}},
    {{"platform":"instagram","variant":"A","text":"...","hashtags":["..."]}},
    {{"platform":"instagram","variant":"B","text":"...","hashtags":["..."]}},
    {{"platform":"facebook","variant":"A","text":"...","hashtags":["..."]}},
    {{"platform":"facebook","variant":"B","text":"...","hashtags":["..."]}},
    {{"platform":"linkedin","variant":"A","text":"...","hashtags":["..."]}},
    {{"platform":"linkedin","variant":"B","text":"...","hashtags":["..."]}},
    {{"platform":"tiktok","variant":"A","text":"...","hashtags":["..."]}},
    {{"platform":"tiktok","variant":"B","text":"...","hashtags":["..."]}},
    {{"platform":"threads","variant":"A","text":"...","hashtags":["..."]}},
    {{"platform":"threads","variant":"B","text":"...","hashtags":["..."]}},
    {{"platform":"bluesky","variant":"A","text":"...","hashtags":["..."]}},
    {{"platform":"bluesky","variant":"B","text":"...","hashtags":["..."]}},
    {{"platform":"pinterest","variant":"A","text":"...","hashtags":["..."]}},
    {{"platform":"pinterest","variant":"B","text":"...","hashtags":["..."]}},
    {{"platform":"reddit","variant":"A","text":"...","hashtags":["..."]}},
    {{"platform":"reddit","variant":"B","text":"...","hashtags":["..."]}},
    {{"platform":"youtube","variant":"A","text":"...","hashtags":["..."]}},
    {{"platform":"youtube","variant":"B","text":"...","hashtags":["..."]}}
  ],
  "short_script": {{"hook":"...","beats":["...","..."],"cta":"...","est_duration_seconds":45}},
  "story": {{"slides":[{{"slide_number":1,"text":"...","image_prompt":"..."}}]}},
  "podcast_script": {{"title":"...","intro":"...","segments":["..."],"outro":"...","est_duration_minutes":4}},
  "video_script": {{"title":"...","shots":[{{"shot_number":1,"description":"...","duration_seconds":5,"on_screen_text":"..."}}]}}
}}
Provide 3-5 story slides with punchy overlay text and an image_prompt suitable for an AI image generator.
Provide 4-6 video shots. Keep the tone factual and engaging, not sensationalized.

social_posts must contain exactly 20 entries: one "A" and one "B" variant for each of the 10
platforms listed above. Variant A and B for the same platform must take genuinely different
angles on the story (a different hook/opening line, a different emphasis or framing - e.g. one
leads with the headline fact, the other leads with why it matters) - never a synonym-swapped
rewrite of the same sentence. Both variants must still fit the platform's own voice described
below and stay factually identical (same facts, different framing/wording).

Each platform's two variants must read as genuinely written for that platform, not the same text
reused with a different hashtag block - no two posts (across platforms or variants) may share a
sentence:
- x: terse and direct, one sharp idea.
- instagram / facebook: a bit more narrative context, visual-first framing.
- linkedin: analytical and professional, written for decision-makers.
- tiktok: reads like a hook for spoken narration, built to be said out loud.
- threads: conversational, like the start of a reply-worthy discussion thread.
- bluesky: plain, direct, slightly wry - written for a text-first, algorithm-free timeline.
- pinterest: a short, benefit-led description framed around saving/trying something, not a
  breaking-news tone.
- reddit: written like a genuine, specific submission title/comment for a relevant subreddit
  audience - no marketing voice, no hashtags-as-sentence-filler.
- youtube: a YouTube Community-post style update - sets up curiosity for a video, not a caption.

Video shot descriptions and story image_prompts must be concrete and specific to this trend
(named subject, setting, action) - never generic filler like "a person looking at a screen."

Avoid AI-generic phrasing: no "In today's fast-paced world", "In conclusion", "Let's dive in",
throat-clearing openers, or hollow superlatives. Every sentence should earn its place.

Visual style for every image_prompt (cover and slides): describe an Apple-style visual -
clean minimalist studio composition, soft gradient or seamless backdrop, premium directional
lighting, generous negative space, no clutter or text baked into the image, the restrained
premium look of an Apple keynote slide or apple.com product photo.

{copy_style_instruction}
"""
        raw_response = (
            self.llm.text_via_openrouter(system_voice, prompt, model=content_model)
            if content_model
            else self.llm.text(system_voice, prompt)
        )
        data = self.llm.extract_json(raw_response)
        return ContentBundle(
            trend=trend,
            social_posts=[SocialPost.model_validate(item) for item in data["social_posts"]],
            short_script=ShortScript.model_validate(data["short_script"]),
            story=StoryContent.model_validate(data["story"]),
            podcast_script=PodcastScript.model_validate(data["podcast_script"]),
            video_script=VideoScript.model_validate(data["video_script"]),
            assets=[],
        )
