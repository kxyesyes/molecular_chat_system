#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ModelScope chat-completions adapter.

The current ModelScope endpoint is OpenAI-compatible, so this module keeps the
existing public class names while delegating the transport logic to the generic
OpenAI-compatible adapter.
"""

from __future__ import annotations

import logging

from src.agent.openai_compatible_model import OpenAICompatibleModel


logger = logging.getLogger(__name__)


class ModelScopeModel(OpenAICompatibleModel):
    """ModelScope API model adapter."""

    def __init__(
        self,
        api_key: str,
        model_name: str = "ZhipuAI/GLM-4.6",
        base_url: str = "https://api-inference.modelscope.cn/v1/chat/completions",
    ):
        super().__init__(
            api_key=api_key,
            model_name=model_name,
            base_url=base_url,
            provider_name="ModelScope",
        )
        logger.info("Initialized ModelScope model: %s", self.model_name)


class ModelScopeModelManager:
    """Small manager used by older call sites that switch ModelScope models."""

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.models: dict[str, ModelScopeModel] = {}
        self.current_model: ModelScopeModel | None = None
        self.available_models = {
            "glm4": "ZhipuAI/GLM-4.6",
            "glm5.1": "ZhipuAI/GLM-5.1",
            "qwen3": "Qwen/Qwen3-235B-A22B-Instruct-2507",
        }

    def get_model(self, model_key: str = "glm4") -> ModelScopeModel:
        if model_key not in self.models:
            model_name = self.available_models.get(model_key)
            if not model_name:
                logger.warning("Unknown ModelScope model key %s; using glm4", model_key)
                model_name = self.available_models["glm4"]
            self.models[model_key] = ModelScopeModel(
                api_key=self.api_key,
                model_name=model_name,
            )
        self.current_model = self.models[model_key]
        return self.current_model

    def switch_model(self, model_key: str) -> ModelScopeModel:
        return self.get_model(model_key)
