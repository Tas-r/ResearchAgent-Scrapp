import json

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from .agent import chat_with_tools


@csrf_exempt
def chat(request):
    if request.method != "POST":
        return JsonResponse({"error": "method_not_allowed"}, status=405)

    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except Exception:
        return JsonResponse({"error": "invalid_json"}, status=400)

    messages = payload.get("messages")
    if not isinstance(messages, list):
        return JsonResponse({"error": "messages_must_be_list"}, status=400)

    # Basic validation: each message should have role/content
    normalized = []
    for m in messages:
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        content = m.get("content")
        if role not in {"user", "assistant"}:
            continue
        if not isinstance(content, str):
            continue
        normalized.append({"role": role, "content": content})

    if not normalized or normalized[-1]["role"] != "user":
        return JsonResponse({"error": "last_message_must_be_user"}, status=400)

    model = payload.get("model")
    if model is not None and not isinstance(model, str):
        model = None

    try:
        assistant_text = chat_with_tools(messages=normalized, model=model)
    except Exception as e:
        return JsonResponse({"error": "backend_error", "message": str(e)}, status=500)

    assistant_msg = {"role": "assistant", "content": assistant_text}
    return JsonResponse(
        {
            "assistant": assistant_msg,
            "messages": normalized + [assistant_msg],
        }
    )
