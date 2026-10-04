"""Local Ollama structured chat, without LM Studio lifecycle/API assumptions."""

import json

from quote_image_generator.deadline import Deadline, request


class OllamaClient:
    provider_name = "Ollama"

    def __init__(self, base_url, context_length, stop_event, release_on_close=False):
        self.base_url = base_url.rstrip("/")
        self.context_length = context_length
        self.stop_event = stop_event
        self.capabilities = None
        self.release_on_close = release_on_close
        self.owned_model = None

    def call_structured(self, *, messages, model, response_format):
        if self.capabilities is None:
            response = request(
                "POST",
                self.base_url + "/api/show",
                deadline=Deadline(30),
                stop_event=self.stop_event,
                json={"model": model},
            )
            details = response.json()
            capabilities = details.get("capabilities", [])
            if "completion" not in capabilities:
                raise ValueError("Ollama requires an installed chat/completion model.")
            if self.release_on_close:
                loaded = request(
                    "GET",
                    self.base_url + "/api/ps",
                    deadline=Deadline(30),
                    stop_event=self.stop_event,
                ).json()
                models = loaded.get("models")
                if not isinstance(models, list) or not all(
                    isinstance(item, dict) for item in models
                ):
                    raise ValueError("Ollama running-model response is malformed.")
                canonical = (
                    model if ":" in model.rsplit("/", 1)[-1] else model + ":latest"
                )
                if not any(
                    item.get("name") in (model, canonical)
                    or item.get("model") in (model, canonical)
                    for item in models
                ):
                    self.owned_model = model
            self.capabilities = capabilities

        schema = response_format["json_schema"]["schema"]
        payload = {
            "model": model,
            "messages": [
                *messages,
                {
                    "role": "system",
                    "content": "Respond with JSON matching this schema: "
                    + json.dumps(schema),
                },
            ],
            "format": schema,
            "stream": False,
            "keep_alive": "5m",
            "options": {
                "temperature": 0.7,
                "num_ctx": self.context_length,
                "num_predict": 512,
            },
        }
        if "thinking" in self.capabilities:
            payload["think"] = False
        response = request(
            "POST",
            self.base_url + "/api/chat",
            deadline=Deadline(60),
            stop_event=self.stop_event,
            json=payload,
        )
        result = response.json()
        if result.get("done") is not True or result.get("done_reason") == "length":
            raise ValueError("Ollama did not complete the structured response.")
        return result.get("message", {}).get("content")

    def close(self):
        if self.owned_model:
            result = request(
                "POST",
                self.base_url + "/api/generate",
                deadline=Deadline(30),
                json={
                    "model": self.owned_model,
                    "prompt": "",
                    "keep_alive": 0,
                    "stream": False,
                },
            ).json()
            if result.get("done") is not True or result.get("done_reason") != "unload":
                raise ValueError("Ollama did not confirm model unloading.")
            self.owned_model = None
