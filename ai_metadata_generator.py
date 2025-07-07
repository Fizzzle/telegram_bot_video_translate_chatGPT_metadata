# ai_metadata_generator.py
import requests
import re

def extract_first_lines_from_srt(sub_path, max_lines=20):
    lines = []
    with open(sub_path, 'r', encoding='utf-8') as f:
        content = f.read()
    text_blocks = re.findall(r"\d+\n\d{2}:\d{2}:\d{2},\d{3} --> .*?\n(.*?)\n", content, re.DOTALL)
    for block in text_blocks:
        for line in block.strip().splitlines():
            if line.strip():
                lines.append(line.strip())
            if len(lines) >= max_lines:
                return lines
    return lines

def generate_title_description_tags(text_lines):
    prompt = (
            "Below is a fragment of subtitles from a video:\n\n"
            + "\n".join(text_lines)
            + "\n\n"
              "Your task:\n"
              "- Detect the language of the subtitles.\n"
              "- Based on the content, generate:\n"
              "  1. A YouTube video title +  3 #hashtags.\n"
              "  2. A video description. + secret Keywords and hashtags. (not short) \n "
              "  3. 10–15 keywords.(Keywords without #)\n"
              "- Return the result in the **same language** as the subtitles.\n"
              "- Format your response strictly as JSON with the fields: `title`, `description`, and `tags` (list of strings).\n"
              "Example output:\n"
              "{\n"
              '  "title": "Sample Title +  3 #hashtags",\n'
              '\n'
              '  "description": "This is a sample description. +  3 #hashtags",\n \n'
              '\n'
              '  "Keywords": [Keywords1, Keywords2, Keywords3, ...keywords15](Keywords without # only ,)\n \n'
              "}"
    )

    response = requests.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={"Authorization": f"Bearer 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"},
        json={
            "model": "openai/gpt-4o-mini",  # или gpt-3.5-turbo
            "messages": [{"role": "user", "content": prompt}],
        }
    )
    return response.json()["choices"][0]["message"]["content"]
