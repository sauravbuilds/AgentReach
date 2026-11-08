"""Optional 1-2 line summaries with Claude. Falls back to the post excerpt when no key is set."""
import json
import os
import re


def summarize(results: list[dict]) -> bool:
    """Replace each result's summary in place. Returns True if Claude was used."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key or not results:
        return False
    try:
        import anthropic

        client = anthropic.Anthropic(api_key=key)
        posts = [{"id": i, "title": r["title"], "text": r["_full"]} for i, r in enumerate(results)]
        msg = client.messages.create(
            model=os.environ.get("SUMMARY_MODEL", "claude-haiku-5-5"),
            max_tokens=2000,
            messages=[
                {
                    "role": "user",
                    "content": (
                        "For each Reddit post below, write a one or two sentence plain summary of what the "
                        "discussion is about. Use only the given text. Reply with a JSON array of "
                        '{"id": number, "summary": string} and nothing else.\n\n' + json.dumps(posts)
                    ),
                }
            ],
        )
        raw = msg.content[0].text
        data = json.loads(re.search(r"\[.*\]", raw, re.DOTALL).group(0))
        for row in data:
            results[int(row["id"])]["summary"] = str(row["summary"]).strip()
        return True
    except Exception:
        return False
