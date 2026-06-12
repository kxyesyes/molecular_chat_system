#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Agent 技能性能监控模块
记录和统计各技能的使用频次、成功率及耗时。
"""

import time
import json
import os
import logging
from typing import Dict, Any, List, Optional
from threading import Lock

logger = logging.getLogger(__name__)

class SkillMetrics:
    _instance = None
    _lock = Lock()

    def __new__(cls, *args, **kwargs):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(SkillMetrics, cls).__new__(cls)
                cls._instance._initialized = False
        return cls._instance

    def __init__(self, log_path: str = "logs/skill_metrics.json"):
        if self._initialized:
            return
        
        self.log_path = log_path
        self.metrics = {}
        self.lock = Lock()
        self._load_metrics()
        self._initialized = True

    def _load_metrics(self):
        """从文件加载历史指标"""
        try:
            os.makedirs(os.path.dirname(self.log_path), exist_ok=True)
            if os.path.exists(self.log_path):
                with open(self.log_path, 'r', encoding='utf-8') as f:
                    self.metrics = json.load(f)
        except Exception as e:
            logger.warning(f"无法加载指标文件: {e}")
            self.metrics = {}

    def save_metrics(self):
        """持久化指标到文件"""
        try:
            with self.lock:
                with open(self.log_path, 'w', encoding='utf-8') as f:
                    json.dump(self.metrics, f, indent=4, ensure_ascii=False)
        except Exception as e:
            logger.error(f"无法保存指标文件: {e}")

    def record_route_attempt(self, skill_name: str, hit: bool):
        """记录一次路由尝试"""
        with self.lock:
            if skill_name not in self.metrics:
                self._init_skill_entry(skill_name)
            
            self.metrics[skill_name]["route_attempts"] += 1
            if hit:
                self.metrics[skill_name]["route_hits"] += 1

    def record_execution(self, skill_name: str, duration: float, success: bool):
        """记录一次技能执行"""
        with self.lock:
            if skill_name not in self.metrics:
                self._init_skill_entry(skill_name)
            
            entry = self.metrics[skill_name]
            entry["call_count"] += 1
            if success:
                entry["success_count"] += 1
            
            # 更新平均耗时 (移动平均)
            n = entry["call_count"]
            old_avg = entry["avg_duration"]
            entry["avg_duration"] = old_avg + (duration - old_avg) / n
            
            # 记录最大/最小耗时
            entry["max_duration"] = max(entry["max_duration"], duration)
            if entry["min_duration"] == 0:
                entry["min_duration"] = duration
            else:
                entry["min_duration"] = min(entry["min_duration"], duration)

    def _init_skill_entry(self, skill_name: str):
        self.metrics[skill_name] = {
            "call_count": 0,
            "success_count": 0,
            "route_attempts": 0,
            "route_hits": 0,
            "avg_duration": 0.0,
            "max_duration": 0.0,
            "min_duration": 0.0,
            "last_used": ""
        }

    def get_report(self) -> Dict[str, Any]:
        """获取所有技能的统计报表"""
        with self.lock:
            report = {}
            for name, data in self.metrics.items():
                hit_rate = (data["route_hits"] / data["route_attempts"] * 100) if data["route_attempts"] > 0 else 0
                success_rate = (data["success_count"] / data["call_count"] * 100) if data["call_count"] > 0 else 0
                
                report[name] = {
                    "总调用": data["call_count"],
                    "路由命中率": f"{hit_rate:.1f}%",
                    "执行成功率": f"{success_rate:.1f}%",
                    "平均耗时": f"{data['avg_duration']:.2f}s",
                    "最大耗时": f"{data['max_duration']:.2f}s"
                }
            return report

# 全局单例
metrics_system = SkillMetrics()
