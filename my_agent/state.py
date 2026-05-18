

from typing import Any, Literal, Optional, TypedDict


Language = Literal["ar", "en", "mixed", "unknown"]
InputType = Literal["text", "image", "text_image", "unknown"]
RiskLevel = Literal["safe", "caution", "needs_clarification", "blocked"]

NextAction = Literal[
    "vision_analysis",
    "rag_answer",
    "web_research",
    "report_generation",
    "command_code_generation",
    "email_draft",
    "email_send",
    "artifact_export",
    "explain_more",
    "ask_user",
    "final_response",
    "refuse",
]

ArtifactType = Literal[
    "report",
    "markdown",
    "pdf",
    "docx",
    "text",
    "code",
    "commands",
    "image_analysis",
    "email",
    "other",
]

SourceType = Literal[
    "web",
    "rag",
    "pdf",
    "docx",
    "csv",
    "txt",
    "md",
    "json",
    "document",
    "image",
    "user_input",
    "tool",
    "other",
]


class MessageItem(TypedDict, total=False):
    role: Literal["user", "assistant", "system", "tool"]
    content: str
    name: Optional[str]
    metadata: dict[str, Any]


class InputMetadata(TypedDict, total=False):
    language: Language
    input_type: InputType
    clean_user_text: str
    has_reference: bool
    reference_phrases: list[str]
    has_external_action: bool
    external_action_type: str
    wants_artifact: bool
    artifact_type: str
    likely_user_intent: str
    notes_for_orchestrator: str


class SafetyResult(TypedDict, total=False):
    status: RiskLevel
    risk_level: Literal["low", "medium", "high", "blocked"]
    reason: str
    allowed_scope: str
    blocked_parts: list[str]
    requires_user_clarification: bool
    clarifying_question: str
    safe_alternative: str


class ContextSnapshot(TypedDict, total=False):
    compact_context: str
    current_topic: Optional[str]
    active_task: Optional[dict[str, Any]]
    latest_items: dict[str, Any]
    pending_approval: Optional[dict[str, Any]]
    important_recent_messages: list[MessageItem]
    context_confidence: float


class ContextResolution(TypedDict, total=False):
    resolved: bool
    phrase: str
    resolved_to: Literal[
        "latest_image_analysis",
        "latest_answer",
        "latest_report",
        "latest_artifact",
        "latest_code",
        "email_draft",
        "current_topic",
        "pending_approval",
        "unknown",
    ]
    resolved_value_summary: str
    confidence: float
    needs_user_confirmation: bool
    question_to_user: str


class OrchestratorDecision(TypedDict, total=False):
    understanding: str
    resolved_goal: str
    next_action: NextAction
    reason: str
    depends_on_previous_context: bool
    target_reference: dict[str, Any]
    needs_user_input: bool
    missing_info: list[str]
    needs_approval: bool
    approval_type: Literal["none", "send_email", "external_action"]
    stop_condition: str


class TaskStep(TypedDict, total=False):
    step: int
    action: str
    agent: str
    input_source: str
    requires_tool: bool
    expected_output: str


class TaskPlan(TypedDict, total=False):
    goal: str
    steps: list[TaskStep]
    approval_gates: list[dict[str, Any]]
    max_steps: int
    stop_condition: str


class SourceItem(TypedDict, total=False):
    source_id: str
    type: SourceType
    title: str
    url: Optional[str]
    path: Optional[str]
    content: str
    snippet: str
    score: Optional[float]
    metadata: dict[str, Any]


class ArtifactItem(TypedDict, total=False):
    artifact_id: str
    type: ArtifactType
    title: str
    filename: str
    path: str
    content_preview: str
    status: Literal["draft", "ready", "failed"]
    created_from: str
    available_actions: list[str]
    metadata: dict[str, Any]


class ImageAnalysis(TypedDict, total=False):
    image_id: str
    image_path: str
    image_type: str
    topic: str
    visible_text: str
    summary: str
    explanation: str
    uncertainties: list[str]
    confidence: float
    raw_output: str


class EmailDraft(TypedDict, total=False):
    draft_id: str
    to: str
    subject: str
    body: str
    attachment_artifact_id: Optional[str]
    attachment_path: Optional[str]
    status: Literal["draft_ready", "sent", "failed"]
    requires_confirmation: bool
    metadata: dict[str, Any]


class PendingApproval(TypedDict, total=False):
    type: Literal["confirm_send_email", "external_action", "need_email_recipient", "other"]
    message: str
    email_draft: Optional[EmailDraft]
    artifact_id: Optional[str]
    action: Optional[str]
    metadata: dict[str, Any]


class CriticResult(TypedDict, total=False):
    passed: bool
    checks: dict[str, Literal["pass", "fail", "unknown"]]
    failure_type: Literal[
        "none",
        "unsupported_claims",
        "unsafe",
        "missing_info",
        "missing_confirmation",
        "language_mismatch",
        "task_not_completed",
    ]
    confidence: float
    feedback: str
    recommended_action: str


class AgentOutput(TypedDict, total=False):
    type: str
    content: Any
    text: str
    confidence: Optional[float]
    metadata: dict[str, Any]


class AgentState(TypedDict, total=False):
    thread_id: str
    user_id: str
    turn_id: str

    raw_input: str
    image_path: Optional[str]
    image_paths: list[str]

    language: Language
    input_type: InputType
    input_metadata: InputMetadata

    messages: list[MessageItem]
    memory_summary: str

    context_snapshot: ContextSnapshot
    context_resolution: ContextResolution
    current_topic: Optional[str]
    active_task: Optional[dict[str, Any]]
    pending_approval: Optional[PendingApproval]

    latest_answer: Optional[str]
    latest_text_output: Optional[str]
    latest_agent_output: Optional[AgentOutput]
    latest_image_analysis: Optional[ImageAnalysis]
    latest_report: Optional[str]
    latest_report_id: Optional[str]
    latest_artifact_id: Optional[str]
    latest_code: Optional[str]
    latest_code_id: Optional[str]
    latest_commands: Optional[str]
    latest_email_draft_id: Optional[str]

    input_safety: SafetyResult
    output_safety: SafetyResult
    risk_level: RiskLevel

    orchestrator_decision: OrchestratorDecision
    plan: TaskPlan
    next_action: Optional[NextAction]
    step_count: int
    retry_count: int
    max_steps: int
    max_retries: int

    rag_query: Optional[str]
    rag_status: Optional[str]
    retrieved_docs: list[SourceItem]

    sources: list[SourceItem]
    web_sources: list[SourceItem]
    web_findings: str

    artifacts: list[ArtifactItem]
    generated_files: list[ArtifactItem]

    report_draft: Optional[str]
    generated_commands: Optional[str]
    generated_code: Optional[str]
    email_draft: Optional[EmailDraft]
    email_sent: bool

    critic_result: CriticResult
    critic_history: list[CriticResult]

    final_response: Optional[str]

    # Working orchestration hints used across turns.
    desired_output_format: Optional[str]
    latest_export_source_content: Optional[str]
    latest_smalltalk_answer: Optional[str]
    task_queue: list[dict[str, Any]]

    debug_log: list[dict[str, Any]]
    errors: list[dict[str, Any]]


def create_initial_state(
    *,
    thread_id: str = "local-thread",
    user_id: str = "local-user",
) -> AgentState:
    return AgentState(
        thread_id=thread_id,
        user_id=user_id,
        turn_id=None,
        raw_input="",
        image_path=None,
        image_paths=[],
        language="unknown",
        input_type="unknown",
        input_metadata={},
        messages=[],
        memory_summary="",
        context_snapshot={},
        context_resolution={},
        current_topic=None,
        active_task=None,
        pending_approval=None,
        latest_answer=None,
        latest_text_output=None,
        latest_agent_output=None,
        latest_image_analysis=None,
        latest_report=None,
        latest_report_id=None,
        latest_artifact_id=None,
        latest_code=None,
        latest_code_id=None,
        latest_commands=None,
        latest_email_draft_id=None,
        input_safety={},
        output_safety={},
        risk_level="safe",
        orchestrator_decision={},
        plan={},
        next_action=None,
        step_count=0,
        retry_count=0,
        max_steps=8,
        max_retries=2,
        rag_query=None,
        rag_status=None,
        retrieved_docs=[],
        sources=[],
        web_sources=[],
        web_findings="",
        artifacts=[],
        generated_files=[],
        report_draft=None,
        generated_commands=None,
        generated_code=None,
        email_draft=None,
        email_sent=False,
        critic_result={},
        critic_history=[],
        final_response=None,
        desired_output_format=None,
        latest_export_source_content=None,
        latest_smalltalk_answer=None,
        task_queue=[],
        debug_log=[],
        errors=[],
    )


def reset_turn_state(
    state: AgentState,
    *,
    raw_input: str,
    image_path: Optional[str] = None,
) -> AgentState:
    state["raw_input"] = raw_input
    state["image_path"] = image_path
    state["image_paths"] = [image_path] if image_path else []

    state["language"] = "unknown"
    state["input_type"] = "unknown"
    state["input_metadata"] = {}

    state["context_snapshot"] = {}
    state["context_resolution"] = {}

    state["input_safety"] = {}
    state["output_safety"] = {}
    state["risk_level"] = "safe"

    state["orchestrator_decision"] = {}
    state["plan"] = {}
    state["next_action"] = None

    state["step_count"] = 0
    state["retry_count"] = 0

    state["rag_query"] = None
    state["rag_status"] = None
    state["retrieved_docs"] = []

    state["latest_text_output"] = None
    state["latest_agent_output"] = None
    state["critic_result"] = {}
    state["final_response"] = None
    state["desired_output_format"] = None

    return state
