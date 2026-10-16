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


# Sent as a follow-up turn when the user presses "re" on the answer
RETHINK_PROMPT = """\
Your previous answer may be wrong or not optimal. Rethink the task carefully from scratch: re-read every \
constraint, check the edge cases, and verify against the examples by hand. Then give the corrected final \
answer in exactly the same format as before (only the code in one fenced block, or only the short answer). \
Do not explain.
"""

# ---- "check my solution" (cc + er + >>)
REVIEW_SYSTEM_PROMPT = """\
You are a meticulous code reviewer and debugger.

You receive:
- TASK (optional): the problem the user is solving, copied from a web page in two parts.
- USER SOLUTION: the code (or answer) the user wrote.
- ERROR OUTPUT (optional): what the user got when running it (error message, wrong output, failed test).

Find what is wrong in the USER SOLUTION and show exactly how to fix it.
- If ERROR OUTPUT is given, find its exact cause first and fix it.
- If no ERROR OUTPUT is given, the code ran without errors: check the logic, edge cases, constraints and \
efficiency against the TASK.
- If the solution is already correct and efficient, reply with only the VERDICT line.

Reply in plain text (no markdown headings, no bold) in exactly this layout:
VERDICT: <Correct | Has mistakes | Runs but gives wrong output | Too slow> (one short line)
MISTAKES:
1. <the line or snippet> -> <what is wrong and why>
2. ...
CORRECTED PART:
<only the lines that must change, fixed, inside ONE fenced code block, keeping the user's names and \
4-space indentation; if most of the code changes, give the complete corrected code>

Be brief. No praise, no repeating the task.
"""

REVIEW_TEMPLATE = """\
TASK:
{task}

USER SOLUTION:
{cc}

ERROR OUTPUT:
{er}
"""

REVIEW_RETHINK_PROMPT = """\
Your previous review may have missed something or been wrong. Re-check the user's solution line by line \
(and the error output, if any) and give the corrected review in exactly the same layout as before.
"""

# keep only the most recent attempts when retrying (keeps requests small for free models)
MAX_PREVIOUS = 2


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


def _with_system(system, turns, merge):
    if merge:                                   # some free models reject a separate system message
        first = dict(turns[0])
        first["content"] = system + "\n\n" + first["content"]
        return [first] + [dict(t) for t in turns[1:]]
    return [{"role": "system", "content": system}] + [dict(t) for t in turns]


def clean_review(text):
    """Plain, readable review text: no hidden thinking, no markdown fences or bold markers."""
    text = _THINK.sub("", (text or "")).replace("\r\n", "\n").replace("\r", "\n")
    lines = [l for l in text.split("\n") if not l.strip().startswith("```")]
    out = "\n".join(l.rstrip() for l in lines).replace("**", "").expandtabs(4)
    return out.strip("\n")


def _ask(system, turns, cfg, clean):
    """Send the conversation, falling back to the next free model on retryable failures.
    Returns the cleaned reply."""
    models = []
    for m in [cfg.get("model")] + list(cfg.get("fallbacks") or []):
        if m and m not in models:
            models.append(m)
    last = None
    for model in models:
        for merge in (False, True):                   # 2nd try: system prompt merged into the user message
            try:
                data = _post(model, _with_system(system, turns, merge), cfg)
                answer = clean(_text_of(data))
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


def _task_text(q, co):
    q, co = (q or "").strip(), (co or "").strip()
    if not q and not co:
        return "(not provided)"
    return USER_TEMPLATE.replace("{q}", q or "(empty)").replace("{co}", co or "(empty)").strip()


def solve(q, co, cfg=None, previous=None):
    """Combine q and co and return only the cleaned answer.
    previous = earlier answers to the same task: the model is told to rethink and answer again."""
    cfg = cfg or load_config()
    q, co = q or "", co or ""
    if not q.strip() and not co.strip():
        raise LLMError("Nothing stored in q or co.")
    system = SYSTEM_PROMPT.replace("{language}", (cfg.get("language") or "Python 3").strip())
    first = USER_TEMPLATE.replace("{q}", q.strip() or "(empty)").replace("{co}", co.strip() or "(empty)")
    turns = [{"role": "user", "content": first}]
    for prev in (previous or [])[-MAX_PREVIOUS:]:
        turns += [{"role": "assistant", "content": prev}, {"role": "user", "content": RETHINK_PROMPT}]
    return _ask(system, turns, cfg, extract_answer)


def review(q, co, cc, er, cfg=None, previous=None):
    """Check the user's own solution (cc), using the error output (er) if there is one.
    q / co (the task) are optional context. Returns a plain-text review with the corrected part."""
    cfg = cfg or load_config()
    if not (cc or "").strip():
        raise LLMError("Nothing stored in cc (your solution).")
    er_text = (er or "").strip() or "(none - no error was reported; the code ran without errors)"
    first = (REVIEW_TEMPLATE.replace("{task}", _task_text(q, co))
             .replace("{cc}", cc.strip("\n")).replace("{er}", er_text))
    turns = [{"role": "user", "content": first}]
    for prev in (previous or [])[-MAX_PREVIOUS:]:
        turns += [{"role": "assistant", "content": prev}, {"role": "user", "content": REVIEW_RETHINK_PROMPT}]
    return _ask(REVIEW_SYSTEM_PROMPT, turns, cfg, clean_review)