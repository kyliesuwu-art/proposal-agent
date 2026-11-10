"""程序启动入口；命令解析和业务实现分别位于其他模块。"""

import sys
from pathlib import Path

# 直接运行脚本时，把项目根目录加入模块搜索路径。
_BOOTSTRAP_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BOOTSTRAP_PROJECT_ROOT))

from src.config import load_project_environment

# 保持历史启动顺序：加载项目环境后再导入业务依赖。
load_project_environment()

# 保留历史模块级接口，供 CLI 和已有调用方使用。
from src import pipeline
from src.adapters.parser import SUPPORTED_EXTENSIONS
from src.adapters.llm_client import LLMClient
from src.markdown_proposal import ProposalGenerationError, ProposalQualityError, ProposalWriteError, diagnose_markdown_proposal, generate_markdown_proposal
from src.render_word import RenderWordError, render_word
from src.render_pptx import RenderPptxError, render_pptx, write_pptx_layout_audit
from src.application import GenerateRequest, GenerateWorkflowError, ProposalWorkflow
from src.cli_ingest_helpers import _ingest_path, _ingest_cache_dir_dry_run, _ingest_cache_dir_resume
from src.proposal_logging import _write_proposal_request, _write_proposal_run_log
from src.bootstrap import build_proposal_workflow


def _proposal_workflow() -> ProposalWorkflow:
    """把当前模块的依赖交给工作流组装函数。"""
    return build_proposal_workflow(
        workflow_class=ProposalWorkflow, llm_factory=LLMClient, retriever=pipeline.retrieve_evidence,
        markdown_generator=generate_markdown_proposal, word_renderer=render_word,
        pptx_renderer=render_pptx, run_log_writer=_write_proposal_run_log,
        request_writer=_write_proposal_request,
    )


def main() -> None:
    """启动 CLI，并保留历史依赖替换方式。"""
    from src.cli import dispatch
    dispatch(sys.modules[__name__])


if __name__ == "__main__":
    main()
