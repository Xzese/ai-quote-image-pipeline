"""Local Ollama structured chat, without LM Studio lifecycle/API assumptions."""

import json

from quote_image_generator.deadline import Deadline, request


class OllamaClient:
    provider_name = "Ollama"

    def __init__(self, base_url, context_length, stop_event):
        self.base_url = base_url.rstrip("/")
        self.context_length = context_length
        self.stop_event = stop_event
        self.capabilities = None

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
        # The server's keep_alive expires naturally; never unload another caller's model.
        pass
