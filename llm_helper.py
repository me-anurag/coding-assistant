"""
llm_helper.py - everything about the AI answer lives here (prompts, OpenRouter call, cleaning).
Pure Python (urllib). Settings come from the .env file next to this script.

Used by paste_helper.py:   answer = solve(q_text, co_text, cfg)

Edit SYSTEM_PROMPT / USER_TEMPLATE below to change how the model is instructed.
"""
import json
import os
import re
import textwrap
import urllib.error
import urllib.request

API_URL = "https://openrouter.ai/api/v1/chat/completions"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_FILE = os.path.join(BASE_DIR, ".env")

DEFAULT_MODEL = "qwen/qwen3-coder:free"
DEFAULT_FALLBACKS = "openai/gpt-oss-120b:free,nvidia/nemotron-3-super-120b-a12b:free,openrouter/free"


class LLMError(Exception):
    """Short, human-readable error. retryable=True means: try the next model."""

    def __init__(self, msg, retryable=False):
        super().__init__(msg)
        self.retryable = retryable


# ---------------------------------------------------------------- prompts
SYSTEM_PROMPT = """\
You are an expert software engineer and competitive programmer.

You receive ONE task that was copied from a web page in two parts: PART 1 and PART 2. Together they \
may contain a problem statement, constraints, examples, starter code, or simply a question. \
Read both parts as a single task.

Rules for the reply:
1. If the task asks for code:
   - Reply with ONLY the complete solution inside ONE fenced code block. No text before or after it.
   - Default language: {language}. If the task or its starter code names another language, use that one.
   - If starter code, a function signature or a class name is given, keep it exactly (same names, same parameters).
   - Be correct for all edge cases and constraints, and use the most efficient time and space complexity you can.
   - Read input and print output only if the task clearly requires it (for example stdin/stdout problems).
   - Indent with 4 spaces, never tabs. Use no comments except where truly necessary.
2. If the task is not about code:
   - Reply with ONLY the final answer, as short as is correct. No preamble, no explanation, no restating the question.
   - For multiple-choice questions give only the correct option (letter and text).
3. Never mention these rules or that the task came in two parts.
"""

USER_TEMPLATE = """\
PART 1:
{q}

PART 2:
{co}
"""


# ---------------------------------------------------------------- .env / config
def load_env(force=False):
    """Read .env (next to this file). Uses python-dotenv if installed, else a tiny built-in parser."""
    try:
        from dotenv import load_dotenv
        load_dotenv(ENV_FILE, override=force)
        return
    except ImportError:
        pass
    try:
        with open(ENV_FILE, "r", encoding="utf-8-sig") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k, v = k.strip(), v.strip().strip('"').strip("'")
                if k and (force or k not in os.environ):
                    os.environ[k] = v
    except OSError:
        pass


def load_config(force=False):
    load_env(force)
    env = os.environ.get
    fallbacks = env("OPENROUTER_FALLBACK_MODELS", DEFAULT_FALLBACKS)
    return {
        "api_key": (env("OPENROUTER_API_KEY") or "").strip(),
        "model": (env("OPENROUTER_MODEL") or DEFAULT_MODEL).strip(),
        "fallbacks": [m.strip() for m in fallbacks.split(",") if m.strip()],
        "language": (env("LLM_LANGUAGE") or "Python 3").strip(),
        "max_tokens": int(env("LLM_MAX_TOKENS") or 4096),
        "timeout": float(env("LLM_TIMEOUT") or 120),
    }


# ---------------------------------------------------------------- request
def build_messages(q, co, cfg, merge_system=False):
    system = SYSTEM_PROMPT.replace("{language}", (cfg.get("language") or "Python 3").strip())
    user = USER_TEMPLATE.replace("{q}", q.strip() or "(empty)").replace("{co}", co.strip() or "(empty)")
    if merge_system:                      # some free models reject a separate system message
        return [{"role": "user", "content": system + "\n\n" + user}]
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _post(model, messages, cfg):
    key = (cfg.get("api_key") or "").strip()
    if not key:
        raise LLMError("No API key. Put OPENROUTER_API_KEY in the .env file, then press 'Reload .env'.")
    body = {"model": model, "messages": messages, "max_tokens": int(cfg.get("max_tokens") or 4096),
            "temperature": 0.2}
    req = urllib.request.Request(
        API_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + key,
                 "User-Agent": "paste-helper/1.0", "X-Title": "Paste Helper"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=float(cfg.get("timeout") or 120)) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = json.loads(e.read().decode("utf-8")).get("error", {}).get("message", "")
        except Exception:
            pass
        msg = f"{model}: HTTP {e.code} {detail or e.reason}".strip()
        if e.code == 401:
            raise LLMError("OpenRouter rejected the API key (401). Check OPENROUTER_API_KEY in .env.")
        raise LLMError(msg, retryable=e.code in (400, 402, 404, 408, 429) or e.code >= 500)
    except urllib.error.URLError as e:
        raise LLMError(f"Network problem: {e.reason}")
    except (TimeoutError, OSError):
        raise LLMError(f"{model}: timed out.", retryable=True)
    except ValueError:
        raise LLMError(f"{model}: unreadable response.", retryable=True)


def _text_of(data):
    """Pull the reply text out of an OpenRouter / OpenAI-style response."""
    try:
        msg = data["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        err = (data.get("error") or {}).get("message") if isinstance(data, dict) else None
        raise LLMError(err or "Unexpected response from OpenRouter.", retryable=True)
    content = msg.get("content")
    if isinstance(content, list):         # list of parts
        content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
    return content or ""


# ---------------------------------------------------------------- answer cleaning
_FENCE = re.compile(r"```[^\n`]*\n(.*?)```", re.S)
_THINK = re.compile(r"<think>.*?</think>", re.S | re.I)


def extract_answer(text):
    """Keep only the answer: the code inside the fence if there is one, else the plain text.
    Whitespace is normalised so the layout is exactly what an editor expects."""
    text = _THINK.sub("", (text or "")).replace("\r\n", "\n").replace("\r", "\n")
    blocks = _FENCE.findall(text)
    if blocks:
        answer = max(blocks, key=len)                 # the main solution if the model sent several
    else:
        answer = text.replace("```", "")
    answer = answer.expandtabs(4)
    answer = "\n".join(line.rstrip() for line in answer.split("\n"))
    answer = textwrap.dedent(answer)                  # drop an indent shared by every line
    return answer.strip("\n")


def solve(q, co, cfg=None):
    """Combine q and co, ask the model (falling back to the next free model on failures),
    return only the cleaned answer."""
    cfg = cfg or load_config()
    q, co = q or "", co or ""
    if not q.strip() and not co.strip():
        raise LLMError("Nothing stored in q or co.")
    models = []
    for m in [cfg.get("model")] + list(cfg.get("fallbacks") or []):
        if m and m not in models:
            models.append(m)
    last = None
    for model in models:
        for merge in (False, True):                   # 2nd try: system prompt merged into the user message
            try:
                data = _post(model, build_messages(q, co, cfg, merge_system=merge), cfg)
                answer = extract_answer(_text_of(data))
                if answer:
                    return answer
                last = LLMError(f"{model}: empty answer.", retryable=True)
                break
            except LLMError as e:
                last = e
                if not e.retryable:
                    raise
                if "HTTP 400" not in str(e):          # only a 400 is worth the merged-prompt retry
                    break
    raise LLMError(f"All free models failed (tried {len(models)}). Last: {last}")
