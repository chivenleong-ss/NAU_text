#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Small server-side adapter for optional Dify OCR, workflow and knowledge calls."""

from __future__ import annotations

import json
import mimetypes
import os
import uuid
from typing import Any, Dict, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def _load_dotenv() -> None:
    """Load local secrets without adding a third-party dependency."""
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if not os.path.exists(env_path):
        return
    try:
        with open(env_path, "r", encoding="utf-8") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip("\"'"))
    except OSError:
        return


_load_dotenv()


class DifyAdapter:
    """Keep Dify optional so local deterministic demo mode remains usable."""

    def __init__(self) -> None:
        configured_url = (os.getenv("DIFY_BASE_URL") or "").rstrip("/")
        self.base_url = configured_url[:-3].rstrip("/") if configured_url.endswith("/v1") else configured_url
        self.api_key = os.getenv("DIFY_API_KEY") or ""
        self.ocr_api_key = os.getenv("DIFY_OCR_API_KEY") or self.api_key
        self.expert_api_key = os.getenv("DIFY_EXPERT_API_KEY") or self.api_key
        self.knowledge_api_key = os.getenv("DIFY_KNOWLEDGE_API_KEY") or self.api_key
        self.ocr_app_key = os.getenv("DIFY_OCR_APP_KEY") or self.ocr_api_key
        self.expert_app_key = os.getenv("DIFY_EXPERT_APP_KEY") or self.expert_api_key
        self.knowledge_dataset_id = os.getenv("DIFY_KNOWLEDGE_DATASET_ID") or os.getenv("DIFY_DATASET_ID") or ""
        self.ocr_file_input = os.getenv("DIFY_OCR_FILE_INPUT", "document")
        self.timeout = max(int(os.getenv("DIFY_TIMEOUT_SECONDS", "30")), 3)

    @property
    def enabled(self) -> bool:
        return bool(self.base_url)

    def status(self) -> Dict[str, Any]:
        return {
            "provider": "dify",
            "enabled": self.enabled,
            "base_url_configured": bool(self.base_url),
            "ocr": {"configured": bool(self.base_url and self.ocr_app_key), "mode": "workflow"},
            "expert_conference": {"configured": bool(self.base_url and self.expert_app_key), "mode": "chat/workflow"},
            "knowledge_base": {"configured": bool(self.base_url and self.knowledge_api_key and self.knowledge_dataset_id), "dataset_configured": bool(self.knowledge_dataset_id)},
            "robot_scheduler": {"provider": "local", "dify_role": "可选的识别后处理"},
        }

    def _request(self, method: str, path: str, api_key: str, payload: Optional[Dict[str, Any]] = None, body: Optional[bytes] = None, content_type: str = "application/json") -> Dict[str, Any]:
        if not self.base_url or not api_key:
            return {"status": "not_configured", "message": "Dify尚未配置，当前使用本地演示能力"}
        request_body = body
        if request_body is None and payload is not None:
            request_body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = Request(
            f"{self.base_url}{path}",
            data=request_body,
            method=method.upper(),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": content_type,
                "Accept": "application/json",
            },
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8", errors="replace")
                parsed = json.loads(raw) if raw else {}
                return {"status": "success", "data": parsed}
        except HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            return {"status": "error", "message": f"Dify请求失败({error.code})", "detail": detail[:1000]}
        except (URLError, TimeoutError, OSError) as error:
            return {"status": "error", "message": "Dify服务不可达", "detail": str(error)[:500]}

    def _upload_file(self, filename: str, raw: bytes) -> Dict[str, Any]:
        boundary = f"----SettlementAudit{uuid.uuid4().hex}"
        mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        parts = [
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{filename}\"\r\nContent-Type: {mime}\r\n\r\n".encode("utf-8"),
            raw,
            f"\r\n--{boundary}--\r\n".encode("utf-8"),
        ]
        return self._request("POST", "/v1/files/upload", self.ocr_api_key, body=b"".join(parts), content_type=f"multipart/form-data; boundary={boundary}")

    def _run_workflow(self, api_key: str, app_key: str, inputs: Dict[str, Any], files: Optional[list] = None, user: str = "settlement-audit") -> Dict[str, Any]:
        if not app_key:
            return {"status": "not_configured", "message": "Dify应用密钥未配置"}
        payload: Dict[str, Any] = {"inputs": inputs, "response_mode": "blocking", "user": user}
        if files:
            payload["files"] = files
        return self._request("POST", "/v1/workflows/run", api_key, payload=payload)

    def extract_document(self, filename: str, raw: bytes, document_type: str = "") -> Dict[str, Any]:
        if not self.base_url or not self.ocr_app_key:
            return {"status": "not_configured", "provider": "local", "message": "Dify OCR未配置，文件先进入本地待核验层"}
        uploaded = self._upload_file(filename, raw)
        if uploaded.get("status") != "success":
            return uploaded
        file_data = uploaded.get("data", {})
        file_id = file_data.get("id") or file_data.get("upload_file_id")
        if not file_id:
            return {"status": "error", "message": "Dify文件上传未返回文件ID", "data": file_data}
        file_object = {"type": "document", "transfer_method": "local_file", "upload_file_id": file_id}
        result = self._run_workflow(
            self.ocr_api_key,
            self.ocr_app_key,
            {self.ocr_file_input: file_object, "file_name": filename, "document_type": document_type},
            user="settlement-audit-ocr",
        )
        return {"status": result.get("status"), "provider": "dify", "file_id": file_id, "result": result}

    def expert_conference(self, question: str, context: Dict[str, Any], conversation_id: str = "") -> Dict[str, Any]:
        if not self.base_url or not self.expert_app_key:
            return {"status": "not_configured", "provider": "local", "message": "Dify专家会谈未配置"}
        prompt = json.dumps({"question": question, "context": context}, ensure_ascii=False)
        result = self._request(
            "POST",
            "/v1/chat-messages",
            self.expert_api_key,
            payload={"inputs": {}, "query": prompt, "response_mode": "blocking", "conversation_id": conversation_id, "user": "settlement-audit-expert"},
        )
        return {"status": result.get("status"), "provider": "dify", "result": result}

    def retrieve(self, query: str, top_k: int = 5) -> Dict[str, Any]:
        if not self.base_url or not self.knowledge_api_key or not self.knowledge_dataset_id:
            return {"status": "not_configured", "provider": "local", "message": "Dify制度知识库未配置"}
        return self._request(
            "POST",
            f"/v1/datasets/{self.knowledge_dataset_id}/retrieve",
            self.knowledge_api_key,
            payload={"query": query, "retrieval_model": {"search_method": "hybrid_search", "reranking_enable": True, "top_k": top_k, "score_threshold_enabled": True, "score_threshold": 0.2}},
        )
