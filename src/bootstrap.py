"""把现有业务能力连接成统一方案工作流。"""

def build_proposal_workflow(*, workflow_class, llm_factory, retriever, markdown_generator, word_renderer,
                            pptx_renderer, run_log_writer, request_writer):
    """创建工作流；本函数不执行检索或模型调用。"""
    from src.render_pptx import build_slides_markdown

    return workflow_class(
        llm_factory=llm_factory, retriever=retriever, markdown_generator=markdown_generator,
        word_renderer=word_renderer, slides_builder=build_slides_markdown,
        pptx_renderer=pptx_renderer, run_log_writer=run_log_writer, request_writer=request_writer,
    )
