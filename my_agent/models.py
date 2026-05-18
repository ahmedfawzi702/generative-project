
import os
from dotenv import load_dotenv
from langchain_openrouter import ChatOpenRouter

load_dotenv()

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

if not OPENROUTER_API_KEY:
    raise ValueError(
        "OPENROUTER_API_KEY is missing. Add it to your .env file."
    )


# ------------------------------------------------------------
# Main reasoning model
# ------------------------------------------------------------
# Used by:
# - Smart Orchestrator
# - Context Resolver
# - Task Planner
#
# Keep temperature low because these nodes must make stable decisions.
orchestrator_llm = ChatOpenRouter(
    model=os.getenv("ORCHESTRATOR_MODEL", "deepseek/deepseek-v4-flash:free"),
    temperature=0.1,
    api_key=OPENROUTER_API_KEY,
)


# ------------------------------------------------------------
# Critic / verifier model
# ------------------------------------------------------------
# Used by:
# - Critic Agent
# - Output verification
#
# Temperature 0.0 for strict checking and stable JSON.
critic_llm = ChatOpenRouter(
    model=os.getenv("CRITIC_MODEL", "deepseek/deepseek-v4-flash:free"),
    temperature=0.0,
    api_key=OPENROUTER_API_KEY,
)


# ------------------------------------------------------------
# General model
# ------------------------------------------------------------
# Used by:
# - Safety Gate
# - Response Composer
# - Explanation Agent
# - General small reasoning tasks
general_llm = ChatOpenRouter(
    model=os.getenv("GENERAL_MODEL", "deepseek/deepseek-v4-flash:free"),
    temperature=0.2,
    api_key=OPENROUTER_API_KEY,
)


# ------------------------------------------------------------
# Report writing model
# ------------------------------------------------------------
# Used by:
# - Report Agent
#
# Slightly creative but still controlled.
report_llm = ChatOpenRouter(
    model=os.getenv("REPORT_MODEL", "deepseek/deepseek-v4-flash:free"),
    temperature=0.25,
    api_key=OPENROUTER_API_KEY,
)


# ------------------------------------------------------------
# Command / code generation model
# ------------------------------------------------------------
# Used by:
# - Command / Code Agent
#
# Keep moderate temperature for practical commands without randomness.
command_llm = ChatOpenRouter(
    model=os.getenv("COMMAND_MODEL", "deepseek/deepseek-v4-flash:free"),
    temperature=0.2,
    api_key=OPENROUTER_API_KEY,
)


# ------------------------------------------------------------
# Vision model
# ------------------------------------------------------------
# Used by:
# - Vision Agent
#
# Must be a vision-capable model available through OpenRouter.
# If your chosen OpenRouter model does not support images, replace it in .env.
vision_llm = ChatOpenRouter(
    model=os.getenv("VISION_MODEL", "openai/gpt-4o-mini"),
    temperature=0.1,
    api_key=OPENROUTER_API_KEY,
)


# ------------------------------------------------------------
# Optional fast model
# ------------------------------------------------------------
# You can use this later for cheap/simple tasks if you want.
# If FAST_MODEL is not in .env, it uses GENERAL_MODEL fallback.
fast_llm = ChatOpenRouter(
    model=os.getenv(
        "FAST_MODEL",
        os.getenv("GENERAL_MODEL", "deepseek/deepseek-v4-flash:free"),
    ),
    temperature=0.2,
    api_key=OPENROUTER_API_KEY,
)


# ------------------------------------------------------------
# Simple registry
# ------------------------------------------------------------
# This is useful if nodes want to dynamically select a model by name.
# Still simple. No factories. No abstraction mess.
LLMS = {
    "orchestrator": orchestrator_llm,
    "critic": critic_llm,
    "general": general_llm,
    "report": report_llm,
    "command": command_llm,
    "vision": vision_llm,
    "fast": fast_llm,
}


def get_llm(name: str):
    """
    Optional helper for dynamic model selection.

    Example:
        llm = get_llm("critic")
    """
    if name not in LLMS:
        raise ValueError(f"Unknown LLM name: {name}. Available: {list(LLMS.keys())}")
    return LLMS[name]
