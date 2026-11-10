"""离线检查脚本入口、模块入口和兼容依赖组装。"""
import subprocess
import sys

from src import main


def test_script_and_module_help_are_identical():
    script = subprocess.run([sys.executable, "-X", "utf8", "src/main.py", "--help"], capture_output=True, text=True, encoding="utf-8", check=False)
    module = subprocess.run([sys.executable, "-X", "utf8", "-m", "src.main", "--help"], capture_output=True, text=True, encoding="utf-8", check=False)
    assert script.returncode == module.returncode == 0
    assert script.stdout == module.stdout and script.stderr == module.stderr == ""


def test_script_execution_does_not_import_second_main_module():
    program = "import runpy,sys; sys.argv=['src/main.py','--help']; runpy.run_path('src/main.py',run_name='__main__'); assert 'src.main' not in sys.modules"
    result = subprocess.run([sys.executable, "-X", "utf8", "-c", program], capture_output=True, text=True, encoding="utf-8", check=False)
    assert result.returncode == 0, result.stderr


def test_workflow_factory_uses_current_compatibility_dependencies(monkeypatch):
    class FakeLLM:
        pass
    def fake_retriever(queries):
        return queries
    monkeypatch.setattr(main, "LLMClient", FakeLLM)
    monkeypatch.setattr(main.pipeline, "retrieve_evidence", fake_retriever)
    workflow = main._proposal_workflow()
    assert workflow._llm_factory is FakeLLM
    assert workflow._retriever is fake_retriever


def test_workflow_class_remains_replaceable(monkeypatch):
    class FakeWorkflow:
        def __init__(self, **dependencies):
            self.dependencies = dependencies
    monkeypatch.setattr(main, "ProposalWorkflow", FakeWorkflow)
    assert isinstance(main._proposal_workflow(), FakeWorkflow)
