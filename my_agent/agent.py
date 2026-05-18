# my_agent/agent.py
"""
LangGraph graph for the flexible multi-agent assistant.
"""

from __future__ import annotations

from typing import Literal

from langgraph.graph import END, START, StateGraph

try:
    from langgraph.checkpoint.memory import MemorySaver
except Exception:
    MemorySaver = None

from my_agent.state import AgentState
from my_agent.nodes import (
    artifact_agent,
    command_code_agent,
    context_builder,
    context_resolver,
    critic_agent,
    email_agent,
    explanation_agent,
    input_normalizer,
    memory_writer,
    orchestrator,
    output_safety_gate,
    pre_safety_gate,
    rag_agent,
    report_agent,
    response_composer,
    safety_gate,
    task_planner,
    vision_agent,
    web_research_agent,
)


RouteName = Literal[
    "orchestrator",
    "context_resolver",
    "task_planner",
    "vision_agent",
    "rag_agent",
    "web_research_agent",
    "report_agent",
    "command_code_agent",
    "email_agent",
    "artifact_agent",
    "explanation_agent",
    "output_safety_gate",
    "critic_agent",
    "approval_gate",
    "response_composer",
    "memory_writer",
]

DEFAULT_MAX_STEPS = 8
DEFAULT_MAX_RETRIES = 2


def _max_steps(state: AgentState) -> int:
    return int(state.get("max_steps") or DEFAULT_MAX_STEPS)


def _max_retries(state: AgentState) -> int:
    return int(state.get("max_retries") or DEFAULT_MAX_RETRIES)


def _step_count(state: AgentState) -> int:
    return int(state.get("step_count") or 0)


def _retry_count(state: AgentState) -> int:
    return int(state.get("retry_count") or 0)
def route_after_pre_safety(state: AgentState) -> str:
    if state.get("risk_level") == "blocked":
        return "response_composer"

    if state.get("next_action") in ["refuse", "ask_user"]:
        return "response_composer"

    return "input_normalizer"


def route_after_safety(state: AgentState) -> str:
    if state.get("risk_level") == "blocked":
        return "response_composer"

    if state.get("next_action") in ["refuse", "ask_user"]:
        return "response_composer"

    return "orchestrator"


def route_after_orchestrator(state: AgentState) -> str:
    """
    Fast path for responses that do not need context resolution or task planning.
    This prevents simple greetings/refusals/clarifications from calling extra LLM nodes.
    """
    if state.get("next_action") in ["final_response", "ask_user", "refuse"]:
        return "response_composer"

    return "context_resolver"


def route_after_task_planner(state: AgentState) -> str:
    action = state.get("next_action")

    routes = {
        "vision_analysis": "vision_agent",
        "rag_answer": "rag_agent",
        "web_research": "web_research_agent",
        "report_generation": "report_agent",
        "command_code_generation": "command_code_agent",
        "email_draft": "email_agent",
        "email_send": "email_agent",
        "artifact_export": "artifact_agent",
        "explain_more": "explanation_agent",
        "ask_user": "response_composer",
        "final_response": "response_composer",
        "refuse": "response_composer",
    }

    return routes.get(action, "response_composer")


def route_after_critic(state: AgentState) -> str:
    critic = state.get("critic_result") or {}

    if critic.get("passed") is True:
        return "approval_gate"

    recommended = critic.get("recommended_action")

    if recommended in ["ask_user", "refuse"]:
        return "response_composer"

    if _step_count(state) >= _max_steps(state):
        return "response_composer"

    if _retry_count(state) >= _max_retries(state):
        return "response_composer"

    return "increment_retry"


def increment_retry(state: AgentState) -> AgentState:
    return {
        **state,
        "retry_count": _retry_count(state) + 1,
    }


def route_after_approval(state: AgentState) -> str:
    return "response_composer"


def build_graph():
    builder = StateGraph(AgentState)
    builder.add_node("pre_safety_gate", pre_safety_gate)
    builder.add_node("input_normalizer", input_normalizer)
    builder.add_node("context_builder", context_builder)
    builder.add_node("safety_gate", safety_gate)
    builder.add_node("orchestrator", orchestrator)
    builder.add_node("context_resolver", context_resolver)
    builder.add_node("task_planner", task_planner)

    builder.add_node("vision_agent", vision_agent)
    builder.add_node("rag_agent", rag_agent)
    builder.add_node("web_research_agent", web_research_agent)
    builder.add_node("report_agent", report_agent)
    builder.add_node("command_code_agent", command_code_agent)
    builder.add_node("email_agent", email_agent)
    builder.add_node("artifact_agent", artifact_agent)
    builder.add_node("explanation_agent", explanation_agent)

    builder.add_node("output_safety_gate", output_safety_gate)
    builder.add_node("critic_agent", critic_agent)
    builder.add_node("increment_retry", increment_retry)
    builder.add_node("approval_gate", lambda state: state)
    builder.add_node("response_composer", response_composer)
    builder.add_node("memory_writer", memory_writer)

    builder.add_edge(START, "pre_safety_gate")
    builder.add_conditional_edges(
    "pre_safety_gate",
    route_after_pre_safety,
    {
        "input_normalizer": "input_normalizer",
        "response_composer": "response_composer",
    },
)
    builder.add_edge("input_normalizer", "context_builder")
    builder.add_edge("context_builder", "safety_gate")

    builder.add_conditional_edges(
        "safety_gate",
        route_after_safety,
        {
            "orchestrator": "orchestrator",
            "response_composer": "response_composer",
        },
    )

    builder.add_conditional_edges(
        "orchestrator",
        route_after_orchestrator,
        {
            "context_resolver": "context_resolver",
            "response_composer": "response_composer",
        },
    )
    builder.add_edge("context_resolver", "task_planner")

    builder.add_conditional_edges(
        "task_planner",
        route_after_task_planner,
        {
            "vision_agent": "vision_agent",
            "rag_agent": "rag_agent",
            "web_research_agent": "web_research_agent",
            "report_agent": "report_agent",
            "command_code_agent": "command_code_agent",
            "email_agent": "email_agent",
            "artifact_agent": "artifact_agent",
            "explanation_agent": "explanation_agent",
            "response_composer": "response_composer",
        },
    )

    quality_checked_nodes = [
        "vision_agent",
        "rag_agent",
        "web_research_agent",
        "report_agent",
        "command_code_agent",
        "explanation_agent",
    ]

    deterministic_nodes = [
        "artifact_agent",
        "email_agent",
    ]

    for node in quality_checked_nodes:
        builder.add_edge(node, "output_safety_gate")

    # Artifact and email nodes perform deterministic file/email workflow state changes.
    # Route them straight to the composer to avoid extra LLM latency and hallucinated rewrites.
    for node in deterministic_nodes:
        builder.add_edge(node, "response_composer")

    builder.add_edge("output_safety_gate", "critic_agent")

    builder.add_conditional_edges(
        "critic_agent",
        route_after_critic,
        {
            "approval_gate": "approval_gate",
            "increment_retry": "increment_retry",
            "response_composer": "response_composer",
        },
    )

    builder.add_edge("increment_retry", "orchestrator")

    builder.add_conditional_edges(
        "approval_gate",
        route_after_approval,
        {
            "response_composer": "response_composer",
        },
    )

    builder.add_edge("response_composer", "memory_writer")
    builder.add_edge("memory_writer", END)

    if MemorySaver is not None:
        return builder.compile(checkpointer=MemorySaver())

    return builder.compile()


graph = build_graph()
