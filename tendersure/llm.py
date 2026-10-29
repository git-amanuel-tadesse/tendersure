"""Provider-agnostic model access, anonymised and checked.

Every model step in this package asks one question: "given this text, return
JSON matching this schema". Two backends answer it:

  openrouter  any model OpenRouter serves (one key, many providers); routed
              only to providers that neither store nor train on the data
  gemini      Google's Gemini API directly, with your own key - paid tier
              only (on the unpaid tier Google may use prompts to improve its
              products and human reviewers may read them)

Whatever the backend, every request goes through the same steps:

  1. anonymise: client names, sheet codes and contact details are replaced
     by placeholders (privacy.py); raw PDFs are never sent
  2. guard: if anything identifying survives, the request is refused
  3. log: the exact text sent is written to .cache/llm_outbox/ for audit
  4. validate: the reply must match the schema; if a provider rejects or
     bends the schema request, the question is asked once more with the
     schema written into the prompt
  5. restore: placeholders in the reply are mapped back locally

Settings come from the environment, or from a `.env` file in the project
folder (git-ignored; copy `.env.example`):
  TENDERSURE_PROVIDER    openrouter (default) | gemini
  TENDERSURE_MODEL       model id for that provider
  OPENROUTER_API_KEY     for openrouter
  GEMINI_API_KEY         for gemini
  GEMINI_PAID_TIER       must be "yes" to use the gemini backend
  TENDERSURE_FALLBACKS   openrouter: comma-separated model ids to try if the first errors
  TENDERSURE_JSON_MODE   schema (default) | prompt
  TENDERSURE_ZDR         openrouter: "false" to allow providers without zero data retention
"""

import json
import os
import re
import time
import urllib.error
import urllib.request
from typing import Callable, List, Optional, Sequence

from .privacy import Anonymiser, PrivacyError

URL = "https://openrouter.ai/api/v1/chat/completions"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
DEFAULT_MODEL = "google/gemini-4-argon"
DEFAULT_GEMINI_MODEL = "gemini-4-argon"
OUTBOX = os.path.join(".cache", "llm_outbox")


class LLMError(RuntimeError):
    pass


class SchemaError(LLMError):
    pass


def load_dotenv(path: Optional[str] = None):
    """KEY=VALUE lines from .env into the environment, never overriding
    variables already set. No dependency; quotes and comments are handled."""
    path = path or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    if not os.path.exists(path):
        return
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            v = v.strip()
            if not v.startswith(("'", '"')):
                v = re.split(r"\s+#", v, maxsplit=1)[0]      # inline comment
            k, v = k.strip().removeprefix("export ").strip(), v.strip().strip("'\"")
            if k and k not in os.environ:
                os.environ[k] = v


# --- schema check ------------------------------------------------------------

_TYPES = {"object": dict, "array": list, "string": str, "boolean": bool}


def check_schema(value, schema: dict, path: str = "$") -> List[str]:
    """The subset of JSON Schema this package uses: type, properties,
    required, items, enum. Returns a list of problems, empty if valid."""
    t = schema.get("type")
    if t == "integer":
        if not isinstance(value, int) or isinstance(value, bool):
            return [f"{path}: expected integer, got {type(value).__name__}"]
    elif t in _TYPES and not isinstance(value, _TYPES[t]):
        return [f"{path}: expected {t}, got {type(value).__name__}"]
    if "enum" in schema and value not in schema["enum"]:
        return [f"{path}: {value!r} not one of {schema['enum']}"]
    out = []
    if t == "object":
        for k in schema.get("required", []):
            if k not in value:
                out.append(f"{path}.{k}: missing")
        for k, sub in schema.get("properties", {}).items():
            if k in value:
                out += check_schema(value[k], sub, f"{path}.{k}")
    elif t == "array" and "items" in schema:
        for i, v in enumerate(value):
            out += check_schema(v, schema["items"], f"{path}[{i}]")
    return out


def parse_json(text: str):
    """JSON from a model reply, tolerating a ```json fence or leading prose."""
    text = (text or "").strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if m:
        text = m.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = min((i for i in (text.find("{"), text.find("[")) if i >= 0), default=-1)
        if start < 0:
            raise
        return json.loads(text[start:])


# --- transport ---------------------------------------------------------------

Transport = Callable[[str, dict, dict], dict]


def _http(url: str, headers: dict, body: dict, timeout: float = 600) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:500]
        if e.code == 402:
            raise LLMError("the model account is out of credit (HTTP 402). Each request reserves credit "
                           "for its longest possible answer; add credit and retry") from None
        if e.code in (401, 403):
            raise LLMError(f"the provider rejected the key (HTTP {e.code}); check the key in .env") from None
        raise LLMError(f"HTTP {e.code}: {detail}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        # No connection, a DNS failure, a timeout: a model error like any
        # other, so every step falls back (rules, or "nothing applied")
        # instead of stopping the batch.
        raise LLMError(f"network: {getattr(e, 'reason', e)}") from None


# --- shared request path -------------------------------------------------------

class _Base:
    provider = ""

    def __init__(self, model, json_mode, transport):
        self.model = model
        self.json_mode = json_mode or os.environ.get("TENDERSURE_JSON_MODE", "schema")
        self.transport = transport or _http
        self.last_model: Optional[str] = None     # which model actually answered
        self.retried = 0                          # schema requests retried in prompt mode

    def complete_json(self, system: str, prompt: str, schema: dict, name: str,
                      client: Optional[str] = None, pdf: Optional[bytes] = None,
                      max_tokens: int = 32000, **_):
        """Ask one question; `client` is the client being worked on, so its
        name becomes [CLIENT] and every other client's a different placeholder."""
        if pdf is not None:
            raise PrivacyError("refused to send a raw PDF: it cannot be anonymised reliably; "
                               "send extracted, anonymised text instead")
        anon = Anonymiser(client)
        system_a, prompt_a = anon.anonymise(system), anon.anonymise(prompt)
        anon.guard(system_a, prompt_a)
        _log_outbox(self.provider, self.model, name, system_a, prompt_a)
        if self.json_mode == "prompt":
            data = self._ask("prompt", system_a, prompt_a, schema, name, max_tokens)
        else:
            try:
                data = self._ask("schema", system_a, prompt_a, schema, name, max_tokens)
            except LLMError as e:
                if not _schema_trouble(e):
                    raise
                # The provider rejected or bent the schema request: ask once
                # more with the schema in the prompt. The reply is still checked.
                self.retried += 1
                data = self._ask("prompt", system_a, prompt_a, schema, name, max_tokens)
        return anon.restore(data)

    def _ask(self, mode, system, prompt, schema, name, max_tokens):
        if mode == "prompt":
            system = (f"{system}\n\nReply with a single JSON value and nothing else. "
                      f"It must match this JSON Schema:\n{json.dumps(schema)}")
        text = self._send(mode, system, prompt, schema, name, max_tokens)
        try:
            data = parse_json(text)
        except json.JSONDecodeError as e:
            raise SchemaError(f"{self.last_model}: reply is not JSON ({e}): {text[:200]!r}") from None
        problems = check_schema(data, schema)
        if problems:
            raise SchemaError(f"{self.last_model}: reply does not match the schema: "
                              + "; ".join(problems[:8]))
        return data

    def _send(self, mode, system, prompt, schema, name, max_tokens) -> str:
        raise NotImplementedError


def _log_outbox(provider, model, name, system, prompt):
    """Exactly what left this machine, for anyone auditing privacy."""
    outbox = os.environ.get("TENDERSURE_OUTBOX") or OUTBOX
    os.makedirs(outbox, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = os.path.join(outbox, f"{stamp}-{name}-{abs(hash(prompt)) % 10**6:06d}.txt")
    with open(path, "w") as fh:
        fh.write(f"provider: {provider}\nmodel: {model}\n\n=== system ===\n{system}\n\n=== prompt ===\n{prompt}\n")


def _schema_trouble(e: LLMError) -> bool:
    """Errors worth one retry without the schema request: a reply that bent
    the schema, or a provider/route that refused the structured-output
    parameters. Not auth, credit, rate limits or cut-offs."""
    if isinstance(e, SchemaError):
        return True
    msg = str(e).lower()
    return ("http 400" in msg or "http 404" in msg) and any(
        w in msg for w in ("schema", "response_format", "structured", "parameter", "json"))


# --- backends ----------------------------------------------------------------

class OpenRouterLLM(_Base):
    provider = "openrouter"

    def __init__(self, model: Optional[str] = None, fallbacks: Optional[Sequence[str]] = None,
                 json_mode: Optional[str] = None, api_key: Optional[str] = None,
                 transport: Optional[Transport] = None, **_):
        load_dotenv()
        super().__init__(model or os.environ.get("TENDERSURE_MODEL") or DEFAULT_MODEL, json_mode, transport)
        env_fb = os.environ.get("TENDERSURE_FALLBACKS", "")
        self.fallbacks = list(fallbacks) if fallbacks is not None else \
            [m.strip() for m in env_fb.split(",") if m.strip()]
        self.zdr = os.environ.get("TENDERSURE_ZDR", "true").lower() != "false"
        self.api_key = api_key or os.environ.get("OPENROUTER_API_KEY")
        if transport is None and not self.api_key:
            raise LLMError("set OPENROUTER_API_KEY (in the environment or in .env) to use a model step")

    def _send(self, mode, system, prompt, schema, name, max_tokens) -> str:
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": [{"type": "text", "text": prompt}]}],
            "max_tokens": max_tokens,
            # Only providers that neither store nor train on the data.
            "provider": {"data_collection": "deny", **({"zdr": True} if self.zdr else {})},
        }
        if mode == "schema":
            body["response_format"] = {"type": "json_schema",
                                       "json_schema": {"name": name, "strict": True, "schema": schema}}
            body["provider"]["require_parameters"] = True
        if self.fallbacks:
            body["models"] = self.fallbacks
        headers = {"Content-Type": "application/json", "X-Title": "criteria toolkit"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        resp = self.transport(URL, headers, body)
        if resp.get("error"):
            raise LLMError(f"{self.model}: {resp['error'].get('message', resp['error'])}")
        choice = (resp.get("choices") or [{}])[0]
        if choice.get("error"):
            raise LLMError(f"{self.model}: {choice['error'].get('message', choice['error'])}")
        self.last_model = resp.get("model", self.model)
        reason = choice.get("finish_reason")
        if reason == "length":
            raise LLMError(f"{self.last_model}: answer cut off at max_tokens")
        if reason == "content_filter":
            raise LLMError(f"{self.last_model}: answer withheld by the provider's content filter")
        return (choice.get("message") or {}).get("content") or ""


class GeminiLLM(_Base):
    """Google's Gemini API with your own key. Paid tier only: on the unpaid
    tier Google may use prompts to improve its products and human reviewers
    may read them (ai.google.dev/gemini-api/terms), which client criteria
    must not be exposed to."""
    provider = "gemini"

    def __init__(self, model: Optional[str] = None, json_mode: Optional[str] = None,
                 api_key: Optional[str] = None, transport: Optional[Transport] = None,
                 paid_tier: Optional[bool] = None, **_):
        load_dotenv()
        m = model or os.environ.get("TENDERSURE_MODEL") or DEFAULT_GEMINI_MODEL
        super().__init__(m.removeprefix("google/"), json_mode, transport)
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY")
        if transport is None and not self.api_key:
            raise LLMError("set GEMINI_API_KEY (in the environment or in .env) to use the gemini backend")
        paid = paid_tier if paid_tier is not None else os.environ.get("GEMINI_PAID_TIER", "").lower() == "yes"
        if not paid:
            raise PrivacyError(
                "the gemini backend needs a paid-tier key: on the unpaid tier Google may use prompts to "
                "improve its products and human reviewers may read them. Enable billing on the key's "
                "Google Cloud project, then set GEMINI_PAID_TIER=yes in .env")

    def _send(self, mode, system, prompt, schema, name, max_tokens) -> str:
        cfg = {"maxOutputTokens": max_tokens}
        if mode == "schema":
            cfg["responseMimeType"] = "application/json"
            cfg["responseJsonSchema"] = schema
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": cfg,
        }
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["x-goog-api-key"] = self.api_key
        resp = self.transport(GEMINI_URL.format(model=self.model), headers, body)
        if resp.get("error"):
            raise LLMError(f"{self.model}: {resp['error'].get('message', resp['error'])}")
        block = (resp.get("promptFeedback") or {}).get("blockReason")
        if block:
            raise LLMError(f"{self.model}: request blocked by Gemini ({block})")
        cand = (resp.get("candidates") or [{}])[0]
        self.last_model = resp.get("modelVersion", self.model)
        reason = cand.get("finishReason")
        if reason == "MAX_TOKENS":
            raise LLMError(f"{self.last_model}: answer cut off at max_tokens")
        if reason in ("SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII"):
            raise LLMError(f"{self.last_model}: answer withheld by Gemini ({reason})")
        parts = (cand.get("content") or {}).get("parts") or []
        return "".join(p.get("text", "") for p in parts if not p.get("thought"))


def make_llm(model: Optional[str] = None, **kw):
    """The configured backend: TENDERSURE_PROVIDER = openrouter (default) | gemini."""
    load_dotenv()
    provider = (kw.pop("provider", None) or os.environ.get("TENDERSURE_PROVIDER", "openrouter")).lower()
    if provider == "gemini":
        return GeminiLLM(model=model, **kw)
    if provider == "openrouter":
        return OpenRouterLLM(model=model, **kw)
    raise LLMError(f"unknown TENDERSURE_PROVIDER '{provider}' (use openrouter or gemini)")
