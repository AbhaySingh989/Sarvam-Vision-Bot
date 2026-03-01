import json
import httpx

class SarvamChatClient:
    def __init__(self, api_key: str, base_url: str, timeout_seconds: int = 60) -> None:
        self.api_key = api_key
        self.base_url = base_url
        self.timeout_seconds = timeout_seconds

    def _auth_headers(self) -> dict[str, str]:
        return {"api-subscription-key": self.api_key}

    async def complete(
        self,
        model: str,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.2,
    ) -> str:
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature,
        }

        async with httpx.AsyncClient(
            base_url=self.base_url,
            headers=self._auth_headers(),
            timeout=self.timeout_seconds,
        ) as client:
            response = await client.post("/v1/chat/completions", json=payload)

        if response.is_error:
            details = response.text
            try:
                details = json.dumps(response.json(), ensure_ascii=False)
            except Exception:
                pass
            raise RuntimeError(f"Chat completion failed ({response.status_code}): {details}")

        data = response.json()
        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError(f"Missing choices in chat response: {data}")

        message = choices[0].get("message") or {}
        content = message.get("content")
        if isinstance(content, str):
            return content.strip()

        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict):
                    text = item.get("text")
                    if text:
                        parts.append(str(text))
            merged = "\n".join(parts).strip()
            if merged:
                return merged

        raise RuntimeError(f"Unsupported chat response shape: {data}")
