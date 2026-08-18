#!/usr/bin/env python3

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from api.app import main


BLOCKED_CASES = {
    "symbol-separated profanity": "f.u.c.k off",
    "full-width abuse": "ｎｍｓｌ",
    "CJK inserted noise": "你真是傻b逼",
    "CJK political inserted noise": "台x独",
    "leet pornography": "p0rn resources",
    "accented pornography": "pórn resources",
    "Cyrillic homoglyph": "wеbshеll for sale",
    "zero-width evasion": "杀​猪​盘",
    "platform bypass": "加 v 联系，私下转账",
    "threat and doxxing": "我要开你盒并曝光住址",
}

ALLOWED_CASES = {
    "project inquiry": "我想讨论3D建模和AI Agent评估。",
    "course inquiry": "Please contact me about UC Berkeley Summer Sessions.",
    "engineering stack": "这个项目使用FastAPI、React和TypeScript。",
    "research direction": "I am interested in digital twins and simulation systems.",
    "technical collaboration": "Can we discuss traffic simulation, BIM, and LLM agent evaluation?",
    "robotics research": "我正在研究仓储机器人路径规划和多智能体协同。",
}


def assert_blocked(label: str, value: str) -> None:
    matches = main.find_sensitive_words(value)
    if not matches:
        raise AssertionError(f"blocked case missed: {label}: {value}")


def assert_allowed(label: str, value: str) -> None:
    matches = main.find_sensitive_words(value)
    if matches:
        raise AssertionError(f"allowed case blocked: {label}: {matches}")


def main_check() -> None:
    active_terms = [
        line.strip()
        for line in main.SENSITIVE_WORDS_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if len(active_terms) < 1800:
        raise AssertionError(f"sensitive-word coverage unexpectedly shrank: {len(active_terms)}")

    main.load_sensitive_words()

    for label, value in BLOCKED_CASES.items():
        assert_blocked(label, value)

    for label, value in ALLOWED_CASES.items():
        assert_allowed(label, value)

    print(
        "Sensitive-word checks passed: "
        f"{len(active_terms)} active terms, {len(BLOCKED_CASES)} blocked cases, "
        f"{len(ALLOWED_CASES)} allowed cases."
    )


if __name__ == "__main__":
    main_check()
