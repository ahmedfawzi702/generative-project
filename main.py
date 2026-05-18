

import os
from dotenv import load_dotenv

load_dotenv()

from my_agent.agent import graph
from my_agent.state import create_initial_state, reset_turn_state
from my_agent.rag import (
    build_index_from_folder,
    clear_vectorstore,
    rag_status,
)


def get_langfuse_config():
    public_key = os.getenv("LANGFUSE_PUBLIC_KEY")
    secret_key = os.getenv("LANGFUSE_SECRET_KEY")
    host = os.getenv("LANGFUSE_HOST") or os.getenv("LANGFUSE_BASE_URL")

    if not public_key or not secret_key:
        print("[Langfuse] disabled: missing LANGFUSE_PUBLIC_KEY or LANGFUSE_SECRET_KEY")
        return {}

    if host:
        os.environ["LANGFUSE_HOST"] = host
        os.environ["LANGFUSE_BASE_URL"] = host

    try:
        from langfuse.langchain import CallbackHandler

        try:
            handler = CallbackHandler(
                public_key=public_key,
                secret_key=secret_key,
                host=host,
            )
        except TypeError:
            handler = CallbackHandler()

        print(f"[Langfuse] enabled: host={host}")
        return {
            "callbacks": [handler],
            "metadata": {"app": "Shieldy CLI"},
        }

    except Exception as exc:
        print(f"[Langfuse] disabled: {exc}")
        return {}


def parse_input(user_input: str):
    image_path = None
    raw_input = user_input

    if user_input.startswith("image:"):
        rest = user_input.replace("image:", "", 1).strip()
        parts = rest.split(" ", 1)
        image_path = parts[0]
        raw_input = parts[1] if len(parts) > 1 else "حلل الصورة دي"

    return raw_input, image_path


def print_help():
    print("Agent ready.")
    print("Commands:")
    print("- exit / quit")
    print("- /ingest or /build-index")
    print("- /rag-status")
    print("- /clear-rag")
    print("- image:/path/to/image.png your message")
    print()


def run_chat():
    print_help()

    thread_id = os.getenv("THREAD_ID", "local-thread")
    user_id = os.getenv("USER_ID", "local-user")

    state = create_initial_state(thread_id=thread_id, user_id=user_id)

    config = {
        "configurable": {
            "thread_id": thread_id,
        }
    }
    config.update(get_langfuse_config())

    while True:
        user_input = input("You: ").strip()

        if not user_input:
            continue

        if user_input.lower() in ["exit", "quit"]:
            break

        if user_input in ["/ingest", "/build-index"]:
            try:
                result = build_index_from_folder()
                print("\nRAG index built:")
                print(result)
                print()
            except Exception as exc:
                print("\nRAG build failed:")
                print(exc)
                print()
            continue

        if user_input == "/rag-status":
            print("\nRAG status:")
            print(rag_status())
            print()
            continue

        if user_input == "/clear-rag":
            print("\nRAG clear:")
            print(clear_vectorstore())
            print()
            continue

        raw_input, image_path = parse_input(user_input)
        state = reset_turn_state(state, raw_input=raw_input, image_path=image_path)

        try:
            result = graph.invoke(state, config=config)
        except Exception as exc:
            print("\nAssistant:")
            print(f"Runtime error: {exc}")
            print()
            continue

        print("\nAssistant:")
        print(result.get("final_response", "No response"))
        print()

        state.update(result)


if __name__ == "__main__":
    run_chat()
