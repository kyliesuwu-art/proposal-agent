"""Application-layer use cases for the proposal agent."""

from src.application.proposal_workflow import (
    GenerateRequest,
    GenerateResult,
    GenerateWorkflowError,
    ProposalWorkflow,
)

__all__ = [
    "GenerateRequest",
    "GenerateResult",
    "GenerateWorkflowError",
    "ProposalWorkflow",
]
