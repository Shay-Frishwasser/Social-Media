from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

from common import LOG_DIR, ResourceSampler, gpu_snapshot, write_json

SERVER = Path(os.environ["BRANDAI_LLAMACPP_DIR"]) / "llama-server.exe"
MODEL = Path(os.environ["BRANDAI_LLM_DIR"]) / "qwen3.5-9b-q8" / "Qwen_Qwen3.5-9B-Q8_0.gguf"
PORT = 18220

CASES = [
    {
        "id": "formal_b2b",
        "brand": "NovaSec",
        "platform": "LinkedIn",
        "register": "formal_corporate",
        "gender": "mixed_plural",
        "depth": "standard",
        "target_words": [110, 150],
        "facts": [
            {"id": "product", "value": "המוצר מסייע לצוותי IT למפות הרשאות"},
            {"id": "audience", "value": "קהל היעד הוא מנהלי IT בארגונים בינוניים"},
            {"id": "offer", "value": "ההצעה היא הדגמה של 20 דקות"},
        ],
        "requirements": ["טון בטוח וענייני", "ללא הפחדה", "סיום בקריאה לפעולה"],
        "forbidden": ["לקוחות", "מספרי הצלחה", "פרסים", "תאימות", "אוטומציה", "זמן אמת", "הבטחת הפחתת סיכון"],
    },
    {
        "id": "conversational_cafe",
        "brand": "Luma",
        "platform": "Instagram",
        "register": "conversational_israeli",
        "gender": "mixed_plural",
        "depth": "short",
        "target_words": [55, 80],
        "facts": [
            {"id": "coffee", "value": "Luma מציעה קלייה מקומית"},
            {"id": "setting", "value": "ב-Luma יש ישיבה שכונתית"},
            {"id": "days", "value": "Luma פתוחה בימים א׳–ו׳; שעות הפתיחה המדויקות אינן ידועות"},
        ],
        "requirements": ["טון קליל וישראלי", "להזמין לעצירה לקפה באמצע היום", "לשמור את Luma באנגלית"],
        "forbidden": ["שעות פתיחה מדויקות", "פתוח כל היום", "פתוח בכל שעה", "ספה", "תפריט", "כתובת", "משלוחים"],
    },
    {
        "id": "feminine_coaching",
        "brand": "מאמנת קריירה",
        "platform": "Instagram",
        "register": "warm_professional",
        "gender": "feminine_singular",
        "depth": "short",
        "target_words": [65, 95],
        "facts": [
            {"id": "session", "value": "פגישת המיקוד נמשכת 45 דקות"},
            {"id": "audience", "value": "הפגישה מיועדת לנשים ששוקלות שינוי מקצועי"},
            {"id": "cta", "value": "הקריאה לפעולה היא לקבוע שיחה"},
        ],
        "requirements": ["לפנות לקוראת אחת בנקבה יחידה בלבד", "ללא לוכסנים", "מורפולוגיה תקינה ועברית טבעית"],
        "forbidden": ["הבטחת תוצאה", "תוכנית פעולה", "ייעוץ חינם", "ניסיון או הסמכה שלא נמסרו"],
    },
    {
        "id": "rich_educational",
        "brand": "FlowDesk AI",
        "platform": "LinkedIn",
        "register": "expert_accessible",
        "gender": "mixed_plural",
        "depth": "long_educational",
        "target_words": [320, 430],
        "facts": [
            {"id": "product", "value": "FlowDesk AI הוא כלי מקומי לארגון משימות"},
            {"id": "privacy", "value": "הנתונים נשמרים במחשב המשתמש"},
            {"id": "framework_1", "value": "שלב 1 במסגרת העבודה: לרכז את כל המשימות במקום אחד"},
            {"id": "framework_2", "value": "שלב 2 במסגרת העבודה: לסווג משימות לפי תחום והקשר"},
            {"id": "framework_3", "value": "שלב 3 במסגרת העבודה: לבחור שלוש משימות מרכזיות ליום"},
            {"id": "framework_4", "value": "שלב 4 במסגרת העבודה: לבצע סקירה שבועית ולעדכן סדרי עדיפויות"},
        ],
        "requirements": [
            "פוסט עומק שימושי ולא טיזר",
            "פתיח שמציג את בעיית פיזור המשימות בלי נתון מספרי",
            "ארבעה שלבים עם הסבר מעשי לכל שלב",
            "סיכום וקריאה לפעולה מתונה",
        ],
        "forbidden": ["נתוני מחקר", "אחוזים", "הבטחות פרודוקטיביות", "לקוחות", "אינטגרציות", "ענן", "תכונות מוצר נוספות"],
    },
]

BRIEF_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["case_id", "brand", "platform", "register", "address_gender", "content_depth", "target_words", "allowed_facts", "requirements", "forbidden_claims", "structure"],
    "properties": {
        "case_id": {"type": "string"}, "brand": {"type": "string"}, "platform": {"type": "string"},
        "register": {"type": "string"}, "address_gender": {"type": "string"}, "content_depth": {"type": "string"},
        "target_words": {"type": "array", "prefixItems": [{"type": "integer"}, {"type": "integer"}], "minItems": 2, "maxItems": 2},
        "allowed_facts": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["id", "value"], "properties": {"id": {"type": "string"}, "value": {"type": "string"}}}},
        "requirements": {"type": "array", "items": {"type": "string"}},
        "forbidden_claims": {"type": "array", "items": {"type": "string"}},
        "structure": {"type": "array", "items": {"type": "string"}},
    },
}

EDITOR_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["issues_found", "facts_removed", "gender_check", "final_post"],
    "properties": {
        "issues_found": {"type": "array", "items": {"type": "string"}},
        "facts_removed": {"type": "array", "items": {"type": "string"}},
        "gender_check": {"type": "string"},
        "final_post": {"type": "string"},
    },
}


def request(payload: dict, timeout: int = 1800) -> dict:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}/v1/chat/completions", data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def wait_ready(process: subprocess.Popen, timeout: int = 180):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"server exited: {process.returncode}")
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/health", timeout=2):
                return
        except (urllib.error.URLError, TimeoutError):
            time.sleep(0.5)
    raise TimeoutError("server readiness timeout")


def chat(messages: list[dict], temperature: float, max_tokens: int, schema: dict | None = None) -> tuple[dict, float]:
    payload = {
        "model": "qwen3.5-9b-q8", "messages": messages, "temperature": temperature,
        "top_p": 0.9, "max_tokens": max_tokens, "seed": 20261122, "stream": False,
    }
    if schema:
        payload["response_format"] = {"type": "json_schema", "json_schema": {"name": "result", "strict": True, "schema": schema}}
    started = time.time()
    response = request(payload)
    return response, time.time() - started


server_log = LOG_DIR / "hebrew-three-pass-server.log"
command = [
    str(SERVER), "--model", str(MODEL), "--host", "127.0.0.1", "--port", str(PORT),
    "--ctx-size", "8192", "--n-gpu-layers", "99", "--parallel", "1", "--no-webui", "--jinja",
    "--reasoning-format", "deepseek", "--chat-template-kwargs", '{"enable_thinking":false}',
]
before = gpu_snapshot()
with server_log.open("w", encoding="utf-8") as log:
    process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, text=True)
    sampler = ResourceSampler(process.pid).start()
    results = []
    try:
        load_started = time.time()
        wait_ready(process)
        load_seconds = time.time() - load_started
        for case in CASES:
            source = json.dumps(case, ensure_ascii=False, indent=2)
            brief_response, brief_seconds = chat([
                {"role": "system", "content": "את/ה מתכנן/ת תוכן קפדני/ת. העתיקו את העובדות בדיוק, בלי להוסיף או להסיק. החזירו JSON בלבד."},
                {"role": "user", "content": f"הפכו את המפרט הבא לבריף נעול. allowed_facts חייב להכיל בדיוק את זוגות id/value שנמסרו. בנו structure שמתאים לעומק ולפלטפורמה.\n{source}"},
            ], 0.0, 900, BRIEF_SCHEMA)
            brief_text = brief_response["choices"][0]["message"]["content"]
            brief = json.loads(brief_text)
            source_facts = [(fact["id"], fact["value"]) for fact in case["facts"]]
            brief_facts = [(fact["id"], fact["value"]) for fact in brief["allowed_facts"]]
            brief_grounded = source_facts == brief_facts
            if not brief_grounded:
                raise ValueError(f"brief facts changed for {case['id']}")

            draft_response, draft_seconds = chat([
                {"role": "system", "content": "את/ה כותב/ת תוכן עברי מקורי. השתמשו אך ורק ב-allowed_facts. אסור להפוך דרישה סגנונית לעובדה. אם פרט אינו בבריף, השמיטו אותו. כתבו עברית טבעית ולא תרגום מאנגלית. החזירו רק את הפוסט."},
                {"role": "user", "content": "כתבו פוסט לפי הבריף המאושר הבא:\n" + json.dumps(brief, ensure_ascii=False, indent=2)},
            ], 0.45, 1400)
            draft = draft_response["choices"][0]["message"]["content"]

            editor_response, editor_seconds = chat([
                {"role": "system", "content": "את/ה עורכ/ת עברית ישראלית בכיר/ה ובודק/ת עובדות. בדקו כל טענה מול allowed_facts; הסירו כל המצאה או הסקה. תקנו עברית מתורגמת, ניסוח מאולץ ומורפולוגיית מגדר. שמרו על העומק והמבנה. החזירו JSON בלבד."},
                {"role": "user", "content": "בריף מאושר:\n" + json.dumps(brief, ensure_ascii=False, indent=2) + "\n\nטיוטה לעריכה:\n" + draft},
            ], 0.15, 1800, EDITOR_SCHEMA)
            editor_text = editor_response["choices"][0]["message"]["content"]
            editor = json.loads(editor_text)
            results.append({
                "case_id": case["id"], "brief_grounded": brief_grounded,
                "brief_seconds": brief_seconds, "draft_seconds": draft_seconds, "editor_seconds": editor_seconds,
                "total_seconds": brief_seconds + draft_seconds + editor_seconds,
                "brief_usage": brief_response.get("usage"), "draft_usage": draft_response.get("usage"), "editor_usage": editor_response.get("usage"),
                "brief": brief, "draft": draft, "editor": editor,
            })
    finally:
        process.terminate()
        try:
            process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=30)
        resources = sampler.stop()

payload = {
    "model": str(MODEL), "load_seconds": load_seconds, "before_gpu": before, "after_gpu": gpu_snapshot(),
    "resources": resources, "results": results,
}
path = LOG_DIR / "hebrew-three-pass-benchmark.json"
write_json(path, payload)
print(path)
